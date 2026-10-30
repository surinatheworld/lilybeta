"""Tài liệu nhiều layer kiểu Photoshop dựng trên một trang (Page).

Thứ tự layer (dưới → trên): Background (ảnh raw, khoá), Redraw (chỗ đã xoá chữ / vẽ lại, gồm cả bản redraw đã
upload), các layer ảnh người dùng thêm và layer chữ (mỗi vùng đã có bản dịch là một layer). Có vùng chọn (mask 0–255)
và lịch sử hoàn tác nhiều bước: mỗi bước lưu trạng thái vùng chữ/layer và phần điểm ảnh bị đổi (chỉ khung bao quanh).
"""
from __future__ import annotations

import copy
import itertools
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import cv2
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from .page import Page, Region
from .typeset import draw_text

Box = tuple[int, int, int, int]
BLENDS = {"normal": "Normal", "multiply": "Multiply", "screen": "Screen", "darken": "Darken", "lighten": "Lighten"}
MAX_HISTORY = 60
MERGE_SECONDS = 1.5  # các lần sửa liền nhau cùng loại (gõ chữ, kéo thanh trượt) gộp thành một bước
_layer_ids = itertools.count(1)


@dataclass(eq=False)
class Layer:
    name: str
    kind: str  # "background" | "redraw" | "pixel" | "text"
    visible: bool = True
    opacity: float = 1.0  # 0–1
    locked: bool = False
    blend: str = "normal"
    rgba: np.ndarray | None = None  # layer ảnh: BGRA cỡ trang
    uid: int = 0  # layer chữ: uid của vùng chữ
    custom_name: bool = False  # người dùng đã đổi tên (layer chữ thôi tự lấy tên theo nội dung)
    id: int = field(default_factory=lambda: next(_layer_ids))

    @property
    def paintable(self) -> bool:
        return self.kind in ("redraw", "pixel") and not self.locked


@dataclass
class _Step:
    name: str
    before: dict
    after: dict | None = None
    patches: list = field(default_factory=list)  # (chủ, thuộc tính, khung, trước, sau)
    merge: str | None = None
    time: float = 0.0


def doc_for(page: Page) -> "Document":
    """Tài liệu (layer + lịch sử) của trang; tạo khi cần lần đầu."""
    doc = getattr(page, "doc", None)
    if doc is None:
        doc = page.doc = Document(page)
    return doc


