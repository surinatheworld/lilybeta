"""Mở file PSD thành tài liệu Lilyzono: Background → ảnh raw, layer ảnh giữ nguyên (tên, ẩn/hiện, opacity, blend),
layer chữ → khung chữ sửa được (nội dung, font, cỡ, màu, căn lề, vị trí khung đoạn văn)."""
from __future__ import annotations

import math
import os
import tempfile

import cv2
import numpy as np

from .document import Layer, doc_for
from .fonts import family_from_postscript
from .page import Page, Region
from .typeset import TextStyle

BLENDS = {"MULTIPLY": "multiply", "SCREEN": "screen", "DARKEN": "darken", "LIGHTEN": "lighten"}
JUSTIFY = {0: "left", 1: "right", 2: "center"}


def _rgba_full(layer, w: int, h: int) -> np.ndarray | None:
    """Điểm ảnh của layer đặt vào khung cả trang (BGRA)."""
    pil = layer.composite()
    if pil is None:
        return None
    arr = np.array(pil.convert("RGBA"))
    out = np.zeros((h, w, 4), np.uint8)
    x1, y1 = layer.left, layer.top
    ax1, ay1 = max(0, -x1), max(0, -y1)
    bx1, by1 = max(0, x1), max(0, y1)
    bx2, by2 = min(w, x1 + arr.shape[1]), min(h, y1 + arr.shape[0])
    if bx1 >= bx2 or by1 >= by2:
        return out
    out[by1:by2, bx1:bx2] = arr[ay1:ay1 + by2 - by1, ax1:ax1 + bx2 - bx1]
    return cv2.cvtColor(out, cv2.COLOR_RGBA2BGRA)


def _color(desc, default: str = "#000000") -> str:
    try:
        r, g, b = (float(desc[k]) for k in (b"Rd  ", b"Grn ", b"Bl  "))
        return "#{:02x}{:02x}{:02x}".format(round(r), round(g), round(b))
    except (KeyError, TypeError, ValueError):
        return default


def _effects(layer, st: TextStyle) -> None:
    """Stroke / Drop Shadow của layer chữ → kiểu chữ."""
    for e in getattr(layer, "effects", None) or []:
        if not getattr(e, "enabled", False):
            continue
        kind = type(e).__name__
        if kind == "Stroke":
            st.stroke_width = float(e.size or 0)
            st.stroke_color = _color(e.color, "#ffffff")
        elif kind == "DropShadow":
            st.shadow = True
            st.shadow_color = _color(e.color)
            st.shadow_opacity = float(e.opacity or 75)
            st.shadow_angle = float(e.angle if e.angle is not None else 120)
            st.shadow_distance = float(e.distance or 0)
            st.shadow_size = float(e.size or 0)


def _text_region(layer) -> Region | None:
    text = (layer.text or "").replace("\r", "\n").replace("\x03", "\n").strip("\n")
    if not text.strip():
        return None
    xx, xy, yx, yy, tx, ty = layer.transform
    scale = math.hypot(xx, xy) or 1.0
    rotation = math.degrees(math.atan2(xy, xx))
    eng = layer.engine_dict
    sheet = eng["StyleRun"]["RunArray"][0]["StyleSheet"]["StyleSheetData"]
    fonts = layer.resource_dict["FontSet"]
    ps = str(getattr(fonts[int(sheet.get("Font", 0))]["Name"], "value", fonts[int(sheet.get("Font", 0))]["Name"]))
    family, style_name = family_from_postscript(ps)
    color = "#000000"
    fill = sheet.get("FillColor")
    if fill is not None:
        a, r, g, b = (float(v) for v in fill["Values"])
        color = "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))
    props = eng["ParagraphRun"]["RunArray"][0]["ParagraphSheet"]["Properties"]
    align = JUSTIFY.get(int(props.get("Justification", 0)), "center")
    size = float(sheet.get("FontSize", 24)) * scale
    # Khung đoạn văn (paragraph text); chữ điểm (point text) thì lấy khung bao chữ.
    # Photoshop đặt chữ đoạn văn sát mép trên khung, Lilyzono căn giữa khung: lấy bề ngang theo khung đoạn văn
    # (để ngắt dòng như cũ) và chiều dọc theo khung bao chữ thật, chữ sẽ nằm đúng chỗ cũ.
    gx1, gy1, gx2, gy2 = layer.bbox
    cx, cy = (gx1 + gx2) / 2, (gy1 + gy2) / 2
    para = None
    try:
        shape = eng["Rendered"]["Shapes"]["Children"][0]
        bb = shape["Cookie"]["Photoshop"].get("BoxBounds")
        if bb is not None and int(shape["ShapeType"]) == 1:
            bx1, by1, bx2, by2 = (float(v) for v in bb)
            para = (bx2 - bx1) * scale, (by2 - by1) * scale, tx + bx1 * scale
    except (KeyError, IndexError, TypeError):
        pass
    pad = max(2, round(size * 0.15))
    if para is None or para[0] < 4:  # chữ điểm (point text): khung bao chữ
        box = (gx1 - pad, gy1 - pad, gx2 + pad, gy2 + pad)
    elif abs(rotation) < 0.5:
        slack = para[0] * 0.05  # đo chữ của Qt rộng hơn Photoshop một chút: nới để ngắt dòng như cũ
        box = (round(para[2] - slack), gy1 - pad, round(para[2] + para[0] + slack), gy2 + pad)
    else:  # chữ xoay: khung đoạn văn đặt quanh tâm chữ thật
        w, h = para[0], max(gy2 - gy1, para[1] * 0.6)
        box = (round(cx - w / 2), round(cy - h / 2), round(cx + w / 2), round(cy + h / 2))
    st = TextStyle(family=family or "Arial", style_name=style_name if family else "", size=round(size), color=color,
                   align=align, rotation=rotation, bold=bool(sheet.get("FauxBold", False)),
                   italic=bool(sheet.get("FauxItalic", False)), padding=0.0)
    if not sheet.get("AutoLeading", True) and sheet.get("Leading"):
        st.leading = float(sheet["Leading"]) * scale
    _effects(layer, st)
    r = Region(box, text=text, style=st, cleaned=True, text_rects=[box], kind="manual", manual_rect=box)
    r.missing_font = "" if family else ps
    return r


