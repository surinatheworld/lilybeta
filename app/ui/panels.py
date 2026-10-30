"""Các panel kiểu Photoshop: Layers, History, ô màu trước/màu nền, thanh tuỳ chọn công cụ."""
from __future__ import annotations

import cv2
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.document import BLENDS, Document, Layer
from .theme import TEXT_DIM, glyph_icon

THUMB = 40


def _bgr_icon(bgr) -> QIcon:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
    pm = QPixmap(THUMB, THUMB)
    pm.fill(QColor(38, 38, 38))
    p = QPainter(pm)
    p.drawImage((THUMB - w) // 2, (THUMB - h) // 2, img)
    p.end()
    return QIcon(pm)


def tool_button(glyph: str, tip: str, slot=None, checkable: bool = False) -> QToolButton:
    b = QToolButton()
    b.setIcon(glyph_icon(glyph, 18))
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setAutoRaise(True)
    if slot:
        b.clicked.connect(slot)
    return b


class LayersPanel(QWidget):
    """Danh sách layer (trên cùng ở đầu), ô Blend + Opacity, các nút tạo / nhân đôi / xoá / đổi thứ tự."""

    activated = Signal(object)  # Layer
    visibility = Signal(object, bool)
    renamed = Signal(object, str)
    opacityChanged = Signal(float)
    blendChanged = Signal(str)
    action = Signal(str)  # new | dup | delete | up | down | merge

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc: Document | None = None
        self._building = False
        self._text_icon = glyph_icon("T", THUMB, font="Times New Roman", bold=True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        self.blend = QComboBox()
        for key, label in BLENDS.items():
            self.blend.addItem(label, key)
        self.blend.currentIndexChanged.connect(lambda _: self._building or self.blendChanged.emit(self.blend.currentData()))
        self.opacity = QSpinBox()
        self.opacity.setRange(0, 100)
        self.opacity.setSuffix(" %")
        self.opacity.valueChanged.connect(lambda v: self._building or self.opacityChanged.emit(v / 100))
        top.addWidget(self.blend, 1)
        top.addWidget(QLabel("Opacity:"))
        top.addWidget(self.opacity)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.currentRowChanged.connect(self._row_changed)
        self.list.itemChanged.connect(self._item_changed)
        lay.addWidget(self.list, 1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        for glyph, tip, key in (("▲", "Đưa layer lên (Ctrl+])", "up"), ("▼", "Đưa layer xuống (Ctrl+[)", "down"),
                                ("⤓", "Gộp xuống (Ctrl+E)", "merge"), ("⧉", "Nhân đôi layer (Ctrl+J)", "dup"),
                                ("＋", "Layer mới (Ctrl+Shift+N)", "new"), ("🗑", "Xoá layer", "delete")):
            bottom.addWidget(tool_button(glyph, tip, lambda _=False, k=key: self.action.emit(k)))
        lay.addLayout(bottom)

    def set_doc(self, doc: Document | None) -> None:
        self._doc = doc
        self._building = True
        self.list.clear()
        if doc is not None:
            for L in reversed(doc.layers):
                prefix = "T   " if L.kind == "text" else ""
                icon = self._text_icon if L.kind == "text" else _bgr_icon(doc.thumbnail(L, THUMB))
                item = QListWidgetItem(icon, prefix + L.name + ("  🔒" if L.locked else ""))
                flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsUserCheckable
                if L.kind not in ("background", "redraw"):
                    flags |= Qt.ItemIsEditable
                item.setFlags(flags)
                item.setCheckState(Qt.Checked if L.visible else Qt.Unchecked)
                item.setData(Qt.UserRole, L.id)
                item.setToolTip("Bấm ô vuông để ẩn/hiện · bấm đúp để đổi tên")
                self.list.addItem(item)
                if L is doc.active:
                    self.list.setCurrentItem(item)
            a = doc.active
            self.opacity.setValue(round(a.opacity * 100))
            self.blend.setCurrentIndex(max(0, self.blend.findData(a.blend)))
            self.blend.setEnabled(a.kind == "pixel")
        self._building = False

    def _layer(self, item) -> Layer | None:
        if self._doc is None or item is None:
            return None
        lid = item.data(Qt.UserRole)
        return next((L for L in self._doc.layers if L.id == lid), None)

    def _row_changed(self, row: int):
        if self._building:
            return
        L = self._layer(self.list.item(row))
        if L is not None:
            self.activated.emit(L)

    def _item_changed(self, item: QListWidgetItem):
        if self._building:
            return
        L = self._layer(item)
        if L is None:
            return
        vis = item.checkState() == Qt.Checked
        if vis != L.visible:
            self.visibility.emit(L, vis)
            return
        name = item.text().replace("T   ", "", 1).replace("  🔒", "").strip()
        if name and name != L.name:
            self.renamed.emit(L, name)


class HistoryPanel(QListWidget):
    """Các bước đã làm; bấm một bước để quay về đúng trạng thái đó (bước sau mờ đi, vẫn làm lại được)."""

    jump = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._building = False
        self.currentRowChanged.connect(lambda r: self._building or r < 0 or self.jump.emit(r))

    def set_doc(self, doc: Document | None, opened: str = "Mở") -> None:
        self._building = True
        self.clear()
        if doc is not None:
            self.addItem(QListWidgetItem(glyph_icon("🖼", 16), opened))
            for i, st in enumerate(doc.history):
                item = QListWidgetItem(glyph_icon("◆", 12), st.name)
                if i >= doc.index:
                    item.setForeground(QColor(TEXT_DIM))
                self.addItem(item)
            self.setCurrentRow(doc.index)
            self.scrollToItem(self.currentItem())
        self._building = False


class ColorPair(QWidget):
    """Ô màu trước (foreground) / màu nền (background) ở cuối hộp công cụ. D: mặc định, X: đổi chỗ."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.fg, self.bg = QColor("#000000"), QColor("#ffffff")
        self.setFixedSize(40, 40)
        self.setToolTip("Màu trước / màu nền – bấm để chọn · D: đen/trắng · X: đổi chỗ")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setPen(QPen(QColor("#999"), 1))
        p.setBrush(self.bg)
        p.drawRect(QRectF(15, 15, 22, 22))
        p.setBrush(self.fg)
        p.drawRect(QRectF(3, 3, 22, 22))
        p.end()

    def mousePressEvent(self, e):
        on_fg = e.position().x() < 26 and e.position().y() < 26
        cur = self.fg if on_fg else self.bg
        c = QColorDialog.getColor(cur, self, "Màu trước" if on_fg else "Màu nền")
        if c.isValid():
            if on_fg:
                self.fg = c
            else:
                self.bg = c
            self.update()
            self.changed.emit()

    def reset(self):
        self.fg, self.bg = QColor("#000000"), QColor("#ffffff")
        self.update()
        self.changed.emit()

    def swap(self):
        self.fg, self.bg = self.bg, self.fg
        self.update()
        self.changed.emit()


class SelectionModes(QWidget):
    """Chế độ vùng chọn: mới / cộng (Shift) / trừ (Alt) / giao (Shift+Alt)."""

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.buttons = {}
        for key, glyph, tip in (("new", "▢", "Vùng chọn mới"), ("add", "⊞", "Cộng vào vùng chọn (giữ Shift)"),
                                ("sub", "⊟", "Trừ khỏi vùng chọn (giữ Alt)"), ("inter", "⊠", "Giao với vùng chọn (Shift+Alt)")):
            b = tool_button(glyph, tip, checkable=True)
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k, emit=True))
            lay.addWidget(b)
            self.buttons[key] = b
        self.set_mode("new")

    def set_mode(self, mode: str, emit: bool = False):
        for k, b in self.buttons.items():
            b.setChecked(k == mode)
        if emit:
            self.changed.emit(mode)


def labeled(text: str, w: QWidget) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(6, 0, 6, 0)
    lay.setSpacing(4)
    lab = QLabel(text)
    lab.setStyleSheet(f"color:{TEXT_DIM};")
    lay.addWidget(lab)
    lay.addWidget(w)
    return box


def spin(lo: int, hi: int, value: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


def check(text: str, on: bool) -> QCheckBox:
    c = QCheckBox(text)
    c.setChecked(on)
    return c
