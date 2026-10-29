"""Trạng thái một trang: ảnh gốc, ảnh đã xoá chữ và các vùng chữ."""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from .clean import MARGIN, bubble_rect, clean_region
from .detect import TextDetector, sort_reading_order
from .typeset import TextStyle, draw_text

Box = tuple[int, int, int, int]


@dataclass
class Region:
    box: Box
    text: str = ""
    style: TextStyle = field(default_factory=TextStyle)
    cleaned: bool = False
    text_rect: Box | None = None  # khung đặt chữ (mặc định = box, hoặc lòng bóng thoại)

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

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.original.shape[:2]
        return w, h

    def detect(self, detector: TextDetector, right_to_left: bool = True) -> None:
        boxes, self.text_mask = detector.detect(self.original)
        boxes = sort_reading_order(boxes, right_to_left)
        self.regions = [r for r in self.regions if r.cleaned]
        self.regions += [Region((b.x1, b.y1, b.x2, b.y2)) for b in boxes]

    def add_region(self, box: Box) -> Region:
        x1, y1, x2, y2 = box
        w, h = self.size
        box = (max(0, min(x1, x2)), max(0, min(y1, y2)), min(w, max(x1, x2)), min(h, max(y1, y2)))
        r = Region(box)
        self.regions.append(r)
        return r

    def remove_region(self, region: Region) -> None:
        self.reset(region)
        self.regions.remove(region)

    def clean(self, region: Region, fit_bubble: bool = True) -> None:
        if not region.cleaned:
            clean_region(self.working, self.text_mask, region.box)
            region.cleaned = True
        region.text_rect = bubble_rect(self.working, region.box) if fit_bubble else region.box

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
                    clean_region(self.working, self.text_mask, other.box)
        region.cleaned = False
        region.text = ""
        region.text_rect = None

    def render(self) -> QImage:
        rgb = cv2.cvtColor(self.working, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        p = QPainter(img)
        for r in self.regions:
            if r.cleaned and r.text.strip():
                x1, y1, x2, y2 = r.text_rect or r.box
                draw_text(p, r.text, r.style, QRectF(x1, y1, x2 - x1, y2 - y1))
        p.end()
        return img

    def export(self, path: str) -> None:
        if not self.render().save(path):
            raise IOError(f"Không lưu được: {path}")


def _overlap(a: Box, b: Box) -> bool:
    m = 2 * MARGIN
    return a[0] - m < b[2] and b[0] < a[2] + m and a[1] - m < b[3] and b[1] < a[3] + m
