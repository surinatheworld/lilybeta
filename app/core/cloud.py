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

from .inpaint_util import blend_back, resize_to

GEMINI_DEFAULT_MODEL = "gemini-nano-banana-2.1"
OPENAI_DEFAULT_MODEL = "gpt-image-2.5-sunburst"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
OPENAI_URL = "https://api.openai.com/v1"
TIMEOUT = 180

# Tỉ lệ khung Gemini nhận (rộng/cao); ô cắt được nới cho khớp tỉ lệ gần nhất để ảnh trả về không bị méo.
GEMINI_RATIOS = {"1:1": 1, "2:3": 2 / 3, "3:2": 3 / 2, "3:4": 3 / 4, "4:3": 4 / 3, "4:5": 4 / 5, "5:4": 5 / 4,
                 "9:16": 9 / 16, "16:9": 16 / 9, "21:9": 21 / 9, "1:4": 1 / 4, "4:1": 4}
MIN_CONTEXT = 768  # cạnh ngắn tối thiểu của vùng gửi đi (px ảnh gốc): đủ rộng để AI hiểu tranh xung quanh
FEATHER = 8  # px: mép hoà dần khi dán kết quả AI vào ảnh

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
def gemini_edit(crop: np.ndarray, api_key: str, model: str = GEMINI_DEFAULT_MODEL,
                ratio: str = "1:1") -> np.ndarray:
    """Gửi ô ảnh cho Gemini, nhận ảnh đã xoá chữ (Gemini không nhận mask, chỉ nhận lời dặn)."""
    image_config = {"aspectRatio": ratio}
    if max(crop.shape[:2]) > 1024:
        image_config["imageSize"] = "2K"  # vùng lớn: xin ảnh 2K để không mất nét khi dán lại
    body = {
        "contents": [{"parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": "image/png", "data": _png_b64(crop)}},
        ]}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": image_config},
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


def _openai_size(w: int, h: int, model: str) -> tuple[int, int]:
    """gpt-image-2 / 2.5 nhận cỡ tuỳ ý (bội số 16, tỉ lệ 1:3–3:1); model cũ chỉ nhận 3 cỡ chuẩn."""
    if model.startswith("gpt-image-1") or model.startswith("chatgpt"):
        r = w / h
        return (1536, 1024) if r > 1.25 else (1024, 1536) if r < 0.8 else (1024, 1024)
    scale = 1536 / max(w, h)
    return max(16, round(w * scale / 16) * 16), max(16, round(h * scale / 16) * 16)


