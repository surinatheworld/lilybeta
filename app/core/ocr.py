"""Đọc chữ Nhật trong khung chữ (manga-ocr bản ONNX, models/manga-ocr) để khớp vùng chữ với cột câu gốc của script.

Chạy trên card đồ hoạ (DirectML) nếu có, không thì CPU. Không có model thì `reader()` trả về None và tool ghép câu
theo độ dài / thứ tự như trước.
"""
from __future__ import annotations

import os
import re
import unicodedata
from difflib import SequenceMatcher

import cv2
import numpy as np

from .paths import model_path

MODEL_DIR = model_path("manga-ocr")
FILES = ("encoder_model.onnx", "decoder_model.onnx", "vocab.txt")
SIZE = 224
START, END, SPECIAL = 2, 3, 5  # [CLS] bắt đầu, [SEP] kết thúc; 0–4 là token đặc biệt
MAX_TOKENS = 120
PAD = 0.06  # nới khung chữ 6% mỗi bên: model đọc tốt hơn khi không cắt sát nét


class MangaOCR:
    def __init__(self, folder: str = MODEL_DIR):
        self.folder = folder
        with open(os.path.join(folder, "vocab.txt"), encoding="utf-8") as f:
            self.vocab = [line.rstrip("\n") for line in f]
        self._enc = self._dec = None

    @staticmethod
    def available(folder: str = MODEL_DIR) -> bool:
        return all(os.path.exists(os.path.join(folder, f)) for f in FILES)

    def _sessions(self):
        if self._enc is None:
            from .detect import gpu_session

            paths = [os.path.join(self.folder, f) for f in FILES[:2]]
            self._enc, self._dec = (gpu_session(p) or _cpu_session(p) for p in paths)
        return self._enc, self._dec

    def release(self) -> None:
        self._enc = self._dec = None

    def read(self, img_bgr: np.ndarray, box) -> str:
        """Chữ trong khung `box` (x1, y1, x2, y2) của ảnh."""
        h, w = img_bgr.shape[:2]
        x1, y1, x2, y2 = box
        px, py = int((x2 - x1) * PAD) + 2, int((y2 - y1) * PAD) + 2
        crop = img_bgr[max(0, y1 - py):min(h, y2 + py), max(0, x1 - px):min(w, x2 + px)]
        if crop.size == 0:
            return ""
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        x = cv2.resize(cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB), (SIZE, SIZE), interpolation=cv2.INTER_LINEAR)
        x = ((x.astype(np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)[None]
        enc, dec = self._sessions()
        hidden = enc.run(None, {"pixel_values": x})[0]
        ids = [START]
        for _ in range(MAX_TOKENS):
            logits = dec.run(None, {"input_ids": np.array([ids], np.int64), "encoder_hidden_states": hidden})[0]
            nxt = int(logits[0, -1].argmax())
            if nxt == END:
                break
            ids.append(nxt)
        return "".join(self.vocab[i].replace("##", "") for i in ids[1:] if i >= SPECIAL)


def _cpu_session(path: str):
    import onnxruntime as ort

    return ort.InferenceSession(path, providers=["CPUExecutionProvider"])


_READER: MangaOCR | None = None


def reader() -> MangaOCR | None:
    """Bộ đọc chữ dùng chung (nạp model một lần); None nếu chưa có model hoặc thiếu onnxruntime."""
    global _READER
    if _READER is None and MangaOCR.available():
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            return None
        _READER = MangaOCR()
    return _READER


_DROP = re.compile(r"[\s\.\,!?！？。、…‥・ー―－\-~〜～「」『』（）()\[\]【】♡♥♪☆★*＊]")


_SMALL = str.maketrans("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ", "あいうえおつやゆよわアイウエオツヤユヨワカケ")


def normalize(text: str) -> str:
    """Bỏ dấu câu, khoảng trắng; NFKC (ｶ → カ, ！ → !); kana nhỏ = kana lớn (OCR hay lẫn ぉ / お) – để so chữ đọc được
    với câu gốc trong script."""
    return _DROP.sub("", unicodedata.normalize("NFKC", text or "")).translate(_SMALL)


def coverage(source: str, read: str) -> float:
    """Phần câu gốc `source` (tiếng Nhật trong script) có mặt trong chữ đọc được `read`, 0..1."""
    a, b = normalize(source), normalize(read)
    if not a or not b:
        return 0.0
    m = SequenceMatcher(None, a, b, autojunk=False)
    # Chữ trùng lẻ tẻ ("よっす" với "とっても美味しいです": っ, す) chỉ tính khi vùng gần như chỉ có câu này.
    single = len(b) <= 2 * len(a)
    return sum(blk.size for blk in m.get_matching_blocks() if blk.size >= 2 or single) / len(a)
