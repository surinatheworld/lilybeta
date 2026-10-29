"""Xoá chữ gốc và vẽ lại nền trong một vùng."""
from __future__ import annotations

import cv2
import numpy as np

MARGIN = 20
FURIGANA_PAD = 10


def build_text_mask(text_mask: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """Mask nhị phân (0/255) các nét chữ trong khung, nới rộng để ăn cả viền chữ."""
    h, w = text_mask.shape[:2]
    # Nới khung ra một chút để ăn cả furigana nằm sát mép khung chữ.
    x1, y1 = max(0, box[0] - FURIGANA_PAD), max(0, box[1] - FURIGANA_PAD)
    x2, y2 = min(w, box[2] + FURIGANA_PAD), min(h, box[3] + FURIGANA_PAD)
    out = np.zeros((h, w), np.uint8)
    out[y1:y2, x1:x2] = (text_mask[y1:y2, x1:x2] > 0.3).astype(np.uint8) * 255
    if out[y1:y2, x1:x2].sum() == 0:
        bx1, by1, bx2, by2 = box
        out[by1:by2, bx1:bx2] = 255  # model không ra nét chữ: xoá cả khung
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return cv2.dilate(out, k, iterations=2)


def clean_region(img: np.ndarray, text_mask: np.ndarray, box: tuple[int, int, int, int],
                 area_mask: np.ndarray | None = None) -> None:
    """Xoá chữ trong `box` trực tiếp trên `img` (BGR).

    `area_mask` (vùng khoanh lasso, 0/255 cỡ ảnh): xoá toàn bộ trong vùng đó thay vì chỉ nét chữ.
    Nền đồng màu (bóng thoại trắng…) được tô bằng màu nền; nền phức tạp dùng inpaint.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    cx1, cy1 = max(0, x1 - MARGIN), max(0, y1 - MARGIN)
    cx2, cy2 = min(w, x2 + MARGIN), min(h, y2 + MARGIN)

    if area_mask is not None:
        mask = cv2.dilate(area_mask[cy1:cy2, cx1:cx2], np.ones((3, 3), np.uint8))
    else:
        mask = build_text_mask(text_mask, box)[cy1:cy2, cx1:cx2]
    if not mask.any():
        return
    crop = img[cy1:cy2, cx1:cx2]

    ring = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=2)
    ring[mask > 0] = 0
    border = crop[ring > 0]
    if len(border) and border.std(axis=0).max() < 14:
        bg = np.median(border, axis=0).astype(np.uint8)
        crop[mask > 0] = bg
        if area_mask is None:
            _remove_leftover_strokes(crop, (x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1))
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


def _remove_leftover_strokes(crop: np.ndarray, box: tuple[int, int, int, int]) -> None:
    """Nền trơn: xoá nét mảnh model bỏ sót (vd "──") nằm lọt thỏm trong lòng bóng.

    Chỉ xoá những điểm bị nền bao quanh hoàn toàn, nên viền bóng và nền ngoài bóng không bị đụng tới.
    """
    x1, y1, x2, y2 = box
    seed = ((x1 + x2) // 2, (y1 + y2) // 2)
    ff = np.zeros((crop.shape[0] + 2, crop.shape[1] + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    cv2.floodFill(crop.copy(), ff, seed, (0, 0, 0), (30, 30, 30), (30, 30, 30), flags)
    region = ff[1:-1, 1:-1]
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    interior = np.zeros_like(region)
    cv2.drawContours(interior, contours, -1, 255, cv2.FILLED)
    holes = (interior > 0) & (region == 0)
    inside_box = np.zeros_like(holes)
    inside_box[y1:y2, x1:x2] = True
    holes = cv2.dilate((holes & inside_box).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    holes &= interior > 0
    crop[holes] = crop[seed[1], seed[0]]


def _surface(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    x, y = xs.astype(np.float32) / 100, ys.astype(np.float32) / 100
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)


def _fit_surface(xs, ys, values):
    a = _surface(xs, ys)
    coef, *_ = np.linalg.lstsq(a, values, rcond=None)
    return coef, a @ coef


def text_area_candidates(img: np.ndarray, box: tuple[int, int, int, int],
                         area_mask: np.ndarray | None = None) -> list[tuple[int, int, int, int]]:
    """Các khung có thể đặt bản dịch trong lòng bóng thoại chứa `box` (sau khi đã xoá chữ).

    Loang màu từ tâm khung để lấy lòng bóng, rồi liệt kê các hình chữ nhật nằm trọn trong đó.
    Nếu vùng loang không giống bóng thoại (tràn ra mép ảnh, quá lớn) thì dùng vùng lasso
    `area_mask` (nếu có), không thì chỉ dùng khung gốc.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box)
    seed = ((x1 + x2) // 2, (y1 + y2) // 2)
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    cv2.floodFill(img.copy(), ff_mask, seed, (0, 0, 0), (12, 12, 12), (12, 12, 12), flags)
    region = _fill_holes(ff_mask[1:-1, 1:-1])
    n = int(region.sum())
    box_area = max(1, (x2 - x1) * (y2 - y1))
    ys, xs = np.nonzero(region) if n else (np.array([0]), np.array([0]))
    is_bubble = (n > 0 and xs.min() > 0 and ys.min() > 0 and xs.max() < w - 1 and ys.max() < h - 1
                 and n <= 0.3 * w * h and n <= 80 * box_area)
    if not is_bubble:
        if area_mask is None:
            return [box]
        region = _fill_holes(area_mask)
        ys, xs = np.nonzero(region)
        if len(xs) == 0:
            return [box]
    bx1, by1, bx2, by2 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1

    pad = 3
    cands = []
    seed_rel = (seed[0] - bx1, seed[1] - by1)
    for rx1, ry1, rx2, ry2 in _inner_rects(region[by1:by2, bx1:bx2], seed_rel):
        r = (int(bx1 + rx1 + pad), int(by1 + ry1 + pad), int(bx1 + rx2 - pad), int(by1 + ry2 - pad))
        if r[2] - r[0] > 8 and r[3] - r[1] > 8:
            cands.append(r)
    # Khung chữ gốc có thể chòi ra ngoài lòng bóng (bóng đôi, bóng méo) nên chỉ dùng khi không còn cách khác.
    return cands or [box]


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Lấp lỗ trong vùng (điểm mờ còn sót sau khi xoá chữ) để không chặn việc tìm khung đặt chữ."""
    contours, _ = cv2.findContours((mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(out, contours, -1, 1, cv2.FILLED)
    return out > 0


def _inner_rects(region: np.ndarray, seed: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    """Các hình chữ nhật nằm trọn trong `region` và chứa điểm `seed` (tâm chữ gốc).

    Bóng thoại có thể méo hoặc là bóng đôi, nên thử nhiều tâm dọc và chiều cao khác nhau rồi để
    bước dàn chữ chọn khung cho cỡ chữ lớn nhất. Mỗi hàng chỉ lấy đoạn liền chứa cột `seed`,
    nên chữ của nửa bóng nào nằm trong nửa bóng đó.
    """
    h, w = region.shape
    sx, sy = int(np.clip(seed[0], 0, w - 1)), int(np.clip(seed[1], 0, h - 1))
    cols = np.arange(w)
    valid = region[:, sx]
    left = np.where(~region[:, :sx + 1], cols[:sx + 1], -1).max(axis=1) + 1
    right = np.where(~region[:, sx:], cols[sx:], w).min(axis=1)
    left = np.where(valid, left, w)
    right = np.where(valid, right, 0)
    centers = {sy}
    centers.update(int(h * f) for f in (0.3, 0.4, 0.5, 0.6, 0.7))
    step = max(2, h // 40)
    out = []
    for cy in sorted(centers):
        cur_l, cur_r = 0, w
        for hh in range(1, h):
            top, bot = cy - hh, cy + hh - 1
            if top < 0 or bot >= h:
                break
            cur_l = max(cur_l, left[top], left[bot])
            cur_r = min(cur_r, right[top], right[bot])
            if cur_r - cur_l <= 0:
                break
            if hh % step == 0 and top <= sy <= bot:
                out.append((int(cur_l), top, int(cur_r), bot + 1))
    return out


