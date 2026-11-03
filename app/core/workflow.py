"""Quy trình 8 bước của bảng điều khiển Photoshop, lưu theo ID vùng thoại.

1 Mở ảnh (bản Redraw hoặc Raw) → 2 Nhận diện + phân loại bóng (model) → 3 Thiết lập bảng thoại → 4 Nạp Raw
→ 5 OCR Raw → 6 Xoá chữ trong bóng kín → 7 Ghép bản dịch (model) → 8 Typeset.

Mỗi vùng thoại có ID bền (R01, R02…) dùng xuyên suốt. Trạng thái cả chương nằm trong một thư mục dự án (state.json +
ảnh từng trang). Ô nào người dùng sửa tay được đánh dấu "manual" và không bị bước khác ghi đè khi chạy lại.
Phần việc với Photoshop (chụp ảnh, gắn raw, tạo layer) do bảng điều khiển làm; ở đây chỉ đọc / ghi file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from . import ai
from .docs import load_pages, page_number

STEPS = [("open", "Mở ảnh"), ("detect", "Nhận diện & phân loại bóng (AI)"), ("setup", "Thiết lập bảng thoại"),
         ("raw", "Nạp bản Raw"), ("ocr", "OCR bản Raw"), ("clean", "Xoá chữ (bóng kín)"),
         ("match", "Ghép bản dịch (AI)"), ("typeset", "Typeset")]
STEP_NAMES = dict(STEPS)
# Thứ tự chạy "toàn bộ": cần raw trước khi nhận diện (bản redraw đã xoá chữ Nhật, không còn gì để nhận).
RUN_ORDER = ["open", "raw", "detect", "setup", "ocr", "clean", "match", "typeset"]
# Chạy lại một bước thì các bước dùng kết quả của nó cần chạy lại (dữ liệu cũ vẫn giữ, chỉ đổi trạng thái).
DOWNSTREAM = {"open": ["raw", "detect", "setup", "ocr", "clean", "match", "typeset"],
              "raw": ["ocr", "clean", "match", "typeset"], "detect": ["setup", "ocr", "clean", "match", "typeset"],
              "setup": ["clean", "typeset"], "ocr": ["match"], "clean": ["typeset"], "match": ["typeset"]}
BUBBLES = {"round": "Tròn kín", "square": "Vuông kín", "ray": "Có tia", "cloud": "Mây suy nghĩ", "sfx": "SFX",
           "free": "Ngoài khung", "skip": "Không dịch"}
CLEAN_KINDS = ["round", "square", "ray", "cloud"]  # mặc định: chỉ xoá chữ trong bóng thoại kín
MATCH_IOU = 0.5  # nhận diện lại: khung chồng ≥ 50% là cùng một vùng (giữ ID và phần sửa tay)
ERASED_DIFF = 60  # bản redraw khác raw > 60 mức sáng: chỗ chữ đã bị xoá
ERASED_MIN = 0.25  # vùng có ≥ 25% nét chữ bị xoá trong bản redraw: vùng cần dịch
ERASED_MAX_PAGE = 0.25  # khác nhau > 25% trang: hai ảnh lệch khung, không dùng phép so
STATES = {"pending": "Chưa chạy", "done": "Xong", "warn": "Xong · cần kiểm tra", "error": "Lỗi", "stale": "Cần chạy lại",
          "running": "Đang chạy"}


def projects_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Lilyzono" / "panel"


def slug(text: str) -> str:
    text = re.sub(r"[^\w\-. ]+", "_", text, flags=re.UNICODE).strip(" ._") or "trang"
    return text[:60]


def default_kind_map(kinds: dict) -> dict:
    """Loại bóng → kiểu chữ (loại font) của preset."""
    def pick(*names):
        return next((n for n in names if n in kinds), "normal")
    return {"round": pick("normal"), "square": pick("square"), "ray": pick("bold", "thought"),
            "cloud": pick("thought"), "sfx": pick("sfx"), "free": pick("free", "normal")}


def new_region(rid: str, box, det_kind: str = "text", src: str = "detect") -> dict:
    return {"id": rid, "box": [int(v) for v in box], "det_kind": det_kind, "src": src, "bubble": "round",
            "bubble_src": "auto", "order": 0, "ja": "", "ja_src": "", "lines": [], "lines_src": "",
            "confidence": None, "uncertain": False, "note": "", "style_kind": "", "font": {}, "size": 0,
            "erased": None, "cleaned": False}


def manual(r: dict) -> bool:
    """Vùng có phần người dùng sửa tay (giữ lại khi nhận diện lại)."""
    return (r["src"] == "manual" or "manual" in (r["bubble_src"], r["ja_src"], r["lines_src"])
            or bool(r["style_kind"] or r["font"] or r["size"]))


def iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def imread(path) -> np.ndarray:
    img = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Không đọc được ảnh: {path}")
    return img


def imwrite(path, img) -> None:
    cv2.imencode(Path(path).suffix or ".png", img)[1].tofile(str(path))


def file_hash(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Project:
    """Một chương: thiết lập chung + các trang (mỗi trang là một tài liệu đang mở trong Photoshop)."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "state.json"
        self.data = {"version": 1, "source": "raw", "profile": "gai_bang", "right_to_left": True,
                     "translation_file": "", "numbering": "name", "raw_folder": "", "kind_map": {}, "kind_fonts": {},
                     "clean_kinds": list(CLEAN_KINDS), "pages": []}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text(encoding="utf-8")))
        self._tl_cache = (None, None)

    # --- lưu / đọc ---------------------------------------------------------------------------
    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    @property
    def pages(self) -> list[dict]:
        return self.data["pages"]

    def page(self, key: str) -> dict:
        for p in self.pages:
            if p["key"] == key:
                return p
        raise KeyError(key)

    def page_dir(self, page: dict) -> Path:
        d = self.folder / slug(page["key"])
        d.mkdir(exist_ok=True)
        return d

    def file(self, page: dict, name: str) -> Path:
        return self.page_dir(page) / name

    def has(self, page: dict, name: str) -> bool:
        return self.file(page, name).exists()

    def sync_documents(self, docs: list[dict]) -> None:
        """Danh sách trang theo các tài liệu đang mở: giữ dữ liệu trang cũ (khớp theo tên), thêm trang mới."""
        old = {p["key"]: p for p in self.pages}
        pages = []
        for d in docs:
            p = old.get(d["name"]) or {"key": d["name"], "regions": [], "lines": [], "lines_manual": False,
                                       "tl_page": None, "unplaced": [], "status": {}, "next_id": 1, "size": None}
            p["doc_id"], p["doc_name"], p["open"] = d.get("id"), d["name"], True
            pages.append(p)
        rest = [p for k, p in old.items() if k not in {d["name"] for d in docs}]  # trang đã đóng: giữ lại, để cuối
        for p in rest:
            p["open"] = False
        self.data["pages"] = sorted(pages, key=lambda p: _page_sort(p["key"])) + rest

    # --- trạng thái từng bước --------------------------------------------------------------------
    def status(self, page: dict, step: str) -> dict:
        return page["status"].get(step) or {"state": "pending", "msg": ""}

    def set_status(self, page: dict, step: str, state: str, msg: str = "") -> None:
        page["status"][step] = {"state": state, "msg": msg, "at": time.strftime("%H:%M:%S")}

    def invalidate(self, page: dict, step: str) -> None:
        """Kết quả bước `step` đã đổi: các bước sau đã chạy thì thành “cần chạy lại”."""
        for s in DOWNSTREAM.get(step, []):
            if self.status(page, s)["state"] in ("done", "warn"):
                self.set_status(page, s, "stale", f"{STEP_NAMES[step]} đã đổi")

    # --- bản dịch ---------------------------------------------------------------------------
    def translation(self) -> dict[int, list[str]]:
        path = self.data["translation_file"]
        if not path:
            return {}
        if not Path(path).is_file():
            raise ValueError(f"Không tìm thấy file bản dịch: {path}")
        key = (path, os.path.getmtime(path))
        if self._tl_cache[0] != key:
            self._tl_cache = (key, load_pages(path))
        return self._tl_cache[1]

    def tl_page(self, page: dict) -> int | None:
        """Số trang trong bản dịch: tự chỉnh tay, hoặc theo số trong tên ảnh / thứ tự ảnh."""
        if page.get("tl_page"):
            return page["tl_page"]
        if self.data["numbering"] == "order":
            return self.pages.index(page) + 1
        return page_number(page["key"])

    def page_lines(self, page: dict) -> list[str]:
        if page.get("lines_manual"):
            return list(page["lines"])
        pages = self.translation()
        n = self.tl_page(page)
        if n is not None and n in pages:
            return list(pages[n])
        if list(pages) == [0] and len(self.pages) == 1:
            return list(pages[0])
        return []

    def region(self, page: dict, rid: str) -> dict:
        for r in page["regions"]:
            if r["id"] == rid:
                return r
        raise KeyError(rid)

    def next_id(self, page: dict) -> str:
        used = {r["id"] for r in page["regions"]}
        n = page.get("next_id", 1)
        while f"R{n:02d}" in used:
            n += 1
        page["next_id"] = n + 1
        return f"R{n:02d}"

    def style_kind(self, region: dict) -> str:
        """Kiểu chữ (loại font của preset) của vùng: chỉnh tay, không thì theo loại bóng."""
        from .typeset_profiles import kinds_for

        kinds = kinds_for(self.data["profile"])
        if region["style_kind"] in kinds:
            return region["style_kind"]
        mapping = {**default_kind_map(kinds), **self.data.get("kind_map", {})}
        kind = mapping.get(region["bubble"], "normal")
        return kind if kind in kinds else "normal"


