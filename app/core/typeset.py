"""Dàn chữ bản dịch vào một khung bằng font hệ thống (cần QGuiApplication đã khởi tạo).

Các thông số chữ theo kiểu bảng Character / Paragraph của Photoshop. Đơn vị là px của ảnh
(tương đương pt ở 72 ppi).
"""
from __future__ import annotations

from dataclasses import asdict, astuple, dataclass
import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QTransform,
)

from .fonts import qt_family

MIN_SIZE = 8
MAX_SIZE = 150
SCRIPT_SIZE = 0.583  # cỡ chỉ số trên/dưới so với cỡ chữ (mặc định Photoshop 58.3%)
SCRIPT_POS = 0.333  # độ nâng/hạ chỉ số trên/dưới (mặc định Photoshop 33.3%)


@dataclass
class TextStyle:
    family: str = "Arial"
    size: int = 0  # 0 = tự co cho vừa khung
    bold: bool = False  # giả đậm (Faux Bold)
    italic: bool = False  # giả nghiêng (Faux Italic)
    uppercase: bool = False  # IN HOA (All Caps)
    color: str = "#000000"
    stroke_color: str = "#ffffff"
    stroke_width: float = 0.0
    line_spacing: float = 1.0  # hệ số khoảng cách dòng khi leading = Tự động
    padding: float = 0.04  # chừa lề theo tỉ lệ cạnh ngắn của khung
    max_size: float = 0.0  # cỡ tối đa khi tự co (0 = không giới hạn); trang tự đặt theo bề ngang ảnh
    align: str = "center"  # "left" | "center" | "right"
    rotation: float = 0.0  # độ, xoay quanh tâm khung chữ
    # --- Character ---
    style_name: str = ""  # kiểu mặt chữ của font (Regular, Light, Bold…); "" = mặc định
    leading: float = 0.0  # khoảng cách giữa các dòng (px); 0 = Tự động
    tracking: int = 0  # giãn đều các ký tự, đơn vị 1/1000 em
    kerning: str = "metrics"  # "metrics" (theo font) | "none" (0)
    vscale: float = 100.0  # co giãn dọc (%)
    hscale: float = 100.0  # co giãn ngang (%)
    baseline_shift: float = 0.0  # dịch đường cơ sở (px, dương = lên)
    small_caps: bool = False
    superscript: bool = False
    subscript: bool = False
    underline: bool = False
    strike: bool = False
    antialias: str = "smooth"  # "none" | "sharp" | "crisp" | "strong" | "smooth"
    # --- Paragraph ---
    justify: str = ""  # "" = không dàn đều; dàn đều với dòng cuối "left" | "center" | "right" | "all"
    indent_left: float = 0.0
    indent_right: float = 0.0
    indent_first: float = 0.0
    space_before: float = 0.0
    space_after: float = 0.0
    hyphenate: bool = False  # ngắt từ quá dài kèm dấu gạch nối
    # --- Hiệu ứng: bóng đổ (Drop Shadow) ---
    shadow: bool = False
    shadow_color: str = "#000000"
    shadow_opacity: float = 75.0  # %
    shadow_angle: float = 120.0  # độ, hướng ánh sáng như Photoshop (bóng đổ về phía ngược lại)
    shadow_distance: float = 5.0  # px
    shadow_size: float = 5.0  # px, độ nhoè

    def copy(self) -> "TextStyle":
        return TextStyle(**asdict(self))


def _is_script(style: TextStyle) -> bool:
    return style.superscript or style.subscript


def make_font(style: TextStyle, size: float) -> QFont:
    px = max(1, round(size * (SCRIPT_SIZE if _is_script(style) else 1)))
    family = qt_family(style.family)
    f = QFontDatabase.font(family, style.style_name, 12) if style.style_name else QFont(family)
    f.setPixelSize(px)
    if style.bold:
        f.setBold(True)
    if style.italic:
        f.setItalic(True)
    if style.small_caps:
        f.setCapitalization(QFont.SmallCaps)
    if style.tracking:
        f.setLetterSpacing(QFont.AbsoluteSpacing, px * style.tracking / 1000)
    f.setKerning(style.kerning != "none")
    f.setUnderline(False)  # gạch chân/ngang tự vẽ để có cả viền
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


