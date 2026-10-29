"""Xoá chữ gốc và vẽ lại nền trong một vùng."""
from __future__ import annotations

import cv2
import numpy as np

MARGIN = 12


def build_text_mask(text_mask: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """Mask nhị phân (0/255) các nét chữ trong khung, nới rộng để ăn cả viền chữ."""
    x1, y1, x2, y2 = box
    h, w = text_mask.shape[:2]
    out = np.zeros((h, w), np.uint8)
    out[y1:y2, x1:x2] = (text_mask[y1:y2, x1:x2] > 0.3).astype(np.uint8) * 255
    if out[y1:y2, x1:x2].sum() == 0:
        out[y1:y2, x1:x2] = 255  # model không ra nét chữ: xoá cả khung
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return cv2.dilate(out, k, iterations=2)


def clean_region(img: np.ndarray, text_mask: np.ndarray, box: tuple[int, int, int, int]) -> None:
    """Xoá chữ trong `box` trực tiếp trên `img` (BGR).

    Nền đồng màu (bóng thoại trắng…) được tô bằng màu nền; nền phức tạp dùng inpaint.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    cx1, cy1 = max(0, x1 - MARGIN), max(0, y1 - MARGIN)
    cx2, cy2 = min(w, x2 + MARGIN), min(h, y2 + MARGIN)

    mask = build_text_mask(text_mask, box)[cy1:cy2, cx1:cx2]
    if not mask.any():
        return
    crop = img[cy1:cy2, cx1:cx2]

    ring = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=2)
    ring[mask > 0] = 0
    border = crop[ring > 0]
    if len(border) and border.std(axis=0).max() < 14:
        crop[mask > 0] = np.median(border, axis=0).astype(np.uint8)
        return
    # Nền chuyển màu mượt (gradient): khớp mặt cong bậc 2 theo viền rồi tô lại.
    if len(border) >= 30:
        ys, xs = np.nonzero(ring)
        coef, fitted = _fit_surface(xs, ys, border.astype(np.float32))
        if np.abs(fitted - border).mean() < 6:
            my, mx = np.nonzero(mask)
            crop[my, mx] = np.clip(_surface(mx, my) @ coef, 0, 255).astype(np.uint8)
            return
    crop[:] = cv2.inpaint(crop, mask, 5, cv2.INPAINT_TELEA)


def _surface(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    x, y = xs.astype(np.float32) / 100, ys.astype(np.float32) / 100
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)


def _fit_surface(xs, ys, values):
    a = _surface(xs, ys)
    coef, *_ = np.linalg.lstsq(a, values, rcond=None)
    return coef, a @ coef


def bubble_rect(img: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Tìm lòng bóng thoại chứa `box` (sau khi đã xoá chữ) để đặt bản dịch cho rộng rãi.

    Loang màu từ tâm khung; nếu vùng loang quá lớn (không phải bóng thoại) thì giữ nguyên khung.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    seed = ((x1 + x2) // 2, (y1 + y2) // 2)
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    cv2.floodFill(img.copy(), ff_mask, seed, (0, 0, 0), (12, 12, 12), (12, 12, 12), flags)
    region = ff_mask[1:-1, 1:-1]
    ys, xs = np.nonzero(region)
    if len(xs) == 0:
        return box
    bx1, by1, bx2, by2 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    box_area = (x2 - x1) * (y2 - y1)
    if (bx2 - bx1) * (by2 - by1) > box_area * 8 or bx1 == 0 or by1 == 0 or bx2 == w or by2 == h:
        return box
    # Hình chữ nhật nội tiếp gần đúng của bóng (thường là elip): thu vào ~15% mỗi chiều.
    ix, iy = int((bx2 - bx1) * 0.15), int((by2 - by1) * 0.15)
    rx1, ry1, rx2, ry2 = bx1 + ix, by1 + iy, bx2 - ix, by2 - iy
    return (min(rx1, x1), min(ry1, y1), max(rx2, x2), max(ry2, y2))
