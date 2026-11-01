"""Dàn chữ bản dịch vào một khung bằng font hệ thống (cần QGuiApplication đã khởi tạo).

Các thông số chữ theo kiểu bảng Character / Paragraph của Photoshop. Đơn vị là px của ảnh
(tương đương pt ở 72 ppi).
"""
from __future__ import annotations

from dataclasses import asdict, astuple, dataclass
import math
import re
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
from .vi_words import bare, dangling, ends_clause, final_particle, joined

MIN_SIZE = 8
MAX_SIZE = 150
SHAPES = {"round": "Bo tròn", "even": "Đều dòng", "": "Lấp đầy dòng"}
ROUND_POWER = 0.75  # độ nhọn của dáng bo tròn: 0.5 = hình tròn, 1 = nhọn hơn (9 từ → 1·2·3·2·1)
ASPECT_WEIGHT = 0.3  # mức ưu tiên khối chữ cùng tỉ lệ với khung so với dáng dòng
BREAK_WORD = 0.8  # phạt xuống dòng giữa từ ghép, tính như dòng lệch dáng 80%
BREAK_PARTICLE = 0.3  # phạt nhẹ để tiểu từ kết câu ("ạ.") không đứng trơ một mình
BREAK_FUNCTION = 0.5  # phạt hư từ đứng cuối dòng ("của | tôi", "những | người"): nhẹ hơn cắt đôi từ ghép
BREAK_CLAUSE = 0.15  # thưởng xuống dòng sau dấu phẩy / chấm
BUBBLE_FILL = 0.8  # mỗi dòng ≤ 80% bề ngang bóng ở độ cao của nó (lề dòng, dáng bo tròn) → chữ phủ ~70% bề ngang bóng
BUBBLE_VPAD = 0.06  # lề trên/dưới: 6% bề cao bóng
BUBBLE_FILL_NARROW = (0.86, 0.92)  # bóng hẹp mà chữ bị teo: nới lề dần theo các mức này…
MIN_SIZE_RATIO = 0.85  # …tới khi chữ ≥ 85% cỡ chuẩn của trang
SHORT_FILL = 0.6  # câu ngắn được to hơn cỡ chuẩn tới khi phủ nửa bề ngang bóng…
SHORT_GROW = 1.5  # …nhưng không quá 1,5 lần cỡ chuẩn (bản typeset tay: "Vâng", "Nagi" 30px khi cỡ chuẩn 24px)
BREAK_HYPHEN = 0.3  # ngắt ở dấu gạch trong tên ("Tanaka-|kun", "Yamada-|san"): được, nhưng ưu tiên chỗ cách
TALL = math.log(1 / 1.6)  # bóng cao hơn 1,6 lần bề ngang: mỗi dòng một từ cũng được ("THẬT / SAO / CƠ!?")
SHY = "\u00ad"  # đánh dấu chỗ ngắt được trong từ có gạch nối: nối lại thành "-"
SOFT_BREAK = "\u2028"  # giữa hai câu dịch gộp chung một bóng (khác "\n" = xuống dòng tay): nên, không bắt buộc, xuống dòng
PARA = "\u2029"  # đánh dấu từ cuối mỗi câu dịch như vậy khi ngắt dòng
BREAK_PARA = 0.25  # thưởng xuống dòng giữa hai câu dịch gộp chung bóng (hơn dấu phẩy một chút)
SPLIT_WEIGHT = 0.5  # mỗi chỗ cắt đôi từ ghép trong khối chữ đã chọn…
SPLIT_MIN_WORDS = 3  # …khi câu dài hơn 3 từ
JAG_WEIGHT = 1.0  # phạt dòng lởm chởm (dài ngắn đảo chiều nhiều lần)…
JAG_SLACK = 0.1  # …phần lệch quá 10% dòng dài nhất
PEAK_WEIGHT = 3.0  # phạt dòng thò ra so với cả hai dòng kề…
PEAK_SLACK = 0.2  # …quá 20% dòng dài nhất
SYMMETRY_WEIGHT = 0.6  # phạt khối bo tròn lệch: dòng trên và dòng dưới tương ứng dài khác nhau
SHRINK_MAX = 0.25  # được nhỏ chữ tối đa 25% so với cỡ lớn nhất vừa khung (khi nhờ đó khỏi cắt từ, khỏi thụt thò)…
TIGHT_SHRINK = 0.08  # …chỗ chật thì chỉ 8%: chữ to, dòng ngắn theo dáng bóng (tách cả từ ghép) hơn là chữ bé dòng đều
SHRINK_COST = 1.2  # …nếu điểm dáng tốt lên hơn 0,12 cho mỗi 10% nhỏ đi
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
    kerning: str = "optical"  # "optical" (Photoshop tự căn theo nét) | "metrics" (theo font) | "none" (0)
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
    # --- Dáng khối chữ khi tự xuống dòng (chỉ với căn giữa, một đoạn) ---
    shape: str = "round"  # "round" = bo tròn (dòng giữa dài, trên/dưới ngắn) | "even" = các dòng dài đều | "" = lấp đầy dòng
    fit_scale: float = 1.0  # tự co: dàn xong thì thu cỡ chữ theo tỉ lệ này, giữ nguyên cách xuống dòng (chừa lề rộng hơn)
    tight: bool = False  # chỗ chật (bóng chung hai câu không tách được thuỳ): ưu tiên chữ to, dòng ngắn, được tách từ ghép

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
    key = astuple(style)
    placed = _PLACED.get((text, key, int(w), int(h)))
    if placed:  # khung do place_in_shape dàn theo dáng bóng
        return _Metrics(style, placed[0]), list(placed[1])
    _, size, lines = _layout(text, key, int(w), int(h))
    return _Metrics(style, size), list(lines)


