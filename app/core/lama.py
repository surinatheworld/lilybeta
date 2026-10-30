"""Vẽ lại nền bằng model AI LaMa (chạy trên máy, không cần mạng sau lần tải đầu)."""
from __future__ import annotations

import os
import threading
import urllib.request

import cv2
import numpy as np

from .inpaint_util import blend_back, resize_to, square_around
from .paths import model_path

MODEL_URL = "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
MODEL_PATH = model_path("lama_fp32.onnx")
SIZE = 512  # model nhận đúng ảnh 512x512

_session = None
_lock = threading.Lock()


def get_session():
    """Nạp model (lần đầu mất ~30-40 giây, nên gọi sẵn ở luồng nền)."""
    global _session
    with _lock:
        if _session is None:
            import onnxruntime as ort

            if not os.path.exists(MODEL_PATH):
                tmp = MODEL_PATH + ".part"
                urllib.request.urlretrieve(MODEL_URL, tmp)
                os.replace(tmp, MODEL_PATH)
            _session = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
        return _session


def is_ready() -> bool:
    return _session is not None


TILE_CORE = 320  # phần giữa mỗi ô 512px dùng để vẽ, phần còn lại là nền xung quanh cho model nhìn


def inpaint(img: np.ndarray, mask: np.ndarray) -> None:
    """Vẽ lại vùng `mask` (0/255, cỡ ảnh) trên `img` (BGR) tại chỗ.

    Vùng nhỏ: một ô vuông quanh vùng. Vùng lớn: chia thành nhiều ô 512px ở độ phân giải gốc
    (không thu nhỏ ảnh, để giữ được lưới chấm/hoạ tiết mảnh), vẽ lần lượt từng ô.
    """
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return
    if max(xs.max() - xs.min(), ys.max() - ys.min()) * 2.2 <= SIZE:
        _inpaint_window(img, mask, square_around(mask))
        return
    h, w = mask.shape[:2]
    for cy in range(int(ys.min()) + TILE_CORE // 2, int(ys.max()) + TILE_CORE, TILE_CORE):
        for cx in range(int(xs.min()) + TILE_CORE // 2, int(xs.max()) + TILE_CORE, TILE_CORE):
            cy0, cx0 = min(cy, int(ys.max())), min(cx, int(xs.max()))
            core = np.zeros_like(mask)
            core[max(0, cy0 - TILE_CORE // 2):cy0 + TILE_CORE // 2, max(0, cx0 - TILE_CORE // 2):cx0 + TILE_CORE // 2] = 1
            part = np.where(core > 0, mask, 0).astype(np.uint8)
            if not part.any():
                continue
            side = min(SIZE, max(h, w))
            x1 = int(np.clip(cx0 - side // 2, 0, max(0, w - side)))
            y1 = int(np.clip(cy0 - side // 2, 0, max(0, h - side)))
            _inpaint_window(img, part, (x1, y1, min(w, x1 + side), min(h, y1 + side)))


def _inpaint_window(img: np.ndarray, mask: np.ndarray, box) -> None:
    if box is None:
        return
    x1, y1, x2, y2 = box
    crop = img[y1:y2, x1:x2]
    inp = resize_to(crop, SIZE, SIZE)
    mk = cv2.resize(mask[y1:y2, x1:x2], (SIZE, SIZE), interpolation=cv2.INTER_NEAREST)
    mk = (cv2.dilate(mk, np.ones((3, 3), np.uint8)) > 0).astype(np.float32)

    image = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    out = get_session().run(None, {"image": image, "mask": mk[None, None]})[0][0]
    out = cv2.cvtColor(np.clip(out.transpose(1, 2, 0), 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
    blend_back(img, box, resize_to(out, x2 - x1, y2 - y1), mask)
