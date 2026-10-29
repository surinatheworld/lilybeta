"""Vẽ lại nền bằng AI trên mạng (Google Gemini, OpenAI) qua API key của người dùng.

Chỉ gửi một ô vuông nhỏ quanh vùng cần xoá (không gửi cả trang), rồi chỉ dán lại phần trong mask,
nên phần còn lại của ảnh giữ nguyên từng điểm ảnh.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
import uuid

import cv2
import numpy as np

from .inpaint_util import blend_back, resize_to, square_around

GEMINI_DEFAULT_MODEL = "gemini-nano-banana-2.1"
OPENAI_DEFAULT_MODEL = "gpt-image-2.5-sunburst"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_URL = "https://api.openai.com/v1"
TIMEOUT = 180

PROMPT = (
    "This is a crop of a black-and-white manga page. Remove ALL text, lettering, sound effects and "
    "speech marks from the image and redraw the background behind them so it continues seamlessly "
    "(screentone, hatching, lines, art). Keep everything else exactly the same: same framing, same "
    "size, same style, no new objects, no added text."
)
OPENAI_PROMPT = (
    "Remove the text, lettering and sound effects in the masked area of this manga crop and redraw "
    "the background so it continues seamlessly (screentone, hatching, line art). Do not add anything."
)


class CloudError(Exception):
    pass


def _png_b64(img: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf.tobytes()).decode()


def _decode(b64: str) -> np.ndarray:
    out = cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)
    if out is None:
        raise CloudError("AI trả về ảnh không đọc được.")
    return out


def _send(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except ValueError:
            msg = body
        raise CloudError(f"Lỗi API ({e.code}): {msg[:400]}") from None
    except urllib.error.URLError as e:
        raise CloudError(f"Không kết nối được: {e.reason}") from None


# --- Gemini --------------------------------------------------------------------
def gemini_edit(crop: np.ndarray, api_key: str, model: str = GEMINI_DEFAULT_MODEL) -> np.ndarray:
    """Gửi ô ảnh vuông cho Gemini, nhận ảnh đã xoá chữ (Gemini không nhận mask, chỉ nhận lời dặn)."""
    body = {
        "contents": [{"parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": "image/png", "data": _png_b64(crop)}},
        ]}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "1:1"}},
    }
    req = urllib.request.Request(
        f"{GEMINI_URL}/models/{model}:generateContent",
        data=json.dumps(body).encode(),
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
    )
    data = _send(req)
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                return _decode(inline["data"])
    reason = (data.get("candidates") or [{}])[0].get("finishReason") or data.get("promptFeedback", {})
    raise CloudError(f"Gemini không trả về ảnh ({reason}).")


def gemini_check(api_key: str, model: str = GEMINI_DEFAULT_MODEL) -> str:
    req = urllib.request.Request(f"{GEMINI_URL}/models/{model}", headers={"x-goog-api-key": api_key})
    info = _send(req)
    return info.get("displayName") or model


# --- OpenAI --------------------------------------------------------------------
def _multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes]]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (fname, data) in files.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\n"
                f"Content-Type: image/png\r\n\r\n").encode() + data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def openai_edit(crop: np.ndarray, crop_mask: np.ndarray, api_key: str,
                model: str = OPENAI_DEFAULT_MODEL) -> np.ndarray:
    """OpenAI nhận mask: điểm trong suốt (alpha = 0) là chỗ được vẽ lại."""
    size = 1024
    img = resize_to(crop, size, size)
    m = cv2.resize(crop_mask, (size, size), interpolation=cv2.INTER_NEAREST)
    rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    mask_png = rgba.copy()
    mask_png[..., 3] = np.where(m > 0, 0, 255).astype(np.uint8)
    body, ctype = _multipart(
        {"model": model, "prompt": OPENAI_PROMPT, "size": f"{size}x{size}", "quality": "high", "n": "1"},
        {"image[]": ("image.png", cv2.imencode(".png", rgba)[1].tobytes()),
         "mask": ("mask.png", cv2.imencode(".png", mask_png)[1].tobytes())},
    )
    req = urllib.request.Request(
        f"{OPENAI_URL}/images/edits", data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": ctype},
    )
    data = _send(req)
    try:
        return _decode(data["data"][0]["b64_json"])
    except (KeyError, IndexError):
        raise CloudError("OpenAI không trả về ảnh.") from None


def openai_check(api_key: str, model: str = OPENAI_DEFAULT_MODEL) -> str:
    req = urllib.request.Request(f"{OPENAI_URL}/models/{model}", headers={"Authorization": f"Bearer {api_key}"})
    return _send(req).get("id", model)


# --- dùng trong bước xoá chữ ---------------------------------------------------------
def make_inpainter(provider: str, api_key: str, model: str):
    """Trả về hàm inpaint(img, mask) tại chỗ dùng AI trên mạng."""
    if not api_key:
        raise CloudError("Chưa nhập API key.")

    def inpaint(img: np.ndarray, mask: np.ndarray) -> None:
        # Gemini xử lý ảnh vuông 1K: ô cắt tối thiểu 384px để đủ nét, nhiều nền xung quanh.
        box = square_around(mask, min_side=384, context=2.5)
        if box is None:
            return
        x1, y1, x2, y2 = box
        crop = img[y1:y2, x1:x2].copy()
        if provider == "openai":
            out = openai_edit(crop, mask[y1:y2, x1:x2], api_key, model or OPENAI_DEFAULT_MODEL)
        else:
            out = gemini_edit(crop, api_key, model or GEMINI_DEFAULT_MODEL)
        blend_back(img, box, resize_to(out, x2 - x1, y2 - y1), mask)

    return inpaint
