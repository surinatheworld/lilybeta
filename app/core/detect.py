"""Phát hiện vùng chữ trên ảnh truyện bằng comic-text-detector (ONNX)."""
from __future__ import annotations

import os
import urllib.request
from dataclasses import dataclass

import cv2
import numpy as np

from .paths import model_path

MODEL_URL = (
    "https://github.com/zyddnys/manga-image-translator/releases/download/"
    "beta-0.3/comictextdetector.pt.onnx"
)
MODEL_PATH = model_path("comictextdetector.pt.onnx")
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
    kind: str = "text"  # "text" = khung chữ model tìm thấy, "sfx" = cụm nét chữ lẻ (SFX, chữ nhỏ)

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
        boxes = grow_to_strokes(split_double_bubbles(_nms(boxes), mask), mask)
        return boxes + find_sfx(mask, boxes), mask

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


def split_double_bubbles(boxes: list[TextBox], mask: np.ndarray) -> list[TextBox]:
    """Tách khối chữ của bóng thoại đôi thành từng phần.

    Gom nét chữ trong khung thành cụm; các cụm chung hàng hoặc chung cột (cùng một bóng)
    được gộp lại. Còn lại từ 2 nhóm nằm chéo nhau thì đó là các nửa của bóng đôi.
    """
    out = []
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    for b in boxes:
        sub = (mask[b.y1:b.y2, b.x1:b.x2] > 0.3).astype(np.uint8)
        n, _, st, _ = cv2.connectedComponentsWithStats(cv2.dilate(sub, k))
        total = st[1:, 4].sum() if n > 1 else 0
        groups = [[int(x), int(y), int(x + w), int(y + h)] for x, y, w, h, a in st[1:] if a >= 0.1 * total]
        merged = True
        while merged and len(groups) > 1:
            merged = False
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    a, c = groups[i], groups[j]
                    if a[0] < c[2] and c[0] < a[2] or a[1] < c[3] and c[1] < a[3]:
                        groups[i] = [min(a[0], c[0]), min(a[1], c[1]), max(a[2], c[2]), max(a[3], c[3])]
                        del groups[j]
                        merged = True
                        break
                if merged:
                    break
        if len(groups) < 2:
            out.append(b)
            continue
        for x1, y1, x2, y2 in groups:
            # Nét đã bị nới 6px khi gom cụm, trả lại phần đó.
            out.append(TextBox(b.x1 + x1 + 4, b.y1 + y1 + 4, b.x1 + x2 - 4, b.y1 + y2 - 4, b.score))
    return out


def grow_to_strokes(boxes: list[TextBox], mask: np.ndarray) -> list[TextBox]:
    """Nới khung cho trùm cả nét chữ dính liền mà model khoanh hụt (vd chữ đầu cột nét mờ).

    Chỉ lấy cụm nét chạm khung gốc, trong phạm vi nửa cạnh ngắn của khung, không lấn sang khung khác.
    """
    h, w = mask.shape[:2]
    out = []
    for b in boxes:
        e = int(max(12, 0.5 * min(b.x2 - b.x1, b.y2 - b.y1)))
        wx1, wy1, wx2, wy2 = max(0, b.x1 - e), max(0, b.y1 - e), min(w, b.x2 + e), min(h, b.y2 + e)
        strokes = (mask[wy1:wy2, wx1:wx2] > 0.2).astype(np.uint8)
        for o in boxes:  # không lấn sang chữ của khung khác
            if o is not b:
                strokes[max(0, o.y1 - wy1):max(0, o.y2 - wy1), max(0, o.x1 - wx1):max(0, o.x2 - wx1)] = 0
        n, lab = cv2.connectedComponents(cv2.dilate(strokes, np.ones((7, 7), np.uint8)))
        inner = lab[b.y1 - wy1:b.y2 - wy1, b.x1 - wx1:b.x2 - wx1]
        keep = np.isin(lab, np.unique(inner[inner > 0])) & (strokes > 0)
        ys, xs = np.nonzero(keep)
        if len(xs):
            out.append(TextBox(min(b.x1, int(xs.min()) + wx1), min(b.y1, int(ys.min()) + wy1),
                               max(b.x2, int(xs.max()) + 1 + wx1), max(b.y2, int(ys.max()) + 1 + wy1), b.score, b.kind))
        else:
            out.append(b)
    return out