@lru_cache(maxsize=20000)
def _layout(text: str, style_key: tuple, w: int, h: int):
    """(vừa khung?, cỡ chữ, các dòng) – có cache vì được gọi rất nhiều lần."""
    style = TextStyle(*style_key)
    text = prepare(text, style)
    _, iw, ih = _inner(style, w, h)
    first_w = max(1.0, iw - style.indent_first)

    # Từ có gạch nối ("Tanaka-kun") được xuống dòng ngay sau dấu gạch ("Tanaka- / kun") khi thiếu chỗ.
    flow = "\n".join(" ".join(_words(para.replace(SOFT_BREAK, " "))) for para in text.split("\n"))

    def fit(size: int):
        m = _Metrics(style, size)
        lines = [(_join([ln]), a, b) for ln, a, b in wrap(flow, m, iw, first_w, style.hyphenate)]
        widest = max((m.adv(ln) for ln, _, _ in lines), default=0)
        # Không cắt đôi một từ (vd "Chu|yện"): từ dài nhất phải vừa một dòng.
        longest_word = max((m.adv(_join([wd])) for wd in _words(text)), default=0)
        ok = _block_height(lines, m, style) <= ih and widest <= iw + 0.5 and longest_word <= first_w + 0.5
        return ok, size, tuple(lines)

    if style.size > 0:
        best = fit(style.size)
    else:
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
        best = best or fit(MIN_SIZE)
    ok, size, lines = best
    if not ok:
        return ok, size, lines
    shaped = _shape_lines(text, style, style_key, size, iw, ih, lines)
    if shaped and style.size <= 0:
        # Như người typeset: chịu nhỏ chữ đi chút (≤25%) nếu nhờ đó khối chữ đẹp hơn, không cắt đôi từ ghép.
        top = size
        for smaller in range(top - 1, max(MIN_SIZE, int(top * (1 - _shrink_max(style)))) - 1, -1):
            res = _shape_lines(text, style, style_key, smaller, iw, ih, fit(smaller)[2])
            if res and res[0] + SHRINK_COST * (1 - smaller / top) < shaped[0] + SHRINK_COST * (1 - size / top):
                shaped, size = res, smaller
    return ok, _fit(style, size), shaped[1] if shaped else lines


def _shrink_max(style: TextStyle) -> float:
    return TIGHT_SHRINK if style.tight else SHRINK_MAX


def _fit(style: TextStyle, size: int) -> int:
    """Cỡ chữ cuối cùng khi tự co: thu theo style.fit_scale (giữ nguyên các dòng đã ngắt)."""
    if style.size > 0 or style.fit_scale >= 1:
        return size
    return max(MIN_SIZE, int(round(size * style.fit_scale)))