def prepare(text: str, style: TextStyle) -> str:
    text = text.replace("|", "\n").replace("\\n", "\n").strip()
    return text.upper() if style.uppercase else text


class _Metrics:
    """Đo chữ có tính co giãn ngang/dọc và khoảng cách dòng."""

    def __init__(self, style: TextStyle, size: float):
        self.font = make_font(style, size)
        self.fm = QFontMetricsF(self.font)
        self.hs, self.vs = style.hscale / 100, style.vscale / 100
        self.size = size
        # Khoảng cách dòng tự động như Photoshop: 120% cỡ chữ (đủ chỗ cho dấu tiếng Việt chồng tầng).
        auto = 1.2 * size * style.line_spacing * self.vs
        self.lh = style.leading if style.leading > 0 else auto

    def adv(self, s: str) -> float:
        return self.fm.horizontalAdvance(s) * self.hs


def wrap(text: str, m: _Metrics, max_w: float, first_w: float, hyphenate: bool = False):
    """Xuống dòng theo từ. Trả về [(dòng, đầu đoạn?, cuối đoạn?)]."""
    out: list[tuple[str, bool, bool]] = []
    for para in text.split("\n"):
        lines: list[str] = []
        cur = ""

        def width():
            return first_w if not lines else max_w

        for word in para.split():
            cand = f"{cur} {word}" if cur else word
            if m.adv(cand) <= width() + 0.5:
                cur = cand
                continue
            if cur:
                lines.append(cur)
                cur = ""
            if m.adv(word) <= width() + 0.5:
                cur = word
                continue
            # Từ dài hơn cả dòng thì cắt theo ký tự (có gạch nối nếu bật).
            dash = "-" if hyphenate else ""
            for ch in word:
                if cur and m.adv(cur + ch + dash) > width():
                    lines.append(cur + dash)
                    cur = ""
                cur += ch
        lines.append(cur)
        out += [(ln, i == 0, i == len(lines) - 1) for i, ln in enumerate(lines)]
    return out


def _inner(style: TextStyle, w: float, h: float):
    pad = min(w, h) * style.padding
    iw = max(1.0, w - 2 * pad - style.indent_left - style.indent_right)
    return pad, iw, max(1.0, h - 2 * pad)


def _block_height(lines, m: _Metrics, style: TextStyle) -> float:
    paras = sum(1 for _, start, _ in lines if start)
    return len(lines) * m.lh + max(0, paras - 1) * (style.space_before + style.space_after)


def layout(text: str, style: TextStyle, w: float, h: float):
    """Chọn cỡ chữ lớn nhất vừa khung. Trả về (_Metrics, các dòng)."""
    _, size, lines = _layout(text, astuple(style), int(w), int(h))
    return _Metrics(style, size), list(lines)


