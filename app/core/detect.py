"""Phát hiện vùng chữ trên ảnh truyện bằng comic-text-detector (ONNX)."""
from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass

import cv2
import numpy as np

MODEL_URL = (
    "https://github.com/zyddnys/manga-image-translator/releases/download/"
    "beta-0.3/comictextdetector.pt.onnx"
)
MODEL_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "models", "comictextdetector.pt.onnx")
)
INPUT_SIZE = 1024
CONF_THRESH = 0.4
NMS_THRESH = 0.35


@dataclass
class TextBox:
    x1: int
    y1: int
    x2: int
    y2: int
    score: float

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)


def ensure_model(path: str = MODEL_PATH) -> str:
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        urllib.request.urlretrieve(MODEL_URL, tmp)
        os.replace(tmp, path)
    return path


class TextDetector:
    def __init__(self, model_path: str = MODEL_PATH):
        ensure_model(model_path)
        # OpenCV DNN chạy model này nhanh hơn onnxruntime rất nhiều trên CPU (~1-2s/ảnh).
        self.net = cv2.dnn.readNetFromONNX(model_path)

    def detect(self, img_bgr: np.ndarray) -> tuple[list[TextBox], np.ndarray]:
        """Trả về (danh sách khung chữ, mask nét chữ float32 0..1 cùng cỡ ảnh)."""
        h, w = img_bgr.shape[:2]
        mask = np.zeros((h, w), np.float32)
        boxes: list[TextBox] = []
        # Ảnh dài (manhwa) được cắt thành nhiều ô gần vuông, chồng lấn nhau.
        for y0, y1 in _tiles(h, w):
            tile_boxes, tile_mask = self._infer(img_bgr[y0:y1])
            np.maximum(mask[y0:y1], tile_mask, out=mask[y0:y1])
            for b in tile_boxes:
                boxes.append(TextBox(b.x1, b.y1 + y0, b.x2, b.y2 + y0, b.score))
        return _nms(boxes), mask

    def _infer(self, img: np.ndarray) -> tuple[list[TextBox], np.ndarray]:
        h, w = img.shape[:2]
        r = INPUT_SIZE / max(h, w)
        nh, nw = min(INPUT_SIZE, round(h * r)), min(INPUT_SIZE, round(w * r))
        canvas = np.full((INPUT_SIZE, INPUT_SIZE, 3), 114, np.uint8)
        canvas[:nh, :nw] = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0, (INPUT_SIZE, INPUT_SIZE), swapRB=True)
        self.net.setInput(blob)
        blk, seg, _ = self.net.forward(["blk", "seg", "det"])

        pred = blk[0]  # (N, 5 + số lớp): cx, cy, w, h, obj, cls...
        scores = pred[:, 4] * pred[:, 5:].max(axis=1)
        pred, scores = pred[scores > CONF_THRESH], scores[scores > CONF_THRESH]
        boxes = []
        for (cx, cy, bw, bh), s in zip(pred[:, :4], scores):
            x1 = int(np.clip((cx - bw / 2) / r, 0, w))
            y1 = int(np.clip((cy - bh / 2) / r, 0, h))
            x2 = int(np.clip((cx + bw / 2) / r, 0, w))
            y2 = int(np.clip((cy + bh / 2) / r, 0, h))
            if x2 - x1 > 4 and y2 - y1 > 4:
                boxes.append(TextBox(x1, y1, x2, y2, float(s)))

        m = seg[0, 0, :nh, :nw].astype(np.float32)
        if m.max() > 1.0 or m.min() < 0.0:
            m = 1.0 / (1.0 + np.exp(-m))
        return _nms(boxes), cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)


def _tiles(h: int, w: int) -> list[tuple[int, int]]:
    tile_h = int(w * 1.4)
    if h <= tile_h * 1.15:
        return [(0, h)]
    overlap = tile_h // 4
    out, y = [], 0
    while True:
        end = min(h, y + tile_h)
        out.append((y, end))
        if end >= h:
            return out
        y = end - overlap


def _nms(boxes: list[TextBox]) -> list[TextBox]:
    if not boxes:
        return []
    rects = [[b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1] for b in boxes]
    keep = cv2.dnn.NMSBoxes(rects, [b.score for b in boxes], CONF_THRESH, NMS_THRESH)
    kept = [boxes[i] for i in np.array(keep).flatten()]
    # Bỏ khung nằm gần như trọn trong khung lớn hơn (hay gặp ở chỗ chồng lấn giữa các ô).
    kept.sort(key=lambda b: b.area, reverse=True)
    result: list[TextBox] = []
    for b in kept:
        if not any(_inside_ratio(b, k) > 0.8 for k in result):
            result.append(b)
    return result


def _inside_ratio(a: TextBox, b: TextBox) -> float:
    ix = max(0, min(a.x2, b.x2) - max(a.x1, b.x1))
    iy = max(0, min(a.y2, b.y2) - max(a.y1, b.y1))
    return ix * iy / max(1, a.area)


def sort_reading_order(boxes: list[TextBox], right_to_left: bool = True) -> list[TextBox]:
    """Sắp theo thứ tự đọc: từ trên xuống, trong cùng một hàng thì phải→trái (manga) hoặc trái→phải."""
    rows: list[list[TextBox]] = []
    for b in sorted(boxes, key=lambda b: b.y1):
        cy = (b.y1 + b.y2) / 2
        for row in rows:
            r0 = row[0]
            if r0.y1 <= cy <= r0.y2 or b.y1 <= (r0.y1 + r0.y2) / 2 <= b.y2:
                row.append(b)
                break
        else:
            rows.append([b])
    out = []
    for row in rows:
        out.extend(sorted(row, key=lambda b: b.x1, reverse=right_to_left))
    return out
