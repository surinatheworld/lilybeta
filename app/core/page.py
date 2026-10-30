"""Trạng thái một trang: ảnh gốc, ảnh đã xoá chữ và các vùng chữ."""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from .clean import MARGIN, clean_local, clean_region, text_area_candidates
from .detect import TextDetector, sort_reading_order
from .typeset import TextStyle, best_rect, draw_text

Box = tuple[int, int, int, int]


@dataclass
class Region:
    box: Box
    text: str = ""
    style: TextStyle = field(default_factory=TextStyle)
    cleaned: bool = False
    text_rects: list[Box] = field(default_factory=list)  # các khung có thể đặt chữ (lòng bóng thoại)
    kind: str = "text"  # "text" | "sfx" | "manual" (vẽ tay) | "lasso"
    polygon: list[tuple[int, int]] | None = None  # đường khoanh lasso (toạ độ ảnh)
    manual_rect: Box | None = None  # khung chữ người dùng tự kéo/căn chỉnh (ưu tiên hơn khung tự động)

    @property
    def text_rect(self) -> Box:
        """Khung đặt chữ: khung người dùng tự chỉnh, không thì khung cho cỡ chữ lớn nhất."""
        if self.manual_rect:
            return self.manual_rect
        return best_rect(self.text, self.style, self.text_rects) if self.text_rects else self.box

    @property
    def done(self) -> bool:
        return self.cleaned and bool(self.text.strip())


class Page:
    def __init__(self, path: str):
        self.path = path
        data = np.fromfile(path, np.uint8)  # đọc được cả đường dẫn có dấu tiếng Việt
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Không đọc được ảnh: {path}")
        self.original = img
        self.working = img.copy()
        self.text_mask = np.zeros(img.shape[:2], np.float32)
        self.regions: list[Region] = []
        self.inpainter = None  # hàm inpaint(img, mask) bằng AI cho nền có hoạ tiết; None = OpenCV

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.original.shape[:2]
        return w, h

    def detect(self, detector: TextDetector, right_to_left: bool = True) -> None:
        boxes, mask = detector.detect(self.original)
        self.text_mask = np.maximum(mask, color_text_mask(self.original))
        boxes = sort_reading_order(boxes, right_to_left)
        self.regions = [r for r in self.regions if r.cleaned]
        self.regions += [Region((b.x1, b.y1, b.x2, b.y2), kind=b.kind) for b in boxes]

    def add_region(self, box: Box) -> Region:
        x1, y1, x2, y2 = box
        w, h = self.size
        box = (max(0, min(x1, x2)), max(0, min(y1, y2)), min(w, max(x1, x2)), min(h, max(y1, y2)))
        r = Region(box, kind="manual")
        self.regions.append(r)
        return r

    def add_lasso(self, points: list[tuple[int, int]]) -> Region:
        w, h = self.size
        pts = [(int(np.clip(x, 0, w - 1)), int(np.clip(y, 0, h - 1))) for x, y in points]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        r = Region((min(xs), min(ys), max(xs) + 1, max(ys) + 1), kind="lasso", polygon=pts)
        self.regions.append(r)
        return r

    def area_mask(self, region: Region) -> np.ndarray | None:
        if not region.polygon:
            return None
        w, h = self.size
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [np.array(region.polygon, np.int32)], 255)
        return m

    def _clean_pixels(self, region: Region) -> None:
        clean_region(self.working, self.text_mask, region.box, self.area_mask(region), self.inpainter)

    def remove_region(self, region: Region) -> None:
        self.reset(region)
        self.regions.remove(region)

    def clean(self, region: Region, fit_bubble: bool = True) -> None:
        if not region.cleaned:
            self._clean_pixels(region)
            region.cleaned = True
        if fit_bubble:
            region.text_rects = text_area_candidates(self.working, region.box, self.area_mask(region))
        else:
            region.text_rects = [region.box]

    def clean_many(self, regions: list[Region], fit_bubble: bool = True) -> None:
        """Xoá chữ nhiều vùng; phần cần AI trên mạng của các vùng gần nhau được gom vào một lần gọi."""
        pending: list[tuple[list[Region], np.ndarray]] = []
        for r in regions:
            if r.cleaned:
                continue
            need = clean_local(self.working, self.text_mask, r.box, self.area_mask(r), self.inpainter)
            if need is None:
                r.cleaned = True
            else:
                pending.append(([r], need))
        ctx = getattr(self.inpainter, "context_box", None)
        if ctx is not None:  # gộp các vùng có vùng gửi đi chồng lên nhau
            merged = True
            while merged:
                merged = False
                for i in range(len(pending)):
                    for j in range(i + 1, len(pending)):
                        a, b = ctx(pending[i][1]), ctx(pending[j][1])
                        if a and b and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                            pending[i] = (pending[i][0] + pending[j][0], np.maximum(pending[i][1], pending[j][1]))
                            del pending[j]
                            merged = True
                            break
                    if merged:
                        break
        for group, mask in pending:
            self.inpainter(self.working, mask)
            for r in group:
                r.cleaned = True
        for r in regions:
            if r.cleaned:
                self.clean(r, fit_bubble)  # đã xoá rồi: chỉ tính lại khung đặt chữ

    def reset(self, region: Region) -> None:
        """Hoàn tác vùng: khôi phục ảnh gốc và bỏ chữ."""
        if region.cleaned:
            x1, y1, x2, y2 = region.box
            w, h = self.size
            x1, y1, x2, y2 = max(0, x1 - MARGIN), max(0, y1 - MARGIN), min(w, x2 + MARGIN), min(h, y2 + MARGIN)
            self.working[y1:y2, x1:x2] = self.original[y1:y2, x1:x2]
            # Vẽ lại các vùng khác đã xoá mà chồng lên phần vừa khôi phục.
            for other in self.regions:
                if other is not region and other.cleaned and _overlap(other.box, (x1, y1, x2, y2)):
                    self._clean_pixels(other)
        region.cleaned = False
        region.text = ""
        region.text_rects = []
        region.manual_rect = None

    def render(self) -> QImage:
        rgb = cv2.cvtColor(self.working, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        p = QPainter(img)
        for r in self.regions:
            if r.cleaned and r.text.strip():
                x1, y1, x2, y2 = r.text_rect
                draw_text(p, r.text, r.style, QRectF(x1, y1, x2 - x1, y2 - y1))
        p.end()
        return img

    def export(self, path: str) -> None:
        if not self.render().save(path):
            raise IOError(f"Không lưu được: {path}")


def color_text_mask(img: np.ndarray) -> np.ndarray:
    """Truyện đen trắng: điểm có màu (chữ tên truyện hồng/xanh, hoạ tiết trang trí) coi như chữ.

    Truyện màu thì bỏ qua, không thì cả tranh trong khung chữ cũng bị xoá.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    colored = (hsv[..., 1] > 50) & (hsv[..., 2] > 60)
    if colored.mean() > 0.05:
        return np.zeros(img.shape[:2], np.float32)
    return colored.astype(np.float32)


def _overlap(a: Box, b: Box) -> bool:
    m = 2 * MARGIN
    return a[0] - m < b[2] and b[0] < a[2] + m and a[1] - m < b[3] and b[1] < a[3] + m
