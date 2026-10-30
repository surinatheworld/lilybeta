"""Tệp dự án Lilyzono (.lzp): lưu để mở lại làm tiếp, giữ nguyên mọi layer, khung chữ, kiểu chữ và bản dịch.

Là một file zip: project.json + ảnh raw gốc (để mở được trên máy khác) + điểm ảnh của từng layer (PNG) + mask chữ.
Một file chứa một trang, hoặc cả chương (các trang đã mở) kèm bản dịch chia theo trang.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import zipfile
from dataclasses import asdict, fields

import cv2
import numpy as np

from .document import Document, Layer
from .page import Page, Region, reserve_uids
from .typeset import TextStyle

VERSION = 1
EXT = ".lzp"


def _png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise IOError("Không mã hoá được ảnh")
    return buf.tobytes()


def _img(data: bytes, flags=cv2.IMREAD_UNCHANGED) -> np.ndarray:
    return cv2.imdecode(np.frombuffer(data, np.uint8), flags)


def _region_dict(r: Region) -> dict:
    d = asdict(r)
    d["style"] = asdict(r.style)
    return d


def _region_from(d: dict) -> Region:
    known = {f.name for f in fields(Region)}
    d = {k: v for k, v in d.items() if k in known}
    style_keys = {f.name for f in fields(TextStyle)}
    d["style"] = TextStyle(**{k: v for k, v in (d.get("style") or {}).items() if k in style_keys})
    d["box"] = tuple(d["box"])
    d["text_rects"] = [tuple(b) for b in d.get("text_rects") or []]
    d["polygon"] = [tuple(pt) for pt in d["polygon"]] if d.get("polygon") else None
    d["manual_rect"] = tuple(d["manual_rect"]) if d.get("manual_rect") else None
    if d.get("text_shape"):
        cx, cy, rows = d["text_shape"]
        d["text_shape"] = (cx, cy, tuple(tuple(r) for r in rows))
    return Region(**d)


def save(path: str, pages: list[Page], chapter=None, direction: int = 0) -> None:
    """Ghi các trang (đã mở) – và chương nếu có – vào `path`."""
    meta = {"version": VERSION, "app": "Lilyzono", "direction": direction, "pages": [], "chapter": None}
    if chapter is not None:
        meta["chapter"] = {"folder": os.path.abspath(chapter.folder),
                           "translations": {str(k): v for k, v in chapter.translations.items()},
                           "filled": [os.path.basename(p) for p in chapter.filled]}
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for i, page in enumerate(pages):
            doc: Document = getattr(page, "doc", None) or Document(page)
            doc.sync()
            base = f"pages/{i}/"
            ext = os.path.splitext(page.path)[1].lower() or ".png"
            with open(page.path, "rb") as f:
                z.writestr(base + "raw" + ext, f.read(), compress_type=zipfile.ZIP_STORED)
            z.writestr(base + "working.png", _png(page.working), compress_type=zipfile.ZIP_STORED)
            z.writestr(base + "paint_mask.png", _png(page.paint_mask), compress_type=zipfile.ZIP_STORED)
            if page.redrawn is not None:
                z.writestr(base + "redrawn.png", _png(page.redrawn), compress_type=zipfile.ZIP_STORED)
            buf = io.BytesIO()
            np.savez_compressed(buf, text_mask=page.text_mask.astype(np.float16))
            z.writestr(base + "text_mask.npz", buf.getvalue(), compress_type=zipfile.ZIP_STORED)
            layers = []
            for j, L in enumerate(doc.layers):
                item = {"kind": L.kind, "name": L.name, "visible": L.visible, "opacity": L.opacity, "locked": L.locked,
                        "blend": L.blend, "custom_name": L.custom_name, "uid": L.uid}
                if L.kind == "pixel":
                    item["png"] = base + f"layer{j}.png"
                    z.writestr(item["png"], _png(L.rgba), compress_type=zipfile.ZIP_STORED)
                layers.append(item)
            meta["pages"].append({
                "name": os.path.basename(page.path), "raw_path": os.path.abspath(page.path), "raw": base + "raw" + ext,
                "detected": page.detected, "typeset_profile": page.typeset_profile,
                "regions": [_region_dict(r) for r in page.regions], "layers": layers,
                "active": doc.layers.index(doc.active) if doc.active in doc.layers else 1,
            })
        z.writestr("project.json", json.dumps(meta, ensure_ascii=False, indent=1))
    os.replace(tmp, path)
    for page in pages:
        d = getattr(page, "doc", None)
        if d is not None:
            d.saved_index = d.index


def load(path: str) -> dict:
    """Đọc dự án. Trả về {"pages": [Page], "chapter_folder": str | None, "translations": {...}, "filled": [...],
    "direction": int}. Ảnh raw lấy từ chỗ cũ nếu còn, không thì giải nén ra thư mục tạm."""
    with zipfile.ZipFile(path) as z:
        meta = json.loads(z.read("project.json").decode("utf-8"))
        infos = meta["pages"]
        ch = meta.get("chapter")
        # Raw: dùng file gốc nếu vẫn còn nguyên (chế độ chương: cả thư mục), không thì giải nén.
        def same(info):
            p = info["raw_path"]
            return os.path.exists(p) and os.path.getsize(p) == z.getinfo(info["raw"]).file_size

        folder = ch["folder"] if ch else None
        if not all(same(i) for i in infos) or (folder and not os.path.isdir(folder)):
            folder = tempfile.mkdtemp(prefix="lilyzono_project_")
            for info in infos:
                with open(os.path.join(folder, info["name"]), "wb") as f:
                    f.write(z.read(info["raw"]))
            raw_paths = [os.path.join(folder, i["name"]) for i in infos]
        else:
            raw_paths = [i["raw_path"] for i in infos]
        pages = []
        max_uid = 0
        for info, raw in zip(infos, raw_paths):
            base = info["raw"].rsplit("/", 1)[0] + "/"
            page = Page(raw)
            page.working = _img(z.read(base + "working.png"), cv2.IMREAD_COLOR)
            page.paint_mask = _img(z.read(base + "paint_mask.png"), cv2.IMREAD_GRAYSCALE)
            if base + "redrawn.png" in z.namelist():
                page.redrawn = _img(z.read(base + "redrawn.png"), cv2.IMREAD_COLOR)
            page.text_mask = np.load(io.BytesIO(z.read(base + "text_mask.npz")))["text_mask"].astype(np.float32)
            page.detected = info["detected"]
            page.typeset_profile = info.get("typeset_profile", "")
            page.regions = [_region_from(d) for d in info["regions"]]
            max_uid = max([max_uid] + [r.uid for r in page.regions])
            doc = Document(page)
            layers = []
            for item in info["layers"]:
                kind = item["kind"]
                if kind in ("background", "redraw"):
                    L = doc.layers[0 if kind == "background" else 1]
                else:
                    rgba = _img(z.read(item["png"])) if kind == "pixel" else None
                    L = Layer(item["name"], kind, rgba=rgba, uid=item.get("uid", 0))
                L.name, L.visible, L.opacity = item["name"], item["visible"], item["opacity"]
                L.locked, L.blend, L.custom_name = item["locked"], item["blend"], item.get("custom_name", False)
                layers.append(L)
            doc.layers = layers
            doc.active = layers[min(info.get("active", 1), len(layers) - 1)]
            doc.sync()
            doc.saved_index = 0
            page.doc = doc
            pages.append(page)
        reserve_uids(max_uid)
    return {"pages": pages, "chapter_folder": folder if ch else None,
            "translations": {int(k): v for k, v in (ch or {}).get("translations", {}).items()},
            "filled": (ch or {}).get("filled", []), "direction": meta.get("direction", 0)}
