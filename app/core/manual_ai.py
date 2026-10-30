"""Redraw thủ công qua ChatGPT / Gemini (dùng gói Plus/Pro trên web, không cần API).

Tool cắt sẵn một vùng đủ rộng quanh chữ cần xoá (tỉ lệ khung mà ChatGPT/Gemini trả về được nguyên vẹn),
người dùng dán vào ứng dụng chat, rồi dán ảnh kết quả về: tool co về đúng cỡ, căn khớp với ảnh gốc
(AI hay dịch/co giãn nhẹ) và chỉ thay đúng phần chữ.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .clean import build_text_mask
from .cloud import align_to, context_box
from .inpaint_util import blend_back, resize_to

# ChatGPT trả ảnh 1024x1024, 1024x1536, 1536x1024; Gemini giữ được các tỉ lệ này.
RATIOS = {"1:1": 1.0, "2:3": 2 / 3, "3:2": 3 / 2}
FEATHER = 8

PROMPT = (
    "Remove ALL text, lettering, speech marks and sound effects from this manga image and redraw the "
    "background behind them so it continues seamlessly (screentone dots, hatching, gradients, line art). "
    "Keep everything else exactly the same: same framing, same size and aspect ratio, same art style, "
    "no new objects, no added text, no border. Return only the edited image."
)


@dataclass
class Job:
    """Một lần redraw đang chờ kết quả từ AI."""

    page: object
    regions: list
    box: tuple[int, int, int, int]  # vùng cắt gửi đi (toạ độ ảnh)
    mask: np.ndarray  # 0/255 cỡ ảnh: phần sẽ được thay bằng kết quả AI
    crop: np.ndarray  # ảnh BGR đã gửi đi

    @property
    def size(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.box
        return x2 - x1, y2 - y1


def region_mask(page, r) -> np.ndarray:
    """Phần cần vẽ lại của một vùng: vùng lasso, hoặc nét chữ (nới rộng) + cả khung chữ thoại."""
    area = page.area_mask(r)
    if area is not None:
        return cv2.dilate(area, np.ones((5, 5), np.uint8))
    m = build_text_mask(page.text_mask, r.box)
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=2)
    if r.kind == "text":
        h, w = m.shape[:2]
        x1, y1, x2, y2 = r.box
        m[max(0, y1 - 4):min(h, y2 + 4), max(0, x1 - 4):min(w, x2 + 4)] = 255
    return m


def prepare(page, regions) -> Job:
    """Cắt vùng gửi cho AI. Chỗ của các vùng này lấy lại ảnh gốc (bỏ lần xoá trước, nếu có)."""
    mask = np.zeros(page.original.shape[:2], np.uint8)
    for r in regions:
        np.maximum(mask, region_mask(page, r), out=mask)
    box, _ = context_box(mask, RATIOS)
    if box is None:
        raise ValueError("Vùng chọn không có chữ để xoá.")
    src = page.working.copy()
    src[mask > 0] = page.original[mask > 0]
    x1, y1, x2, y2 = box
    return Job(page, list(regions), box, mask, src[y1:y2, x1:x2].copy())


def preview(job: Job) -> np.ndarray:
    """Ảnh xem trước: vùng cắt, viền đỏ quanh phần sẽ được thay."""
    x1, y1, x2, y2 = job.box
    vis = job.crop.copy()
    cs, _ = cv2.findContours(job.mask[y1:y2, x1:x2], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(vis, cs, -1, (0, 0, 255), 2)
    return vis


def aspect_warning(job: Job, result: np.ndarray) -> str | None:
    w, h = job.size
    rh, rw = result.shape[:2]
    diff = abs(np.log((rw / rh) / (w / h)))
    if diff > 0.12:
        return (f"Ảnh AI trả về có tỉ lệ {rw}x{rh}, khác ảnh gửi đi ({w}x{h}): có thể AI đã cắt/đổi khung. "
                "Tool vẫn co về đúng cỡ và căn khớp, nhưng nên kiểm tra kỹ.")
    return None


def apply(job: Job, result: np.ndarray, fit_bubble: bool = True) -> dict:
    """Dán kết quả AI vào trang: co về đúng cỡ, căn khớp với ảnh đã gửi, chỉ thay phần trong mask."""
    if result.ndim == 2:
        result = cv2.cvtColor(result, cv2.COLOR_GRAY2BGR)
    elif result.shape[2] == 4:
        result = cv2.cvtColor(result, cv2.COLOR_BGRA2BGR)
    w, h = job.size
    x1, y1, x2, y2 = job.box
    out = resize_to(result, w, h)
    crop_mask = job.mask[y1:y2, x1:x2]
    aligned = align_to(out, job.crop, crop_mask)
    # Độ lệch còn lại ở phần không bị sửa: để báo nếu AI vẽ khác tranh gốc quá nhiều.
    keep = cv2.dilate(crop_mask, np.ones((15, 15), np.uint8)) == 0
    err = float(np.abs(aligned.astype(np.int16) - job.crop)[keep].mean()) if keep.any() else 0.0
    page = job.page
    page.working[job.mask > 0] = page.original[job.mask > 0]
    blend_back(page.working, job.box, aligned, job.mask, feather=FEATHER)
    for r in job.regions:
        r.cleaned = True
        page.clean(r, fit_bubble)  # đã xoá: chỉ tính lại khung đặt chữ
    return {"mismatch": err, "aligned": aligned is not out}
