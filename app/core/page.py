"""Trạng thái một trang: ảnh gốc, ảnh đã xoá chữ và các vùng chữ."""
from __future__ import annotations

import itertools
import os
from dataclasses import dataclass, field, replace

import cv2
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from .clean import MARGIN, clean_local, fill_bubble, is_small_sfx, text_area_candidates
from .detect import TextDetector, sort_reading_order
from .typeset import TextStyle, best_rect, draw_text
from .typeset_profiles import apply_profile, profile_style

Box = tuple[int, int, int, int]
AUTO_CAP_FRAC = 0.031
_region_ids = itertools.count(1)


def new_uid() -> int:
    return next(_region_ids)


def reserve_uids(upto: int) -> None:
    """Mở lại dự án: uid mới phải lớn hơn mọi uid đã lưu."""
    global _region_ids
    _region_ids = itertools.count(max(next(_region_ids), upto + 1))


@dataclass
class Region:
    box: Box
    text: str = ""
    style: TextStyle = field(default_factory=TextStyle)
    cleaned: bool = False
    text_rects: list[Box] = field(default_factory=list)  # các khung có thể đặt chữ (lòng bóng thoại)
    kind: str = "text"  # "text" | "sfx" | "manual" (vẽ tay) | "lasso"
    polygon: list[tuple[int, int]] | None = None  # đường khoanh lasso (toạ độ ảnh)
    auto_cap: float = 0.0  # cỡ chữ chuẩn của trang (do Page đặt)
    manual_rect: Box | None = None  # khung chữ người dùng tự kéo/căn chỉnh (ưu tiên hơn khung tự động)
    needs_redraw: bool = False  # nền khó, tool để nguyên chữ gốc cho người dùng tự redraw
    uid: int = field(default_factory=new_uid)  # định danh bền (layer chữ, lịch sử hoàn tác trỏ tới vùng qua uid)
    typeset_kind: str = "auto"  # automatic balloon shape, or a user-selected font category

    @property
    def text_rect(self) -> Box:
        """Khung đặt chữ: khung người dùng tự chỉnh, không thì khung cho cỡ chữ lớn nhất."""
        if self.manual_rect:
            return self.manual_rect
        return best_rect(self.text, self.effective_style, self.text_rects) if self.text_rects else self.box

    @property
    def effective_style(self) -> TextStyle:
        """Kiểu chữ dùng để dàn chữ: cỡ tự động thì giới hạn theo cỡ chuẩn của trang (nếu chưa đặt tay)."""
        if self.style.size == 0 and self.style.max_size == 0 and self.auto_cap:
            return replace(self.style, max_size=self.auto_cap)
        return self.style

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
        self.redrawn: np.ndarray | None = None  # bản đã redraw (xoá chữ) nạp từ ngoài, cùng cỡ ảnh gốc
        self.working = img.copy()
        # Điểm người dùng tự vẽ trên layer Redraw (cọ, clone…): thuộc layer Redraw dù trùng màu ảnh gốc.
        self.paint_mask = np.zeros(img.shape[:2], np.uint8)
        self.text_mask = np.zeros(img.shape[:2], np.float32)
        self.regions: list[Region] = []
        self.detected = False
        self.typeset_profile = ""
        self.inpainter = None  # hàm inpaint(img, mask) bằng AI cho nền có hoạ tiết; None = OpenCV

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.original.shape[:2]
        return w, h

    @property
    def base(self) -> np.ndarray:
        """Nền để xoá chữ / hoàn tác: bản redraw nạp từ ngoài nếu có, không thì ảnh raw."""
        return self.original if self.redrawn is None else self.redrawn

    def set_redrawn(self, path: str | None) -> None:
        """Dùng ảnh đã redraw sẵn (xoá chữ bằng Photoshop, AI…) làm nền; None = bỏ, quay về ảnh raw.

        Vị trí chữ vẫn nhận diện trên ảnh raw. Vùng nào bản redraw đã xoá chữ thì giữ nguyên bản redraw,
        vùng còn chữ thì tool xoá như thường. Các vùng đã xử lý được xoá lại trên nền mới (giữ câu dịch).
        """
        if path is None:
            self.redrawn = None
        else:
            img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"Không đọc được ảnh: {path}")
            w, h = self.size
            rh, rw = img.shape[:2]
            if abs(np.log((rw / rh) / (w / h))) > 0.02:
                raise ValueError(f"{os.path.basename(path)} ({rw}x{rh}) khác tỉ lệ ảnh raw ({w}x{h}): "
                                 "bản redraw phải cùng khung với ảnh gốc.")
            if (rw, rh) != (w, h):
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA if rw > w else cv2.INTER_CUBIC)
            self.redrawn = img
        self.working = self.base.copy()
        self.paint_mask[:] = 0
        for r in self.regions:
            r.cleaned = r.needs_redraw = False
            r.text_rects = []

    def redrawn_covers(self, region: Region) -> bool:
        """Bản redraw nạp từ ngoài đã xoá chữ ở vùng này chưa (nét chữ trên raw đã đổi màu hẳn)."""
        if self.redrawn is None:
            return False
        w, h = self.size
        x1, y1, x2, y2 = region.box
        x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
        strokes = self.text_mask[y1:y2, x1:x2] > 0.5
        area = self.area_mask(region)
        if area is not None:
            strokes &= area[y1:y2, x1:x2] > 0
        if strokes.sum() < 20:  # model không ra nét chữ (vùng vẽ tay): xét các điểm tối
            strokes = cv2.cvtColor(self.original[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY) < 128
        if strokes.sum() < 20:
            return True
        diff = np.abs(self.redrawn[y1:y2, x1:x2].astype(np.int16) - self.original[y1:y2, x1:x2]).max(axis=2)
        return float((diff[strokes] > 60).mean()) > 0.5

    def detect(self, detector: TextDetector, right_to_left: bool = True) -> None:
        boxes, mask = detector.detect(self.original)
        self.text_mask = np.maximum(mask, color_text_mask(self.original))
        boxes = sort_reading_order(boxes, right_to_left, self.original)
        self.regions = [r for r in self.regions if r.cleaned]
        self.regions += [Region((b.x1, b.y1, b.x2, b.y2), kind=b.kind) for b in boxes]
        apply_profile(self)
        self.detected = True

    def add_region(self, box: Box) -> Region:
        x1, y1, x2, y2 = box
        w, h = self.size
        box = (max(0, min(x1, x2)), max(0, min(y1, y2)), min(w, max(x1, x2)), min(h, max(y1, y2)))
        r = Region(box, kind="manual")
        r.style = profile_style(self, r, r.style)
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

    def _clean_local(self, region: Region):
        """Xoá chữ phần làm được trên máy; trả về mask còn cần AI vẽ lại (None nếu xong / để lại)."""
        if self.redrawn_covers(region):
            return None
        need = clean_local(self.working, self.text_mask, region.box, self.area_mask(region), self.inpainter,
                           whole_box=region.kind == "text")
        skip = getattr(self.inpainter, "skip", False)
        if need is not None and (skip(self, region) if callable(skip) else skip):
            region.needs_redraw = True
            return None
        return need

    def auto_clean(self, small_sfx: bool = True, fit_bubble: bool = True) -> int:
        """Xoá ngay những chỗ chắc ăn, chưa cần câu dịch: chữ trong bóng thoại / hộp dẫn truyện sáng khép kín,
        chỗ bản redraw đã xoá, và (nếu `small_sfx`) SFX nhỏ bằng `self.inpainter`. Chữ đè lên tranh, SFX lớn,
        chữ ngoài bóng (tên tác giả…) để nguyên. Trả về số vùng đã xoá."""
        n = 0
        for r in self.regions:
            if r.cleaned or r.polygon:
                continue
            if r.kind == "sfx" and not small_sfx:
                continue  # detector false positives can be hearts, hair or other drawing details
            if self.redrawn_covers(r) or fill_bubble(self.working, self.text_mask, r.box):
                pass
            elif small_sfx and is_small_sfx(r, self.size):
                need = clean_local(self.working, self.text_mask, r.box, None, self.inpainter)
                if need is not None:
                    self.inpainter(self.working, need)
            else:
                continue
            r.cleaned = True
            self.clean(r, fit_bubble)  # đã xoá: chỉ tính khung đặt chữ
            n += 1
        return n

    def _clean_pixels(self, region: Region) -> None:
        need = self._clean_local(region)
        if need is not None:
            self.inpainter(self.working, need)

    def remove_region(self, region: Region) -> None:
        self.reset(region)
        self.regions.remove(region)

    @property
    def auto_cap(self) -> float:
        """Cỡ chữ chuẩn khi tự co: ~3,1% bề ngang trang (đo từ chương typeset tay: 836px → 26px)."""
        return round(self.size[0] * AUTO_CAP_FRAC)

    def clean(self, region: Region, fit_bubble: bool = True) -> None:
        region.style = profile_style(self, region, region.style)
        region.auto_cap = self.auto_cap
        if not region.cleaned:
            self._clean_pixels(region)
            region.cleaned = True
        if region.kind == "sfx":
            # Dịch SFX đặt ngay tại chỗ SFX gốc (không loang ra nền trắng của panel).
            x1, y1, x2, y2 = region.box
            ex, ey = (x2 - x1) // 5, (y2 - y1) // 5
            w, h = self.size
            region.text_rects = [(max(0, x1 - ex), max(0, y1 - ey), min(w, x2 + ex), min(h, y2 + ey))]
        elif fit_bubble:
            cands = text_area_candidates(self.working, region.box, self.area_mask(region))
            # Khung đặt chữ không được đè lên chữ gốc của vùng khác (vd hai câu chung một bóng).
            others = [o.box for o in self.regions if o is not region]
            free = [c for c in cands if not any(c[0] < o[2] - 2 and o[0] < c[2] - 2 and c[1] < o[3] - 2
                                                and o[1] < c[3] - 2 for o in others)]
            region.text_rects = free or [region.box]
        else:
            region.text_rects = [region.box]

    def clean_many(self, regions: list[Region], fit_bubble: bool = True) -> None:
        """Xoá chữ nhiều vùng; phần cần AI trên mạng của các vùng gần nhau được gom vào một lần gọi."""
        pending: list[tuple[list[Region], np.ndarray]] = []
        for r in regions:
            if r.cleaned:
                continue
            need = self._clean_local(r)
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
            self.working[y1:y2, x1:x2] = self.base[y1:y2, x1:x2]
            # Vẽ lại các vùng khác đã xoá mà chồng lên phần vừa khôi phục.
            for other in self.regions:
                if other is not region and other.cleaned and _overlap(other.box, (x1, y1, x2, y2)):
                    self._clean_pixels(other)
        region.cleaned = region.needs_redraw = False
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
                draw_text(p, r.text, r.effective_style, QRectF(x1, y1, x2 - x1, y2 - y1))
        p.end()
        return img

    def export(self, path: str) -> None:
        if not self.render().save(path, None, 95):  # JPEG chất lượng 95
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