@lru_cache(maxsize=20000)
def _layout(text: str, style_key: tuple, w: int, h: int):
    """(vừa khung?, cỡ chữ, các dòng) – có cache vì được gọi rất nhiều lần."""
    style = TextStyle(*style_key)
    text = prepare(text, style)
    _, iw, ih = _inner(style, w, h)
    first_w = max(1.0, iw - style.indent_first)

    def fit(size: int):
        m = _Metrics(style, size)
        lines = wrap(text, m, iw, first_w, style.hyphenate)
        widest = max((m.adv(ln) for ln, _, _ in lines), default=0)
        # Không cắt đôi một từ (vd "Chu|yện"): từ dài nhất phải vừa một dòng.
        longest_word = max((m.adv(wd) for wd in text.split()), default=0)
        ok = _block_height(lines, m, style) <= ih and widest <= iw + 0.5 and longest_word <= first_w + 0.5
        return ok, size, tuple(lines)

    if style.size > 0:
        return fit(style.size)

    cap = int(style.max_size) if style.max_size > 0 else MAX_SIZE
    lo, hi = MIN_SIZE, max(MIN_SIZE, min(MAX_SIZE, cap, int(ih)))
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
    """Trong các khung ứng viên, chọn khung đặt được chữ to nhất. Cùng cỡ (thường gặp khi chạm cỡ tối đa)
    thì chọn khung mà khối chữ lấp đều nhất – dáng khối chữ gần dáng khung, như người typeset hay làm."""
    key = astuple(style)
    t = prepare(text, style)
    metrics: dict[int, _Metrics] = {}

    def score(r):
        x1, y1, x2, y2 = r
        w, h = x2 - x1, y2 - y1
        ok, size, lines = _layout(t, key, w, h)
        m = metrics.setdefault(size, _Metrics(style, size))
        bw = max((m.adv(ln) for ln, _, _ in lines), default=1)
        bh = _block_height(lines, m, style)
        fill = min(bw / max(w, 1), bh / max(h, 1)) / max(bw / max(w, 1), bh / max(h, 1), 1e-6)
        return (ok, size, w * h * fill)

    return max(rects, key=score)


def _line_path(text: str, m: _Metrics, gap: float = 0.0) -> QPainterPath:
    """Đường nét của một dòng tại gốc (0, đường cơ sở); `gap` = khoảng thêm giữa các từ khi dàn đều."""
    path = QPainterPath()
    if gap <= 0:
        path.addText(QPointF(0, 0), m.font, text)
    else:
        x = 0.0
        for word in text.split(" "):
            path.addText(QPointF(x, 0), m.font, word)
            x += m.fm.horizontalAdvance(word + " ") + gap / m.hs
    return path


def _draw_shadow(p: QPainter, path: QPainterPath, style: TextStyle) -> None:
    """Bóng đổ: hình chữ (cả viền) làm mờ, lệch theo góc/khoảng cách, nằm dưới chữ."""
    import cv2
    import numpy as np

    dev = p.worldTransform().map(path)
    blur = max(0.0, style.shadow_size)
    margin = int(style.stroke_width + blur * 2 + 4)
    br = dev.boundingRect().adjusted(-margin, -margin, margin, margin)
    x0, y0 = int(math.floor(br.left())), int(math.floor(br.top()))
    w, h = int(math.ceil(br.width())) + 2, int(math.ceil(br.height())) + 2
    if w <= 0 or h <= 0 or w * h > 40_000_000:
        return
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    q = QPainter(img)
    q.setRenderHint(QPainter.Antialiasing)
    q.translate(-x0, -y0)
    if style.stroke_width > 0:
        pen = QPen(Qt.black, style.stroke_width * 2)
        pen.setJoinStyle(Qt.RoundJoin)
        q.strokePath(dev, pen)
    q.fillPath(dev, QBrush(Qt.black))
    q.end()
    arr = np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(h, img.bytesPerLine())[:, :w * 4]
    alpha = arr.reshape(h, w, 4)[..., 3].astype(np.float32)
    if blur > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), blur / 2)
    alpha *= max(0.0, min(100.0, style.shadow_opacity)) / 100
    c = QColor(style.shadow_color)
    out = np.empty((h, w, 4), np.uint8)  # BGRA, màu nhân sẵn alpha (Format_ARGB32_Premultiplied)
    for i, v in enumerate((c.blue(), c.green(), c.red())):
        out[..., i] = (alpha * v / 255).astype(np.uint8)
    out[..., 3] = alpha.astype(np.uint8)
    shadow = QImage(out.data, w, h, 4 * w, QImage.Format_ARGB32_Premultiplied)
    a = math.radians(style.shadow_angle)
    dx, dy = -math.cos(a) * style.shadow_distance, math.sin(a) * style.shadow_distance
    p.save()
    p.resetTransform()
    p.drawImage(QPointF(x0 + dx, y0 + dy), shadow)
    p.restore()


