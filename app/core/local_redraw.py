"""Redraw ink on artwork with the local LaMa model."""
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from .clean import opencv_inpaint
from .paths import model_path


SIZE = 512


@lru_cache(maxsize=1)
def _session():
    import onnxruntime as ort

    filename = model_path("lama_fp32.onnx")
    if not Path(filename).is_file():
        raise FileNotFoundError("Thiếu models/lama_fp32.onnx. Chọn cách xoá khác hoặc bổ sung model LaMa.")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = 4
    opts.inter_op_num_threads = 1
    return ort.InferenceSession(filename, sess_options=opts, providers=["CPUExecutionProvider"])


def prepare_local_redraw():
    """Check model availability before the user starts a manual edit."""
    if not Path(model_path("lama_fp32.onnx")).is_file():
        raise FileNotFoundError("Thiếu models/lama_fp32.onnx. Chọn cách xoá khác hoặc bổ sung model LaMa.")
    return redraw_local


def _crop(mask):
    x, y, width, height = cv2.boundingRect(mask)
    if not width or not height:
        return None
    h, w = mask.shape
    side = max(SIZE, width + 56, height + 56)
    cw, ch = min(w, side), min(h, side)
    left = min(max(0, x + (width - cw) // 2), w - cw)
    top = min(max(0, y + (height - ch) // 2), h - ch)
    return top, left, ch, cw, side


def redraw_local(image, mask):
    """Restore textured artwork inside the supplied selection only."""
    crop = _crop(mask)
    if crop is None:
        return
    top, left, height, width, side = crop
    view = image[top:top + height, left:left + width]
    ink = mask[top:top + height, left:left + width] > 0
    if max(height, width) < 20 or ink.sum() < 4:
        opencv_inpaint(image, mask)
        return
    pad = ((0, side - height), (0, side - width))
    photo = np.pad(view[..., ::-1], (*pad, (0, 0)), mode="edge")
    cut = np.pad(ink.astype(np.uint8), pad)
    if side != SIZE:
        photo = cv2.resize(photo, (SIZE, SIZE), interpolation=cv2.INTER_AREA)
        cut = cv2.resize(cut, (SIZE, SIZE), interpolation=cv2.INTER_NEAREST)
    tensor = np.ascontiguousarray(photo.transpose(2, 0, 1)[None], dtype=np.float32) / 255
    selection = np.ascontiguousarray(cut[None, None], dtype=np.float32)
    result = _session().run(["output"], {"image": tensor, "mask": selection})[0]
    result = result[0].transpose(1, 2, 0)[..., ::-1]
    if result.max() <= 1.1:
        result = result * 255
    if side != SIZE:
        result = cv2.resize(result, (side, side), interpolation=cv2.INTER_CUBIC)
    view[ink] = np.clip(result[:height, :width][ink], 0, 255).astype(np.uint8)
