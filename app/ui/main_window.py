"""Cửa sổ chính: mở ảnh → nhận diện chữ → chọn vùng + câu dịch → chọn font → xử lý."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.detect import TextDetector
from ..core.docs import load_lines
from ..core.page import Page, Region
from ..core.typeset import TextStyle, missing_chars
from .image_view import ImageView

IMAGE_FILTER = "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp)"
DOCS_FILTER = "Bản dịch (*.docx *.txt)"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
DOCS_EXTS = (".docx", ".txt")


class DetectWorker(QThread):
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, window: "MainWindow", page: Page, right_to_left: bool):
        super().__init__(window)
        self.window, self.page, self.rtl = window, page, right_to_left

    def run(self):
        try:
            if self.window.detector is None:
                self.window.detector = TextDetector()
            self.page.detect(self.window.detector, self.rtl)
            self.finished_ok.emit()
        except Exception as e:  # noqa: BLE001 - báo lỗi cho người dùng
            self.failed.emit(str(e))


class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color: str):
        super().__init__()
        self.setFixedWidth(60)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color: str):
        self.color = color
        self.setStyleSheet(f"background:{color}; border:1px solid #888; min-height:20px;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self)
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(self.color)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EditManga – Tool type truyện")
        self.resize(1400, 900)
        self.setAcceptDrops(True)
        self.settings = QSettings("EditManga", "EditManga")

        self.detector: TextDetector | None = None
        self.worker: DetectWorker | None = None
        self.page: Page | None = None
        self.selected = -1
        self.lines: list[str] = []
        self.used_lines: set[int] = set()
        self._loading_ui = False

        self.view = ImageView()
        self.view.regionClicked.connect(self.select_region)
        self.view.regionDrawn.connect(self.add_region)

        splitter = QSplitter()
        splitter.addWidget(self.view)
        splitter.addWidget(self._build_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([1000, 400])
        self.setCentralWidget(splitter)
        self._build_toolbar()
        self._load_style_settings()
        self.statusBar().showMessage("Mở ảnh raw (Ctrl+O) hoặc kéo thả ảnh vào cửa sổ.")

    # --- dựng giao diện ----------------------------------------------------
    def _build_toolbar(self):
        tb = QToolBar("Công cụ")
        tb.setMovable(False)
        self.addToolBar(tb)

        def act(text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            return a

        act("Mở ảnh", self.open_image, "Ctrl+O")
        act("Mở bản dịch", self.open_docs, "Ctrl+D")
        tb.addSeparator()
        self.direction = QComboBox()
        self.direction.addItems(["Manga: phải → trái", "Manhwa/Manhua: trái → phải"])
        self.direction.setCurrentIndex(int(self.settings.value("direction", 0)))
        self.direction.currentIndexChanged.connect(lambda i: self.settings.setValue("direction", i))
        tb.addWidget(self.direction)
        self.act_detect = act("Nhận diện chữ", self.run_detect, "F5")
        self.act_boxes = act("Hiện khung", self.toggle_boxes, "B")
        self.act_boxes.setCheckable(True)
        self.act_boxes.setChecked(True)
        act("Vừa khung", self.view.fit_width, "Ctrl+0")
        tb.addSeparator()
        act("Xuất ảnh", self.export_image, "Ctrl+S")

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)

        # Vùng chữ đang chọn
        g_region = QGroupBox("Vùng chữ")
        rl = QVBoxLayout(g_region)
        self.lbl_region = QLabel("Chưa chọn vùng. Bấm vào khung trên ảnh, hoặc kéo chuột để tự vẽ vùng.")
        self.lbl_region.setWordWrap(True)
        rl.addWidget(self.lbl_region)
        self.txt = QPlainTextEdit()
        self.txt.setPlaceholderText("Nội dung sẽ chèn (chọn câu ở danh sách bản dịch bên dưới, hoặc gõ tay). "
                                    "Dùng | hoặc Enter để ép xuống dòng.")
        self.txt.setFixedHeight(80)
        self.txt.textChanged.connect(self._text_edited)
        rl.addWidget(self.txt)
        row = QHBoxLayout()
        self.btn_process = QPushButton("Xử lý  (Ctrl+Enter)")
        self.btn_process.setStyleSheet("font-weight:bold; padding:6px;")
        self.btn_process.clicked.connect(self.process_selected)
        btn_clean = QPushButton("Chỉ xoá chữ")
        btn_clean.clicked.connect(self.clean_selected)
        btn_reset = QPushButton("Hoàn tác vùng")
        btn_reset.clicked.connect(self.reset_selected)
        btn_del = QPushButton("Xoá khung")
        btn_del.clicked.connect(self.delete_selected)
        for b in (self.btn_process, btn_clean, btn_reset, btn_del):
            row.addWidget(b)
        rl.addLayout(row)
        lay.addWidget(g_region)

        # Bản dịch
        g_docs = QGroupBox("Bản dịch")
        dl = QVBoxLayout(g_docs)
        self.lbl_docs = QLabel("Chưa mở file bản dịch (.docx / .txt).")
        self.lbl_docs.setWordWrap(True)
        dl.addWidget(self.lbl_docs)
        self.list_lines = QListWidget()
        self.list_lines.setWordWrap(True)
        self.list_lines.itemClicked.connect(self._line_clicked)
        self.list_lines.itemDoubleClicked.connect(self._line_double_clicked)
        dl.addWidget(self.list_lines)
        hint = QLabel("Bấm một câu để gán cho vùng đang chọn · bấm đúp để gán và xử lý luôn.")
        hint.setStyleSheet("color:#888;")
        hint.setWordWrap(True)
        dl.addWidget(hint)
        lay.addWidget(g_docs, 1)

        # Font & kiểu chữ
        g_font = QGroupBox("Font chữ (lấy từ máy tính)")
        fl = QFormLayout(g_font)
        self.font_box = QFontComboBox()
        self.font_box.setEditable(True)
        self.font_box.currentFontChanged.connect(self._style_changed)
        fl.addRow("Font", self.font_box)
        self.size_box = QSpinBox()
        self.size_box.setRange(0, 300)
        self.size_box.setSpecialValueText("Tự động")
        self.size_box.valueChanged.connect(self._style_changed)
        fl.addRow("Cỡ chữ", self.size_box)
        checks = QHBoxLayout()
        self.chk_bold = QCheckBox("Đậm")
        self.chk_italic = QCheckBox("Nghiêng")
        self.chk_upper = QCheckBox("IN HOA")
        for c in (self.chk_bold, self.chk_italic, self.chk_upper):
            c.toggled.connect(self._style_changed)
            checks.addWidget(c)
        fl.addRow("Kiểu", checks)
        colors = QHBoxLayout()
        self.btn_color = ColorButton("#000000")
        self.btn_color.changed.connect(self._style_changed)
        self.btn_stroke = ColorButton("#ffffff")
        self.btn_stroke.changed.connect(self._style_changed)
        self.stroke_box = QDoubleSpinBox()
        self.stroke_box.setRange(0, 30)
        self.stroke_box.setSingleStep(0.5)
        self.stroke_box.valueChanged.connect(self._style_changed)
        colors.addWidget(QLabel("Chữ"))
        colors.addWidget(self.btn_color)
        colors.addWidget(QLabel("Viền"))
        colors.addWidget(self.btn_stroke)
        colors.addWidget(self.stroke_box)
        fl.addRow("Màu", colors)
        self.spacing_box = QDoubleSpinBox()
        self.spacing_box.setRange(0.6, 2.5)
        self.spacing_box.setSingleStep(0.05)
        self.spacing_box.setValue(1.0)
        self.spacing_box.valueChanged.connect(self._style_changed)
        fl.addRow("Giãn dòng", self.spacing_box)
        self.chk_bubble = QCheckBox("Đặt chữ theo lòng bóng thoại")
        self.chk_bubble.setChecked(True)
        self.chk_bubble.setToolTip("Mở rộng vùng đặt chữ ra cả bóng thoại thay vì chỉ khung chữ gốc "
                                   "(chữ Nhật dọc thường có khung hẹp và cao).")
        self.chk_bubble.toggled.connect(self._bubble_toggled)
        fl.addRow("", self.chk_bubble)
        self.lbl_warn = QLabel()
        self.lbl_warn.setStyleSheet("color:#d9534f;")
        self.lbl_warn.setWordWrap(True)
        fl.addRow(self.lbl_warn)
        lay.addWidget(g_font)

        QShortcut(QKeySequence("Ctrl+Return"), self, self.process_selected)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self.process_selected)
        QShortcut(QKeySequence("Ctrl+Z"), self, self.reset_selected)
        QShortcut(QKeySequence(Qt.Key_Delete), self.view, self.delete_selected)
        QShortcut(QKeySequence(Qt.Key_Tab), self.view, lambda: self._step_region(1))
        return panel

    # --- kiểu chữ ------------------------------------------------------------
    def current_style(self) -> TextStyle:
        return TextStyle(
            family=self.font_box.currentFont().family(),
            size=self.size_box.value(),
            bold=self.chk_bold.isChecked(),
            italic=self.chk_italic.isChecked(),
            uppercase=self.chk_upper.isChecked(),
            color=self.btn_color.color,
            stroke_color=self.btn_stroke.color,
            stroke_width=self.stroke_box.value(),
            line_spacing=self.spacing_box.value(),
        )

    def _apply_style_to_ui(self, s: TextStyle):
        self._loading_ui = True
        self.font_box.setCurrentFont(QFont(s.family))
        self.size_box.setValue(s.size)
        self.chk_bold.setChecked(s.bold)
        self.chk_italic.setChecked(s.italic)
        self.chk_upper.setChecked(s.uppercase)
        self.btn_color.set_color(s.color)
        self.btn_stroke.set_color(s.stroke_color)
        self.stroke_box.setValue(s.stroke_width)
        self.spacing_box.setValue(s.line_spacing)
        self._loading_ui = False

    def _load_style_settings(self):
        s = TextStyle()
        for key, default in vars(TextStyle()).items():
            v = self.settings.value(f"style/{key}")
            if v is not None:
                setattr(s, key, type(default)(v) if not isinstance(default, bool) else v in (True, "true"))
        self._apply_style_to_ui(s)
        self._update_warning()

    def _save_style_settings(self, s: TextStyle):
        for key, v in vars(s).items():
            self.settings.setValue(f"style/{key}", v)

    def _style_changed(self, *_):
        if self._loading_ui:
            return
        style = self.current_style()
        self._save_style_settings(style)
        self._update_warning()
        # Vùng đã xử lý thì đổi kiểu là thấy ngay.
        r = self.region
        if r is not None and r.cleaned:
            r.style = style
            self.refresh()

    def _bubble_toggled(self, on: bool):
        r = self.region
        if r is not None and r.cleaned:
            self.page.clean(r, fit_bubble=on)
            self.refresh()

    def _update_warning(self):
        text = self.txt.toPlainText() or "Ẩ ầ ặ ễ ộ ở ự ỹ đ Ư Ơ"
        miss = missing_chars(self.current_style(), text)
        self.lbl_warn.setText(f"⚠ Font này thiếu ký tự: {miss}" if miss else "")

    # --- mở file -------------------------------------------------------------
    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Mở ảnh raw", self.settings.value("dir/image", ""), IMAGE_FILTER)
        if path:
            self.load_image(path)

    def load_image(self, path: str):
        try:
            self.page = Page(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Lỗi", str(e))
            return
        self.settings.setValue("dir/image", os.path.dirname(path))
        self.setWindowTitle(f"EditManga – {os.path.basename(path)}")
        self.selected = -1
        self.view.set_image(self.page.render(), fit=True)
        self.refresh()
        self.run_detect()

    def open_docs(self):
        path, _ = QFileDialog.getOpenFileName(self, "Mở bản dịch", self.settings.value("dir/docs", ""), DOCS_FILTER)
        if path:
            self.load_docs(path)

    def load_docs(self, path: str):
        try:
            self.lines = load_lines(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Lỗi", f"Không đọc được bản dịch:\n{e}")
            return
        self.settings.setValue("dir/docs", os.path.dirname(path))
        self.used_lines.clear()
        self.lbl_docs.setText(f"{os.path.basename(path)} – {len(self.lines)} câu")
        self._fill_lines()

    def _fill_lines(self):
        self.list_lines.clear()
        for i, line in enumerate(self.lines):
            item = QListWidgetItem(f"{i + 1}. {line}")
            item.setData(Qt.UserRole, i)
            if i in self.used_lines:
                item.setForeground(QColor("#999"))
                item.setText(f"✓ {i + 1}. {line}")
            self.list_lines.addItem(item)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            ext = os.path.splitext(path)[1].lower()
            if ext in IMAGE_EXTS:
                self.load_image(path)
            elif ext in DOCS_EXTS:
                self.load_docs(path)

    # --- nhận diện -----------------------------------------------------------
    def run_detect(self):
        if self.page is None or (self.worker and self.worker.isRunning()):
            return
        self.act_detect.setEnabled(False)
        self.statusBar().showMessage("Đang nhận diện chữ…")
        self.worker = DetectWorker(self, self.page, self.direction.currentIndex() == 0)
        self.worker.finished_ok.connect(self._detect_done)
        self.worker.failed.connect(self._detect_failed)
        self.worker.start()

    def _detect_done(self):
        self.act_detect.setEnabled(True)
        n = len(self.page.regions)
        self.statusBar().showMessage(f"Tìm thấy {n} vùng chữ. Bấm vào một khung để chọn, kéo chuột để vẽ thêm.")
        self.select_region(0 if n else -1)

    def _detect_failed(self, msg: str):
        self.act_detect.setEnabled(True)
        self.statusBar().showMessage("Nhận diện lỗi.")
        QMessageBox.warning(self, "Lỗi nhận diện", msg)

    # --- vùng chữ ------------------------------------------------------------
    @property
    def region(self) -> Region | None:
        if self.page and 0 <= self.selected < len(self.page.regions):
            return self.page.regions[self.selected]
        return None

    def select_region(self, idx: int):
        self.selected = idx
        r = self.region
        self._loading_ui = True
        if r is None:
            self.lbl_region.setText("Chưa chọn vùng.")
            self.txt.setPlainText("")
        else:
            state = "đã xử lý" if r.done else ("đã xoá chữ" if r.cleaned else "chưa xử lý")
            self.lbl_region.setText(f"Vùng #{idx + 1} / {len(self.page.regions)} – {state}")
            text = r.text
            if not text:
                nxt = self._next_unused_line()
                if nxt is not None:
                    text = self.lines[nxt]
                    self._highlight_line(nxt)
            self.txt.setPlainText(text)
        self._loading_ui = False
        if r is not None and r.cleaned:
            self._apply_style_to_ui(r.style)
        self._update_warning()
        self.view.set_regions(self.page.regions if self.page else [], self.selected)

    def add_region(self, box):
        if self.page is None:
            return
        self.page.add_region(box)
        self.select_region(len(self.page.regions) - 1)

    def _step_region(self, d: int):
        if self.page and self.page.regions:
            self.select_region((self.selected + d) % len(self.page.regions))

    def _text_edited(self):
        if self._loading_ui:
            return
        self._update_warning()
        r = self.region
        if r is not None and r.cleaned:
            r.text = self.txt.toPlainText()
            self.refresh()

    def process_selected(self):
        r = self.region
        if r is None:
            QMessageBox.information(self, "Chưa chọn vùng", "Hãy bấm chọn một khung chữ trên ảnh trước.")
            return
        r.text = self.txt.toPlainText()
        r.style = self.current_style()
        self.page.clean(r, fit_bubble=self.chk_bubble.isChecked())
        line = self._line_of_text(r.text)
        if line is not None:
            self.used_lines.add(line)
            self._fill_lines()
        self.refresh()
        self._goto_next_pending()

    def clean_selected(self):
        r = self.region
        if r is None:
            return
        self.page.clean(r, fit_bubble=self.chk_bubble.isChecked())
        r.text = ""
        self.refresh()
        self.select_region(self.selected)

    def reset_selected(self):
        r = self.region
        if r is None:
            return
        line = self._line_of_text(r.text)
        if line is not None:
            self.used_lines.discard(line)
            self._fill_lines()
        self.page.reset(r)
        self.refresh()
        self.select_region(self.selected)

    def delete_selected(self):
        r = self.region
        if r is None:
            return
        self.page.remove_region(r)
        self.refresh()
        self.select_region(min(self.selected, len(self.page.regions) - 1))

    def _goto_next_pending(self):
        regions = self.page.regions
        for k in range(1, len(regions) + 1):
            i = (self.selected + k) % len(regions)
            if not regions[i].done:
                self.select_region(i)
                return
        self.select_region(self.selected)
        self.statusBar().showMessage("Đã xử lý hết các vùng. Bấm Xuất ảnh (Ctrl+S) để lưu.")

    # --- danh sách bản dịch ------------------------------------------------
    def _next_unused_line(self) -> int | None:
        for i in range(len(self.lines)):
            if i not in self.used_lines:
                return i
        return None

    def _line_of_text(self, text: str) -> int | None:
        text = text.strip()
        for i, line in enumerate(self.lines):
            if line == text:
                return i
        return None

    def _highlight_line(self, i: int):
        item = self.list_lines.item(i)
        if item:
            self.list_lines.setCurrentItem(item)
            self.list_lines.scrollToItem(item)

    def _line_clicked(self, item: QListWidgetItem):
        self.txt.setPlainText(self.lines[item.data(Qt.UserRole)])

    def _line_double_clicked(self, item: QListWidgetItem):
        self._line_clicked(item)
        self.process_selected()

    # --- hiển thị & xuất -----------------------------------------------------
    def toggle_boxes(self):
        self.view.show_boxes = self.act_boxes.isChecked()
        self.refresh()

    def refresh(self):
        if self.page is None:
            return
        self.view.set_image(self.page.render())
        self.view.set_regions(self.page.regions, self.selected)

    def export_image(self):
        if self.page is None:
            return
        base, _ = os.path.splitext(self.page.path)
        path, _ = QFileDialog.getSaveFileName(self, "Xuất ảnh", base + "_typed.png", "PNG (*.png);;JPG (*.jpg)")
        if not path:
            return
        try:
            self.page.export(path)
            self.statusBar().showMessage(f"Đã lưu: {path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Lỗi", str(e))
