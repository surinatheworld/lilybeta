"""Phần dùng chung cho các model vẽ lại nền: cắt ô vuông quanh vùng cần vẽ và dán kết quả lại."""
from __future__ import annotations

import cv2
import numpy as np


def square_around(mask: np.ndarray, min_side: int = 192, context: float = 2.2):
    """Ô vuông (x1, y1, x2, y2) quanh vùng `mask`, chừa nền xung quanh để model nhìn hoạ tiết."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    h, w = mask.shape[:2]
    x1, y1, x2, y2 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    side = int(max(x2 - x1, y2 - y1) * context)
    side = min(max(side, min_side), max(h, w))
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    sx1 = int(np.clip(cx - side // 2, 0, max(0, w - side)))
    sy1 = int(np.clip(cy - side // 2, 0, max(0, h - side)))
    return sx1, sy1, min(w, sx1 + side), min(h, sy1 + side)


def resize_to(img: np.ndarray, w: int, h: int) -> np.ndarray:
    shrink = w < img.shape[1] or h < img.shape[0]
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA if shrink else cv2.INTER_CUBIC)


def blend_back(img: np.ndarray, box, out: np.ndarray, mask: np.ndarray) -> None:
    """Dán `out` (đã đúng cỡ ô `box`) vào `img`, chỉ trong `mask`, mép hoà dần để không lộ đường cắt."""
    x1, y1, x2, y2 = box
    crop = img[y1:y2, x1:x2]
    m = (mask[y1:y2, x1:x2] > 0).astype(np.float32)
    alpha = np.maximum(cv2.GaussianBlur(m, (5, 5), 0), m)[..., None]
    crop[:] = (out * alpha + crop * (1 - alpha)).astype(np.uint8)