def openai_edit(crop: np.ndarray, crop_mask: np.ndarray, api_key: str,
                model: str = OPENAI_DEFAULT_MODEL) -> np.ndarray:
    """OpenAI nhận mask: điểm trong suốt (alpha = 0) là chỗ được vẽ lại."""
    sw, sh = _openai_size(crop.shape[1], crop.shape[0], model)
    size = f"{sw}x{sh}"
    img = resize_to(crop, sw, sh)
    m = cv2.resize(crop_mask, (sw, sh), interpolation=cv2.INTER_NEAREST)
    rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    mask_png = rgba.copy()
    mask_png[..., 3] = np.where(m > 0, 0, 255).astype(np.uint8)
    body, ctype = _multipart(
        {"model": model, "prompt": OPENAI_PROMPT, "size": size, "quality": "high", "n": "1"},
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
def context_box(mask: np.ndarray, ratios: dict[str, float] | None = None):
    """Vùng gửi cho AI: quanh vùng cần xoá, chừa rộng (≥ MIN_CONTEXT px hoặc gấp ~3 lần chữ) để AI
    thấy đủ tranh xung quanh; nới theo tỉ lệ khung gần nhất trong `ratios`. Trả về (box, tên tỉ lệ)."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None, None
    h, w = mask.shape[:2]
    x1, y1, x2, y2 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
    cw = max(MIN_CONTEXT, int((x2 - x1) * 3))
    ch = max(MIN_CONTEXT, int((y2 - y1) * 3))
    name = None
    if ratios:
        name = min(ratios, key=lambda k: abs(np.log(ratios[k] * ch / cw)))
        r = ratios[name]
        if cw / ch < r:
            cw = int(ch * r)
        else:
            ch = int(cw / r)
    cw, ch = min(cw, w), min(ch, h)
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    bx1 = int(np.clip(cx - cw // 2, 0, w - cw))
    by1 = int(np.clip(cy - ch // 2, 0, h - ch))
    box = (bx1, by1, bx1 + cw, by1 + ch)
    if ratios:  # ảnh nhỏ hơn vùng mong muốn: chọn lại tỉ lệ theo vùng thực tế
        name = min(ratios, key=lambda k: abs(np.log(ratios[k] * ch / cw)))
    return box, name


def align_to(out: np.ndarray, ref: np.ndarray, edit_mask: np.ndarray) -> np.ndarray:
    """Căn ảnh AI trả về cho khớp ảnh gốc (AI hay dịch/co giãn nhẹ), chỉ so phần không bị sửa.

    Không căn được (ảnh khác quá nhiều) thì giữ nguyên.
    """
    h, w = ref.shape[:2]
    scale = min(1.0, 640 / max(h, w))
    small = (max(8, int(w * scale)), max(8, int(h * scale)))
    a = cv2.resize(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY), small).astype(np.float32)
    b = cv2.resize(cv2.cvtColor(out, cv2.COLOR_BGR2GRAY), small).astype(np.float32)
    keep = cv2.resize((edit_mask == 0).astype(np.uint8), small, interpolation=cv2.INTER_NEAREST)
    keep = cv2.erode(keep, np.ones((5, 5), np.uint8))
    if keep.sum() < 0.2 * keep.size:
        return out
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        cc, warp = cv2.findTransformECC(a, b, warp, cv2.MOTION_AFFINE,
                                        (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), keep, 5)
    except cv2.error:
        return out
    lin = warp[:, :2]
    if cc < 0.6 or np.abs(lin - np.eye(2)).max() > 0.08 or np.abs(warp[:, 2]).max() > 0.08 * max(small):
        return out
    warp[:, 2] /= scale  # dịch chuyển đo ở ảnh thu nhỏ → đổi về cỡ thật
    return cv2.warpAffine(out, warp, (w, h), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REPLICATE)


def make_inpainter(provider: str, api_key: str, model: str):
    """Trả về hàm inpaint(img, mask) tại chỗ dùng AI trên mạng.

    Hàm có thuộc tính `whole_box` (dán lại cả khung chữ, không chỉ nét) và `context_box`
    (vùng gửi đi) để bước xoá chữ gom các vùng gần nhau vào một lần gọi.
    """
    if not api_key:
        raise CloudError("Chưa nhập API key.")
    ratios = GEMINI_RATIOS if provider == "gemini" else {"1:3": 1 / 3, "1:1": 1, "3:1": 3}

    def ctx(mask):
        return context_box(mask, ratios)

    def inpaint(img: np.ndarray, mask: np.ndarray) -> None:
        box, ratio = ctx(mask)
        if box is None:
            return
        x1, y1, x2, y2 = box
        crop = img[y1:y2, x1:x2].copy()
        crop_mask = mask[y1:y2, x1:x2]
        if provider == "openai":
            out = openai_edit(crop, crop_mask, api_key, model or OPENAI_DEFAULT_MODEL)
        else:
            out = gemini_edit(crop, api_key, model or GEMINI_DEFAULT_MODEL, ratio)
        out = align_to(resize_to(out, x2 - x1, y2 - y1), crop, crop_mask)
        blend_back(img, box, out, mask, feather=FEATHER)

    inpaint.whole_box = True
    inpaint.context_box = lambda mask: ctx(mask)[0]
    return inpaint