def _page_sort(name: str):
    nums = re.findall(r"\d+", Path(name).stem)
    return (int(nums[0]) if nums else 10 ** 6, name)


# --- bước 2: nhận diện + phân loại ----------------------------------------------------------------
def heuristic_bubble(img: np.ndarray, mask: np.ndarray, box, det_kind: str) -> str:
    """Loại bóng đoán bằng hình học (khi chưa có / không có câu trả lời của model)."""
    from .typeset_profiles import detailed_kind

    if det_kind == "sfx":
        return "sfx"
    kind = detailed_kind(SimpleNamespace(original=img, text_mask=mask), tuple(box))
    return {"square": "square", "thought": "cloud", "free": "free", "bold": "ray"}.get(kind, "round")


def erased_mask(raw: np.ndarray, redraw: np.ndarray) -> np.ndarray | None:
    """Chỗ bản redraw đã xoá chữ (khác raw rõ rệt); None nếu hai ảnh lệch khung."""
    if raw.shape != redraw.shape:
        redraw = cv2.resize(redraw, (raw.shape[1], raw.shape[0]), interpolation=cv2.INTER_AREA)
    diff = (np.abs(raw.astype(np.int16) - redraw).max(axis=2) > ERASED_DIFF).astype(np.uint8)
    diff = cv2.morphologyEx(diff, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return None if diff.mean() > ERASED_MAX_PAGE else diff


def _erased_fraction(erased: np.ndarray, mask: np.ndarray, box) -> float:
    x1, y1, x2, y2 = box
    ink = mask[y1:y2, x1:x2] > 0.3
    if not ink.any():
        return float(erased[y1:y2, x1:x2].mean() > 0.02)
    near = cv2.dilate(erased[y1:y2, x1:x2], np.ones((5, 5), np.uint8)) > 0
    return float((ink & near).sum() / ink.sum())


def _missed_blobs(erased: np.ndarray, boxes: list, size) -> list[list[int]]:
    """Chữ đã xoá trong bản redraw mà model nhận diện bỏ sót (băng tên chương, chú thích): mỗi cụm một vùng mới."""
    w, h = size
    k = max(3, round(w * 0.012))
    blobs = cv2.dilate(erased, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(blobs)
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if bw * bh < (0.02 * w) ** 2 or erased[y:y + bh, x:x + bw].sum() < 0.05 * bw * bh:
            continue
        box = [int(x), int(y), int(x + bw), int(y + bh)]
        if any(iou(box, b) > 0.1 or (b[0] <= box[0] and b[1] <= box[1] and b[2] >= box[2] and b[3] >= box[3])
               for b in boxes):
            continue
        out.append(box)
    return out


def _snap(mask: np.ndarray, box, size) -> list[int]:
    """Khung model trả về (ước lượng): thu về đúng nét chữ bên trong nếu có."""
    w, h = size
    x1, y1, x2, y2 = box
    ex, ey = (x2 - x1) // 10, (y2 - y1) // 10
    x1, y1, x2, y2 = max(0, x1 - ex), max(0, y1 - ey), min(w, x2 + ex), min(h, y2 + ey)
    ys, xs = np.nonzero(mask[y1:y2, x1:x2] > 0.3)
    if len(xs) < 20:
        return [int(v) for v in box]
    return [int(x1 + xs.min()), int(y1 + ys.min()), int(x1 + xs.max() + 1), int(y1 + ys.max() + 1)]


def detect(project: Project, page: dict, detector, cfg: ai.AIConfig, report=None) -> list[str]:
    """Nhận diện vùng chữ (model comic-text-detector) rồi gửi ảnh có đánh số ID cho model AI phân loại bóng, xếp thứ tự
    đọc, tìm chữ bị sót. Vùng nhận diện được luôn được lưu; model AI lỗi thì ném AIError sau khi lưu."""
    from .detect import sort_reading_order
    from .page import color_text_mask

    report = report or (lambda s: None)
    redraw = project.data["source"] == "redraw"
    has_raw = project.has(page, "raw.png")
    img = imread(project.file(page, "raw.png" if has_raw else "work.png"))
    warnings = []
    if redraw and not has_raw:
        warnings.append("Bản redraw chưa có raw: nhận diện trên bản redraw (chữ đã xoá sẽ không thấy). Nạp Raw rồi chạy lại.")
    report("Nhận diện vùng chữ…")
    boxes, mask = detector.detect(img)
    mask = np.maximum(mask, color_text_mask(img))
    boxes = sort_reading_order(boxes, project.data["right_to_left"], img)
    np.savez_compressed(project.file(page, "mask.npz"), mask=mask.astype(np.float16))
    h, w = img.shape[:2]
    found = [([b.x1, b.y1, b.x2, b.y2], b.kind, "detect") for b in boxes]
    erased = None
    if redraw and has_raw:
        erased = erased_mask(img, imread(project.file(page, "work.png")))
        if erased is None:
            warnings.append("Bản redraw lệch khung so với raw: không so được chỗ đã xoá chữ.")
        else:
            found += [(b, "text", "diff") for b in _missed_blobs(erased, [f[0] for f in found], (w, h))]
    # Ghép với vùng cũ: giữ ID và phần sửa tay.
    old, used, regions = list(page["regions"]), set(), []
    for box, det_kind, src in found:
        best = max((o for o in old if o["id"] not in used), key=lambda o: iou(o["box"], box), default=None)
        if best is not None and iou(best["box"], box) >= MATCH_IOU:
            used.add(best["id"])
            r = best
            if r["src"] != "manual":
                r["box"], r["det_kind"], r["src"] = [int(v) for v in box], det_kind, src
        else:
            r = new_region(project.next_id(page), box, det_kind, src)
        regions.append(r)
    regions += [o for o in old if o["id"] not in used and manual(o)]
    for i, r in enumerate(regions):
        r["order"] = i + 1
        if r["bubble_src"] != "manual":
            r["bubble"], r["bubble_src"] = heuristic_bubble(img, mask, r["box"], r["det_kind"]), "auto"
            r["confidence"], r["uncertain"], r["note"] = None, False, ""
        if erased is not None:
            r["erased"] = _erased_fraction(erased, mask, r["box"]) >= ERASED_MIN
            if r["src"] == "diff":
                r["note"] = "Chữ đã xoá trong bản redraw (nhận diện bỏ sót)"
            elif not r["erased"] and r["bubble_src"] != "manual":
                r["bubble"], r["note"] = "skip", "Bản redraw giữ nguyên chữ này: không dịch"
    page["regions"] = regions
    page["size"] = [w, h]
    project.save()
    report(f"AI phân loại {len(regions)} vùng…")
    result = ai.classify(cfg, img, regions, project.data["right_to_left"],
                         {r["id"]: r["erased"] for r in regions} if erased is not None else None)
    for r in regions:
        got = result["regions"].get(r["id"])
        if got is None or r["bubble_src"] == "manual":
            continue
        r["bubble"], r["bubble_src"] = got["bubble"], "ai"
        r["confidence"], r["note"] = got["confidence"], got["note"] or r["note"]
        r["uncertain"] = got["confidence"] < ai.UNSURE
        if got["order"]:
            r["order"] = got["order"]
    for m in result["missed"]:
        box = _snap(mask, m["box"], (w, h))
        if any(iou(box, r["box"]) > 0.3 for r in regions):
            continue
        r = new_region(project.next_id(page), box, "text", "ai")
        r.update(bubble=m["bubble"], bubble_src="ai", uncertain=True, order=10 ** 4,
                 note="AI thêm vùng bị sót – kiểm tra khung" + (f": {m['note']}" if m["note"] else ""))
        regions.append(r)
    # Thứ tự đọc theo AI; vùng không dịch xuống cuối.
    regions.sort(key=lambda r: (r["bubble"] == "skip", r["order"]))
    for i, r in enumerate(regions):
        r["order"] = i + 1
    project.save()
    return warnings + result["warnings"]


# --- bước 3: thiết lập ----------------------------------------------------------------------------
def setup(project: Project, page: dict) -> list[str]:
    """Kiểm tra bảng thoại trước khi xoá chữ / type: font của preset và font chọn tay phải có trên máy."""
    from .fonts import files_for
    from .typeset_profiles import CUSTOM, fonts_for

    if not page["regions"]:
        raise ValueError("Trang chưa có vùng thoại nào (chạy bước 2).")
    warnings, profile = [], project.data["profile"]
    families = set() if profile == CUSTOM else {fonts_for(profile)[project.style_kind(r)] for r in page["regions"]
                                                     if r["bubble"] != "skip"}
    families |= {f["family"] for f in project.data.get("kind_fonts", {}).values() if f.get("family")}
    families |= {r["font"]["family"] for r in page["regions"] if r["font"].get("family")}
    missing = sorted(f for f in families if not files_for(f))
    if missing:
        warnings.append("Máy chưa cài font (chữ sẽ đo sai cỡ): " + ", ".join(missing))
    unsure = [r["id"] for r in page["regions"] if r["uncertain"] and r["bubble_src"] != "manual"]
    if unsure:
        warnings.append("Vùng AI chưa chắc loại bóng: " + ", ".join(unsure))
    return warnings


# --- bước 4: raw ---------------------------------------------------------------------------
def raw_files_for(page_name: str, folder: str, right_to_left: bool = True) -> list[str]:
    """Ảnh raw của một trang trong folder, xếp từ trái sang phải: cùng số trang; trang đôi "03-04" lấy raw 4 (trái) và
    3 (phải) khi đọc phải → trái. Thiếu ảnh thì trả về []."""
    if not folder or not Path(folder).is_dir():
        return []
    exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")
    by_number = {}
    for f in sorted(Path(folder).iterdir()):
        if f.suffix.lower() in exts:
            n = page_number(f.name)
            if n is not None:
                by_number.setdefault(n, str(f))
    stem = Path(page_name).stem
    m = re.search(r"(\d+)\s*[-–_~]\s*(\d+)\s*$", stem)
    if m and 0 < int(m.group(2)) - int(m.group(1)) < 4:
        first, last = int(m.group(1)), int(m.group(2))
        order = range(last, first - 1, -1) if right_to_left else range(first, last + 1)
        files = [by_number.get(k) for k in order]
        return files if all(files) else []
    n = page_number(page_name)
    return [by_number[n]] if n in by_number else []


# --- bước 5: OCR ---------------------------------------------------------------------------
def ocr(project: Project, page: dict, report=None) -> list[str]:
    from .ocr import reader

    read = reader()
    if read is None:
        raise ValueError("Thiếu model đọc chữ Nhật (models/manga-ocr). Xem README, mục “Card đồ hoạ và đọc chữ Nhật”.")
    if not project.has(page, "raw.png"):
        raise ValueError("Chưa có bản Raw (bước 4).")
    img = imread(project.file(page, "raw.png"))
    todo = [r for r in page["regions"] if r["bubble"] != "skip" and r["ja_src"] != "manual"]
    for i, r in enumerate(todo):
        if report:
            report(f"Đọc chữ {r['id']} ({i + 1}/{len(todo)})…")
        r["ja"], r["ja_src"] = read.read(img, r["box"]), "ocr"
    project.save()
    empty = [r["id"] for r in todo if not r["ja"].strip()]
    return ["Không đọc được chữ ở: " + ", ".join(empty)] if empty else []


# --- trang dựng lại từ file ---------------------------------------------------------------------
def _page_object(project: Project, page: dict):
    from .page import Page

    if not project.has(page, "mask.npz"):
        raise ValueError("Chưa nhận diện vùng chữ (bước 2).")
    base = project.file(page, "raw.png" if project.has(page, "raw.png") else "work.png")
    pg = Page(str(base))
    pg.text_mask = np.load(project.file(page, "mask.npz"))["mask"].astype(np.float32)
    pg.typeset_profile = project.data["profile"]
    pg.detected = True
    return pg


# --- bước 6: xoá chữ ---------------------------------------------------------------------------
def clean(project: Project, page: dict) -> tuple[str | None, list[str]]:
    """Chỉ xoá chữ Nhật trong bóng thoại kín (loại trong `clean_kinds`), chỉ chỗ nền một màu. Trả về (layer PNG, cảnh báo).
    Bản redraw: chữ đã xoá sẵn, không làm gì."""
    from .clean import easy_only
    from .page import Region
    from .psd_export import clean_layer

    if project.data["source"] == "redraw":
        for r in page["regions"]:
            r["cleaned"] = False
        return None, ["Bản redraw: chữ đã được xoá sẵn, bỏ qua bước này."]
    pg = _page_object(project, page)
    kinds = set(project.data.get("clean_kinds") or CLEAN_KINDS)
    pairs = [(rd, Region(tuple(rd["box"]), kind="sfx" if rd["bubble"] == "sfx" else "text"))
             for rd in page["regions"] if rd["bubble"] in kinds]
    pg.regions = [r for _, r in pairs]
    pg.inpainter = easy_only
    for r in pg.regions:  # như luồng type cũ: bóng sáng khép kín, hoặc nền đúng một màu quanh chữ
        if pg._clean_local(r) is None and not r.needs_redraw:
            r.cleaned = True
    for rd in page["regions"]:
        rd["cleaned"] = False
    for rd, r in pairs:
        rd["cleaned"] = bool(r.cleaned)
    imwrite(project.file(page, "cleaned.png"), pg.working)
    layer = project.file(page, "clean_layer.png")
    imwrite(layer, clean_layer(pg))
    project.save()
    left = [rd["id"] for rd, r in pairs if not r.cleaned]
    return str(layer), (["Nền không trơn, để nguyên chữ gốc (chữ dịch đặt đè, viền trắng): " + ", ".join(left)]
                        if left else [])


# --- bước 7: ghép bản dịch ---------------------------------------------------------------------
def match(project: Project, page: dict, cfg: ai.AIConfig, report=None) -> list[str]:
    """Model AI ghép từng câu dịch của trang vào ID vùng thoại. Vùng đã sửa tay giữ nguyên câu của nó."""
    lines = [s for s in project.page_lines(page) if s.strip()]
    if not lines:
        n = project.tl_page(page)
        raise ValueError(f"Bản dịch không có trang {n if n is not None else '?'} (chọn file bản dịch hoặc sửa số trang).")
    locked = [r for r in page["regions"] if r["lines_src"] == "manual"]
    pool = list(range(len(lines)))
    for r in locked:  # câu đã gán tay: bỏ khỏi danh sách gửi đi
        for s in r["lines"]:
            i = next((i for i in pool if lines[i].strip() == s.strip()), None)
            if i is not None:
                pool.remove(i)
    cands = [r for r in page["regions"] if r["bubble"] != "skip" and r["lines_src"] != "manual"]
    if report:
        report(f"AI ghép {len(pool)} câu vào {len(cands)} vùng…")
    img = imread(project.file(page, "raw.png" if project.has(page, "raw.png") else "work.png"))
    size = (img.shape[1], img.shape[0])
    result = ai.match(cfg, cands, [lines[i] for i in pool], size, img)
    for r in cands:
        r["lines"], r["lines_src"] = [], ""
        if r["src"] != "manual" and r["bubble_src"] != "manual":
            r["uncertain"] = bool(r["confidence"] is not None and r["confidence"] < ai.UNSURE)
    by_id = {r["id"]: r for r in cands}
    for k in sorted(result["assign"]):
        rid, conf, note = result["assign"][k]
        r = by_id[rid]
        r["lines"].append(lines[pool[k]])
        r["lines_src"] = "ai"
        r["confidence"] = conf if r["confidence"] is None or len(r["lines"]) == 1 else min(r["confidence"], conf)
        if conf < ai.UNSURE:
            r["uncertain"] = True
            r["note"] = f"Câu “{lines[pool[k]][:30]}”: {note or 'AI chưa chắc'}"
    page["unplaced"] = [{"text": lines[pool[k]], "note": "AI không tìm được vùng"} for k in result["unplaced"]]
    project.save()
    warnings = list(result["warnings"])
    if page["unplaced"]:
        warnings.append(f"{len(page['unplaced'])} câu chưa có vùng: gán tay trong bảng thoại")
    unsure = [r["id"] for r in cands if r["uncertain"] and r["lines"]]
    if unsure:
        warnings.append("Ghép chưa chắc: " + ", ".join(unsure))
    return warnings


# --- bước 8: typeset ---------------------------------------------------------------------------
def typeset(project: Project, page: dict, report=None) -> tuple[str, list[str]]:
    """Dàn chữ theo bảng thoại; ghi typeset.json (thông số layer chữ cho Photoshop) và preview.png."""
    from .assign import apply_plan
    from .document import Document
    from .page import Region
    from .psd_export import layer_specs
    from .typeset import TextStyle, clear_caches
    from .typeset_profiles import PROFILES, kinds_for, region_kind

    rows = sorted((r for r in page["regions"] if r["lines"] and r["bubble"] != "skip"), key=lambda r: r["order"])
    if not rows:
        raise ValueError("Chưa có vùng nào có câu dịch (chạy bước 7 hoặc nhập câu dịch trong bảng thoại).")
    profile = project.data["profile"]
    kinds = kinds_for(profile)
    pg = _page_object(project, page)
    redraw = project.data["source"] == "redraw"
    if redraw:
        pg.set_redrawn(str(project.file(page, "work.png")))
    elif project.has(page, "cleaned.png"):
        pg.working = imread(project.file(page, "cleaned.png"))
    owner, plan = {}, []
    for rd in rows:
        reg = Region(tuple(rd["box"]), kind="sfx" if rd["bubble"] == "sfx" else "text")
        reg.override = {**project.data.get("kind_fonts", {}).get(rd["bubble"], {}), **rd["font"]}
        reg.style = TextStyle(size=int(rd["size"] or 0))
        owner[reg.uid] = rd
        plan.append((reg, list(rd["lines"])))
    pg.regions = [reg for reg, _ in plan]
    if report:
        report("Dàn chữ…")
    apply_plan(pg, plan, None)
    for r in pg.regions:
        rd = owner.get(r.uid) or owner.get(r.group)
        marked = r.marked[1:] if r.marked.startswith("!") else r.marked
        kind = rd["style_kind"] if rd["style_kind"] in kinds else (marked if marked in kinds else project.style_kind(rd))
        r.typeset_kind = kind
        if redraw:
            r.cleaned = r.kind == "sfx"
        else:
            r.cleaned, r.needs_redraw = True, not rd["cleaned"]
    for r in pg.regions:
        if r.kind == "sfx":
            pg.clean(r)
    pg.clean_many([r for r in pg.regions if r.kind != "sfx"])
    translated = [r for r in pg.regions if r.cleaned and r.text.strip()]
    specs = layer_specs(pg)
    for r, spec in zip(translated, specs):
        rd = owner.get(r.uid) or owner.get(r.group)
        spec["id"] = rd["id"] if rd else ""
        spec["typeset_kind"] = region_kind(pg, r)
        spec["family"] = r.style.family
        if r.override.get("postscript"):
            spec["font"] = r.override["postscript"]  # đúng font chọn trong danh sách của Photoshop
        spec["name"] = f"{spec['id']} · {spec['contents']}".replace("\r", " ").replace("\u0003", " ")[:120]
    pg.doc = Document(pg)
    preview = pg.doc.composite()
    imwrite(project.file(page, "preview.png"), preview)
    out = project.file(page, "typeset.json")
    out.write_text(json.dumps({"size": list(pg.size), "profile_name": PROFILES.get(profile, "Typeset"), "redraw": "",
                               "layers": specs}, ensure_ascii=False), encoding="utf-8")
    clear_caches()
    warnings = []
    if page.get("unplaced"):
        warnings.append(f"{len(page['unplaced'])} câu chưa có vùng nên chưa type")
    overlay = [s["id"] for s in specs if s.get("redraw")]
    if overlay:
        warnings.append("Chữ đặt đè lên chữ gốc (chưa xoá): " + ", ".join(dict.fromkeys(overlay)))
    return str(out), warnings


def copy_image(src, dst) -> bool:
    """Chép ảnh; trả về True nếu nội dung đổi so với bản cũ."""
    dst = Path(dst)
    before = file_hash(dst) if dst.exists() else None
    shutil.copyfile(src, dst)
    return file_hash(dst) != before