def find_sfx(mask: np.ndarray, boxes: list[TextBox]) -> list[TextBox]:
    """Cụm nét chữ model nhìn thấy nhưng không khoanh khung (SFX, chữ nhỏ ngoài bóng).

    Chỉ giữ cụm gồm nhiều nét rời (chữ) và thưa (loại vệt sáng, mảng đặc).
    """
    strong = (mask > 0.6).astype(np.uint8)
    for b in boxes:
        strong[max(0, b.y1 - 10):b.y2 + 10, max(0, b.x1 - 10):b.x2 + 10] = 0
    grown = cv2.dilate(strong, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
    n, lab, st, _ = cv2.connectedComponentsWithStats(grown)
    out = []
    for i in range(1, n):
        x, y, w, h, _ = st[i]
        if max(w, h) < 15:
            continue
        sub = strong[y:y + h, x:x + w] * (lab[y:y + h, x:x + w] == i)
        fill = sub.sum() / (w * h)
        nc, _, cst, _ = cv2.connectedComponentsWithStats(sub)
        parts = sum(1 for j in range(1, nc) if cst[j][4] >= 4)
        if parts >= 2 and fill < 0.2:
            out.append(TextBox(int(x), int(y), int(x + w), int(y + h), 0.5, "sfx"))
    return out


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


def sort_reading_order(boxes: list[TextBox], right_to_left: bool = True, img: np.ndarray | None = None) -> list[TextBox]:
    """Sắp theo thứ tự đọc. Có ảnh thì đọc hết từng khung tranh (panel) trước: các hàng panel từ trên
    xuống, trong hàng thì phải→trái (manga) hoặc trái→phải; trong một panel thì sắp theo hàng chữ."""
    if img is not None and len(boxes) > 1:
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return _xy_cut(gray >= 235, boxes, (0, 0, gray.shape[1], gray.shape[0]), right_to_left, 0)
    return _row_order(boxes, right_to_left)


def _gutters(white: np.ndarray, axis: int, lo: int, hi: int) -> list[int]:
    """Vị trí giữa các dải trắng (khe giữa panel) chạy hết bề ngang/dọc của khối."""
    frac = white.mean(axis=axis)
    is_white = frac >= 0.995  # khe thật giữa panel trắng gần như tuyệt đối
    cuts, start = [], None
    for i, wv in enumerate(np.append(is_white, False)):
        if wv and start is None:
            start = i
        elif not wv and start is not None:
            if i - start >= 4 and start > 0 and i < len(is_white):
                cuts.append(lo + (start + i) // 2)
            start = None
    return [c for c in cuts if lo < c < hi]


def _xy_cut(white, boxes, area, rtl, depth):
    x1, y1, x2, y2 = area
    if len(boxes) <= 1 or depth > 10:
        return _row_order(boxes, rtl)
    sub = white[y1:y2, x1:x2]
    for axis in (1, 0):  # 1: cắt ngang (hàng panel), 0: cắt dọc (panel trong hàng)
        lo, hi = (y1, y2) if axis == 1 else (x1, x2)
        cuts = []
        for c in _gutters(sub, axis, lo, hi):
            # Chỉ cắt ở khe không chạy xuyên qua khung chữ nào; phần không có chữ sẽ bị bỏ đi,
            # nhờ vậy panel không có thoại (vd panel đầu trang) không làm dính các panel còn lại.
            spans = [(b.y1, b.y2) if axis == 1 else (b.x1, b.x2) for b in boxes]
            if not any(s0 + 2 < c < s1 - 2 for s0, s1 in spans):
                cuts.append(c)
        if not cuts:
            continue
        edges = [lo] + cuts + [hi]
        parts = []
        for a, b in zip(edges, edges[1:]):
            mid = [bx for bx in boxes if a <= ((bx.y1 + bx.y2) / 2 if axis == 1 else (bx.x1 + bx.x2) / 2) < b]
            if mid:
                parts.append(((x1, a, x2, b) if axis == 1 else (a, y1, b, y2), mid))
        if len(parts) == 1 and parts[0][0] == area:
            continue
        if axis == 0 and rtl:
            parts.reverse()
        out = []
        for part_area, part in parts:
            out += _xy_cut(white, part, part_area, rtl, depth + 1)
        return out
    return _row_order(boxes, rtl)


def _row_order(boxes: list[TextBox], right_to_left: bool = True) -> list[TextBox]:
    """Trong một panel: từ trên xuống, cùng một hàng thì phải→trái (manga) hoặc trái→phải."""
    rows: list[list[TextBox]] = []
    for b in sorted(boxes, key=lambda b: b.y1):
        cy = (b.y1 + b.y2) / 2
        for row in rows:
            r0 = row[0]
            overlap = min(b.y2, r0.y2) - max(b.y1, r0.y1)
            separate_columns = b.x1 >= r0.x2 or r0.x1 >= b.x2
            # Side-by-side narration and speech often start at different heights.
            # Their centres can miss each other's span while they still share a row.
            staggered_row = separate_columns and overlap >= .3 * min(b.y2 - b.y1, r0.y2 - r0.y1)
            if r0.y1 <= cy <= r0.y2 or b.y1 <= (r0.y1 + r0.y2) / 2 <= b.y2 or staggered_row:
                row.append(b)
                break
        else:
            rows.append([b])
    out = []
    for row in rows:
        out.extend(sorted(row, key=lambda b: b.x1, reverse=right_to_left))
    return out
