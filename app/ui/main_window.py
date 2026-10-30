"""Cửa sổ chính: mở ảnh → nhận diện chữ → chọn vùng + câu dịch → chọn font → xử lý."""
from __future__ import annotations

import json
import os
from dataclasses import asdict

from PySide6.QtCore import QSettings, QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QInputDialog,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
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
    QScrollArea,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core import cloud, lama, secrets
from ..core.detect import TextDetector
from ..core.assign import apply_plan, assign_lines
from ..core.chapter import Chapter
from ..core.document import Stroke, doc_for
from ..core.clean import skip_hard, small_only
from ..core.docs import load_lines, load_pages, page_number
from ..core.psd_export import PhotoshopExporter, export_without_photoshop, photoshop_available
from ..core.page import Page, Region
from ..core.typeset import TextStyle, missing_chars
from ..core import manual_ai
from .ai_bridge import AiBridgeDialog
from .api_dialog import PROVIDERS, ApiDialog, model_for
from .image_view import (
    PAINT_TOOLS,
    TOOL_BRUSH,
    TOOL_CLONE,
    TOOL_ERASER,
    TOOL_HAND,
    TOOL_HEAL,
    TOOL_LASSO,
    TOOL_LASSO_SEL,
    TOOL_MARQUEE,
    TOOL_MOVE,
    TOOL_RECT,
    TOOL_TEXT,
    TOOL_WAND,
    TOOL_ZOOM,
    ImageView,
)
from .panels import ColorPair, HistoryPanel, LayersPanel, SelectionModes, check, labeled, spin
from .theme import glyph_icon
from .text_panel import TextPanel

IMAGE_FILTER = "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp)"
DOCS_FILTER = "Bản dịch (*.docx *.txt)"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
DOCS_EXTS = (".docx", ".txt")


class DetectWorker(QThread):
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, window: "MainWindow", page: Page, right_to_left: bool, auto=None):
        super().__init__(window)
        self.window, self.page, self.rtl = window, page, right_to_left
        self.auto = auto  # (inpainter, xoá SFX nhỏ?, đặt chữ theo lòng bóng?) hoặc None = không tự xoá
        self.cleaned = 0

    def run(self):
        try:
            if self.window.detector is None:
                self.window.detector = TextDetector()
            self.page.detect(self.window.detector, self.rtl)
            if self.auto is not None:
                self.page.inpainter, small, fit = self.auto
                self.cleaned = self.page.auto_clean(small, fit)
            self.finished_ok.emit()
        except Exception as e:  # noqa: BLE001 - báo lỗi cho người dùng
            self.failed.emit(str(e))


class LamaLoader(QThread):
    """Nạp sẵn model AI vẽ nền ở luồng nền (mất ~30-40 giây)."""

    done = Signal(str)

    def run(self):
        try:
            lama.get_session()
            self.done.emit("")
        except Exception as e:  # noqa: BLE001
            self.done.emit(str(e))


class BatchWorker(QThread):
    """Tác vụ dài cho cả chương (nhận diện, xử lý, xuất PSD) ở luồng nền; báo tiến độ từng bước."""

    progress = Signal(str)
    failed = Signal(str)

    def __init__(self, parent, job):
        super().__init__(parent)
        self.job = job

    def run(self):
        try:
            self.job(self.progress.emit)
        except Exception as e:  # noqa: BLE001 - báo lỗi cho người dùng
            self.failed.emit(str(e))


class CleanWorker(QThread):
    """Xoá chữ + vẽ lại nền ở luồng nền (AI trên mạng có thể mất vài chục giây)."""

    failed = Signal(str)

    def __init__(self, parent, job):
        super().__init__(parent)
        self.job = job

    def run(self):
        try:
            self.job()
        except Exception as e:  # noqa: BLE001 - báo lỗi cho người dùng
            self.failed.emit(str(e))


ENGINES = [
    ("auto", "Tự động – bóng thoại + SFX nhỏ (LaMa), chữ trên tranh để redraw"),
    ("simple", "Chỉ xoá chỗ đơn giản – nền khó để nguyên, tự redraw sau"),
    ("opencv", "Thường (OpenCV) – nhanh, nền tranh hơi mờ"),
    ("lama", "AI LaMa – chạy trên máy, miễn phí"),
    ("gemini", "AI Google Gemini – cần API key"),
    ("openai", "AI OpenAI (ChatGPT) – cần API key"),
]
LOCAL_AI = ("auto", "lama")  # các lựa chọn dùng LaMa trên máy