def draw_text(p: QPainter, text: str, style: TextStyle, rect: QRectF) -> None:
    """Vẽ chữ trong `rect`: căn giữa theo chiều dọc, căn/dàn đều theo chiều ngang, có viền nếu
    stroke_width > 0, xoay quanh tâm khung nếu style.rotation khác 0."""
    if not text.strip():
        return
    m, lines = layout(text, style, rect.width(), rect.height())
    pad, iw, _ = _inner(style, rect.width(), rect.height())
    left = rect.left() + pad + style.indent_left
    right = left + iw
    fm = m.fm
    shift = style.baseline_shift
    if style.superscript:
        shift += m.size * SCRIPT_POS
    elif style.subscript:
        shift -= m.size * SCRIPT_POS * 0.5

    path = QPainterPath()
    y = rect.center().y() - _block_height(lines, m, style) / 2
    first_para = True
    for ln, start, end in lines:
        if start and not first_para:
            y += style.space_before + style.space_after
        first_para = False
        lx = left + (style.indent_first if start else 0)
        avail = right - lx
        adv = m.adv(ln)
        gap = 0.0
        if style.justify and (not end or style.justify == "all") and " " in ln:
            gap = (avail - adv) / ln.count(" ")
            x = lx
        else:
            align = style.justify if style.justify in ("left", "center", "right") else style.align
            x = lx if align == "left" else (lx + avail - adv if align == "right" else lx + (avail - adv) / 2)
        baseline = y + (m.lh - fm.height() * m.vs) / 2 + fm.ascent() * m.vs - shift
        lp = _line_path(ln, m, gap)
        if style.underline or style.strike:
            width = fm.horizontalAdvance(ln) + (gap / m.hs) * ln.count(" ")
            t = max(1.0, fm.lineWidth())
            if style.underline:
                lp.addRect(QRectF(0, fm.underlinePos(), width, t))
            if style.strike:
                lp.addRect(QRectF(0, -fm.strikeOutPos() - t / 2, width, t))
        path.addPath(QTransform(m.hs, 0, 0, m.vs, x, baseline).map(lp))
        y += m.lh

    p.save()
    p.setRenderHint(QPainter.Antialiasing, style.antialias != "none")
    if style.rotation:
        c = rect.center()
        p.translate(c)
        p.rotate(style.rotation)
        p.translate(-c)
    if style.shadow:
        _draw_shadow(p, path, style)
    if style.stroke_width > 0:
        pen = QPen(QColor(style.stroke_color), style.stroke_width * 2)
        pen.setJoinStyle(Qt.RoundJoin)
        p.strokePath(path, pen)
    color = QColor(style.color)
    if style.antialias == "strong":  # "Mạnh": nét dày thêm một chút như Photoshop
        p.strokePath(path, QPen(color, max(0.6, m.size * 0.025)))
    p.fillPath(path, QBrush(color))
    p.restore()


def text_block(text: str, style: TextStyle, rect: tuple[float, float, float, float]) -> dict:
    """Bố cục đã dàn (để xuất sang Photoshop dựng lại y hệt): cỡ chữ, các dòng, khung, đường cơ sở."""
    x1, y1, x2, y2 = rect
    w, h = x2 - x1, y2 - y1
    m, lines = layout(text, style, w, h)
    pad, iw, _ = _inner(style, w, h)
    bh = _block_height(lines, m, style)
    top = (y1 + y2) / 2 - bh / 2
    # Photoshop: "\r" = sang đoạn mới, "\u0003" = xuống dòng trong cùng đoạn.
    parts = []
    for ln, start, _ in lines:
        if parts:
            parts.append("\r" if start else "\u0003")
        parts.append(ln)
    return {
        "size": m.size, "contents": "".join(parts), "lines": len(lines), "leading": m.lh,
        "left": x1 + pad + style.indent_left, "width": iw, "top": top, "block_height": bh,
        "first_baseline": top + (m.lh - m.fm.height() * m.vs) / 2 + m.fm.ascent() * m.vs,
        "ascent": m.fm.ascent() * m.vs,
    }