def _profile(n: int, shape: str) -> list[float]:
    """Bề ngang mong muốn của từng dòng so với dòng dài nhất: bo tròn thì dòng giữa dài, càng ra mép càng ngắn."""
    if shape != "round" or n < 3:
        return [1.0] * n
    r = n / 2
    return [(1 - ((i + .5 - r) / r) ** 2) ** ROUND_POWER for i in range(n)]


def raw_text(editor) -> str:
    """Chữ trong ô soạn Qt, giữ SOFT_BREAK (toPlainText đổi nó thành xuống dòng tay)."""
    return editor.document().toRawText().replace("\u2029", "\n")


def split_lines(text: str) -> list[str]:
    """Các câu dịch trong chữ của một vùng (cách nhau bằng xuống dòng tay hoặc SOFT_BREAK)."""
    return re.split("[\n" + SOFT_BREAK + "]", text)


def _words(text: str) -> tuple[str, ...]:
    """Các từ để ngắt dòng; từ có gạch nối giữa hai chữ ("Tanaka-kun") tách thành hai phần ngắt được.
    Nhiều câu dịch gộp chung bóng (cách nhau SOFT_BREAK) thì từ cuối mỗi câu mang dấu PARA: cả bóng dàn thành một
    khối, chỗ giữa hai câu chỉ là chỗ nên xuống dòng (người dịch hay tách một lời thoại theo cột chữ Nhật:
    "không tệ nhỉ" / "ha~" / "sư phụ này, …")."""
    out = []
    for para in text.split(SOFT_BREAK):
        words = []
        for word in para.split():
            parts = re.split(r"(?<=\w)-(?=\w)", word)
            words += [p + SHY for p in parts[:-1]] + [parts[-1]]
        if words and out:
            out[-1] += PARA
        out += words
    return tuple(out)


def _join(words) -> str:
    """Ghép các từ thành một dòng: phần của từ có gạch nối nối lại bằng "-", cuối dòng thì giữ "-"."""
    return " ".join(words).replace(PARA, "").replace(SHY + " ", "-").replace(SHY, "-")


@lru_cache(maxsize=2048)
def _segments(words: tuple[str, ...], style_key: tuple, size: int):
    """seg[i][j] = bề ngang của words[i:j] khi đặt trên một dòng."""
    m = _Metrics(TextStyle(*style_key), size)
    k = len(words)
    return tuple(tuple(m.adv(_join(words[i:j])) if j > i else 0.0 for j in range(k + 1)) for i in range(k + 1))


@lru_cache(maxsize=20000)
def _break(words: tuple[str, ...], style_key: tuple, size: int, caps: tuple[float, ...]):
    """Chia các từ thành đúng len(caps) dòng, dòng i dài ≤ caps[i], bề ngang các dòng sát dáng `style.shape` nhất.

    Quy hoạch động trên vị trí ngắt. Dòng dài nhất W suy từ tổng bề ngang chữ để khối chữ có đúng dáng đó.
    Trả về (độ lệch so với dáng, theo tỉ lệ W; các điểm ngắt) hoặc None nếu không chia được."""
    seg = _segments(words, style_key, size)
    k, n = len(words), len(caps)
    p = _profile(n, TextStyle(*style_key).shape)
    letters = sum(seg[i][i + 1] for i in range(k))
    space = (seg[0][k] - letters) / (k - 1) if k > 1 else 0.0
    W = max(1.0, (letters + (k - n) * space) / sum(p))
    # Xuống dòng giữa từ ghép ("Cảm | ơn") bị phạt nặng (BREAK_WORD); sau dấu phẩy/chấm thì được thưởng.
    after = [0.0] * (k + 1)
    w = [x.rstrip(PARA) for x in words]
    for j in range(1, k):
        if words[j - 1].endswith(PARA):  # giữa hai câu dịch gộp chung bóng
            after[j] = -(BREAK_PARA * W) ** 2
        elif w[j - 1].endswith(SHY):
            after[j] = (BREAK_HYPHEN * W) ** 2
        elif joined(w[j - 1], w[j]):
            after[j] = (BREAK_WORD * W) ** 2
        elif final_particle(w[j - 1], w[j]):  # "ạ." một chữ cái đứng riêng trông như rơi chữ: phạt nặng
            after[j] = ((BREAK_WORD if len(bare(w[j])) == 1 else BREAK_PARTICLE) * W) ** 2
        elif dangling(w[j - 1], w[j], w[j - 2] if j > 1 else ""):
            after[j] = (BREAK_FUNCTION * W) ** 2
        elif ends_clause(w[j - 1]):
            after[j] = -(BREAK_CLAUSE * W) ** 2
    inf = float("inf")
    cost = [[inf] * (k + 1) for _ in range(n + 1)]
    back = [[0] * (k + 1) for _ in range(n + 1)]
    cost[0][0] = 0.0
    for line in range(1, n + 1):
        cap = caps[line - 1]
        target = min(p[line - 1] * W, cap)
        for j in range(line, k - (n - line) + 1):
            for i in range(j - 1, line - 2, -1):
                a = seg[i][j]
                if a > cap + .5:
                    break  # thêm từ ở đầu dòng chỉ làm dòng dài hơn
                c = cost[line - 1][i] + (a - target) ** 2 + after[j]
                if c < cost[line][j]:
                    cost[line][j], back[line][j] = c, i
    if cost[n][k] == inf:
        return None
    cuts, j = [k], k
    for line in range(n, 0, -1):
        j = back[line][j]
        cuts.append(j)
    return math.sqrt(max(0.0, cost[n][k]) / n) / W, tuple(reversed(cuts))


