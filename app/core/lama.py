"""Vẽ lại nền bằng model AI LaMa (chạy trên máy, không cần mạng sau lần tải đầu)."""
from __future__ import annotations

import os
import threading
import urllib.request

import cv2
import numpy as np

MODEL_URL = "https://huggingface.co/Carve/LaMa-ONNX/resolve/main/lama_fp32.onnx"
MODEL_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "models", "lama_fp32.onnx")
)
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


def inpaint(img: np.ndarray, mask: np.ndarray) -> None:
    """Vẽ lại vùng `mask` (0/255, cỡ ảnh) trên `img` (BGR) tại chỗ."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return
    h, w = img.shape[:2]
    x1, y1, x2, y2 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    # Cắt ô vuông quanh vùng cần vẽ, chừa đủ nền xung quanh để model "nhìn" hoạ tiết.
    side = int(max(x2 - x1, y2 - y1) * 2.2)
    side = min(max(side, 192), max(h, w))
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    sx1 = int(np.clip(cx - side // 2, 0, max(0, w - side)))
    sy1 = int(np.clip(cy - side // 2, 0, max(0, h - side)))
    sx2, sy2 = min(w, sx1 + side), min(h, sy1 + side)

    crop = img[sy1:sy2, sx1:sx2]
    m = mask[sy1:sy2, sx1:sx2]
    ch, cw = crop.shape[:2]
    inp = cv2.resize(crop, (SIZE, SIZE), interpolation=cv2.INTER_AREA if max(ch, cw) > SIZE else cv2.INTER_CUBIC)
    mk = cv2.resize(m, (SIZE, SIZE), interpolation=cv2.INTER_NEAREST)
    mk = (cv2.dilate(mk, np.ones((3, 3), np.uint8)) > 0).astype(np.float32)

    image = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    out = get_session().run(None, {"image": image, "mask": mk[None, None]})[0][0]
    out = cv2.cvtColor(np.clip(out.transpose(1, 2, 0), 0, 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
    out = cv2.resize(out, (cw, ch), interpolation=cv2.INTER_AREA if max(ch, cw) < SIZE else cv2.INTER_CUBIC)

    # Chỉ thay phần trong mask, mép hoà dần để không lộ đường cắt.
    alpha = cv2.GaussianBlur((m > 0).astype(np.float32), (5, 5), 0)
    alpha = np.maximum(alpha, (m > 0).astype(np.float32))[..., None]
    crop[:] = (out * alpha + crop * (1 - alpha)).astype(np.uint8)
