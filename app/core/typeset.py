"""Dàn chữ bản dịch vào một khung bằng font hệ thống (cần QGuiApplication đã khởi tạo)."""
from __future__ import annotations

from dataclasses import asdict, astuple, dataclass
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

MIN_SIZE = 8
MAX_SIZE = 150


@dataclass
class TextStyle:
    family: str = "Arial"
    size: int = 0  # 0 = tự co cho vừa khung
    bold: bool = False
    italic: bool = False
    uppercase: bool = False
    color: str = "#000000"
    stroke_color: str = "#ffffff"
    stroke_width: float = 0.0
    line_spacing: float = 1.0
    padding: float = 0.06  # chừa lề theo tỉ lệ cạnh ngắn của khung

    def copy(self) -> "TextStyle":
        return TextStyle(**asdict(self))


def make_font(style: TextStyle, size: int) -> QFont:
    f = QFont(style.family)
    f.setPixelSize(max(1, size))
    f.setBold(style.bold)
    f.setItalic(style.italic)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def missing_chars(style: TextStyle, text: str) -> str:
    """Các ký tự font không có (vd font thiếu dấu tiếng Việt)."""
    fm = QFontMetricsF(make_font(style, 32))
    seen = []
    for ch in text:
        if not ch.isspace() and ch not in seen and not fm.inFontUcs4(ord(ch)):
            seen.append(ch)
    return "".join(seen)


def wrap(text: str, fm: QFontMetricsF, max_w: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            cand = f"{cur} {word}" if cur else word
            if fm.horizontalAdvance(cand) <= max_w:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            # Từ đơn dài hơn cả khung thì cắt theo ký tự.
            cur = ""
            for ch in word:
                if cur and fm.horizontalAdvance(cur + ch) > max_w:
                    lines.append(cur)
                    cur = ""
                cur += ch
        lines.append(cur)
    return lines


def prepare(text: str, style: TextStyle) -> str:
    text = text.replace("|", "\n").replace("\\n", "\n").strip()
    return text.upper() if style.uppercase else text


def layout(text: str, style: TextStyle, w: float, h: float) -> tuple[QFont, list[str], float]:
    """Chọn cỡ chữ lớn nhất vừa khung. Trả về (font, các dòng, chiều cao dòng)."""
    _, size, lines, lh = _layout(text, astuple(style), int(w), int(h))
    return make_font(style, size), list(lines), lh


@lru_cache(maxsize=20000)
def _layout(text: str, style_key: tuple, w: int, h: int) -> tuple[bool, int, tuple[str, ...], float]:
    """(vừa khung?, cỡ chữ, các dòng, chiều cao dòng) – có cache vì được gọi rất nhiều lần."""
    style = TextStyle(*style_key)
    text = prepare(text, style)
    pad = min(w, h) * style.padding
    iw, ih = max(1.0, w - 2 * pad), max(1.0, h - 2 * pad)

    def fit(size: int):
        fm = QFontMetricsF(make_font(style, size))
        lines = wrap(text, fm, iw)
        lh = fm.height() * style.line_spacing
        widest = max((fm.horizontalAdvance(l) for l in lines), default=0)
        # Không cắt đôi một từ (vd "Chu|yện"): từ dài nhất phải vừa một dòng.
        longest_word = max((fm.horizontalAdvance(wd) for wd in text.split()), default=0)
        ok = len(lines) * lh <= ih and widest <= iw + 0.5 and longest_word <= iw + 0.5
        return ok, size, tuple(lines), lh

    if style.size > 0:
        return fit(style.size)

    lo, hi = MIN_SIZE, max(MIN_SIZE, min(MAX_SIZE, int(ih)))
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        res = fit(mid)
        if res[0]:
            best, lo = res, mid + 1
        else:
            hi = mid - 1
    return best or fit(MIN_SIZE)


def best_rect(text: str, style: TextStyle, rects: list[tuple[int, int, int, int]]):
    """Trong các khung ứng viên, chọn khung đặt được chữ to nhất (cùng cỡ thì khung rộng hơn)."""
    key = astuple(style)
    t = prepare(text, style)

    def score(r):
        x1, y1, x2, y2 = r
        ok, size, _, _ = _layout(t, key, x2 - x1, y2 - y1)
        return (ok, size, (x2 - x1) * (y2 - y1))

    return max(rects, key=score)


def draw_text(p: QPainter, text: str, style: TextStyle, rect: QRectF) -> None:
    """Vẽ chữ căn giữa (cả ngang lẫn dọc) trong `rect`, có viền nếu stroke_width > 0."""
    if not text.strip():
        return
    font, lines, lh = layout(text, style, rect.width(), rect.height())
    fm = QFontMetricsF(font)
    path = QPainterPath()
    top = rect.center().y() - len(lines) * lh / 2
    for i, line in enumerate(lines):
        x = rect.center().x() - fm.horizontalAdvance(line) / 2
        baseline = top + i * lh + (lh - fm.height()) / 2 + fm.ascent()
        path.addText(QPointF(x, baseline), font, line)

    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    if style.stroke_width > 0:
        pen = QPen(QColor(style.stroke_color), style.stroke_width * 2)
        pen.setJoinStyle(Qt.RoundJoin)
        p.strokePath(path, pen)
    p.fillPath(path, QBrush(QColor(style.color)))
    p.restore()