class Document:
    def __init__(self, page: Page):
        self.page = page
        self.layers: list[Layer] = [Layer("Background", "background", locked=True), Layer("Redraw", "redraw")]
        self.active: Layer = self.layers[1]
        self.selection: np.ndarray | None = None
        self.history: list[_Step] = []
        self.index = 0  # số bước đang có hiệu lực (history[:index])
        self._pending: tuple[_Step, list] | None = None
        self._text_cache: dict[int, tuple] = {}
        self.editing_uid: int | None = None  # chữ đang gõ trực tiếp trên ảnh: không vẽ (ô gõ hiện thay)
        self.saved_index = 0  # vị trí trong lịch sử lúc lưu / mở gần nhất
        self.sync()

    @property
    def size(self) -> tuple[int, int]:
        return self.page.size

    # ------------------------------------------------------------------ layer
    def region_of(self, layer: Layer) -> Region | None:
        if layer.kind != "text":
            return None
        return next((r for r in self.page.regions if r.uid == layer.uid), None)

    def text_layer(self, region: Region) -> Layer | None:
        return next((L for L in self.layers if L.kind == "text" and L.uid == region.uid), None)

    def sync(self) -> None:
        """Layer chữ theo vùng chữ: vùng đã xử lý có bản dịch ↔ một layer chữ (thêm mới thì nằm trên cùng)."""
        typed = {r.uid: r for r in self.page.regions if r.cleaned and r.text.strip()}
        self.layers = [L for L in self.layers if L.kind != "text" or L.uid in typed]
        have = {L.uid for L in self.layers if L.kind == "text"}
        for r in self.page.regions:
            if r.uid in typed and r.uid not in have:
                self.layers.append(Layer("", "text", uid=r.uid))
        for L in self.layers:
            if L.kind == "text" and not L.custom_name:
                L.name = " ".join(typed[L.uid].text.split())[:40]
        for uid in self._text_cache.keys() - typed.keys():
            del self._text_cache[uid]
        if self.active not in self.layers:
            self.active = self.layers[1]

    def add_pixel_layer(self, name: str | None = None) -> Layer:
        w, h = self.size
        n = sum(1 for L in self.layers if L.kind == "pixel") + 1
        layer = Layer(name or f"Layer {n}", "pixel", rgba=np.zeros((h, w, 4), np.uint8))
        self._insert_above_active(layer)
        return layer

    def duplicate_layer(self, layer: Layer) -> Layer | None:
        if layer.kind == "text":
            return None
        w, h = self.size
        if layer.kind == "pixel":
            rgba = layer.rgba.copy()
        else:  # Background / Redraw → layer ảnh
            src = self.page.original if layer.kind == "background" else self.page.working
            alpha = np.full((h, w), 255, np.uint8) if layer.kind == "background" else \
                (self.redraw_alpha((0, 0, w, h)) * 255).astype(np.uint8)
            rgba = np.dstack([src, alpha])
        dup = Layer(f"{layer.name} copy", "pixel", rgba=rgba, opacity=layer.opacity, blend=layer.blend)
        self._insert_above_active(dup, layer)
        return dup

    def _insert_above_active(self, layer: Layer, ref: Layer | None = None) -> None:
        ref = ref or self.active
        i = self.layers.index(ref) + 1 if ref in self.layers else len(self.layers)
        self.layers.insert(max(2, i), layer)
        self.active = layer

    def delete_layer(self, layer: Layer) -> bool:
        if layer.kind in ("background", "redraw"):
            return False
        if layer.kind == "text":
            r = self.region_of(layer)
            if r is not None:
                r.text = ""
        self.layers.remove(layer)
        self.sync()
        return True

    def move_layer(self, layer: Layer, delta: int) -> bool:
        """Đưa layer lên (+1) / xuống (-1). Background và Redraw cố định ở đáy."""
        if layer.kind in ("background", "redraw"):
            return False
        i = self.layers.index(layer)
        j = i + delta
        if not 2 <= j < len(self.layers):
            return False
        self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
        return True

    def merge_down(self, layer: Layer) -> bool:
        """Gộp layer ảnh xuống layer ảnh / Redraw ngay dưới nó."""
        i = self.layers.index(layer)
        if layer.kind != "pixel" or i < 2 or self.layers[i - 1].kind not in ("pixel", "redraw"):
            return False
        below = self.layers[i - 1]
        a = layer.rgba[..., 3:4].astype(np.float32) / 255 * layer.opacity
        if below.kind == "redraw":
            base = self.page.working.astype(np.float32)
            src = _blend(base, layer.rgba[..., :3].astype(np.float32), layer.blend)
            self.page.working[:] = np.clip(base + (src - base) * a, 0, 255).astype(np.uint8)
            self.page.paint_mask[a[..., 0] > 0.004] = 255
        else:
            b = below.rgba.astype(np.float32)
            ba = b[..., 3:4] / 255
            src = _blend(b[..., :3], layer.rgba[..., :3].astype(np.float32), layer.blend)
            out_a = a + ba * (1 - a)
            rgb = (src * a + b[..., :3] * ba * (1 - a)) / np.maximum(out_a, 1e-6)
            below.rgba[:] = np.dstack([np.clip(rgb, 0, 255), out_a * 255]).astype(np.uint8)
        self.layers.remove(layer)
        self.active = below
        return True

    # ------------------------------------------------------------- ghép ảnh
    def redraw_alpha(self, box: Box) -> np.ndarray:
        x1, y1, x2, y2 = box
        p = self.page
        diff = cv2.absdiff(p.working[y1:y2, x1:x2], p.original[y1:y2, x1:x2])
        unchanged = cv2.inRange(diff, (0, 0, 0), (0, 0, 0))
        return ((unchanged == 0) | (p.paint_mask[y1:y2, x1:x2] > 0)).astype(np.float32)

    def composite(self, box: Box | None = None, text: bool = True) -> np.ndarray:
        """Ảnh BGR (uint8) ghép mọi layer đang hiện trong `box` (mặc định cả trang)."""
        w, h = self.size
        x1, y1, x2, y2 = box or (0, 0, w, h)
        p = self.page
        bg = self.layers[0]
        redraw = self.layers[1]
        # With an opaque background, unchanged Redraw pixels are already identical
        # to Background. Avoid allocating a full checkerboard and difference mask.
        opaque = bg.visible and bg.opacity == 1.0
        flat_redraw = opaque and redraw.visible and redraw.opacity == 1.0 and redraw.blend == "normal"
        if flat_redraw:
            out = p.working[y1:y2, x1:x2].astype(np.float32)
        elif opaque:
            out = p.original[y1:y2, x1:x2].astype(np.float32)
        else:
            out = _checker(x1, y1, x2, y2)
        if not opaque and bg.visible and bg.opacity > 0:
            src = p.original[y1:y2, x1:x2].astype(np.float32)
            out += (src - out) * bg.opacity
        for L in self.layers[1:]:
            if L is redraw and flat_redraw:
                continue
            if not L.visible or L.opacity <= 0:
                continue
            if L.kind == "text":
                if text and L.uid != self.editing_uid:
                    self._paint_text(out, (x1, y1, x2, y2), L)
                continue
            if L.kind == "redraw":
                src = p.working[y1:y2, x1:x2].astype(np.float32)
                a = self.redraw_alpha((x1, y1, x2, y2))
            else:
                src = L.rgba[y1:y2, x1:x2, :3].astype(np.float32)
                a = L.rgba[y1:y2, x1:x2, 3].astype(np.float32) / 255
            if not a.any():
                continue
            src = _blend(out, src, L.blend)
            out += (src - out) * (a * L.opacity)[..., None]
        return np.clip(out, 0, 255).astype(np.uint8)

    def _paint_text(self, out: np.ndarray, box: Box, layer: Layer) -> None:
        r = self.region_of(layer)
        if r is None:
            return
        tx, ty, prem, alpha = self._text_patch(r)
        x1, y1, x2, y2 = box
        th, tw = alpha.shape
        ix1, iy1, ix2, iy2 = max(x1, tx), max(y1, ty), min(x2, tx + tw), min(y2, ty + th)
        if ix1 >= ix2 or iy1 >= iy2:
            return
        a = alpha[iy1 - ty:iy2 - ty, ix1 - tx:ix2 - tx] * layer.opacity
        c = prem[iy1 - ty:iy2 - ty, ix1 - tx:ix2 - tx] * layer.opacity
        o = out[iy1 - y1:iy2 - y1, ix1 - x1:ix2 - x1]
        o[:] = o * (1 - a[..., None]) + c

    def _text_patch(self, r: Region):
        """Chữ của vùng vẽ sẵn thành ảnh nhỏ (màu đã nhân alpha); vẽ lại khi chữ/kiểu/khung đổi."""
        st = r.effective_style
        rect = r.text_rect
        key = (r.text, repr(st), rect)
        hit = self._text_cache.get(r.uid)
        if hit and hit[0] == key:
            return hit[1]
        w, h = self.size
        x1, y1, x2, y2 = rect
        m = int(max(x2 - x1, y2 - y1) * 0.5 + st.stroke_width * 2 + 16
                + (abs(st.shadow_distance) + st.shadow_size * 3 if st.shadow else 0))
        bx1, by1, bx2, by2 = max(0, x1 - m), max(0, y1 - m), min(w, x2 + m), min(h, y2 + m)
        img = QImage(max(1, bx2 - bx1), max(1, by2 - by1), QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        painter = QPainter(img)
        painter.translate(-bx1, -by1)
        draw_text(painter, r.text, st, QRectF(x1, y1, x2 - x1, y2 - y1))
        painter.end()
        arr = np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())
        arr = arr[:, :img.width() * 4].reshape(img.height(), img.width(), 4)
        ys, xs = np.nonzero(arr[..., 3])
        if len(xs):
            cy1, cy2, cx1, cx2 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
        else:
            cy1 = cy2 = cx1 = cx2 = 0
        crop = arr[cy1:cy2, cx1:cx2].astype(np.float32)
        patch = (bx1 + cx1, by1 + cy1, crop[..., :3].copy(), crop[..., 3] / 255)
        self._text_cache[r.uid] = (key, patch)
        return patch

    def render(self) -> QImage:
        return self.render_box((0, 0, *self.size))

    def render_box(self, box: Box) -> QImage:
        bgr = np.ascontiguousarray(self.composite(box))
        h, w = bgr.shape[:2]
        return QImage(bgr.data, w, h, bgr.strides[0], QImage.Format_BGR888).copy()

    def text_bounds(self, regions) -> Box | None:
        """Actual painted bounds, including rotation/effects; capture before edits too."""
        box = None
        for r in regions:
            if not r.done or r.uid == self.editing_uid:
                continue
            x, y, _, alpha = self._text_patch(r)
            h, w = alpha.shape
            if w and h:
                box = _union(box, (x, y, x + w, y + h))
        return box

    def export(self, path: str) -> None:
        """JPEG (chất lượng 95) / PNG của mọi layer đang hiện."""
        ext = path.rsplit(".", 1)[-1].lower()
        params = [cv2.IMWRITE_JPEG_QUALITY, 95] if ext in ("jpg", "jpeg") else []
        ok, buf = cv2.imencode("." + ext, self.composite(), params)
        if not ok:
            raise IOError(f"Không lưu được: {path}")
        buf.tofile(path)

    def thumbnail(self, layer: Layer, size: int = 40) -> np.ndarray:
        """Ảnh nhỏ BGR cho bảng Layers (layer trong suốt hiện trên nền ô cờ)."""
        w, h = self.size
        s = size / max(w, h)
        tw, th = max(1, int(w * s)), max(1, int(h * s))
        if layer.kind == "background":
            return cv2.resize(self.page.original, (tw, th), interpolation=cv2.INTER_AREA)
        if layer.kind == "text":
            r = self.region_of(layer)
            out = _checker(0, 0, w, h)
            if r is not None:
                self._paint_text(out, (0, 0, w, h), Layer("", "text", uid=r.uid))
            return cv2.resize(np.clip(out, 0, 255).astype(np.uint8), (tw, th), interpolation=cv2.INTER_AREA)
        if layer.kind == "redraw":
            src, a = self.page.working, self.redraw_alpha((0, 0, w, h))
        else:
            src, a = layer.rgba[..., :3], layer.rgba[..., 3].astype(np.float32) / 255
        small = cv2.resize(src, (tw, th), interpolation=cv2.INTER_AREA).astype(np.float32)
        sa = cv2.resize(a, (tw, th), interpolation=cv2.INTER_AREA)[..., None]
        bg = _checker(0, 0, tw, th, cell=4)
        return np.clip(bg + (small - bg) * sa, 0, 255).astype(np.uint8)

    # ------------------------------------------------- Move / Free Transform
    def content_bbox(self, layer: Layer) -> Box | None:
        """Khung bao phần có điểm ảnh của layer ảnh (hoặc vùng chọn nếu có)."""
        if self.selection is not None:
            return self.selection_bbox()
        if layer.kind != "pixel":
            return None
        ys, xs = np.nonzero(layer.rgba[..., 3])
        if not len(xs):
            return None
        return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1

    def transform_pixels(self, layer: Layer, src: np.ndarray, sel: np.ndarray | None, matrix: np.ndarray,
                         smooth: bool = True) -> np.ndarray | None:
        """Biến đổi (dời / co giãn / xoay theo ma trận 2x3) layer ảnh, hoặc chỉ phần trong vùng chọn, tính lại từ bản
        gốc `src`; phần còn lại giữ nguyên. Trả về vùng chọn sau biến đổi (None nếu không có vùng chọn)."""
        h, w = src.shape[:2]
        a = src[..., 3].astype(np.float32) / 255
        part_a = a * (sel.astype(np.float32) / 255) if sel is not None else a
        rest_a = a - part_a
        flags = cv2.INTER_LINEAR if smooth else cv2.INTER_NEAREST
        prem = src[..., :3].astype(np.float32) * part_a[..., None]  # nhân sẵn alpha để mép không bị viền màu lạ
        wp = cv2.warpAffine(prem, matrix, (w, h), flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        wa = cv2.warpAffine(part_a, matrix, (w, h), flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        out_a = wa + rest_a * (1 - wa)
        rgb = (wp + src[..., :3].astype(np.float32) * (rest_a * (1 - wa))[..., None]) / np.maximum(out_a, 1e-6)[..., None]
        layer.rgba[:] = np.dstack([np.clip(rgb, 0, 255), np.clip(out_a * 255, 0, 255)]).astype(np.uint8)
        if sel is None:
            return None
        return cv2.warpAffine(sel, matrix, (w, h), flags=flags, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

    # -------------------------------------------------------------- vùng chọn
    def set_selection(self, mask: np.ndarray | None, mode: str = "new") -> None:
        """mode: "new" | "add" | "sub" | "inter" (như giữ Shift / Alt / Shift+Alt trong Photoshop)."""
        cur = self.selection
        if mask is None:
            sel = None
        elif mode == "add" and cur is not None:
            sel = np.maximum(cur, mask)
        elif mode == "sub":
            sel = None if cur is None else np.minimum(cur, 255 - mask)
        elif mode == "inter":
            sel = None if cur is None else np.minimum(cur, mask)
        else:
            sel = mask
        self.selection = sel if sel is not None and sel.max() > 0 else None

    def _blank(self) -> np.ndarray:
        w, h = self.size
        return np.zeros((h, w), np.uint8)

    def select_rect(self, box: Box, mode: str = "new", feather: float = 0) -> None:
        m = self._blank()
        x1, y1, x2, y2 = box
        m[max(0, y1):max(0, y2), max(0, x1):max(0, x2)] = 255
        self.set_selection(_feather(m, feather), mode)

    def select_polygon(self, points, mode: str = "new", feather: float = 0) -> None:
        m = self._blank()
        cv2.fillPoly(m, [np.array(points, np.int32)], 255, lineType=cv2.LINE_AA)
        self.set_selection(_feather(m, feather), mode)

    def select_wand(self, x: int, y: int, tolerance: int = 32, contiguous: bool = True,
                    sample_all: bool = True, mode: str = "new") -> None:
        img = self.composite(text=True) if sample_all else self.page.working
        w, h = self.size
        if not (0 <= x < w and 0 <= y < h):
            return
        if contiguous:
            ff = np.zeros((h + 2, w + 2), np.uint8)
            t = (tolerance,) * 3
            cv2.floodFill(img.copy(), ff, (x, y), 0, t, t,
                          4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8))
            m = ff[1:-1, 1:-1]
        else:
            seed = img[y, x].astype(np.int16)
            m = ((np.abs(img.astype(np.int16) - seed).max(axis=2) <= tolerance) * 255).astype(np.uint8)
        self.set_selection(m, mode)

    def select_all(self) -> None:
        self.selection = np.full(self._blank().shape, 255, np.uint8)

    def deselect(self) -> None:
        self.selection = None

    def invert_selection(self) -> None:
        self.selection = None if self.selection is None else 255 - self.selection
        if self.selection is None:
            self.select_all()
        elif self.selection.max() == 0:
            self.selection = None

    def feather_selection(self, radius: float) -> None:
        if self.selection is not None:
            self.set_selection(_feather(self.selection, radius))

    def grow_selection(self, px: int) -> None:
        """px > 0: mở rộng, px < 0: thu hẹp."""
        if self.selection is None or px == 0:
            return
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * abs(px) + 1, 2 * abs(px) + 1))
        self.set_selection(cv2.dilate(self.selection, k) if px > 0 else cv2.erode(self.selection, k))

    def selection_outline(self) -> list[np.ndarray]:
        """Đường viền vùng chọn (để vẽ "kiến bò")."""
        if self.selection is None:
            return []
        cs, _ = cv2.findContours((self.selection >= 128).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        return [c[:, 0, :] for c in cs if len(c) >= 2]

    def selection_bbox(self) -> Box | None:
        if self.selection is None:
            return None
        ys, xs = np.nonzero(self.selection)
        return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1

    # ---------------------------------------------------- thao tác điểm ảnh
    def _sel_alpha(self) -> np.ndarray:
        return self.selection.astype(np.float32) / 255 if self.selection is not None else \
            np.ones(self._blank().shape, np.float32)

    def clear_selection_pixels(self, layer: Layer) -> None:
        """Delete: xoá điểm trong vùng chọn trên layer (Redraw: trả về ảnh raw bên dưới)."""
        a = self._sel_alpha()[..., None]
        p = self.page
        if layer.kind == "redraw":
            p.working[:] = np.clip(p.working + (p.original.astype(np.float32) - p.working) * a, 0, 255).astype(np.uint8)
            p.paint_mask[a[..., 0] >= 0.99] = 0
        elif layer.kind == "pixel":
            layer.rgba[..., 3] = (layer.rgba[..., 3] * (1 - a[..., 0])).astype(np.uint8)

    def fill_selection(self, layer: Layer, bgr: tuple[int, int, int]) -> None:
        a = self._sel_alpha()[..., None]
        color = np.array(bgr, np.float32)
        p = self.page
        if layer.kind == "redraw":
            p.working[:] = np.clip(p.working + (color - p.working) * a, 0, 255).astype(np.uint8)
            p.paint_mask[a[..., 0] > 0.004] = 255
        elif layer.kind == "pixel":
            _over(layer.rgba, color, a[..., 0])

    def content_aware_fill(self, layer: Layer, inpainter, mask: np.ndarray | None = None) -> None:
        """Vẽ lại phần trong vùng chọn (hoặc `mask`) theo nền xung quanh bằng AI (LaMa)."""
        m = mask if mask is not None else self.selection
        if m is None:
            return
        m = ((m >= 128) * 255).astype(np.uint8)
        if not m.any():
            return
        p = self.page
        if layer.kind == "redraw":
            inpainter(p.working, m)
            p.paint_mask[m > 0] = 255
        elif layer.kind == "pixel":
            img = self.composite(text=False)
            inpainter(img, m)
            layer.rgba[m > 0, :3] = img[m > 0]
            layer.rgba[m > 0, 3] = 255

    # ----------------------------------------------------------------- lịch sử
    def _meta(self) -> dict:
        p = self.page
        return {
            "regions": copy.deepcopy(p.regions),
            "layers": [(L, L.name, L.visible, L.opacity, L.locked, L.blend, L.custom_name) for L in self.layers],
            "active": self.active, "selection": self.selection, "redrawn": p.redrawn, "text_mask": p.text_mask,
            "detected": p.detected, "typeset_profile": p.typeset_profile,
        }

    def _restore_meta(self, m: dict) -> None:
        p = self.page
        p.regions = copy.deepcopy(m["regions"])
        self.layers = []
        for L, name, visible, opacity, locked, blend, custom in m["layers"]:
            L.name, L.visible, L.opacity, L.locked, L.blend, L.custom_name = name, visible, opacity, locked, blend, custom
            self.layers.append(L)
        self.active = m["active"] if m["active"] in self.layers else self.layers[1]
        self.selection = m["selection"]
        p.redrawn, p.text_mask, p.detected = m["redrawn"], m["text_mask"], m["detected"]
        p.typeset_profile = m["typeset_profile"]

    def _targets(self, layers) -> list[tuple[object, str]]:
        out = []
        for L in layers:
            if L.kind == "redraw":
                out += [(self.page, "working"), (self.page, "paint_mask")]
            elif L.kind == "pixel":
                out.append((L, "rgba"))
        return out

    def begin(self, name: str, pixels=(), merge: str | None = None) -> None:
        """Bắt đầu một bước hoàn tác. `pixels`: các layer sẽ bị đổi điểm ảnh ("redraw" = layer Redraw)."""
        if self._pending is not None:
            self.end()
        layers = [self.layers[1] if L == "redraw" else L for L in pixels]
        targets = self._targets(layers)
        top = self.history[self.index - 1] if self.index else None
        if (merge and top is not None and top.merge == merge and self.index == len(self.history)
                and self.index != self.saved_index
                and time.time() - top.time < MERGE_SECONDS and not targets):
            self.history.pop()
            self.index -= 1
            step = top
        else:
            step = _Step(name, self._meta(), merge=merge)
        self._pending = (step, [(o, a, getattr(o, a).copy()) for o, a in targets])

    def end(self) -> None:
        if self._pending is None:
            return
        step, saved = self._pending
        self._pending = None
        for owner, attr, before in saved:
            after = getattr(owner, attr)
            if after.shape != before.shape:
                continue
            diff = before != after
            if diff.ndim == 3:
                diff = diff.any(axis=2)
            ys, xs = np.nonzero(diff)
            if len(xs):
                box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
                x1, y1, x2, y2 = box
                step.patches.append((owner, attr, box, before[y1:y2, x1:x2].copy(), after[y1:y2, x1:x2].copy()))
        self.sync()
        step.after = self._meta()
        step.time = time.time()
        if not step.patches and _same_meta(step.before, step.after):
            return  # không có gì đổi: không ghi bước
        if self.saved_index > self.index:
            self.saved_index = -1  # a new branch cannot return to the saved state
        del self.history[self.index:]
        self.history.append(step)
        while len(self.history) > MAX_HISTORY:
            self.history.pop(0)
            self.saved_index -= 1
        self.index = len(self.history)

    @property
    def dirty(self) -> bool:
        """Có thay đổi chưa lưu."""
        return self.index != self.saved_index

    @contextmanager
    def step(self, name: str, pixels=(), merge: str | None = None):
        self.begin(name, pixels, merge)
        try:
            yield
        finally:
            self.end()

    def undo(self) -> bool:
        if self._pending is not None:
            self.end()
        if self.index == 0:
            return False
        self.index -= 1
        step = self.history[self.index]
        for owner, attr, (x1, y1, x2, y2), before, _ in reversed(step.patches):
            getattr(owner, attr)[y1:y2, x1:x2] = before
        self._restore_meta(step.before)
        return True

    def redo(self) -> bool:
        if self.index >= len(self.history):
            return False
        step = self.history[self.index]
        self.index += 1
        for owner, attr, (x1, y1, x2, y2), _, after in step.patches:
            getattr(owner, attr)[y1:y2, x1:x2] = after
        self._restore_meta(step.after)
        return True

    def goto(self, index: int) -> None:
        """Nhảy tới trạng thái sau `index` bước (bảng History)."""
        while self.index > index and self.undo():
            pass
        while self.index < index and self.redo():
            pass


# ------------------------------------------------------------------- cọ vẽ ----
class Stroke:
    """Một nét cọ trên layer Redraw / layer ảnh: brush, eraser, clone (Clone Stamp), heal (Spot Healing).

    Độ phủ của cả nét được cộng dồn (lấy max) rồi áp một lần theo độ mờ, như Photoshop: tô đè trong cùng một
    nét không làm đậm thêm. Vùng chọn giới hạn chỗ được vẽ. Heal chỉ gom vùng; AI vẽ lại khi nhả chuột.
    """

    def __init__(self, doc: Document, layer: Layer, tool: str, size: float, hardness: float = 0.8,
                 opacity: float = 1.0, color=(0, 0, 0), clone_offset: tuple[int, int] | None = None):
        self.doc, self.layer, self.tool = doc, layer, tool
        self.size, self.hardness, self.opacity = max(1.0, size), hardness, opacity
        self.color = np.array(color, np.float32)
        w, h = doc.size
        self.cov = np.zeros((h, w), np.float32)
        p = doc.page
        self.before = (p.working.copy(), p.paint_mask.copy()) if layer.kind == "redraw" else layer.rgba.copy()
        self.source = p.working.copy() if tool == "clone" else None
        self.offset = clone_offset or (0, 0)
        self.sel = doc._sel_alpha() if doc.selection is not None else None
        self.last: tuple[float, float] | None = None
        self.bbox: Box | None = None

    def to(self, x: float, y: float) -> Box | None:
        """Kéo nét tới (x, y); trả về khung cần vẽ lại trên màn hình."""
        pts = [(x, y)]
        if self.last is not None:
            lx, ly = self.last
            dist = float(np.hypot(x - lx, y - ly))
            step = max(1.0, self.size * 0.15)
            n = int(dist // step)
            pts = [(lx + (x - lx) * k / max(n, 1), ly + (y - ly) * k / max(n, 1)) for k in range(1, n + 1)] or []
            if not pts:
                return None
        self.last = pts[-1]
        w, h = self.doc.size
        r = self.size / 2
        dirty = None
        for px, py in pts:
            x1, y1 = max(0, int(px - r - 1)), max(0, int(py - r - 1))
            x2, y2 = min(w, int(px + r + 2)), min(h, int(py + r + 2))
            if x1 >= x2 or y1 >= y2:
                continue
            yy, xx = np.mgrid[y1:y2, x1:x2]
            d = np.hypot(xx + 0.5 - px, yy + 0.5 - py)
            inner = r * self.hardness
            t = np.clip((d - inner) / max(r - inner, 0.75), 0, 1)
            dab = (1 - t * t * (3 - 2 * t)).astype(np.float32)
            np.maximum(self.cov[y1:y2, x1:x2], dab, out=self.cov[y1:y2, x1:x2])
            dirty = _union(dirty, (x1, y1, x2, y2))
        if dirty is None:
            return None
        self.bbox = _union(self.bbox, dirty)
        if self.tool != "heal":
            self._apply(dirty)
        return dirty

    def _apply(self, box: Box) -> None:
        x1, y1, x2, y2 = box
        a = self.cov[y1:y2, x1:x2] * self.opacity
        if self.sel is not None:
            a = a * self.sel[y1:y2, x1:x2]
        a3 = a[..., None]
        p = self.doc.page
        if self.tool == "clone":
            dx, dy = self.offset
            h, w = self.source.shape[:2]
            ys = np.clip(np.arange(y1, y2) + dy, 0, h - 1)
            xs = np.clip(np.arange(x1, x2) + dx, 0, w - 1)
            target = self.source[np.ix_(ys, xs)].astype(np.float32)
        elif self.tool == "eraser" and self.layer.kind == "redraw":
            target = p.original[y1:y2, x1:x2].astype(np.float32)
        else:
            target = np.broadcast_to(self.color, (y2 - y1, x2 - x1, 3))
        if self.layer.kind == "redraw":
            bw, bp = self.before
            base = bw[y1:y2, x1:x2].astype(np.float32)
            p.working[y1:y2, x1:x2] = np.clip(base + (target - base) * a3, 0, 255).astype(np.uint8)
            if self.tool == "eraser":
                p.paint_mask[y1:y2, x1:x2] = np.where(a >= 0.98, 0, bp[y1:y2, x1:x2])
            else:
                p.paint_mask[y1:y2, x1:x2] = np.where(a > 0.004, 255, bp[y1:y2, x1:x2])
        else:
            b = self.before[y1:y2, x1:x2].astype(np.float32)
            ba = b[..., 3] / 255
            if self.tool == "eraser":
                out_a = ba * (1 - a)
                rgb = b[..., :3]
            else:
                out_a = a + ba * (1 - a)
                rgb = (target * a3 + b[..., :3] * (ba * (1 - a))[..., None]) / np.maximum(out_a, 1e-6)[..., None]
            self.layer.rgba[y1:y2, x1:x2] = np.dstack([np.clip(rgb, 0, 255), out_a * 255]).astype(np.uint8)

    def heal_mask(self) -> np.ndarray:
        m = ((self.cov > 0.05) * 255).astype(np.uint8)
        if self.sel is not None:
            m[self.sel < 0.5] = 0
        return cv2.dilate(m, np.ones((3, 3), np.uint8))


# ---------------------------------------------------------------- tiện ích ----
def _checker(x1: int, y1: int, x2: int, y2: int, cell: int = 8) -> np.ndarray:
    """Nền ô cờ (chỗ trong suốt) như Photoshop."""
    yy, xx = np.mgrid[y1:y2, x1:x2]
    v = np.where(((xx // cell) + (yy // cell)) % 2 == 0, 255.0, 204.0).astype(np.float32)
    return np.repeat(v[..., None], 3, axis=2)


def _blend(base: np.ndarray, src: np.ndarray, mode: str) -> np.ndarray:
    if mode == "multiply":
        return base * src / 255
    if mode == "screen":
        return 255 - (255 - base) * (255 - src) / 255
    if mode == "darken":
        return np.minimum(base, src)
    if mode == "lighten":
        return np.maximum(base, src)
    return src


def _feather(m: np.ndarray, radius: float) -> np.ndarray:
    if radius <= 0:
        return m
    return cv2.GaussianBlur(m, (0, 0), radius / 2)


def _over(rgba: np.ndarray, color: np.ndarray, a: np.ndarray) -> None:
    b = rgba.astype(np.float32)
    ba = b[..., 3] / 255
    out_a = a + ba * (1 - a)
    rgb = (color * a[..., None] + b[..., :3] * (ba * (1 - a))[..., None]) / np.maximum(out_a, 1e-6)[..., None]
    rgba[:] = np.dstack([np.clip(rgb, 0, 255), out_a * 255]).astype(np.uint8)


def _union(a: Box | None, b: Box | None) -> Box | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def _same_meta(a: dict, b: dict) -> bool:
    return (a["regions"] == b["regions"] and a["layers"] == b["layers"] and a["active"] is b["active"]
            and a["selection"] is b["selection"] and a["redrawn"] is b["redrawn"]
            and a["text_mask"] is b["text_mask"] and a["detected"] == b["detected"]
            and a["typeset_profile"] == b["typeset_profile"])


def affine(src_box, dst_box, angle: float = 0.0) -> np.ndarray:
    """Ma trận 2x3: khung `src_box` → khung `dst_box` (chưa xoay), rồi xoay `angle` độ (chiều kim đồng hồ trên màn
    hình) quanh tâm khung mới."""
    sx1, sy1, sx2, sy2 = src_box
    dx1, dy1, dx2, dy2 = dst_box
    kx = (dx2 - dx1) / max(1e-6, sx2 - sx1)
    ky = (dy2 - dy1) / max(1e-6, sy2 - sy1)
    scale = np.array([[kx, 0, dx1 - sx1 * kx], [0, ky, dy1 - sy1 * ky], [0, 0, 1]], np.float64)
    cx, cy = (dx1 + dx2) / 2, (dy1 + dy2) / 2
    rot = np.vstack([cv2.getRotationMatrix2D((cx, cy), -angle, 1.0), [0, 0, 1]])
    return (rot @ scale)[:2]