def open_psd(path: str) -> tuple[Page, list[str]]:
    """Trả về (trang, các ghi chú: font thiếu…)."""
    from psd_tools import PSDImage

    psd = PSDImage.open(path)
    w, h = psd.size
    leaves = [L for L in psd.descendants() if not L.is_group()]
    notes = []
    bg = leaves[0] if leaves and leaves[0].kind == "pixel" else None
    if bg is not None:
        raw = _rgba_full(bg, w, h)
        a = raw[..., 3:4].astype(np.float32) / 255
        raw = (raw[..., :3] * a + 255 * (1 - a)).astype(np.uint8)  # chỗ trong suốt: nền trắng
        rest = leaves[1:]
    else:
        raw = np.full((h, w, 3), 255, np.uint8)
        rest = leaves
    folder = tempfile.mkdtemp(prefix="lilyzono_psd_")
    raw_path = os.path.join(folder, os.path.splitext(os.path.basename(path))[0] + ".png")
    cv2.imencode(".png", raw)[1].tofile(raw_path)
    page = Page(raw_path)
    page.source_psd = path
    doc = doc_for(page)
    if bg is not None:
        doc.layers[0].visible = bg.visible
    missing = set()
    redraw_used = False
    for L in rest:
        if L.kind == "type":
            r = _text_region(L)
            if r is None:
                continue
            if r.missing_font:
                missing.add(r.missing_font)
            r.auto_cap = page.auto_cap
            page.regions.append(r)
            doc.sync()
            tl = doc.text_layer(r)
            tl.name, tl.custom_name = L.name, True
            tl.visible, tl.opacity = L.visible, L.opacity / 255
            continue
        rgba = _rgba_full(L, w, h)
        if rgba is None:
            continue
        blend = BLENDS.get(str(L.blend_mode).split(".")[-1], "normal")
        if L.name == "Redraw" and not redraw_used:
            # Layer Redraw do Lilyzono xuất: ghép vào layer Redraw (chỗ đã xoá chữ).
            a = rgba[..., 3:4].astype(np.float32) / 255
            page.working[:] = (rgba[..., :3] * a + page.working * (1 - a)).astype(np.uint8)
            page.paint_mask[rgba[..., 3] > 0] = 255
            doc.layers[1].visible, doc.layers[1].opacity = L.visible, L.opacity / 255
            redraw_used = True
            continue
        layer = Layer(L.name, "pixel", visible=L.visible, opacity=L.opacity / 255, blend=blend, rgba=rgba)
        doc.layers.append(layer)
    page.detected = any(L.kind == "type" for L in rest)
    doc.sync()
    doc.active = doc.layers[-1]
    if missing:
        notes.append("Font chưa cài trên máy, tạm dùng Arial: " + ", ".join(sorted(missing)))
    return page, notes