# Hộp công cụ: (công cụ, ký tự icon, mô tả, phím tắt)
TOOLS = [
    (TOOL_MOVE, "✥", "Move – kéo khung chữ để di chuyển chữ", "V"),
    (TOOL_MARQUEE, "⬚", "Rectangular Marquee – vùng chọn chữ nhật", "M"),
    (TOOL_LASSO_SEL, "➰", "Lasso – vùng chọn tự do", "L"),
    (TOOL_WAND, "✦", "Magic Wand – chọn vùng cùng màu", "W"),
    (TOOL_HEAL, "✚", "Spot Healing – tô lên chỗ cần xoá, AI vẽ lại khi nhả chuột", "J"),
    (TOOL_BRUSH, "✎", "Brush – cọ (màu trước)", "B"),
    (TOOL_CLONE, "⎘", "Clone Stamp – Alt+bấm lấy điểm mẫu rồi tô", "S"),
    (TOOL_ERASER, "⌫", "Eraser – tẩy (trên layer Redraw: trả về ảnh raw)", "E"),
    (TOOL_TEXT, "T", "Type – bấm vào bóng thoại để gõ chữ, kéo để tạo khung đoạn văn", "T"),
    (TOOL_RECT, "▣", "Khung chữ – vẽ khung để xoá chữ gốc & đặt bản dịch", "K"),
    (TOOL_LASSO, "◌", "Khung chữ lasso – khoanh tự do quanh chữ/SFX", "Shift+K"),
    (TOOL_HAND, "✋", "Hand – kéo ảnh (hoặc giữ Space)", "H"),
    (TOOL_ZOOM, "⌕", "Zoom – bấm phóng to, Alt+bấm thu nhỏ", "Z"),
]
TOOL_NAMES = {TOOL_BRUSH: "Brush", TOOL_ERASER: "Eraser", TOOL_CLONE: "Clone Stamp", TOOL_HEAL: "Spot Healing"}
PAINT_DEFAULTS = {TOOL_BRUSH: [20, 80, 100], TOOL_ERASER: [40, 80, 100], TOOL_CLONE: [40, 60, 100],
                  TOOL_HEAL: [30, 80, 100]}  # cỡ, độ cứng %, độ mờ %


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
        self.setWindowTitle("Lilyzono – Tool type truyện")
        self.resize(1400, 900)
        self.setAcceptDrops(True)
        self.settings = QSettings("Lilyzono", "Lilyzono")
        if not self.settings.allKeys():  # lần đầu sau khi đổi tên: chép cài đặt cũ (font yêu thích, bộ kiểu…)
            old = QSettings("EditManga", "EditManga")
            for k in old.allKeys():
                self.settings.setValue(k, old.value(k))

        self.detector: TextDetector | None = None
        self.worker: DetectWorker | None = None
        self.clean_worker: CleanWorker | None = None
        self.page: Page | None = None
        self.chapter: Chapter | None = None
        self.batch: BatchWorker | None = None
        self.page_lines = False  # danh sách câu đang hiện là câu của riêng trang này (docs chia theo trang)
        self.selected = -1  # vùng chính (bấm sau cùng)
        self.selection: list[int] = []  # các vùng đang chọn (chọn nhiều bằng Ctrl/Shift + bấm)
        self.lines: list[str] = []
        self.used_lines: set[int] = set()
        self._suggested_text: str | None = None  # câu dịch tự điền sẵn (chưa phải người dùng chọn)
        self._loading_ui = False

        self.doc = None  # tài liệu nhiều layer của trang đang mở
        self.stroke: Stroke | None = None
        self.clone_src: tuple[float, float] | None = None
        self.clone_offset: tuple[int, int] | None = None
        self.paint_opts = {k: list(v) for k, v in PAINT_DEFAULTS.items()}
        self._busy = False

        self.view = ImageView()
        self.view.regionClicked.connect(self._region_clicked)
        self.view.textRectChanged.connect(self._text_rect_dragged)
        self.view.regionDrawn.connect(self.add_region)
        self.view.lassoDrawn.connect(self.add_lasso)
        self.view.selectionMade.connect(self._selection_made)
        self.view.strokeBegan.connect(self._stroke_began)
        self.view.strokeMoved.connect(self._stroke_to)
        self.view.strokeEnded.connect(self._stroke_ended)
        self.view.cloneSourceSet.connect(self._clone_source)
        self.view.textClicked.connect(self._text_clicked)
        self.view.textBoxDrawn.connect(self._text_box_drawn)
        self.view.zoomChanged.connect(lambda z: self.lbl_zoom.setText(f"{z * 100:.0f}%"))
        self.view.cursorMoved.connect(lambda x, y: self.lbl_pos.setText(f"X {x}  Y {y}"))
        self.setCentralWidget(self.view)

        lily, textp = self._build_panel()
        self.layers_panel = LayersPanel()
        self.layers_panel.activated.connect(self._layer_activated)
        self.layers_panel.visibility.connect(self._layer_visibility)
        self.layers_panel.renamed.connect(self._layer_renamed)
        self.layers_panel.opacityChanged.connect(self._layer_opacity)
        self.layers_panel.blendChanged.connect(self._layer_blend)
        self.layers_panel.action.connect(self.layer_action)
        self.history_panel = HistoryPanel()
        self.history_panel.jump.connect(self._history_jump)
        self.page_list = QListWidget()
        self.page_list.currentRowChanged.connect(self._page_row_changed)

        def dock(title, widget, name, scroll=False):
            if scroll:
                area = QScrollArea()
                area.setWidget(widget)
                area.setWidgetResizable(True)
                area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                widget = area
            d = QDockWidget(title, self)
            d.setObjectName(name)
            d.setWidget(widget)
            d.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable
                          | QDockWidget.DockWidgetClosable)
            d.setTitleBarWidget(QWidget())  # chỉ hiện tab tên panel (kéo tab để sắp xếp; bật/tắt ở menu Cửa sổ)
            return d

        self.d_lily = dock("Lilyzono – dịch truyện", lily, "lily", scroll=True)
        self.d_text = dock("Character / Paragraph", textp, "text", scroll=True)
        self.d_layers = dock("Layers", self.layers_panel, "layers")
        self.d_history = dock("History", self.history_panel, "history")
        self.page_dock = dock("Trang", self.page_list, "pages")
        for d in (self.d_lily, self.d_text, self.d_layers, self.d_history, self.page_dock):
            d.setMinimumWidth(330)
        self.setTabPosition(Qt.AllDockWidgetAreas, QTabWidget.North)
        self.setDockOptions(QMainWindow.AnimatedDocks | QMainWindow.AllowTabbedDocks | QMainWindow.GroupedDragging)
        self.addDockWidget(Qt.RightDockWidgetArea, self.d_lily)
        self.splitDockWidget(self.d_lily, self.d_layers, Qt.Vertical)
        self.tabifyDockWidget(self.d_lily, self.d_text)
        self.tabifyDockWidget(self.d_layers, self.d_history)
        self.tabifyDockWidget(self.d_layers, self.page_dock)
        self.d_lily.raise_()
        self.d_layers.raise_()
        self.resizeDocks([self.d_lily, self.d_layers], [560, 300], Qt.Vertical)
        self.resizeDocks([self.d_lily], [400], Qt.Horizontal)

        self._build_menus()
        self._build_toolbox()
        self._build_options()
        self.lbl_pos, self.lbl_size, self.lbl_zoom = QLabel(), QLabel(), QLabel()
        for w in (self.lbl_pos, self.lbl_size, self.lbl_zoom):
            w.setMinimumWidth(90)
            self.statusBar().addPermanentWidget(w)
        self._load_style_settings()
        self._update_ai_label()
        self.set_tool(self.settings.value("tool", TOOL_MOVE))
        self.statusBar().showMessage("Mở ảnh raw (Ctrl+O), cả chương (Ctrl+Shift+O) hoặc kéo thả ảnh vào cửa sổ.")

    # --- dựng giao diện ----------------------------------------------------
    def _action(self, menu, text, slot, shortcut=None, checkable=False, checked=False, tip=None):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            keys = shortcut if isinstance(shortcut, (list, tuple)) else [shortcut]
            a.setShortcuts([QKeySequence(k) for k in keys])
        if checkable:
            a.setCheckable(True)
            a.setChecked(checked)
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(tip)
        menu.addAction(a)
        return a

    def _build_menus(self):
        mb = self.menuBar()
        A = self._action
        m = mb.addMenu("&Tệp")
        A(m, "Mở ảnh…", self.open_image, "Ctrl+O")
        A(m, "Mở chương (thư mục ảnh)…", self.open_chapter, "Ctrl+Shift+O")
        A(m, "Mở bản dịch (.docx / .txt)…", self.open_docs, "Ctrl+Alt+D")
        A(m, "Upload bản redraw…", self.open_redrawn, "Ctrl+Shift+U",
          tip="Ảnh đã xoá chữ/redraw sẵn thành layer Redraw (trên raw, dưới chữ)")
        m.addSeparator()
        A(m, "Lưu PSD…", self.export_psd_page, "Ctrl+S")
        A(m, "Xuất JPEG…", self.export_image, ["Ctrl+Alt+Shift+W", "Ctrl+Shift+S"])
        self.act_export_all = A(m, "Xuất cả chương (PSD / JPEG)…", self.export_psd_chapter)
        m.addSeparator()
        A(m, "Thoát", self.close, "Ctrl+Q")

        m = mb.addMenu("&Sửa")
        A(m, "Hoàn tác", self.undo, "Ctrl+Z")
        A(m, "Làm lại", self.redo, ["Ctrl+Shift+Z", "Ctrl+Y"])
        A(m, "Lùi một bước", self.undo, "Ctrl+Alt+Z")
        m.addSeparator()
        A(m, "Tô vùng chọn (Fill)…", self.fill_dialog, ["Shift+F5", "Shift+Backspace"])
        A(m, "Content-Aware Fill (AI LaMa)", lambda: self.content_aware())
        A(m, "Tô màu trước", lambda: self.fill_color(True), "Alt+Backspace")
        A(m, "Tô màu nền", lambda: self.fill_color(False), "Ctrl+Backspace")
        A(m, "Xoá (Delete)", self.delete_key, "Delete")
        m.addSeparator()
        A(m, "API key AI (Gemini / OpenAI)…", self.open_api_dialog)

        m = mb.addMenu("&Layer")
        A(m, "Layer mới", lambda: self.layer_action("new"), "Ctrl+Shift+N")
        A(m, "Nhân đôi layer", lambda: self.layer_action("dup"), "Ctrl+J")
        A(m, "Xoá layer", lambda: self.layer_action("delete"))
        m.addSeparator()
        A(m, "Đưa lên", lambda: self.layer_action("up"), "Ctrl+]")
        A(m, "Đưa xuống", lambda: self.layer_action("down"), "Ctrl+[")
        A(m, "Gộp xuống", lambda: self.layer_action("merge"), "Ctrl+E")

        m = mb.addMenu("&Chọn")
        A(m, "Chọn tất cả", self.select_all_pixels, "Ctrl+A")
        A(m, "Bỏ chọn", self.deselect, "Ctrl+D")
        A(m, "Đảo vùng chọn", self.invert_selection, "Ctrl+Shift+I")
        m.addSeparator()
        A(m, "Làm mềm (Feather)…", self.feather_dialog, "Shift+F6")
        A(m, "Mở rộng…", lambda: self.grow_dialog(1))
        A(m, "Thu hẹp…", lambda: self.grow_dialog(-1))
        m.addSeparator()
        A(m, "Vùng chọn từ khung chữ đang chọn", self.selection_from_regions)
        A(m, "Tạo khung chữ từ vùng chọn", self.region_from_selection)

        m = mb.addMenu("C&hữ")
        self.act_detect = A(m, "Nhận diện chữ", self.run_detect, "F5")
        A(m, "Xử lý khung đang chọn (xoá chữ + đặt bản dịch)", self.process_selected, ["Ctrl+Return", "Ctrl+Enter"])
        A(m, "Chỉ xoá chữ", self.clean_selected)
        A(m, "Hoàn tác khung (trả ảnh gốc, bỏ chữ)", self.reset_selected)
        A(m, "Redraw bằng ChatGPT / Gemini…", self.open_ai_bridge, "Ctrl+Shift+R")
        m.addSeparator()
        A(m, "Chọn mọi khung chữ", self.select_all, "Ctrl+Alt+A")
        A(m, "Chọn mọi khung đã có bản dịch", self.select_translated, "Ctrl+Shift+A")
        A(m, "Khung tiếp theo", lambda: self._step_region(1), "Tab")
        A(m, "Tự điền bản dịch cho trang này", self._auto_fill)
        m.addSeparator()
        self.act_process_all = A(m, "Xử lý cả chương", self.process_chapter)

        m = mb.addMenu("&Xem")
        A(m, "Phóng to", lambda: self.view.zoom_by(1.25), ["Ctrl+=", "Ctrl++"])
        A(m, "Thu nhỏ", lambda: self.view.zoom_by(0.8), "Ctrl+-")
        A(m, "Vừa màn hình", self.view.fit_page, "Ctrl+0")
        A(m, "Vừa chiều ngang", self.view.fit_width, "Ctrl+Alt+0")
        A(m, "100%", lambda: self.view.zoom_to(1.0), "Ctrl+1")
        m.addSeparator()
        self.act_boxes = A(m, "Hiện khung chữ", self.toggle_boxes, "Ctrl+H", checkable=True, checked=True)

        m = mb.addMenu("Cửa &sổ")
        for d in (self.d_layers, self.d_history, self.d_lily, self.d_text, self.page_dock):
            m.addAction(d.toggleViewAction())

        m = mb.addMenu("Trợ &giúp")
        A(m, "Phím tắt", self.show_shortcuts, "F1")

    def _build_toolbox(self):
        tb = QToolBar("Công cụ")
        tb.setObjectName("tools")
        tb.setOrientation(Qt.Vertical)
        tb.setMovable(False)
        tb.setIconSize(QSize(22, 22))
        self.addToolBar(Qt.LeftToolBarArea, tb)
        group = QActionGroup(self)
        self.tool_actions = {}
        for tool, glyph, tip, key in TOOLS:
            icon = glyph_icon(glyph, 22, font="Times New Roman", bold=True) if glyph == "T" else glyph_icon(glyph, 22)
            a = QAction(icon, f"{tip} ({key})", self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(key))
            a.triggered.connect(lambda _=False, t=tool: self.set_tool(t))
            group.addAction(a)
            tb.addAction(a)
            self.tool_actions[tool] = a
        tb.addSeparator()
        self.colors = ColorPair()
        tb.addWidget(self.colors)
        for key, slot in (("D", self.colors.reset), ("X", self.colors.swap),
                          ("[", lambda: self._brush_key(-1)), ("]", lambda: self._brush_key(1))):
            QShortcut(QKeySequence(key), self, slot)

    def _build_options(self):
        ob = QToolBar("Tuỳ chọn công cụ")
        ob.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, ob)
        self.opt_icon = QLabel()
        ob.addWidget(self.opt_icon)
        self.opt_stack = QStackedWidget()
        ob.addWidget(self.opt_stack)
        self._opt_pages = {}

        def page(*widgets):
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(4, 0, 4, 0)
            for x in widgets:
                if x == "|":
                    sep = QLabel("│")
                    sep.setStyleSheet("color:#555;")
                    lay.addWidget(sep)
                else:
                    lay.addWidget(x)
            lay.addStretch(1)
            self.opt_stack.addWidget(w)
            return self.opt_stack.count() - 1

        def button(text, slot, tip=None):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if tip:
                b.setToolTip(tip)
            return b

        def hint(text):
            lab = QLabel(text)
            lab.setStyleSheet("color:#8f8f8f;")
            return lab

        # Move
        self.opt_boxes = check("Hiện khung chữ (Ctrl+H)", True)
        self.opt_boxes.toggled.connect(lambda on: (self.act_boxes.setChecked(on), self.toggle_boxes()))
        i_move = page(self.opt_boxes, "|", hint("Bấm khung để chọn · kéo khung tím để di chuyển chữ, góc để đổi cỡ · "
                                                "mũi tên: nhích 1px (Shift: 10px)"))
        # Vùng chọn
        self.sel_modes = [SelectionModes(), SelectionModes()]
        for sm in self.sel_modes:
            sm.changed.connect(self._sel_mode_changed)
        self.opt_feather = spin(0, 250, 0, " px")
        i_sel = page(self.sel_modes[0], "|", labeled("Feather:", self.opt_feather), "|",
                     button("Content-Aware Fill", lambda: self.content_aware(), "Vẽ lại phần trong vùng chọn bằng AI LaMa"),
                     button("Tô…", self.fill_dialog), button("Tạo khung chữ", self.region_from_selection,
                                                            "Biến vùng chọn thành khung chữ để xoá chữ & đặt bản dịch"))
        self.opt_tol = spin(0, 255, 32)
        self.opt_contig = check("Liền kề", True)
        self.opt_sample = check("Lấy mẫu mọi layer", True)
        i_wand = page(self.sel_modes[1], "|", labeled("Dung sai:", self.opt_tol), self.opt_contig, self.opt_sample, "|",
                      button("Content-Aware Fill", lambda: self.content_aware()))
        # Cọ / tẩy / clone / healing
        self.opt_size = spin(1, 1000, 20, " px")
        self.opt_hard = spin(0, 100, 80, " %")
        self.opt_opacity = spin(1, 100, 100, " %")
        for w in (self.opt_size, self.opt_hard, self.opt_opacity):
            w.valueChanged.connect(self._paint_opts_changed)
        self.opt_paint_hint = hint("")
        i_paint = page(labeled("Cỡ:", self.opt_size), labeled("Độ cứng:", self.opt_hard),
                       labeled("Opacity:", self.opt_opacity), "|", self.opt_paint_hint)
        # Chữ
        i_text = page(hint("Bấm vào bóng thoại để tạo chữ rồi gõ ở ô nội dung (panel Lilyzono) · kéo chuột để tạo "
                           "khung đoạn văn · font/cỡ/căn lề ở panel Character / Paragraph"), "|",
                      button("Xử lý (Ctrl+Enter)", self.process_selected))
        # Khung chữ
        i_region = page(button("Nhận diện chữ (F5)", self.run_detect), button("Xử lý (Ctrl+Enter)", self.process_selected),
                        button("Chỉ xoá chữ", self.clean_selected), "|",
                        hint("Kéo để vẽ khung (lasso: khoanh tự do – toàn bộ phần khoanh bị xoá) · bấm để chọn khung"))
        # Hand / Zoom
        i_view = page(button("100%", lambda: self.view.zoom_to(1.0)), button("Vừa màn hình", self.view.fit_page),
                      button("Vừa chiều ngang", self.view.fit_width))
        self._opt_pages = {TOOL_MOVE: i_move, TOOL_MARQUEE: i_sel, TOOL_LASSO_SEL: i_sel, TOOL_WAND: i_wand,
                           TOOL_TEXT: i_text, TOOL_RECT: i_region, TOOL_LASSO: i_region, TOOL_HAND: i_view,
                           TOOL_ZOOM: i_view, **{t: i_paint for t in PAINT_TOOLS}}

    def set_tool(self, tool: str):
        if tool not in self.tool_actions:
            tool = TOOL_MOVE
        self.view.set_tool(tool)
        self.tool_actions[tool].setChecked(True)
        self.settings.setValue("tool", tool)
        self.opt_stack.setCurrentIndex(self._opt_pages[tool])
        glyph = next(g for t, g, _, _ in TOOLS if t == tool)
        self.opt_icon.setPixmap(glyph_icon(glyph, 20).pixmap(20, 20))
        if tool in PAINT_TOOLS:
            size, hard, op = self.paint_opts[tool]
            for w, v in ((self.opt_size, size), (self.opt_hard, hard), (self.opt_opacity, op)):
                w.blockSignals(True)
                w.setValue(v)
                w.blockSignals(False)
            self.view.set_brush_size(size)
            self.opt_paint_hint.setText({
                TOOL_BRUSH: "Vẽ màu trước lên layer đang chọn · [ ]: đổi cỡ",
                TOOL_ERASER: "Layer Redraw: tẩy = trả về ảnh raw · layer ảnh: xoá thành trong suốt",
                TOOL_CLONE: "Alt+bấm để lấy điểm mẫu, rồi tô (giữ khoảng cách mẫu giữa các nét)",
                TOOL_HEAL: "Tô kín chỗ cần xoá (chữ, SFX…), nhả chuột: AI LaMa vẽ lại theo nền xung quanh",
            }[tool])

    def _paint_opts_changed(self, *_):
        tool = self.view.tool
        if tool in PAINT_TOOLS:
            self.paint_opts[tool] = [self.opt_size.value(), self.opt_hard.value(), self.opt_opacity.value()]
            self.view.set_brush_size(self.opt_size.value())

    def _brush_key(self, d: int):
        if self.view.tool in PAINT_TOOLS:
            v = self.opt_size.value()
            self.opt_size.setValue(max(1, v + d * max(1, round(v * 0.1))))

    def _sel_mode_changed(self, mode: str):
        self.view.sel_mode = mode
        for sm in self.sel_modes:
            sm.set_mode(mode)

    def show_shortcuts(self):
        rows = [f"{key}\t{tip}" for _, _, tip, key in TOOLS] + [
            "Space (giữ)\tKéo ảnh", "Ctrl / Alt + lăn chuột\tPhóng to / thu nhỏ", "Ctrl+0 / Ctrl+1\tVừa màn hình / 100%",
            "[ ]\tĐổi cỡ cọ", "D / X\tMàu mặc định / đổi chỗ màu", "Ctrl+Z / Ctrl+Shift+Z\tHoàn tác / làm lại",
            "Ctrl+A / Ctrl+D / Ctrl+Shift+I\tChọn tất cả / bỏ chọn / đảo", "Shift / Alt khi chọn\tCộng / trừ vùng chọn",
            "Shift+F5\tTô vùng chọn (có Content-Aware)", "Delete\tXoá điểm trong vùng chọn (không có vùng chọn: xoá khung)",
            "Alt+Backspace / Ctrl+Backspace\tTô màu trước / màu nền", "Ctrl+Shift+N / Ctrl+J / Ctrl+E\tLayer mới / nhân đôi / gộp",
            "Ctrl+] / Ctrl+[\tĐưa layer lên / xuống", "F5\tNhận diện chữ", "Ctrl+Enter\tXử lý khung đang chọn",
            "Tab\tKhung tiếp theo", "Ctrl+Shift+A\tChọn mọi khung có bản dịch", "Ctrl+S\tLưu PSD",
            "Ctrl+Alt+Shift+W\tXuất JPEG"]
        QMessageBox.information(self, "Phím tắt", "\n".join(rows))

    def _build_panel(self) -> tuple[QWidget, QWidget]:
        """Panel Lilyzono (khung chữ, bản dịch) và panel Character / Paragraph (kiểu chữ, hiệu ứng, vẽ nền)."""
        panel = QWidget()
        lay = QVBoxLayout(panel)
        self.direction = QComboBox()
        self.direction.addItems(["Tiếng Nhật (manga): phải → trái", "Tiếng Anh / manhwa: trái → phải"])
        self.direction.setCurrentIndex(int(self.settings.value("direction", 0)))
        self.direction.currentIndexChanged.connect(lambda i: self.settings.setValue("direction", i))
        drow = QHBoxLayout()
        drow.addWidget(QLabel("Raw:"))
        drow.addWidget(self.direction, 1)
        lay.addLayout(drow)

        # Vùng chữ đang chọn
        g_region = QGroupBox("Vùng chữ")
        rl = QVBoxLayout(g_region)
        self.lbl_region = QLabel("Chưa chọn vùng. Bấm vào khung trên ảnh, hoặc kéo chuột để tự vẽ vùng "
                                 "(khung chữ nhật hoặc lasso).")
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
        row.addWidget(self.btn_process, 1)
        row.addWidget(btn_clean)
        rl.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(btn_reset)
        row.addWidget(btn_del)
        rl.addLayout(row)
        btn_ai = QPushButton("Redraw bằng ChatGPT / Gemini…  (Ctrl+Shift+R)")
        btn_ai.setToolTip("Cắt sẵn vùng quanh chữ để dán vào ChatGPT/Gemini (gói Plus/Pro trên web), "
                          "rồi dán kết quả về: tool tự căn khớp và chỉ thay phần chữ")
        btn_ai.clicked.connect(self.open_ai_bridge)
        rl.addWidget(btn_ai)
        btn_sel = QPushButton("Chọn mọi khung đã có bản dịch  (Ctrl+Shift+A)")
        btn_sel.setToolTip("Chọn hết các khung có chữ dịch để đổi font / kiểu chữ hàng loạt")
        btn_sel.clicked.connect(self.select_translated)
        rl.addWidget(btn_sel)
        lay.addWidget(g_region)

        # Bản dịch
        g_docs = QGroupBox("Bản dịch")
        dl = QVBoxLayout(g_docs)
        self.lbl_docs = QLabel("Chưa mở file bản dịch (.docx / .txt).")
        self.lbl_docs.setWordWrap(True)
        dl.addWidget(self.lbl_docs)
        self.list_lines = QListWidget()
        self.list_lines.setWordWrap(True)
        self.list_lines.setMinimumHeight(160)
        self.list_lines.itemClicked.connect(self._line_clicked)
        self.list_lines.itemDoubleClicked.connect(self._line_double_clicked)
        dl.addWidget(self.list_lines)
        hint = QLabel("Bấm một câu để gán cho vùng đang chọn · Ctrl+bấm để nối thêm câu · bấm đúp để gán và xử lý luôn.")
        hint.setStyleSheet("color:#888;")
        hint.setWordWrap(True)
        dl.addWidget(hint)
        lay.addWidget(g_docs, 1)

        panel2 = QWidget()
        tlay = QVBoxLayout(panel2)
        # Chữ: bảng Ký tự / Đoạn kiểu Photoshop
        g_text = QGroupBox("Chữ (Character / Paragraph)")
        tl = QVBoxLayout(g_text)
        self.text_panel = TextPanel()
        self.text_panel.changed.connect(self._style_changed)
        self.font_box = self.text_panel.font_box
        self.btn_star = self.text_panel.btn_star
        self.font_box.currentFontChanged.connect(self._update_star)
        self.btn_star.clicked.connect(self._toggle_favorite)
        tl.addWidget(self.text_panel)
        self.lbl_warn = QLabel()
        self.lbl_warn.setStyleSheet("color:#d9534f;")
        self.lbl_warn.setWordWrap(True)
        tl.addWidget(self.lbl_warn)
        tlay.addWidget(g_text)

        # Hiệu ứng, bộ kiểu, vị trí
        g_font = QGroupBox("Hiệu ứng, bộ kiểu & vị trí")
        fl = QFormLayout(g_font)
        srow = QHBoxLayout()
        self.btn_stroke = ColorButton("#ffffff")
        self.btn_stroke.changed.connect(self._style_changed)
        self.stroke_box = QDoubleSpinBox()
        self.stroke_box.setRange(0, 30)
        self.stroke_box.setSingleStep(0.5)
        self.stroke_box.setSuffix(" px")
        self.stroke_box.valueChanged.connect(self._style_changed)
        srow.addWidget(self.btn_stroke)
        srow.addWidget(self.stroke_box, 1)
        fl.addRow("Viền (Stroke)", srow)
        self.fav_box = QComboBox()
        self.fav_box.setToolTip("Font yêu thích: chọn để dùng ngay (áp cho mọi vùng đang chọn)")
        self.fav_box.activated.connect(self._favorite_picked)
        fl.addRow("Yêu thích", self.fav_box)
        self.preset_box = QComboBox()
        self.preset_box.setToolTip("Bộ kiểu đã lưu (toàn bộ thông số chữ + viền): chọn để áp cho mọi vùng đang chọn")
        self.preset_box.activated.connect(self._preset_picked)
        btn_save_preset = QPushButton("Lưu")
        btn_save_preset.setToolTip("Lưu kiểu chữ hiện tại thành một bộ kiểu có tên (vd Thoại, Hét, SFX)")
        btn_save_preset.clicked.connect(self._save_preset)
        btn_del_preset = QPushButton("Xoá")
        btn_del_preset.clicked.connect(self._delete_preset)
        prow = QHBoxLayout()
        prow.addWidget(self.preset_box, 1)
        prow.addWidget(btn_save_preset)
        prow.addWidget(btn_del_preset)
        fl.addRow("Bộ kiểu", prow)
        rrow = QHBoxLayout()
        self.rotate_box = QDoubleSpinBox()
        self.rotate_box.setRange(-180, 180)
        self.rotate_box.setSingleStep(1)
        self.rotate_box.setSuffix("°")
        self.rotate_box.valueChanged.connect(self._style_changed)
        rrow.addWidget(self.rotate_box, 1)
        btn_auto = QPushButton("Tự động lại")
        btn_auto.setToolTip("Bỏ vị trí/cỡ khung chữ đã chỉnh tay, để tool tự đặt lại")
        btn_auto.clicked.connect(self._reset_text_rect)
        rrow.addWidget(btn_auto)
        fl.addRow("Xoay", rrow)
        hint = QLabel("Chọn 1 vùng đã xử lý: kéo khung tím để di chuyển chữ, kéo góc để đổi cỡ khung; "
                      "phím mũi tên nhích 1px (Shift: 10px).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888;")
        fl.addRow(hint)
        self.chk_bubble = QCheckBox("Đặt chữ theo lòng bóng thoại")
        self.chk_bubble.setChecked(True)
        self.chk_bubble.setToolTip("Mở rộng vùng đặt chữ ra cả bóng thoại thay vì chỉ khung chữ gốc "
                                   "(chữ Nhật dọc thường có khung hẹp và cao).")
        self.chk_bubble.toggled.connect(self._bubble_toggled)
        fl.addRow("", self.chk_bubble)
        self.engine_box = QComboBox()
        for eid, label in ENGINES:
            self.engine_box.addItem(label, eid)
        self.engine_box.setToolTip("Cách vẽ lại nền sau khi xoá chữ. Bóng thoại trắng / nền trơn luôn được tô màu "
                                   "nền (tức thì); AI chỉ dùng cho nền có hoạ tiết (tranh, lưới chấm, SFX).")
        idx = self.engine_box.findData(self.settings.value("ai/engine", "auto"))
        self.engine_box.setCurrentIndex(max(0, idx))
        self.engine_box.currentIndexChanged.connect(self._engine_changed)
        btn_api = QPushButton("API key…")
        btn_api.clicked.connect(self.open_api_dialog)
        erow = QHBoxLayout()
        erow.addWidget(self.engine_box, 1)
        erow.addWidget(btn_api)
        fl.addRow("Vẽ lại nền", erow)
        self.lbl_ai = QLabel()
        self.lbl_ai.setStyleSheet("color:#888;")
        fl.addRow(self.lbl_ai)
        tlay.addWidget(g_font)
        tlay.addStretch(1)
        self._load_favorites()
        self._load_presets()

        for key, dx, dy in ((Qt.Key_Left, -1, 0), (Qt.Key_Right, 1, 0), (Qt.Key_Up, 0, -1), (Qt.Key_Down, 0, 1)):
            QShortcut(QKeySequence(key), self.view, lambda dx=dx, dy=dy: self._nudge(dx, dy))
            QShortcut(QKeySequence(Qt.SHIFT | key), self.view, lambda dx=dx, dy=dy: self._nudge(dx * 10, dy * 10))
        return panel, panel2

    # --- kiểu chữ ------------------------------------------------------------
    def current_style(self) -> TextStyle:
        return TextStyle(
            **self.text_panel.fields(),
            stroke_color=self.btn_stroke.color,
            stroke_width=self.stroke_box.value(),
            rotation=self.rotate_box.value(),
        )

    def _apply_style_to_ui(self, s: TextStyle):
        self._loading_ui = True
        self.text_panel.set_style(s)
        self.btn_stroke.set_color(s.stroke_color)
        self.stroke_box.setValue(s.stroke_width)
        self.rotate_box.setValue(s.rotation)
        self._loading_ui = False
        self._update_star()

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
        # Vùng đã xử lý thì đổi kiểu là thấy ngay – áp cho mọi vùng đang chọn.
        changed = False
        if self.doc is not None:
            self.doc.begin("Đổi kiểu chữ", merge="style")
        for r in self.selected_regions:
            if r.cleaned or r.text.strip():
                r.style = style.copy()
                changed = changed or r.cleaned
        if self.doc is not None:
            self.doc.end()
        if changed:
            self.refresh()

    # --- font yêu thích & bộ kiểu ------------------------------------------------
    def _favorites(self) -> list[str]:
        v = self.settings.value("fonts/favorites", [])
        return [v] if isinstance(v, str) else list(v or [])

    def _load_favorites(self):
        favs = self._favorites()
        self.fav_box.clear()
        self.fav_box.addItem(f"★ {len(favs)} font yêu thích" if favs else "Chưa có – bấm ☆ cạnh ô Font", None)
        for f in favs:
            self.fav_box.addItem(f, f)
        self._update_star()

    def _update_star(self, *_):
        on = self.font_box.currentFont().family() in self._favorites()
        self.btn_star.setText("★" if on else "☆")

    def _toggle_favorite(self):
        fam = self.font_box.currentFont().family()
        favs = self._favorites()
        if fam in favs:
            favs.remove(fam)
        else:
            favs.append(fam)
        self.settings.setValue("fonts/favorites", favs)
        self._load_favorites()

    def _favorite_picked(self, i: int):
        fam = self.fav_box.itemData(i)
        if fam:
            self.font_box.setCurrentFont(QFont(fam))  # kích hoạt _style_changed → áp cho vùng đang chọn
        self.fav_box.setCurrentIndex(0)

    def _presets(self) -> dict:
        try:
            return json.loads(self.settings.value("styles/presets", "{}"))
        except ValueError:
            return {}

    def _load_presets(self):
        presets = self._presets()
        self.preset_box.clear()
        self.preset_box.addItem("Chọn bộ kiểu…" if presets else "Chưa có – bấm Lưu", None)
        for name in presets:
            self.preset_box.addItem(name, name)

    def _save_preset(self):
        name, ok = QInputDialog.getText(self, "Lưu bộ kiểu", "Tên bộ kiểu (vd Thoại, Hét, Suy nghĩ, SFX):")
        name = name.strip()
        if not ok or not name:
            return
        presets = self._presets()
        presets[name] = asdict(self.current_style())
        self.settings.setValue("styles/presets", json.dumps(presets, ensure_ascii=False))
        self._load_presets()
        self.statusBar().showMessage(f"Đã lưu bộ kiểu “{name}”.", 4000)

    def _delete_preset(self):
        name = self.preset_box.currentData()
        if not name:
            return
        presets = self._presets()
        presets.pop(name, None)
        self.settings.setValue("styles/presets", json.dumps(presets, ensure_ascii=False))
        self._load_presets()

    def _preset_picked(self, i: int):
        name = self.preset_box.itemData(i)
        data = self._presets().get(name) if name else None
        if not data:
            return
        style = TextStyle(**{k: v for k, v in data.items() if k in vars(TextStyle())})
        self._apply_style_to_ui(style)
        self._style_changed()
        self.statusBar().showMessage(f"Đã áp bộ kiểu “{name}” cho {len(self.selection)} vùng đang chọn.", 4000)

    # --- căn chỉnh khung chữ --------------------------------------------------------
    def _text_rect_dragged(self, rect):
        r = self.region
        if r is not None and r.cleaned:
            with self.doc.step("Di chuyển chữ"):
                r.manual_rect = tuple(int(v) for v in rect)
            self.refresh()

    def _nudge(self, dx: int, dy: int):
        r = self.region
        if r is None or not r.done:
            return
        x1, y1, x2, y2 = r.text_rect
        with self.doc.step("Nhích chữ", merge=f"nudge:{r.uid}"):
            r.manual_rect = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
        self.refresh()

    def _reset_text_rect(self):
        if self.doc is None:
            return
        with self.doc.step("Tự đặt lại vị trí chữ"):
            for r in self.selected_regions:
                r.manual_rect = None
        self.refresh()

    @property
    def engine(self) -> str:
        return self.engine_box.currentData()

    def _engine_changed(self, *_):
        self.settings.setValue("ai/engine", self.engine)
        self.lbl_ai.setText("")
        if self.engine in LOCAL_AI:
            self._preload_lama()
        elif self.engine in PROVIDERS:
            if not secrets.get_key(self.engine):
                self.open_api_dialog()
            self._update_ai_label()

    def _update_ai_label(self):
        if self.engine in PROVIDERS:
            has_key = bool(secrets.get_key(self.engine))
            self.lbl_ai.setText(f"✓ Dùng {model_for(self.settings, self.engine)}" if has_key
                                else "⚠ Chưa có API key – bấm “API key…”")

    def open_api_dialog(self):
        if ApiDialog(self.settings, self).exec():
            self._update_ai_label()

    def _make_inpainter(self):
        """Hàm vẽ lại nền theo lựa chọn hiện tại (None = OpenCV)."""
        if self.engine == "lama":
            return lama.inpaint
        if self.engine == "auto":
            return small_only(lama.inpaint)
        if self.engine == "simple":
            return skip_hard
        if self.engine in PROVIDERS:
            key = secrets.get_key(self.engine)
            if not key:
                raise cloud.CloudError("Chưa nhập API key. Bấm “API key…” để nhập, hoặc chọn LaMa.")
            return cloud.make_inpainter(self.engine, key, model_for(self.settings, self.engine))
        return None

    def _preload_lama(self):
        if lama.is_ready() or getattr(self, "_lama_loader", None) is not None:
            return
        self._lama_loader = LamaLoader(self)
        self._lama_loader.done.connect(self._lama_loaded)
        self._lama_loader.start()
        self.lbl_ai.setText("⏳ Đang nạp AI vẽ nền (lần đầu ~30 giây)…")

    def _lama_loaded(self, err: str):
        self._lama_loader = None
        if err:
            self.lbl_ai.setText("⚠ Không nạp được LaMa, chuyển sang cách thường.")
            self.engine_box.setCurrentIndex(self.engine_box.findData("opencv"))
            QMessageBox.warning(self, "AI vẽ nền", err)
        else:
            if self.engine in LOCAL_AI:
                self.lbl_ai.setText("✓ AI LaMa sẵn sàng")

    def _clean_then(self, r, after):
        """Xoá chữ (1 vùng hoặc danh sách vùng) ở luồng nền, xong thì gọi `after()`.
        Trong lúc chờ khoá thao tác để không sửa trang song song."""
        if all(x.cleaned for x in (r if isinstance(r, list) else [r])):
            with self.doc.step("Xử lý"):
                for x in (r if isinstance(r, list) else [r]):
                    self.page.clean(x, fit_bubble=self.chk_bubble.isChecked())
            after()
            return
        if self.clean_worker is not None:
            return
        try:
            self.page.inpainter = self._make_inpainter()
        except cloud.CloudError as e:
            QMessageBox.warning(self, "AI vẽ nền", str(e))
            return
        name = self.engine_box.currentText().split(" – ")[0]
        wait = " (đang chờ nạp model)" if self.engine in LOCAL_AI and not lama.is_ready() else ""
        what = f"{len(r)} vùng" if isinstance(r, list) else f"vùng #{self.selected + 1}"
        self.statusBar().showMessage(f"Đang xoá chữ {what} – {name}{wait}…")
        self.centralWidget().setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        fit = self.chk_bubble.isChecked()
        regions = r if isinstance(r, list) else [r]
        doc = self.doc
        doc.begin("Xử lý", ["redraw"])
        self.clean_worker = CleanWorker(self, lambda: self.page.clean_many(regions, fit))
        self.clean_worker.failed.connect(lambda msg: QMessageBox.warning(self, "Lỗi khi xoá chữ", msg))

        def finished():
            self.clean_worker = None
            doc.end()
            QApplication.restoreOverrideCursor()
            self.centralWidget().setEnabled(True)
            self.statusBar().clearMessage()
            if all(x.cleaned for x in regions):
                after()
                hard = sum(x.needs_redraw for x in regions)
                if hard:
                    self.statusBar().showMessage(f"{hard} vùng nền khó: đã đặt chữ nhưng giữ nguyên nền gốc "
                                                 "(khung vàng) – tự redraw, hoặc Ctrl+Shift+R.", 10000)
            else:
                self.refresh()

        self.clean_worker.finished.connect(finished)
        self.clean_worker.start()

    def _bubble_toggled(self, on: bool):
        r = self.region
        if r is not None and r.cleaned:
            with self.doc.step("Đặt chữ theo lòng bóng"):
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
            page = Page(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Lỗi", str(e))
            return
        self.chapter = None
        self.page_dock.hide()
        self.settings.setValue("dir/image", os.path.dirname(path))
        self._show_page(page)

    def _show_page(self, page: Page):
        self.page = page
        name = os.path.basename(page.path)
        self.setWindowTitle(f"Lilyzono – {name}" + (f" ({os.path.basename(self.chapter.folder)})" if self.chapter else ""))
        self.selected, self.selection = -1, []
        self.doc = doc_for(page)
        self.view.set_image(self.doc.render(), fit=True)
        w, h = page.size
        self.lbl_size.setText(f"{w} × {h} px")
        self._lines_for_page()
        self.refresh()
        if page.detected:
            self.select_region(0 if page.regions else -1)
        else:
            self.run_detect()
        if self.engine in LOCAL_AI:
            self._preload_lama()

    # --- chương -------------------------------------------------------------------------
    def open_chapter(self):
        folder = QFileDialog.getExistingDirectory(self, "Mở thư mục ảnh raw của chương",
                                                  self.settings.value("dir/image", ""))
        if folder:
            self.load_chapter(folder)

    def load_chapter(self, folder: str):
        try:
            chapter = Chapter(folder)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Lỗi", str(e))
            return
        old = self.chapter
        self.chapter = chapter
        if old and old.translations:
            chapter.translations = old.translations
        self.settings.setValue("dir/image", folder)
        self.page_list.blockSignals(True)
        self.page_list.clear()
        for path in chapter.paths:
            self.page_list.addItem(os.path.basename(path))
        self.page_list.blockSignals(False)
        self.page_dock.show()
        self.page_list.setCurrentRow(0)
        self._page_row_changed(0)

    def _page_row_changed(self, row: int):
        if self.chapter is None or not (0 <= row < len(self.chapter.paths)):
            return
        if self.page is not None and self.page.path == self.chapter.paths[row]:
            return
        self._show_page(self.chapter.page(self.chapter.paths[row]))

    def _update_page_list(self):
        if self.chapter is None:
            return
        for i, path in enumerate(self.chapter.paths):
            page = self.chapter.pages.get(path)
            label = os.path.basename(path)
            if page is not None and page.detected:
                todo = [r for r in page.regions if r.text.strip()]
                done = sum(1 for r in todo if r.done)
                hard = sum(1 for r in page.regions if r.needs_redraw)
                label += f"   {done}/{len(todo)}" + (" ✓" if todo and done == len(todo) else "")
                label += f"  ⚠{hard} cần redraw" if hard else ""
            if path in self.chapter.redrawn or (page is not None and page.redrawn is not None):
                label += "  [redraw]"
            self.page_list.item(i).setText(label)

    def _lines_for_page(self):
        """Danh sách câu dịch cho trang đang mở (cả file, hoặc riêng trang nếu docs chia theo trang)."""
        if self.page is None:
            return
        pages = self.chapter.translations if self.chapter else getattr(self, "_doc_pages", {})
        n = page_number(self.page.path)
        if pages and (n in pages or (len(pages) == 1 and 0 in pages)):
            self.lines = pages.get(n, pages.get(0, []))
            self.page_lines = n in pages
        elif not self.chapter and getattr(self, "_doc_flat", None) is not None:
            self.lines, self.page_lines = self._doc_flat, False
        else:
            self.lines, self.page_lines = ([], False) if self.chapter else (self.lines, False)
        self._sync_used_lines()
        self._fill_lines()

    def _sync_used_lines(self):
        """Câu đã nằm trong một khung thì đánh dấu đã dùng."""
        texts = [t for r in (self.page.regions if self.page else []) for t in r.text.split("\n") if t.strip()]
        self.used_lines = {i for i, line in enumerate(self.lines) if line in texts}

    def _auto_fill(self):
        """Docs chia theo trang: tự ghép câu của trang vào các khung theo thứ tự đọc."""
        if not (self.page_lines and self.lines and self.page and self.page.detected):
            return
        free = [r for r in self.page.regions if not r.text.strip()]
        plan = assign_lines(self.page, free, self.lines)
        with doc_for(self.page).step("Tự điền bản dịch"):
            apply_plan(self.page, plan, self.current_style())
        if self.chapter:
            self.chapter.filled.add(self.page.path)
        self._sync_used_lines()
        self._fill_lines()
        self.statusBar().showMessage(f"Đã tự điền {sum(len(ls) for _, ls in plan)}/{len(self.lines)} câu vào "
                                     f"{len(plan)} khung. Kiểm tra rồi bấm Xử lý (chọn tất cả: Ctrl+A).", 8000)

    def open_docs(self):
        path, _ = QFileDialog.getOpenFileName(self, "Mở bản dịch", self.settings.value("dir/docs", ""), DOCS_FILTER)
        if path:
            self.load_docs(path)

    def load_docs(self, path: str):
        try:
            pages = load_pages(path)
            flat = load_lines(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Lỗi", f"Không đọc được bản dịch:\n{e}")
            return
        self.settings.setValue("dir/docs", os.path.dirname(path))
        by_page = not (len(pages) == 1 and 0 in pages)
        if self.chapter:
            self.chapter.translations = pages
            self.chapter.filled.clear()
        self._doc_pages, self._doc_flat = (pages if by_page else {}), flat
        info = (f"{len(pages)} trang / {sum(map(len, pages.values()))} câu – tự điền theo trang"
                if by_page else f"{len(flat)} câu")
        self.lbl_docs.setText(f"{os.path.basename(path)} – {info}")
        self._lines_for_page()
        if by_page and self.page and self.page.detected and not any(r.text for r in self.page.regions):
            self._auto_fill()
        self.refresh()

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
            if os.path.isdir(path):
                self.load_chapter(path)
            elif ext in IMAGE_EXTS:
                self.load_image(path)
            elif ext in DOCS_EXTS:
                self.load_docs(path)

    # --- nhận diện -----------------------------------------------------------
    def run_detect(self):
        if self.page is None or (self.worker and self.worker.isRunning()):
            return
        self.act_detect.setEnabled(False)
        self.statusBar().showMessage("Đang nhận diện chữ…")
        doc_for(self.page).begin("Nhận diện chữ", ["redraw"])
        self.worker = DetectWorker(self, self.page, self.direction.currentIndex() == 0, self._auto_clean_args())
        self.worker.finished_ok.connect(self._detect_done)
        self.worker.failed.connect(self._detect_failed)
        self.worker.start()

    def _auto_clean_args(self):
        """Thông số tự xoá sau khi nhận diện: bóng thoại luôn xoá; SFX nhỏ xoá bằng AI trên máy (không tự gọi AI
        trên mạng vì tốn phí)."""
        if self.engine in PROVIDERS or self.engine == "simple":
            return (skip_hard, False, self.chk_bubble.isChecked())
        return (lama.inpaint if self.engine in LOCAL_AI else None, True, self.chk_bubble.isChecked())

    def _detect_done(self):
        self.act_detect.setEnabled(True)
        if self.worker is not None:
            doc_for(self.worker.page).end()
        n = len(self.page.regions)
        k = self.worker.cleaned if self.worker else 0
        self.statusBar().showMessage(f"Tìm thấy {n} vùng chữ, đã tự xoá chữ {k} bóng thoại / SFX nhỏ. "
                                     "Chọn khung rồi gõ bản dịch; chỗ còn lại: Xử lý (Ctrl+Enter) hoặc upload bản redraw.")
        self._auto_fill()
        self.refresh()
        self.select_region(0 if n else -1)

    def _detect_failed(self, msg: str):
        self.act_detect.setEnabled(True)
        if self.worker is not None:
            doc_for(self.worker.page).end()
        self.statusBar().showMessage("Nhận diện lỗi.")
        QMessageBox.warning(self, "Lỗi nhận diện", msg)

    # --- vùng chữ ------------------------------------------------------------
    @property
    def region(self) -> Region | None:
        if self.page and 0 <= self.selected < len(self.page.regions):
            return self.page.regions[self.selected]
        return None

    @property
    def selected_regions(self) -> list[Region]:
        if not self.page:
            return []
        return [self.page.regions[i] for i in self.selection if 0 <= i < len(self.page.regions)]

    def _region_clicked(self, idx: int, mods: int):
        if mods & int(Qt.ControlModifier.value):
            sel = list(self.selection)
            if idx in sel:
                sel.remove(idx)
                primary = sel[-1] if sel else -1
            else:
                sel.append(idx)
                primary = idx
            self.select_region(primary, sel)
        elif mods & int(Qt.ShiftModifier.value) and self.selected >= 0:
            a, b = sorted((self.selected, idx))
            self.select_region(idx, list(range(a, b + 1)))
        else:
            self.select_region(idx)

    def select_translated(self):
        idx = [i for i, r in enumerate(self.page.regions if self.page else []) if r.text.strip()]
        if idx:
            self.select_region(idx[0], idx)
            self.statusBar().showMessage(f"Đã chọn {len(idx)} khung có bản dịch: đổi font/kiểu là áp cho tất cả.", 6000)

    def select_all(self):
        if self.page and self.page.regions:
            self.select_region(self.selected if self.selected >= 0 else 0, list(range(len(self.page.regions))))

    def select_region(self, idx: int, selection: list[int] | None = None):
        self.selected = idx
        self.selection = selection if selection is not None else ([idx] if idx >= 0 else [])
        r = self.region
        self._loading_ui = True
        if r is None:
            self.lbl_region.setText("Chưa chọn vùng.")
            self.txt.setPlainText("")
        else:
            state = "đã xử lý" if r.done else ("đã xoá chữ" if r.cleaned else "chưa xử lý")
            if r.needs_redraw:
                state += " · nền khó, giữ nguyên chữ gốc: tự redraw hoặc Ctrl+Shift+R"
            kind = {"sfx": " · SFX (tự nhận diện)", "manual": " · vẽ tay", "lasso": " · lasso"}.get(r.kind, "")
            text = f"Vùng #{idx + 1} / {len(self.page.regions)}{kind} – {state}"
            if len(self.selection) > 1:
                sel = sorted(self.selection)
                nums = ", ".join(f"#{i + 1}" for i in sel[:8]) + (" …" if len(sel) > 8 else "")
                text = (f"Đang chọn {len(sel)} vùng ({nums}) – đổi font/kiểu áp cho tất cả, "
                        f"Xử lý làm lần lượt từng vùng. Ô dưới: chữ vùng #{idx + 1}.")
            self.lbl_region.setText(text)
            text = r.text
            self._suggested_text = None
            if not text:
                nxt = self._next_unused_line()
                if nxt is not None:
                    text = self._suggested_text = self.lines[nxt]
                    self._highlight_line(nxt)
            self.txt.setPlainText(text)
        self._loading_ui = False
        if r is not None and r.cleaned:
            self._apply_style_to_ui(r.style)
        if r is not None and self.doc is not None:
            L = self.doc.text_layer(r)
            if L is not None and self.doc.active is not L:
                self.doc.active = L
                self.layers_panel.set_doc(self.doc)
        self._update_warning()
        self._draw_overlays()

    def _draw_overlays(self):
        if self.page is None:
            return
        r = self.region
        edit = r.text_rect if (r is not None and r.done and len(self.selection) == 1) else None
        self.view.set_regions(self.page.regions, self.selection, edit)

    def add_region(self, box):
        if self.page is None:
            return
        with self.doc.step("Khung chữ"):
            self.page.add_region(box)
        self.select_region(len(self.page.regions) - 1)

    def add_lasso(self, points):
        if self.page is None:
            return
        with self.doc.step("Khung chữ lasso"):
            self.page.add_lasso(points)
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
            with self.doc.step("Gõ chữ", merge=f"text:{r.uid}"):
                if not r.text.strip():
                    r.style = self.current_style()  # khung mới gõ chữ: dùng font/kiểu đang chọn
                r.text = self.txt.toPlainText()
            self.refresh()

    def process_selected(self):
        r = self.region
        if r is None:
            QMessageBox.information(self, "Chưa chọn vùng", "Hãy bấm chọn một khung chữ trên ảnh trước.")
            return
        if len(self.selection) > 1:
            self._process_many()
            return
        r.text = self.txt.toPlainText()
        r.style = self.current_style()

        def after():
            line = self._line_of_text(r.text)
            if line is not None:
                self.used_lines.add(line)
                self._fill_lines()
            self.refresh()
            self._goto_next_pending()

        self._clean_then(r, after)

    def _process_many(self):
        """Xử lý lần lượt mọi vùng đang chọn với cùng kiểu chữ; vùng chưa có chữ nhận câu dịch kế tiếp."""
        style = self.current_style()
        primary = self.region
        typed = self.txt.toPlainText()
        # Câu chỉ là gợi ý tự điền thì bỏ qua, để chia câu dịch theo đúng thứ tự đọc.
        if typed.strip() and typed != self._suggested_text:
            primary.text = typed
            line = self._line_of_text(typed)
            if line is not None:
                self.used_lines.add(line)
        queue = []
        for i in sorted(self.selection):
            reg = self.page.regions[i]
            if not reg.text.strip():
                nxt = self._next_unused_line()
                if nxt is not None:
                    reg.text = self.lines[nxt]
                    self.used_lines.add(nxt)
            line = self._line_of_text(reg.text)
            if line is not None:
                self.used_lines.add(line)
            reg.style = style.copy()
            queue.append(i)
        self._fill_lines()
        selection = list(self.selection)

        def done():
            self.refresh()
            self.select_region(self.selected, selection)
            self.statusBar().showMessage(f"Đã xử lý {len(selection)} vùng.", 5000)

        # Cả nhóm chạy một lượt: vùng gần nhau dùng chung một lần gọi AI trên mạng.
        self._clean_then([self.page.regions[i] for i in queue], done)

    def open_ai_bridge(self):
        """Redraw vùng đang chọn qua ChatGPT/Gemini trên web bằng copy–dán."""
        regs = self.selected_regions
        if not regs:
            QMessageBox.information(self, "Chưa chọn vùng", "Hãy chọn (Ctrl+bấm để chọn nhiều) vùng chữ cần redraw.")
            return
        primary = self.region
        typed = self.txt.toPlainText()
        if primary is not None and typed.strip() and not primary.cleaned:
            primary.text = typed
        style = self.current_style()
        for r in regs:
            if not r.cleaned:
                r.style = style.copy()
        try:
            job = manual_ai.prepare(self.page, regs)
        except ValueError as e:
            QMessageBox.information(self, "Redraw", str(e))
            return
        selection = list(self.selection)

        def applied(res):
            self._sync_used_lines()
            self._fill_lines()
            self.refresh()
            self.select_region(self.selected, selection)
            self.statusBar().showMessage(f"Đã dán kết quả AI cho {len(regs)} vùng.", 8000)

        self._ai_dialog = AiBridgeDialog(job, self.chk_bubble.isChecked(), self)
        page = self.page
        self._ai_dialog.about_to_apply.connect(lambda: doc_for(page).begin("Redraw bằng AI", ["redraw"]))
        self._ai_dialog.applied.connect(lambda _res: doc_for(page).end())
        self._ai_dialog.applied.connect(applied)
        self._ai_dialog.show()
        self._ai_dialog.copy_image()

    def clean_selected(self):
        r = self.region
        if r is None:
            return
        if len(self.selection) > 1:
            queue = sorted(self.selection)
            selection = list(self.selection)

            def step():
                if not queue:
                    self.refresh()
                    self.select_region(self.selected, selection)
                    return
                i = queue.pop(0)
                self.selected = i
                self.page.regions[i].text = ""
                self._clean_then(self.page.regions[i], step)

            step()
            return
        def after():
            r.text = ""
            self.refresh()
            self.select_region(self.selected)

        self._clean_then(r, after)

    def reset_selected(self):
        if self.region is None:
            return
        with self.doc.step("Hoàn tác khung", ["redraw"]):
            for r in self.selected_regions:
                line = self._line_of_text(r.text)
                if line is not None:
                    self.used_lines.discard(line)
                self.page.reset(r)
        self._fill_lines()
        self.refresh()
        self.select_region(self.selected, list(self.selection))

    def delete_selected(self):
        if self.region is None:
            return
        with self.doc.step("Xoá khung chữ", ["redraw"]):
            for i in sorted(self.selection, reverse=True):
                self.page.remove_region(self.page.regions[i])
        self.refresh()
        self.select_region(min(min(self.selection), len(self.page.regions) - 1))

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
        line = self.lines[item.data(Qt.UserRole)]
        cur = self.txt.toPlainText().strip()
        # Ctrl + bấm: nối thêm câu vào khung (bóng mà người dịch tách thành nhiều câu).
        if QApplication.keyboardModifiers() & Qt.ControlModifier and cur and line not in cur.split("\n"):
            self.txt.setPlainText(cur + "\n" + line)
        else:
            self.txt.setPlainText(line)

    def _line_double_clicked(self, item: QListWidgetItem):
        self._line_clicked(item)
        self.process_selected()

    # --- hiển thị & xuất -----------------------------------------------------
    def toggle_boxes(self):
        self.view.show_boxes = self.act_boxes.isChecked()
        self.opt_boxes.blockSignals(True)
        self.opt_boxes.setChecked(self.view.show_boxes)
        self.opt_boxes.blockSignals(False)
        self.refresh()

    def refresh(self):
        if self.page is None:
            return
        self.doc = doc_for(self.page)
        self.doc.sync()
        self.view.set_image(self.doc.render())
        self._draw_overlays()
        self._update_page_list()
        self.layers_panel.set_doc(self.doc)
        self.history_panel.set_doc(self.doc, "Mở " + os.path.basename(self.page.path))
        self.view.set_selection_outline(self.doc.selection_outline())

    # --- hoàn tác ----------------------------------------------------------------------
    def undo(self):
        if self.doc is not None and self.stroke is None and self.doc.undo():
            self._after_history()

    def redo(self):
        if self.doc is not None and self.stroke is None and self.doc.redo():
            self._after_history()

    def _history_jump(self, index: int):
        if self.doc is not None and self.stroke is None:
            self.doc.goto(index)
            self._after_history()

    def _after_history(self):
        n = len(self.page.regions)
        sel = [i for i in self.selection if i < n]
        self.refresh()
        self.select_region(self.selected if self.selected < n else (sel[0] if sel else -1), sel or None)

    # --- vùng chọn ---------------------------------------------------------------------
    def _selection_made(self, kind: str, data, mode: str):
        if self.doc is None:
            return
        names = {"rect": "Rectangular Marquee", "poly": "Lasso", "wand": "Magic Wand", "none": "Bỏ chọn"}
        with self.doc.step(names[kind]):
            if kind == "none":
                self.doc.deselect()
            elif kind == "rect":
                self.doc.select_rect(data, mode, self.opt_feather.value())
            elif kind == "poly":
                self.doc.select_polygon(data, mode, self.opt_feather.value())
            else:
                self.doc.select_wand(*data, self.opt_tol.value(), self.opt_contig.isChecked(),
                                     self.opt_sample.isChecked(), mode)
        self._after_selection()

    def _after_selection(self):
        self.view.set_selection_outline(self.doc.selection_outline())
        self.history_panel.set_doc(self.doc, "Mở " + os.path.basename(self.page.path))
        bb = self.doc.selection_bbox()
        if bb:
            self.statusBar().showMessage(f"Vùng chọn {bb[2] - bb[0]} × {bb[3] - bb[1]} px", 4000)

    def _select_op(self, name: str, fn):
        if self.doc is None:
            return
        with self.doc.step(name):
            fn()
        self._after_selection()

    def select_all_pixels(self):
        self._select_op("Chọn tất cả", lambda: self.doc.select_all())

    def deselect(self):
        self._select_op("Bỏ chọn", lambda: self.doc.deselect())

    def invert_selection(self):
        self._select_op("Đảo vùng chọn", lambda: self.doc.invert_selection())

    def feather_dialog(self):
        if self.doc is None or self.doc.selection is None:
            return
        r, ok = QInputDialog.getInt(self, "Làm mềm vùng chọn", "Bán kính (px):", 5, 1, 250)
        if ok:
            self._select_op("Feather", lambda: self.doc.feather_selection(r))

    def grow_dialog(self, sign: int):
        if self.doc is None or self.doc.selection is None:
            return
        px, ok = QInputDialog.getInt(self, "Mở rộng" if sign > 0 else "Thu hẹp", "Số px:", 3, 1, 200)
        if ok:
            self._select_op("Mở rộng vùng chọn" if sign > 0 else "Thu hẹp vùng chọn",
                            lambda: self.doc.grow_selection(sign * px))

    def selection_from_regions(self):
        if self.doc is None or not self.selected_regions:
            return
        import numpy as np

        w, h = self.page.size
        m = np.zeros((h, w), np.uint8)
        for r in self.selected_regions:
            area = self.page.area_mask(r)
            if area is not None:
                np.maximum(m, area, out=m)
            else:
                x1, y1, x2, y2 = r.box
                m[y1:y2, x1:x2] = 255
        self._select_op("Vùng chọn từ khung chữ", lambda: self.doc.set_selection(m))

    def region_from_selection(self):
        if self.doc is None or self.doc.selection is None:
            QMessageBox.information(self, "Tạo khung chữ", "Hãy tạo vùng chọn trước (M, L hoặc W).")
            return
        outline = max(self.doc.selection_outline(), key=len, default=None)
        if outline is None or len(outline) < 3:
            return
        self.add_lasso([(int(x), int(y)) for x, y in outline])

    # --- thao tác điểm ảnh -------------------------------------------------------------
    def _paint_layer(self):
        if self.doc is None:
            return None
        L = self.doc.active
        if not L.paintable:
            why = "đang khoá" if L.locked else "là layer chữ"
            self.statusBar().showMessage(f"Layer “{L.name}” {why}: chọn layer Redraw hoặc một layer ảnh "
                                         "(Ctrl+Shift+N) để vẽ.", 6000)
            return None
        return L

    def _bgr(self, fg: bool = True):
        c = self.colors.fg if fg else self.colors.bg
        return c.blue(), c.green(), c.red()

    def delete_key(self):
        if self.doc is not None and self.doc.selection is not None:
            L = self._paint_layer()
            if L is not None:
                with self.doc.step("Xoá", [L]):
                    self.doc.clear_selection_pixels(L)
                self.refresh()
        else:
            self.delete_selected()

    def fill_color(self, fg: bool = True, bgr=None):
        L = self._paint_layer()
        if L is None:
            return
        with self.doc.step("Tô màu", [L]):
            self.doc.fill_selection(L, bgr or self._bgr(fg))
        self.refresh()

    def fill_dialog(self):
        if self.doc is None:
            return
        items = ["Content-Aware (AI LaMa vẽ lại theo nền)", "Màu trước", "Màu nền", "Trắng", "Đen"]
        item, ok = QInputDialog.getItem(self, "Tô (Fill)", "Nội dung:", items, 0, False)
        if not ok:
            return
        i = items.index(item)
        if i == 0:
            self.content_aware()
        else:
            self.fill_color(bgr={1: self._bgr(True), 2: self._bgr(False), 3: (255, 255, 255), 4: (0, 0, 0)}[i])

    def content_aware(self, mask=None, name: str = "Content-Aware Fill"):
        if self.doc is None or self._busy:
            return
        if mask is None and self.doc.selection is None:
            QMessageBox.information(self, "Content-Aware Fill", "Hãy khoanh vùng chọn quanh chỗ cần xoá trước "
                                    "(M, L hoặc W), hoặc dùng Spot Healing (J).")
            return
        L = self._paint_layer()
        if L is None:
            return
        doc = self.doc
        doc.begin(name, [L])
        self._busy = True
        QApplication.setOverrideCursor(Qt.WaitCursor)
        wait = " (đang nạp AI, lần đầu ~30 giây)" if not lama.is_ready() else ""
        self.statusBar().showMessage(f"{name}: AI LaMa đang vẽ lại{wait}…")
        worker = CleanWorker(self, lambda: doc.content_aware_fill(L, lama.inpaint, mask))
        worker.failed.connect(lambda msg: QMessageBox.warning(self, name, msg))

        def finished():
            self._busy = False
            doc.end()
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(f"{name}: xong.", 4000)
            self.refresh()

        worker.finished.connect(finished)
        self._ca_worker = worker
        worker.start()

    # --- cọ ----------------------------------------------------------------------------
    def _clone_source(self, x: float, y: float):
        self.clone_src, self.clone_offset = (x, y), None
        self.statusBar().showMessage(f"Đã lấy điểm mẫu ({int(x)}, {int(y)}). Tô để sao chép.", 4000)

    def _stroke_began(self, x: float, y: float, mods: int):
        if self._busy:
            return
        L = self._paint_layer()
        if L is None:
            return
        tool = self.view.tool
        offset = None
        if tool == TOOL_CLONE:
            if self.clone_src is None:
                self.statusBar().showMessage("Clone Stamp: Alt+bấm vào chỗ muốn lấy mẫu trước.", 5000)
                return
            if self.clone_offset is None:  # nét đầu sau khi lấy mẫu: cố định khoảng cách mẫu ↔ nét
                self.clone_offset = (int(round(self.clone_src[0] - x)), int(round(self.clone_src[1] - y)))
            offset = self.clone_offset
        size, hard, op = self.paint_opts[tool]
        self.doc.begin(TOOL_NAMES[tool], [L])
        self.stroke = Stroke(self.doc, L, tool, size, hard / 100, op / 100, self._bgr(True), offset)
        self._stroke_to(x, y)

    def _stroke_to(self, x: float, y: float):
        s = self.stroke
        if s is None:
            return
        box = s.to(x, y)
        if box and s.tool != TOOL_HEAL:
            self.view.update_patch(box[0], box[1], self.doc.render_box(box))

    def _stroke_ended(self):
        s, self.stroke = self.stroke, None
        if s is None:
            return
        self.doc.end()
        if s.tool == TOOL_HEAL:
            self.content_aware(mask=s.heal_mask(), name="Spot Healing")
        else:
            self.refresh()

    # --- công cụ chữ ---------------------------------------------------------------------
    def _text_clicked(self, x: float, y: float):
        """Bấm vào chỗ trống bằng công cụ chữ: tạo khung chữ theo lòng bóng thoại tại đó (không xoá gì)."""
        if self.page is None:
            return
        from ..core.clean import text_area_candidates

        x, y = int(x), int(y)
        cands = text_area_candidates(self.page.working, (x - 3, y - 3, x + 3, y + 3))
        box = max(cands, key=lambda c: (c[2] - c[0]) * (c[3] - c[1]))
        if (box[2] - box[0]) * (box[3] - box[1]) < 900:  # không phải bóng thoại: khung mặc định quanh điểm bấm
            w, h = self.page.size
            box = (max(0, x - 110), max(0, y - 45), min(w, x + 110), min(h, y + 45))
        self._text_box_drawn(box)

    def _text_box_drawn(self, box):
        if self.page is None:
            return
        with self.doc.step("Tạo khung chữ"):
            r = self.page.add_region(box)
            r.cleaned = True  # khung gõ chữ thuần: không xoá gì bên dưới
            r.manual_rect = tuple(box)
            r.style = self.current_style()
            r.auto_cap = self.page.auto_cap
            r.text_rects = [tuple(box)]
        self.refresh()
        self.select_region(len(self.page.regions) - 1)
        self.d_lily.raise_()
        self.txt.setFocus()
        self.statusBar().showMessage("Gõ bản dịch vào ô nội dung (panel Lilyzono) – chữ hiện ngay trên ảnh.", 5000)

    # --- layer ---------------------------------------------------------------------------
    def _layer_activated(self, L):
        if self.doc is None:
            return
        self.doc.active = L
        r = self.doc.region_of(L)
        if r is not None and r in self.page.regions:
            self.select_region(self.page.regions.index(r))
        self.layers_panel.set_doc(self.doc)

    def _layer_visibility(self, L, visible: bool):
        with self.doc.step("Ẩn/hiện layer"):
            L.visible = visible
        self.refresh()

    def _layer_renamed(self, L, name: str):
        with self.doc.step("Đổi tên layer"):
            L.name, L.custom_name = name, True
        self.refresh()

    def _layer_opacity(self, v: float):
        if self.doc is None:
            return
        with self.doc.step("Opacity", merge=f"opacity:{self.doc.active.id}"):
            self.doc.active.opacity = v
        self.view.set_image(self.doc.render())
        self.history_panel.set_doc(self.doc, "Mở " + os.path.basename(self.page.path))

    def _layer_blend(self, blend: str):
        if self.doc is None:
            return
        with self.doc.step("Blend mode"):
            self.doc.active.blend = blend
        self.refresh()

    def layer_action(self, key: str):
        doc = self.doc
        if doc is None:
            return
        L = doc.active
        if key == "new":
            with doc.step("Layer mới"):
                doc.add_pixel_layer()
        elif key == "dup":
            with doc.step("Nhân đôi layer"):
                if doc.duplicate_layer(L) is None:
                    self.statusBar().showMessage("Layer chữ: nhân đôi bằng cách tạo khung chữ mới.", 4000)
        elif key == "delete":
            with doc.step("Xoá layer"):
                if not doc.delete_layer(L):
                    self.statusBar().showMessage("Không xoá được layer Background / Redraw (ẩn đi nếu không cần).", 5000)
        elif key in ("up", "down"):
            with doc.step("Đổi thứ tự layer"):
                doc.move_layer(L, 1 if key == "up" else -1)
        elif key == "merge":
            i = doc.layers.index(L)
            below = doc.layers[i - 1] if i > 0 else None
            with doc.step("Gộp xuống", [L, below] if below is not None and below.kind in ("pixel", "redraw") else [L]):
                if not doc.merge_down(L):
                    self.statusBar().showMessage("Chỉ gộp được layer ảnh xuống layer ảnh / Redraw ngay dưới.", 5000)
        self.refresh()

    # --- cả chương / PSD ----------------------------------------------------------------
    def _run_batch(self, title: str, job, done_msg: str, extra=None):
        if self.batch is not None:
            return
        self.centralWidget().setEnabled(False)
        self.statusBar().showMessage(title)
        self.batch = BatchWorker(self, job)
        self.batch.progress.connect(lambda m: self.statusBar().showMessage(f"{title} {m}"))
        self.batch.failed.connect(lambda m: QMessageBox.warning(self, "Lỗi", m))

        def finished():
            self.batch = None
            self.centralWidget().setEnabled(True)
            if self.page is not None:
                self._lines_for_page()
                self.refresh()
                self.select_region(self.selected if self.page.regions else -1)
            self.statusBar().showMessage(done_msg + (extra() if extra else ""), 15000)

        self.batch.finished.connect(finished)
        self.batch.start()

    def process_chapter(self):
        if self.chapter is None:
            if self.page is not None:
                self.select_all()
                self.process_selected()
            return
        try:
            inpainter = self._make_inpainter()
        except cloud.CloudError as e:
            QMessageBox.warning(self, "AI vẽ nền", str(e))
            return
        style, fit, rtl = self.current_style(), self.chk_bubble.isChecked(), self.direction.currentIndex() == 0
        auto = self._auto_clean_args()
        ch = self.chapter

        def job(report):
            if self.detector is None:
                self.detector = TextDetector()
            for i, path in enumerate(ch.paths):
                report(f"trang {i + 1}/{len(ch.paths)} ({os.path.basename(path)})…")
                page = ch.page(path)
                page.inpainter = inpainter
                doc = doc_for(page)
                doc.begin("Xử lý cả chương", ["redraw"])
                if not page.detected:
                    page.detect(self.detector, rtl)
                    page.inpainter, small, _ = auto
                    page.auto_clean(small, fit)
                    page.inpainter = inpainter
                if path not in ch.filled and ch.lines_for(path):
                    ch.auto_fill(path, style)
                regs = [r for r in page.regions if r.text.strip() and not r.cleaned]
                page.clean_many(regs, fit)
                doc.end()

        def summary():
            hard = sum(r.needs_redraw for pg in ch.pages.values() for r in pg.regions)
            return (f" {hard} vùng nền khó giữ nguyên chữ gốc (khung vàng, ⚠ ở danh sách trang) – tự redraw sau."
                    if hard else "")

        self._run_batch("Đang xử lý cả chương:", job, "Đã xử lý xong cả chương. Kiểm tra từng trang rồi Xuất PSD.",
                        extra=summary)

    def open_redrawn(self):
        """Nạp bản đã redraw làm nền: chữ vẫn nhận diện trên raw, câu dịch type lên bản redraw."""
        if self.page is None and self.chapter is None:
            QMessageBox.information(self, "Nạp bản redraw", "Mở ảnh raw hoặc chương trước, rồi nạp bản redraw.")
            return
        start = self.settings.value("dir/redrawn", "")
        if self.chapter is not None:
            folder = QFileDialog.getExistingDirectory(self, "Thư mục ảnh đã redraw của chương", start)
            if not folder:
                return
            self.settings.setValue("dir/redrawn", folder)
            for pg in self.chapter.pages.values():
                doc_for(pg).begin("Upload bản redraw", ["redraw"])
            try:
                matched = self.chapter.load_redrawn(folder)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Lỗi", str(e))
                return
            if not matched:
                QMessageBox.warning(self, "Nạp bản redraw", "Không ghép được ảnh nào với trang raw (số trang trong "
                                    "tên file phải khớp, hoặc hai thư mục cùng số ảnh).")
                return
            pages = list(self.chapter.pages.values())
            msg = f"Đã nạp bản redraw cho {len(matched)}/{len(self.chapter.paths)} trang."
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Ảnh đã redraw của trang này", start or
                                                  os.path.dirname(self.page.path), IMAGE_FILTER)
            if not path:
                return
            self.settings.setValue("dir/redrawn", os.path.dirname(path))
            doc_for(self.page).begin("Upload bản redraw", ["redraw"])
            try:
                self.page.set_redrawn(path)
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "Lỗi", str(e))
                return
            pages = [self.page]
            msg = "Đã nạp bản redraw."
        self._retype(pages, msg)

    def _retype(self, pages: list[Page], msg: str):
        """Đặt lại chữ cho các vùng đã có câu dịch sau khi đổi nền (chỗ bản redraw còn chữ thì xoá như thường)."""
        try:
            inpainter = self._make_inpainter()
        except cloud.CloudError as e:
            QMessageBox.warning(self, "AI vẽ nền", str(e))
            inpainter = skip_hard
        fit = self.chk_bubble.isChecked()

        def job(report):
            for i, page in enumerate(pages):
                report(f"{i + 1}/{len(pages)}…")
                page.inpainter = inpainter
                page.clean_many([r for r in page.regions if r.text.strip()], fit)
                doc_for(page).end()

        self._run_batch("Đang đặt chữ lên bản redraw:", job, msg + " Câu dịch đã có được đặt lại trên nền mới.")

    def _export_pages(self, pages: list[Page], out_dir: str, psd: bool = True, jpeg: bool = False):
        def job(report):
            exporter = PhotoshopExporter() if psd and photoshop_available() else None
            for i, page in enumerate(pages):
                report(f"{i + 1}/{len(pages)} ({os.path.basename(page.path)})…")
                base = os.path.join(out_dir, os.path.splitext(os.path.basename(page.path))[0])
                if jpeg:
                    doc_for(page).export(base + ".jpg")
                if not psd:
                    continue
                if exporter:
                    exporter.export(page, base + ".psd")
                else:
                    export_without_photoshop(page, base + ".psd")

        what = " + ".join(x for x, on in (("PSD", psd), ("JPEG", jpeg)) if on)
        how = ("" if not psd else ", lớp chữ sửa được (Photoshop)" if photoshop_available()
               else ", lớp chữ dạng ảnh (máy không có Photoshop)")
        self._run_batch("Đang xuất:", job, f"Đã xuất {len(pages)} trang ({what}{how}) → {out_dir}")

    def export_psd_page(self):
        if self.page is None:
            return
        base, _ = os.path.splitext(self.page.path)
        path, _ = QFileDialog.getSaveFileName(self, "Xuất PSD", base + ".psd", "Photoshop (*.psd)")
        if not path:
            return

        def job(report):
            if photoshop_available():
                report("đang điều khiển Photoshop…")
                PhotoshopExporter().export(self.page, path)
            else:
                export_without_photoshop(self.page, path)

        self._run_batch("Đang xuất PSD:", job, f"Đã lưu: {path}")

    def export_psd_chapter(self):
        if self.chapter is None:
            self.export_psd_page()
            return
        kinds = ["PSD + JPEG", "PSD", "JPEG"]
        last = self.settings.value("export/kind", kinds[0])
        kind, ok = QInputDialog.getItem(self, "Xuất cả chương", "Định dạng:", kinds,
                                        kinds.index(last) if last in kinds else 0, False)
        if not ok:
            return
        self.settings.setValue("export/kind", kind)
        out_dir = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu", self.chapter.folder)
        if not out_dir:
            return
        pages = [self.chapter.page(p) for p in self.chapter.paths if p in self.chapter.pages]
        self._export_pages(pages, out_dir, psd="PSD" in kind, jpeg="JPEG" in kind)

    def export_image(self):
        if self.page is None:
            return
        base, _ = os.path.splitext(self.page.path)
        path, _ = QFileDialog.getSaveFileName(self, "Xuất JPEG", base + "_typed.jpg", "JPEG (*.jpg);;PNG (*.png)")
        if not path:
            return
        try:
            self.doc.export(path)
            self.statusBar().showMessage(f"Đã lưu: {path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Lỗi", str(e))
