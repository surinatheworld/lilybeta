"""Bảng chỉnh chữ theo bố cục Character / Paragraph của Photoshop 2023."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFontComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.typeset import TextStyle

AUTO = "(Tự động)"
SIZES = [6, 8, 9, 10, 11, 12, 14, 18, 24, 30, 36, 48, 60, 72]
TRACKING = [-100, -75, -50, -25, -10, -5, 0, 5, 10, 25, 50, 75, 100, 200]
ANTIALIAS = [("none", "Không"), ("sharp", "Sắc"), ("crisp", "Rõ"), ("strong", "Mạnh"), ("smooth", "Mượt")]
# (khoá, nhãn trên nút, tên đầy đủ) — các nút kiểu chữ ở cuối bảng Character
TOGGLES = [
    ("bold", "T", "Giả đậm (Faux Bold)"),
    ("italic", "T", "Giả nghiêng (Faux Italic)"),
    ("uppercase", "TT", "IN HOA (All Caps)"),
    ("small_caps", "Tᴛ", "Chữ hoa nhỏ (Small Caps)"),
    ("superscript", "T¹", "Chỉ số trên (Superscript)"),
    ("subscript", "T₁", "Chỉ số dưới (Subscript)"),
    ("underline", "T", "Gạch chân (Underline)"),
    ("strike", "T", "Gạch ngang (Strikethrough)"),
]
# (align, justify, tên) — 7 nút căn lề ở đầu bảng Paragraph
ALIGNS = [
    ("left", "", "Căn trái (Left Align Text)"),
    ("center", "", "Căn giữa (Center Text)"),
    ("right", "", "Căn phải (Right Align Text)"),
    ("left", "left", "Dàn đều, dòng cuối căn trái (Justify Last Left)"),
    ("center", "center", "Dàn đều, dòng cuối căn giữa (Justify Last Centered)"),
    ("right", "right", "Dàn đều, dòng cuối căn phải (Justify Last Right)"),
    ("center", "all", "Dàn đều tất cả (Justify All)"),
]


def _align_icon(align: str, justify: str) -> QIcon:
    """Biểu tượng căn lề: 4 vạch ngang, độ dài và vị trí theo kiểu căn."""
    pm = QPixmap(22, 18)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#d0d0d0"))
    lens = [20, 14, 20, 11]
    for i, ln in enumerate(lens):
        last = i == len(lens) - 1
        if justify and (not last or justify == "all"):
            ln = 20
        a = justify if (justify and last and justify != "all") else align
        x = 1 if a == "left" else (21 - ln if a == "right" else (22 - ln) / 2)
        p.drawRect(QRectF(x, 2 + i * 4, ln, 2))
    p.end()
    return QIcon(pm)


def _num_combo(items, auto: str | None = None, suffix: str = "") -> QComboBox:
    """Ô số có danh sách gợi ý và cho gõ tay, như các ô cỡ chữ / leading / tracking của Photoshop."""
    c = QComboBox()
    c.setEditable(True)
    if auto:
        c.addItem(auto)
    for v in items:
        c.addItem(f"{v}{suffix}")
    c.lineEdit().setValidator(None)
    c.setInsertPolicy(QComboBox.NoInsert)
    return c


def _combo_value(c: QComboBox, auto_value: float = 0) -> float:
    txt = c.currentText().strip().replace(",", ".").rstrip("px").strip()
    if not txt or txt.startswith("("):
        return auto_value
    try:
        return float(txt)
    except ValueError:
        return auto_value


def _set_combo(c: QComboBox, value: float, auto: str | None = None, suffix: str = ""):
    if auto and not value:
        c.setCurrentText(auto)
    else:
        c.setCurrentText(f"{value:g}{suffix}")


def _spin(lo, hi, suffix, step=1.0, decimals=1) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


def _icon_label(text: str, tip: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setToolTip(tip)
    lbl.setFixedWidth(26)
    lbl.setAlignment(Qt.AlignCenter)
    f = lbl.font()
    f.setBold(True)
    lbl.setFont(f)
    return lbl


class ColorSwatch(QPushButton):
    changed = Signal()

    def __init__(self, color="#000000", title="Chọn màu"):
        super().__init__()
        self.title = title
        self.setFixedSize(46, 22)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color: str):
        self.color = color
        self.setStyleSheet(f"background:{color}; border:1px solid #888;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self, self.title)
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit()


class TextPanel(QTabWidget):
    """Hai thẻ Ký tự (Character) và Đoạn (Paragraph); phát `changed` khi người dùng đổi bất kỳ ô nào."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self.addTab(self._build_character(), "Ký tự")
        self.addTab(self._build_paragraph(), "Đoạn")

    # --- Character ------------------------------------------------------------------
    def _build_character(self) -> QWidget:
        w = QWidget()
        g = QGridLayout(w)
        g.setHorizontalSpacing(6)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)

        self.font_box = QFontComboBox()
        self.font_box.setEditable(True)
        self.font_box.setToolTip("Họ font (Font Family) – lấy từ font cài trên máy")
        self.btn_star = QPushButton("☆")
        self.btn_star.setFixedWidth(30)
        self.btn_star.setToolTip("Thêm/bỏ font này khỏi danh sách yêu thích")
        frow = QHBoxLayout()
        frow.addWidget(self.font_box, 1)
        frow.addWidget(self.btn_star)
        g.addLayout(frow, 0, 0, 1, 4)

        self.style_box = QComboBox()
        self.style_box.setToolTip("Kiểu mặt chữ (Font Style)")
        g.addWidget(self.style_box, 1, 0, 1, 4)
        self.font_box.currentFontChanged.connect(self._fill_styles)

        self.size_box = _num_combo(SIZES, "Tự động", " px")
        self.size_box.setToolTip("Cỡ chữ (Font Size). Tự động = tự co cho vừa bóng thoại")
        self.leading_box = _num_combo(SIZES, AUTO, " px")
        self.leading_box.setToolTip("Khoảng cách dòng (Leading)")
        g.addWidget(_icon_label("tT", "Cỡ chữ (Font Size)"), 2, 0)
        g.addWidget(self.size_box, 2, 1)
        g.addWidget(_icon_label("A↕", "Khoảng cách dòng (Leading)"), 2, 2)
        g.addWidget(self.leading_box, 2, 3)

        self.kerning_box = QComboBox()
        self.kerning_box.addItem("Theo font", "metrics")
        self.kerning_box.addItem("0", "none")
        self.kerning_box.setToolTip("Kerning giữa các cặp ký tự (Metrics = theo font)")
        self.tracking_box = _num_combo(TRACKING)
        self.tracking_box.setToolTip("Giãn đều ký tự (Tracking), đơn vị 1/1000 em")
        g.addWidget(_icon_label("V/A", "Kerning"), 3, 0)
        g.addWidget(self.kerning_box, 3, 1)
        g.addWidget(_icon_label("VA", "Tracking"), 3, 2)
        g.addWidget(self.tracking_box, 3, 3)

        self.vscale_box = _spin(10, 1000, " %", 5, 0)
        self.vscale_box.setToolTip("Co giãn dọc (Vertical Scale)")
        self.hscale_box = _spin(10, 1000, " %", 5, 0)
        self.hscale_box.setToolTip("Co giãn ngang (Horizontal Scale)")
        g.addWidget(_icon_label("IT", "Co giãn dọc (Vertical Scale)"), 4, 0)
        g.addWidget(self.vscale_box, 4, 1)
        g.addWidget(_icon_label("T↔", "Co giãn ngang (Horizontal Scale)"), 4, 2)
        g.addWidget(self.hscale_box, 4, 3)

        self.baseline_box = _spin(-500, 500, " px", 1, 1)
        self.baseline_box.setToolTip("Dịch đường cơ sở (Baseline Shift), dương = nâng lên")
        self.color_btn = ColorSwatch("#000000", "Màu chữ")
        self.color_btn.setToolTip("Màu chữ (Color)")
        g.addWidget(_icon_label("A↑", "Dịch đường cơ sở (Baseline Shift)"), 5, 0)
        g.addWidget(self.baseline_box, 5, 1)
        g.addWidget(QLabel("Màu:"), 5, 2, Qt.AlignRight)
        g.addWidget(self.color_btn, 5, 3, Qt.AlignLeft)

        trow = QHBoxLayout()
        trow.setSpacing(2)
        self.toggles: dict[str, QToolButton] = {}
        for key, label, tip in TOGGLES:
            b = QToolButton()
            b.setText(label)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setFixedSize(30, 26)
            f = QFont(b.font())
            f.setPointSize(10)
            f.setBold(key == "bold")
            f.setItalic(key == "italic")
            f.setUnderline(key == "underline")
            f.setStrikeOut(key == "strike")
            b.setFont(f)
            b.toggled.connect(lambda on, k=key: self._toggle(k, on))
            trow.addWidget(b)
            self.toggles[key] = b
        trow.addStretch(1)
        g.addLayout(trow, 6, 0, 1, 4)

        self.lang_box = QComboBox()
        self.lang_box.addItem("Tiếng Việt")
        self.lang_box.setEnabled(False)
        self.lang_box.setToolTip("Ngôn ngữ (dùng cho ngắt dòng)")
        self.aa_box = QComboBox()
        for key, label in ANTIALIAS:
            self.aa_box.addItem(label, key)
        self.aa_box.setToolTip("Khử răng cưa (Anti-aliasing)")
        g.addWidget(self.lang_box, 7, 0, 1, 2)
        g.addWidget(_icon_label("aa", "Khử răng cưa (Anti-aliasing)"), 7, 2)
        g.addWidget(self.aa_box, 7, 3)

        for c in (self.size_box, self.leading_box, self.tracking_box):
            c.currentTextChanged.connect(self._emit)
        for c in (self.style_box, self.kerning_box, self.aa_box):
            c.currentIndexChanged.connect(self._emit)
        for s in (self.vscale_box, self.hscale_box, self.baseline_box):
            s.valueChanged.connect(self._emit)
        self.font_box.currentFontChanged.connect(self._emit)
        self.color_btn.changed.connect(self._emit)
        g.setRowStretch(8, 1)
        return w

    def _fill_styles(self, font: QFont):
        cur = self.style_box.currentText()
        was = self._loading
        self._loading = True
        self.style_box.clear()
        styles = QFontDatabase.styles(font.family()) or ["Regular"]
        self.style_box.addItems(styles)
        if cur in styles:
            self.style_box.setCurrentText(cur)
        elif "Regular" in styles:
            self.style_box.setCurrentText("Regular")
        self._loading = was

    def _toggle(self, key: str, on: bool):
        # Chỉ số trên và dưới loại trừ nhau, như Photoshop.
        other = {"superscript": "subscript", "subscript": "superscript"}.get(key)
        if on and other and self.toggles[other].isChecked():
            self._loading, was = True, self._loading
            self.toggles[other].setChecked(False)
            self._loading = was
        self._emit()

    # --- Paragraph ------------------------------------------------------------------
    def _build_paragraph(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        arow = QHBoxLayout()
        arow.setSpacing(2)
        self.align_buttons: list[QToolButton] = []
        for i, (align, justify, tip) in enumerate(ALIGNS):
            b = QToolButton()
            b.setIcon(_align_icon(align, justify))
            b.setIconSize(QSize(22, 18))
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setToolTip(tip)
            b.setFixedSize(32, 28)
            b.clicked.connect(self._emit)
            arow.addWidget(b)
            self.align_buttons.append(b)
            if i in (2, 5):
                arow.addSpacing(8)
        arow.addStretch(1)
        v.addLayout(arow)

        g = QGridLayout()
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        self.indent_left = _spin(0, 1000, " px")
        self.indent_left.setToolTip("Thụt lề trái (Indent Left Margin)")
        self.indent_right = _spin(0, 1000, " px")
        self.indent_right.setToolTip("Thụt lề phải (Indent Right Margin)")
        self.indent_first = _spin(-1000, 1000, " px")
        self.indent_first.setToolTip("Thụt dòng đầu (Indent First Line)")
        self.space_before = _spin(0, 1000, " px")
        self.space_before.setToolTip("Cách trước đoạn (Add Space Before Paragraph)")
        self.space_after = _spin(0, 1000, " px")
        self.space_after.setToolTip("Cách sau đoạn (Add Space After Paragraph)")
        g.addWidget(_icon_label("→▯", "Thụt lề trái"), 0, 0)
        g.addWidget(self.indent_left, 0, 1)
        g.addWidget(_icon_label("▯←", "Thụt lề phải"), 0, 2)
        g.addWidget(self.indent_right, 0, 3)
        g.addWidget(_icon_label("→¶", "Thụt dòng đầu"), 1, 0)
        g.addWidget(self.indent_first, 1, 1)
        g.addWidget(_icon_label("¶↑", "Cách trước đoạn"), 2, 0)
        g.addWidget(self.space_before, 2, 1)
        g.addWidget(_icon_label("¶↓", "Cách sau đoạn"), 2, 2)
        g.addWidget(self.space_after, 2, 3)
        v.addLayout(g)
        self.chk_hyphen = QCheckBox("Gạch nối (Hyphenate)")
        self.chk_hyphen.setToolTip("Ngắt từ quá dài sang dòng sau kèm dấu gạch nối")
        v.addWidget(self.chk_hyphen)
        hint = QLabel("Enter hoặc | trong nội dung = sang đoạn mới.")
        hint.setStyleSheet("color:#888;")
        v.addWidget(hint)
        v.addStretch(1)
        for s in (self.indent_left, self.indent_right, self.indent_first, self.space_before, self.space_after):
            s.valueChanged.connect(self._emit)
        self.chk_hyphen.toggled.connect(self._emit)
        return w

    # --- đọc / ghi ----------------------------------------------------------------------
    def _emit(self, *_):
        if not self._loading:
            self.changed.emit()

    def fields(self) -> dict:
        """Các thông số chữ do bảng này quản lý (khớp tên trường của TextStyle)."""
        align, justify, _ = next((ALIGNS[i] for i, b in enumerate(self.align_buttons) if b.isChecked()), ALIGNS[1])
        style = self.style_box.currentText()
        d = {
            "family": self.font_box.currentFont().family(),
            "style_name": "" if style in ("", "Regular") else style,
            "size": int(round(_combo_value(self.size_box))),
            "leading": _combo_value(self.leading_box),
            "kerning": self.kerning_box.currentData(),
            "tracking": int(_combo_value(self.tracking_box)),
            "vscale": self.vscale_box.value(),
            "hscale": self.hscale_box.value(),
            "baseline_shift": self.baseline_box.value(),
            "color": self.color_btn.color,
            "antialias": self.aa_box.currentData(),
            "align": align,
            "justify": justify,
            "indent_left": self.indent_left.value(),
            "indent_right": self.indent_right.value(),
            "indent_first": self.indent_first.value(),
            "space_before": self.space_before.value(),
            "space_after": self.space_after.value(),
            "hyphenate": self.chk_hyphen.isChecked(),
        }
        d.update({k: b.isChecked() for k, b in self.toggles.items()})
        return d

    def set_style(self, s: TextStyle):
        self._loading = True
        self.font_box.setCurrentFont(QFont(s.family))
        self._fill_styles(QFont(s.family))
        self.style_box.setCurrentText(s.style_name or "Regular")
        _set_combo(self.size_box, s.size, "Tự động", " px")
        _set_combo(self.leading_box, s.leading, AUTO, " px")
        self.kerning_box.setCurrentIndex(max(0, self.kerning_box.findData(s.kerning)))
        _set_combo(self.tracking_box, s.tracking)
        self.vscale_box.setValue(s.vscale)
        self.hscale_box.setValue(s.hscale)
        self.baseline_box.setValue(s.baseline_shift)
        self.color_btn.set_color(s.color)
        self.aa_box.setCurrentIndex(max(0, self.aa_box.findData(s.antialias)))
        for k, b in self.toggles.items():
            b.setChecked(bool(getattr(s, k)))
        idx = next((i for i, (a, j, _) in enumerate(ALIGNS) if j == s.justify and (j or a == s.align)), 1)
        self.align_buttons[idx].setChecked(True)
        self.indent_left.setValue(s.indent_left)
        self.indent_right.setValue(s.indent_right)
        self.indent_first.setValue(s.indent_first)
        self.space_before.setValue(s.space_before)
        self.space_after.setValue(s.space_after)
        self.chk_hyphen.setChecked(s.hyphenate)
        self._loading = False