def _score(style: TextStyle, seg, res, lh: float, want: float, k: int, words=(), aspect: bool = True) -> float:
    """Điểm (càng nhỏ càng đẹp) của một cách ngắt: lệch dáng + khối chữ khác tỉ lệ khung + bị "thắt eo" + cắt từ
    ghép + thụt thò. `aspect=False`: bỏ phần so tỉ lệ khung (để so khối chữ ở các khung khác nhau)."""
    dev, cuts = res
    n = len(cuts) - 1
    widths = [seg[a][b] for a, b in zip(cuts, cuts[1:])]
    widest = max(widths)
    score = dev + (ASPECT_WEIGHT * abs(math.log(widest / (n * lh)) - want) if aspect else 0.0)
    if style.shape == "round" and n >= 3:
        # Khối bo tròn không được "thắt eo": dòng gần giữa phải dài hơn dòng ở mép.
        mid = (n - 1) / 2
        dent = sum(max(0.0, widths[i] - widths[i + (1 if i < mid else -1)]) for i in range(n) if abs(i - mid) > .5)
        score += 2 * dent / widest
        # …và cân hai đầu: dòng trên và dòng dưới tương ứng dài gần bằng nhau (không chỉ một chữ trơ trọi ở đỉnh).
        pairs = n // 2
        score += SYMMETRY_WEIGHT * sum(abs(widths[i] - widths[n - 1 - i]) for i in range(pairs)) / (pairs * widest)
    # Cắt đôi từ ghép ("sư | phụ", "không | tệ"): phạt riêng từng chỗ, để thà nhỏ chữ chút còn hơn cắt từ. Câu
    # 2–3 từ trong bóng hẹp thì không còn chỗ nào khác để ngắt (bản type tay: "THẾ / NHƯNG / …").
    # Tiểu từ một chữ cái đứng trơ một dòng ("đâu / ạ.") cũng vậy.
    if k > SPLIT_MIN_WORDS and not style.tight:
        plain = [w.rstrip(PARA) for w in words]
        score += SPLIT_WEIGHT * sum(1 for j in cuts[1:-1] if joined(plain[j - 1], plain[j]) or
                                    (final_particle(plain[j - 1], plain[j]) and len(bare(plain[j])) == 1))
    # Thụt thò: bề dài các dòng phải lên dần rồi xuống dần (một đỉnh); mỗi lần đảo chiều thêm là dòng lởm chởm
    # (bản type tay: 89% khối không có dòng nào thụt vào so với cả hai dòng kề).
    if n >= 3:
        travel = sum(abs(b - a) for a, b in zip(widths, widths[1:]))
        excess = travel - (2 * widest - widths[0] - widths[-1])
        score += JAG_WEIGHT * max(0.0, excess - JAG_SLACK * widest) / widest
        # …và "thò": một dòng dài vọt hẳn so với cả hai dòng kề ("cậu cũng / đi chơi như này / nhiều lắm"); khối
        # hình thoi dài dần đều thì dòng giữa chỉ nhỉnh hơn hai bên một chút.
        poke = sum(max(0.0, widths[i] - max(widths[i - 1], widths[i + 1]) - PEAK_SLACK * widest) for i in range(1, n - 1))
        score += PEAK_WEIGHT * poke / widest
    if n > max(1, (2 * k + 2) // 3) and want > TALL:
        score += 1.0  # trung bình mỗi dòng dưới 1,5 từ: chỉ dùng khi không còn cách khác (trừ bóng cao, hẹp)
    return score


def _lines_of(words, cuts):
    n = len(cuts) - 1
    return tuple((_join(words[a:b]), i == 0, i == n - 1) for i, (a, b) in enumerate(zip(cuts, cuts[1:])))


def _shapeable(text: str, style: TextStyle) -> bool:
    return bool(style.shape) and style.align == "center" and not style.justify and not style.indent_first \
        and "\n" not in text


def _shape_lines(text: str, style: TextStyle, style_key: tuple, size: int, iw: float, ih: float, lines):
    """Ngắt lại dòng cho đẹp như người typeset: khối chữ bo tròn (vd 9 từ → 1·2·3·2·1) hoặc các dòng dài đều.
    Với cỡ chữ `size`, chọn số dòng để khối chữ cùng tỉ lệ với khung. Trả về (điểm, các dòng) hoặc None (giữ
    cách ngắt thường: văn bản có xuống dòng tay, căn trái/phải, dàn đều, thụt dòng đầu, từ dài hơn cả dòng)."""
    if not _shapeable(text, style):
        return None
    words = _words(text)
    k = len(words)
    if k < 2:
        return None
    seg = _segments(words, style_key, size)
    if max(seg[i][i + 1] for i in range(k)) > iw + .5:
        return None
    lh = _Metrics(style, size).lh
    n_lo = len(lines)
    n_hi = n_lo
    while n_hi < min(k, max(n_lo, (2 * k + 2) // 3)) and (n_hi + 1) * lh <= ih + .5:
        n_hi += 1  # thêm dòng khi khung còn chỗ, nhưng trung bình mỗi dòng vẫn ≥ 1,5 từ
    want = math.log(max(iw, 1) / max(ih, 1))
    best = None
    for n in range(n_lo, n_hi + 1):
        res = _break(words, style_key, size, (1e9,) * n)
        if res is None or max(seg[a][b] for a, b in zip(res[1], res[1][1:])) > iw + .5:
            res = _break(words, style_key, size, (float(int(iw)),) * n)
        if res is None:
            continue
        score = _score(style, seg, res, lh, want, k, words)
        if best is None or score < best[0] - 1e-6:
            best = (score, res[1])
    return None if best is None else (best[0], _lines_of(words, best[1]))


# Bố cục đã dàn theo dáng bóng: (chữ, kiểu, rộng, cao của khung chữ) → (cỡ, các dòng). layout() dùng lại
# đúng kết quả này cho khung place_in_shape trả về, nên mọi nơi vẽ/xuất theo khung chữ đều ra cùng một bố cục.
_PLACED: dict[tuple, tuple] = {}


def place_in_shape(text: str, style: TextStyle, shape) -> tuple[int, int, int, int] | None:
    """Dàn chữ theo đúng hình lòng bóng `shape` = (cx, cy, ((y, trái, phải), …)) (xem clean.text_area):
    mỗi dòng được rộng bằng lòng bóng ở độ cao của nó, khối chữ đặt chính giữa lòng bóng.
    Trả về khung chữ (để vẽ/xuất như khung thường) hoặc None nếu không dàn được theo dáng."""
    key = astuple(style)
    res = _place(text, key, shape)
    if res is None:
        return None
    rect, size, lines = res
    if len(_PLACED) > 50000:
        _PLACED.clear()
    _PLACED[(text, key, rect[2] - rect[0], rect[3] - rect[1])] = (size, lines)
    return rect


@lru_cache(maxsize=4096)
def _place(text: str, style_key: tuple, shape):
    style = TextStyle(*style_key)
    text = prepare(text, style)
    words = _words(text)
    if not words or not _shapeable(text, style) or style.indent_left or style.indent_right:
        return None
    cx, cy, rows = shape
    y0 = min(r[0] for r in rows)
    half = [0.0] * (max(r[0] for r in rows) + 1 - y0)
    for y, left, right in rows:
        half[y - y0] = max(0.0, min(cx - left, right - cx))
    bw = 2 * max(half)
    bh = len(half)
    pad = 1.0 + style.stroke_width
    k = len(words)
    want = math.log(max(bw, 1) / max(bh, 1))
    # Bề ngang: mỗi dòng chỉ chiếm ~fill lòng bóng ở độ cao của nó. Bề cao: chỉ chừa lề nhỏ (thu cả chiều
    # dọc thì bóng thấp / bóng bầu dục bị bóp đầu đuôi, chữ teo lại).
    vpad = bh * BUBBLE_VPAD
    lo_y, hi_y = y0 + vpad, y0 + bh - vpad
    fill = BUBBLE_FILL

    def inner_half(ya: float, yb: float) -> float:
        """Nửa bề ngang dành cho chữ trong dải [ya, yb]."""
        a, b = max(0, int(ya) - y0), min(bh, int(math.ceil(yb)) - y0)
        return fill * min(half[a:b], default=0)

    def fit(size: int):
        m = _Metrics(style, size)
        seg = _segments(words, style_key, size)
        widest_word = max(seg[i][i + 1] for i in range(k))
        best = None
        for n in range(1, (k if want <= TALL else min(k, (2 * k + 2) // 3 + 2)) + 1):
            top = cy - n * m.lh / 2
            if top + .1 * m.lh < lo_y or top + (n - .1) * m.lh > hi_y:
                break
            caps = []
            for i in range(n):  # bề ngang bóng hẹp nhất trong dải của dòng (bỏ phần đệm trên/dưới dòng)
                half_w = inner_half(top + (i + .1) * m.lh, top + (i + .9) * m.lh)
                caps.append(float(int(2 * half_w - 2 * pad)))
            if max(caps) < widest_word:
                continue
            res = _break(words, style_key, size, tuple(caps))
            if res is None:
                continue
            score = _score(style, seg, res, m.lh, want, k, words)
            if best is None or score < best[0] - 1e-6:
                best = (score, size, res[1], m)
        return best

    def solve():
        if style.size > 0:
            best = fit(style.size)
        else:
            cap = int(style.max_size) if style.max_size > 0 else MAX_SIZE
            lo, hi, best = MIN_SIZE, max(MIN_SIZE, min(MAX_SIZE, cap, int(bh))), None
            while lo <= hi:
                mid = (lo + hi) // 2
                res = fit(mid)
                if res:
                    best, lo = res, mid + 1
                else:
                    hi = mid - 1
            if best is not None and best[1] >= cap and k <= SPLIT_MIN_WORDS:
                # Câu ngắn trong bóng rộng ("Ừ.", "Vâng"): người typeset để chữ to hơn cỡ chuẩn của trang (tới ~1,4 lần)
                # cho khỏi lọt thỏm – to dần tới khi chữ phủ được SHORT_FILL bề ngang bóng. Câu dài hơn 3 từ thì giữ cỡ
                # chung của trang, bóng to để lề rộng.
                for size in range(cap + 1, int(cap * SHORT_GROW) + 1):
                    cuts, m = best[2], best[3]
                    seg = _segments(words, style_key, best[1])
                    widest = max(seg[a][b] for a, b in zip(cuts, cuts[1:]))
                    if widest >= SHORT_FILL * bw:
                        break
                    res = fit(size)
                    if not res:
                        break
                    best = res
            if best is not None:
                # Như người typeset: chịu nhỏ chữ đi chút (≤25%) nếu nhờ đó khối chữ tròn hơn, không cắt đôi từ ghép.
                top = best[1]
                for size in range(top - 1, max(MIN_SIZE, int(top * (1 - _shrink_max(style)))) - 1, -1):
                    res = fit(size)
                    if res and res[0] + SHRINK_COST * (1 - size / top) < best[0] + SHRINK_COST * (1 - best[1] / top):
                        best = res
        return best

    best = solve()
    if style.size <= 0 and style.max_size > 0:
        # Bóng hẹp: giữ đúng lề 80% thì chữ teo hẳn (vd 17px khi cỡ chuẩn 26px). Nới dần lề cho bóng hẹp được phủ
        # nhiều hơn, tới khi chữ về lại ≥ MIN_SIZE_RATIO cỡ chuẩn.
        found = [best] if best is not None else []
        for fill in BUBBLE_FILL_NARROW:
            if found and max(r[1] for r in found) >= MIN_SIZE_RATIO * style.max_size:
                break
            res = solve()
            if res is not None:
                found.append(res)
        if found:
            # Lề rộng cho chữ to hơn, nhưng không lấy nếu khối chữ vì thế mà cắt từ / thụt thò: so cùng thang điểm.
            top = max(r[1] for r in found)
            best = min((r for r in found if r[1] >= top * (1 - _shrink_max(style))),
                       key=lambda r: r[0] + SHRINK_COST * (1 - r[1] / top))
    if best is None:
        return None
    _, size, cuts, m = best
    if _fit(style, size) != size:
        size = _fit(style, size)
        m = _Metrics(style, size)
    lines = _lines_of(words, cuts)
    seg = _segments(words, style_key, size)
    widest = max(seg[a][b] for a, b in zip(cuts, cuts[1:]))
    x, y = _balance(_ink_boxes(lines, m, style), rows, cx, cy)
    # Khung chữ đủ rộng cho dòng dài nhất sau khi trừ lề (style.padding) và chừa thêm chút cho Photoshop đo lệch.
    w = int(math.ceil(widest / (1 - 2 * style.padding))) + 6
    h = int(math.ceil(_block_height(lines, m, style) / (1 - 2 * style.padding))) + 4
    x1, y1 = int(round(x - w / 2)), int(round(y - h / 2))
    return (x1, y1, x1 + w, y1 + h), size, lines


def _ink_boxes(lines, m: _Metrics, style: TextStyle):
    """Nét chữ thật của từng dòng (trái, phải, trên, dưới), toạ độ so với tâm khối chữ – đúng như draw_text vẽ.
    Khác khung dòng: chữ IN HOA có khoảng trống trên đầu (chỗ cho dấu) và dưới chân (phần đuôi chữ thường)."""
    top = -_block_height(lines, m, style) / 2
    scale = QTransform(m.hs, 0, 0, m.vs, 0, 0)
    out = []
    for i, (ln, _, _) in enumerate(lines):
        r = scale.mapRect(_line_path(ln, m).boundingRect())
        left = -m.adv(ln) / 2
        baseline = top + i * m.lh + (m.lh - m.fm.height() * m.vs) / 2 + m.fm.ascent() * m.vs - style.baseline_shift
        out.append((left + r.left(), left + r.right(), baseline + r.top(), baseline + r.bottom()))
    return out


def _balance(ink, rows, cx: float, cy: float):
    """Căn giữa theo mắt nhìn: dời khối chữ cho khoảng hở từ nét chữ tới mép lòng bóng bằng nhau hai bên trái/phải
    và trên/dưới (tâm hình học của bóng mây, bóng gai, bóng méo không phải chỗ mắt thấy là giữa)."""
    y0 = rows[0][0]
    span = {y: (left, right) for y, left, right in rows}
    tops: dict[int, int] = {}
    bottoms: dict[int, int] = {}
    for y, left, right in rows:
        for col in range(left, right):
            tops[col] = min(tops.get(col, y), y)
            bottoms[col] = max(bottoms.get(col, y), y)

    def gaps(x, y):
        gl = gr = gt = gb = math.inf
        for a, b, t, u in ink:
            for row in range(int(math.floor(y + t)), int(math.ceil(y + u)) + 1):
                if row not in span:
                    return None
                gl, gr = min(gl, x + a - span[row][0]), min(gr, span[row][1] - (x + b))
            for col in range(int(math.floor(x + a)), int(math.ceil(x + b)) + 1):
                if col not in tops:
                    return None
                gt, gb = min(gt, y + t - tops[col]), min(gb, bottoms[col] - (y + u))
        return gl, gr, gt, gb

    start = gaps(cx, cy)
    if start is None or y0 is None:
        return cx, cy
    x, y = cx, cy
    for _ in range(4):  # dời một bước làm dòng khác chạm chỗ hẹp hơn, nên lặp vài lần cho cân
        g = gaps(x, y)
        if g is None:
            break
        dx, dy = (g[1] - g[0]) / 2, (g[3] - g[2]) / 2
        if abs(dx) < .5 and abs(dy) < .5:
            break
        x, y = x + dx, y + dy
    g = gaps(x, y)
    if g is None or min(g) < min(min(start), 0):
        return cx, cy  # cân xong mà lại chạm mép: giữ tâm cũ
    return x, y


def _quality(text: str, style: TextStyle, style_key: tuple, size: int, lines, want: float) -> float:
    """Điểm riêng của khối chữ đã ngắt (cắt từ ghép, thụt thò, mỗi dòng ít từ…), không tính nó có hợp tỉ lệ khung
    hay không: để so các khung với nhau."""
    if not _shapeable(text, style) or len(lines) < 2:
        return 0.0
    words = _words(text)
    cuts, at = [0], 0
    for ln, _, _ in lines:
        at += len(_words(ln))
        cuts.append(at)
    if at != len(words):
        return 0.0
    seg = _segments(words, style_key, size)
    # Lệch khỏi dáng mong muốn (bo tròn: hình thoi, dòng giữa dài, trên/dưới ngắn) – như trong _break.
    widths = [seg[a][b] for a, b in zip(cuts, cuts[1:])]
    p = _profile(len(widths), style.shape)
    W = max(1.0, sum(widths) / sum(p))
    dev = math.sqrt(sum((w - q * W) ** 2 for w, q in zip(widths, p)) / len(widths)) / W
    return _score(style, seg, (dev, tuple(cuts)), _Metrics(style, size).lh, want, len(words), words, aspect=False)


def best_rect(text: str, style: TextStyle, rects: list[tuple[int, int, int, int]]):
    """Trong các khung ứng viên, chọn khung cho chữ to và khối chữ đẹp: như người typeset, chịu nhỏ chữ đi chút
    (≤ SHRINK_MAX) nếu ở khung khác khối chữ tròn đều hơn hẳn (khung hẹp cho chữ to nhưng mỗi dòng 1–2 từ, dòng dài
    ngắn thụt thò). Cùng điểm thì chọn khung mà khối chữ lấp đều nhất – dáng khối chữ gần dáng khung."""
    key = astuple(style)
    t = prepare(text, style)
    metrics: dict[int, _Metrics] = {}

    def measure(r):
        x1, y1, x2, y2 = r
        w, h = x2 - x1, y2 - y1
        ok, size, lines = _layout(t, key, w, h)
        m = metrics.setdefault(size, _Metrics(style, size))
        bw = max((m.adv(ln) for ln, _, _ in lines), default=1)
        bh = _block_height(lines, m, style)
        fill = min(bw / max(w, 1), bh / max(h, 1)) / max(bw / max(w, 1), bh / max(h, 1), 1e-6)
        return ok, size, w * h * fill, _quality(t, style, key, size, lines, math.log(max(w, 1) / max(h, 1)))

    found = [(r, measure(r)) for r in rects]
    top = max((m[1] for _, m in found if m[0]), default=0)
    if not top:
        return max(found, key=lambda f: f[1][:3])[0]

    def cost(f):
        ok, size, area, shape = f[1]
        if not ok or size < top * (1 - _shrink_max(style)):
            return (1, -size, -area)
        return (0, shape + SHRINK_COST * (1 - size / top), -area)

    return min(found, key=cost)[0]


def block_quality(text: str, style: TextStyle, rect) -> float:
    """Điểm dáng của khối chữ khi dàn trong `rect` (cắt từ ghép, thụt thò, dòng vụn…; càng nhỏ càng đẹp)."""
    x1, y1, x2, y2 = rect
    t = prepare(text, style)
    ok, size, lines = _layout(t, astuple(style), int(x2 - x1), int(y2 - y1))
    return _quality(t, style, astuple(style), size, lines, math.log(max(x2 - x1, 1) / max(y2 - y1, 1)))


def block_box(text: str, style: TextStyle, rect):
    """Khối chữ thật khi dàn trong `rect` (căn giữa như draw_text): (trái, trên, phải, dưới, cỡ chữ, khung từng dòng)."""
    x1, y1, x2, y2 = rect
    m, lines = layout(text, style, x2 - x1, y2 - y1)
    pad, iw, _ = _inner(style, x2 - x1, y2 - y1)
    cx, top = x1 + pad + style.indent_left + iw / 2, (y1 + y2) / 2 - _block_height(lines, m, style) / 2
    rows = tuple((cx - m.adv(ln) / 2, top + i * m.lh, cx + m.adv(ln) / 2, top + (i + 1) * m.lh)
                 for i, (ln, _, _) in enumerate(lines))
    if not rows:
        return cx, top, cx, top, m.size, rows
    return min(r[0] for r in rows), top, max(r[2] for r in rows), rows[-1][3], m.size, rows


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
