"""So sánh kết quả tự động của EditManga với bộ PSD bản Việt làm tay.

Cách dùng:
    python tools/evaluate.py <thư mục raw> <thư mục PSD mẫu> <thư mục kết quả>

Với mỗi trang:
  - lấy câu dịch, font, cỡ chữ, khung chữ từ các lớp chữ trong PSD mẫu;
  - cho tool tự nhận diện vùng chữ trên ảnh raw, gán câu dịch theo thứ tự đọc, xoá chữ, đặt chữ;
  - đo: lớp chữ mẫu nào tool không khoanh được / bị gộp / gán nhầm câu, vùng thừa,
    chữ Nhật còn sót sau khi xoá, độ lệch cỡ chữ và vị trí;
  - lưu ảnh so sánh  raw | tool | mẫu  vào thư mục kết quả.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtGui import QFontDatabase, QGuiApplication  # noqa: E402

from app.core import lama  # noqa: E402
from app.core.assign import assign_lines  # noqa: E402
from app.core.detect import TextBox, TextDetector, sort_reading_order  # noqa: E402
from app.core.page import Page  # noqa: E402
from app.core.typeset import TextStyle  # noqa: E402


def read_reference(psd_path: str):
    from psd_tools import PSDImage

    psd = PSDImage.open(psd_path)
    layers = []
    for L in psd.descendants():
        if L.kind != "type":
            continue
        ss = L.engine_dict["StyleRun"]["RunArray"][0]["StyleSheet"]["StyleSheetData"]
        fonts = [str(f["Name"]).strip("'") for f in L.resource_dict["FontSet"]]
        xx, xy = L.transform[0], L.transform[1]
        x1, y1, x2, y2 = L.bbox
        if not L.text.strip() or L.bbox[2] <= L.bbox[0]:
            continue  # lớp chữ rỗng
        layers.append({
            "text": L.text.replace("\r", "\n"), "bbox": (x1, y1, x2, y2), "font": fonts[int(ss.get("Font", 0))],
            "size": float(ss.get("FontSize", 0)) * math.hypot(xx, xy),
            "rotation": math.degrees(math.atan2(xy, xx)),
        })
    ref_img = cv2.cvtColor(np.array(psd.composite().convert("RGB")), cv2.COLOR_RGB2BGR)
    return layers, ref_img


def family_for(postscript: str, families: list[str]) -> str:
    """Tên PostScript trong PSD (vd 000WildWords2TB-Roman) → tên họ font Qt (vd 000 WildWords2 TB)."""
    key = postscript.split("-")[0].lower().replace("[", "").replace("]", "")
    norm = {f: f.lower().replace(" ", "").replace("[", "").replace("]", "") for f in families}
    exact = [f for f, n in norm.items() if n == key]
    if exact:
        return exact[0]
    cands = sorted((f for f, n in norm.items() if n.startswith(key) or key.startswith(n)), key=len)
    return cands[0] if cands else "Arial"


def inside(pt, box, pad=0):
    return box[0] - pad <= pt[0] <= box[2] + pad and box[1] - pad <= pt[1] <= box[3] + pad


def center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def match_regions(regions, refs):
    """Ghép lớp chữ mẫu ↔ vùng tool tìm được theo vị trí: tâm chữ mẫu nằm trong khung đặt chữ/khung gốc."""
    owner = {}
    for j, ref in enumerate(refs):
        c = center(ref["bbox"])
        best, best_d = None, 1e9
        for i, r in enumerate(regions):
            areas = [r.box] + list(r.text_rects or [])
            if any(inside(c, a, pad=12) for a in areas):
                d = math.dist(c, center(r.box))
                if d < best_d:
                    best, best_d = i, d
        owner[j] = best
    return owner


def evaluate(raw_dir, psd_dir, out_dir, pages=None, engine="lama", order_fix=None):
    """`order_fix`: {trang: [(câu a, câu b), …]} – các cặp câu mà thứ tự đọc đúng phụ thuộc nội dung
    (quy tắc vị trí không đoán được), dùng để sửa đáp án thứ tự."""
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    families = QFontDatabase.families()
    det = TextDetector()
    if engine == "lama":
        lama.get_session()
    os.makedirs(out_dir, exist_ok=True)
    report, totals = [], {"ref": 0, "found": 0, "merged": 0, "auto_ok": 0, "auto_extra_used": 0, "missed": 0, "extra": 0, "wrong_line": 0,
                          "leftover": 0, "size_err": [], "pos_err": []}
    names = sorted(f[:-4] for f in os.listdir(psd_dir) if f.lower().endswith(".psd"))
    for name in names if not pages else [n for n in names if n in pages]:
        raw_path = next(os.path.join(raw_dir, name + e) for e in (".jpg", ".png", ".webp", ".jpeg")
                        if os.path.exists(os.path.join(raw_dir, name + e)))
        refs, ref_img = read_reference(os.path.join(psd_dir, name + ".psd"))
        page = Page(raw_path)
        page.inpainter = lama.inpaint if engine == "lama" else None
        t0 = time.time()
        page.detect(det, right_to_left=True)
        # Đáp án thứ tự đọc (như file docs người dịch đưa): thứ tự panel trên ảnh raw + các cặp sửa tay.
        raw_img = cv2.imread(raw_path)
        order = sort_reading_order([TextBox(*r["bbox"], 1.0) for r in refs], True, raw_img)
        refs = [next(r for r in refs if tuple(r["bbox"]) == (b.x1, b.y1, b.x2, b.y2)) for b in order]
        for a, b in (order_fix or {}).get(name, []):
            ia = next(i for i, r in enumerate(refs) if r["text"].startswith(a))
            ib = next(i for i, r in enumerate(refs) if r["text"].startswith(b))
            refs[ia], refs[ib] = refs[ib], refs[ia]
        owner = match_regions(page.regions, refs)
        by_region: dict[int, list[int]] = {}
        for j, i in owner.items():
            if i is not None:
                by_region.setdefault(i, []).append(j)
        # Người dùng xoá các khung thừa (bản mẫu giữ nguyên chỗ đó), rồi tool gán câu dịch lần lượt
        # theo thứ tự vùng: đếm câu rơi vào sai bóng.
        extra = [i for i in range(len(page.regions)) if i not in by_region]
        seq = [j for i in range(len(page.regions)) for j in sorted(by_region.get(i, []))]
        wrong = sum(1 for k, j in enumerate(seq) if j != k)
        # Tự ghép: đưa toàn bộ câu của trang (như file docs theo trang) cho tool tự ghép vào các vùng.
        plan = assign_lines(page, page.regions, [r["text"] for r in refs])
        pos, used = {}, set()
        texts = [r["text"] for r in refs]
        for reg, ls in plan:
            ri = page.regions.index(reg)
            for line in ls:  # chấm theo nội dung câu (bước đổi chỗ có thể đảo thứ tự)
                j = next(j for j, t in enumerate(texts) if t == line and j not in used)
                used.add(j)
                pos[j] = ri
        auto_ok = sum(1 for j, ri in pos.items() if j in by_region.get(ri, []))
        auto_extra = sum(1 for reg, _ in plan if page.regions.index(reg) not in by_region)
        kept = [page.regions[i] for i in sorted(by_region)]
        page.clean_many(kept)
        # Đặt chữ: dùng đúng câu + font của bản mẫu cho vùng tương ứng (đánh giá riêng phần đặt chữ)
        size_err, pos_err = [], []
        for i, js in by_region.items():
            r = page.regions[i]
            js = sorted(js)
            ref0 = refs[js[0]]
            r.text = "\n".join(refs[j]["text"] for j in js)
            r.style = TextStyle(family=family_for(ref0["font"], families))
            from app.core.typeset import _layout
            from dataclasses import astuple
            x1, y1, x2, y2 = r.text_rect
            _, size, _ = _layout(r.text, astuple(r.effective_style), x2 - x1, y2 - y1)
            if len(js) == 1:
                size_err.append(size / ref0["size"])
                pos_err.append(math.dist(center(r.text_rect), center(ref0["bbox"])))
        typed = page.render()
        typed = cv2.cvtColor(np.frombuffer(typed.constBits(), np.uint8, typed.sizeInBytes())
                             .reshape(typed.height(), typed.bytesPerLine())[:, :typed.width() * 3]
                             .reshape(typed.height(), typed.width(), 3), cv2.COLOR_RGB2BGR)
        # Chữ Nhật còn sót: nhận diện lại trên ảnh đã xoá, chỉ tính trong các vùng đã xử lý
        left_boxes, _ = det.detect(page.working)
        leftover = [b for b in left_boxes if any(inside(center((b.x1, b.y1, b.x2, b.y2)), r.box, 6)
                                                 for r in kept)]
        merged = sum(len(js) - 1 for js in by_region.values())
        missed = [j for j, i in owner.items() if i is None]
        row = {"page": name, "ref": len(refs), "regions": len(page.regions), "found": len(refs) - len(missed),
               "merged": merged, "auto_ok": auto_ok, "auto_extra_used": auto_extra, "missed": [refs[j]["text"][:30] for j in missed],
               "extra": [(page.regions[i].kind, page.regions[i].box) for i in extra], "wrong_line": wrong,
               "leftover": [(b.x1, b.y1, b.x2, b.y2) for b in leftover], "time": round(time.time() - t0, 1),
               "size_ratio": [round(v, 2) for v in size_err], "pos_err": [round(v) for v in pos_err]}
        report.append(row)
        for key in ("found", "merged", "wrong_line", "auto_ok", "auto_extra_used"):
            totals[key] += row[key]
        totals["ref"] += len(refs)
        totals["missed"] += len(missed)
        totals["extra"] += len(extra)
        totals["leftover"] += len(leftover)
        totals["size_err"] += size_err
        totals["pos_err"] += pos_err
        vis = typed.copy()
        for i, r in enumerate(page.regions):
            cv2.rectangle(vis, r.box[:2], r.box[2:], (0, 160, 0) if i in by_region else (0, 0, 255), 1)
        for b in leftover:
            cv2.rectangle(vis, (b.x1, b.y1), (b.x2, b.y2), (255, 0, 255), 2)
        raw = raw_img
        cv2.imwrite(os.path.join(out_dir, f"{name}_cmp.jpg"), np.hstack([raw, vis, ref_img]))
        cv2.imwrite(os.path.join(out_dir, f"{name}_clean.png"), page.working)
        print(json.dumps(row, ensure_ascii=False))
    se, pe = totals.pop("size_err"), totals.pop("pos_err")
    totals["size_ratio_median"] = round(float(np.median(se)), 2) if se else None
    totals["size_ratio_within_25pct"] = f"{sum(0.75 <= v <= 1.33 for v in se)}/{len(se)}"
    totals["pos_err_median_px"] = round(float(np.median(pe)), 1) if pe else None
    print("TOTAL", json.dumps(totals, ensure_ascii=False))
    json.dump({"pages": report, "totals": totals}, open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return totals


if __name__ == "__main__":
    fix = json.loads(os.environ.get("ORDER_FIX", "{}"))
    evaluate(sys.argv[1], sys.argv[2], sys.argv[3], pages=sys.argv[4].split(",") if len(sys.argv) > 4 else None,
             order_fix=fix)
