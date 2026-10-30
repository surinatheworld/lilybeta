"""Cửa sổ chính: mở ảnh → nhận diện chữ → chọn vùng + câu dịch → chọn font → xử lý."""
from __future__ import annotations

import json
import os
from dataclasses import asdict

from PySide6.QtCore import QSettings, Qt, QThread, Signal
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
    QScrollArea,
    QSpinBox,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core import cloud, lama, secrets
from ..core.detect import TextDetector
from ..core.assign import apply_plan, assign_lines
from ..core.chapter import Chapter
from ..core.clean import skip_hard, small_only
from ..core.docs import load_lines, load_pages, page_number
from ..core.psd_export import PhotoshopExporter, export_without_photoshop, photoshop_available
from ..core.page import Page, Region
from ..core.typeset import TextStyle, missing_chars
from ..core import manual_ai
from .ai_bridge import AiBridgeDialog
from .api_dialog import PROVIDERS, ApiDialog, model_for
from .image_view import TOOL_LASSO, TOOL_RECT, ImageView
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

        self.view = ImageView()
        self.view.regionClicked.connect(self._region_clicked)
        self.view.textRectChanged.connect(self._text_rect_dragged)
        self.view.regionDrawn.connect(self.add_region)
        self.view.lassoDrawn.connect(self.add_lasso)

        splitter = QSplitter()
        splitter.addWidget(self.view)
        # Bảng bên phải dài nên cho cuộn, để các ô không bị ép dẹt trên màn hình thấp.
        scroll = QScrollArea()
        scroll.setWidget(self._build_panel())
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(420)
        splitter.addWidget(scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setSizes([1000, 460])
        self.setCentralWidget(splitter)
        self._build_toolbar()
        self.page_list = QListWidget()
        self.page_list.currentRowChanged.connect(self._page_row_changed)
        self.page_dock = QDockWidget("Trang", self)
        self.page_dock.setWidget(self.page_list)
        self.page_dock.setFeatures(QDockWidget.DockWidgetMovable)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.page_dock)
        self.page_dock.hide()
        self._load_style_settings()
        self._update_ai_label()
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

        act("Mở chương", self.open_chapter, "Ctrl+Shift+O").setToolTip(
            "Mở cả thư mục ảnh raw của một chương")
        act("Mở ảnh", self.open_image, "Ctrl+O")
        act("Mở bản dịch", self.open_docs, "Ctrl+D")
        act("Upload bản redraw", self.open_redrawn).setToolTip(
            "Nạp ảnh đã xoá chữ/redraw sẵn (Photoshop, AI…): thành lớp Redraw nằm trên raw, dưới lớp chữ. "
            "Chế độ chương: chọn thư mục, ảnh được ghép với trang theo số trong tên file")
        tb.addSeparator()
        self.direction = QComboBox()
        self.direction.addItems(["Tiếng Nhật (manga): phải → trái", "Tiếng Anh / manhwa: trái → phải"])
        self.direction.setCurrentIndex(int(self.settings.value("direction", 0)))
        self.direction.currentIndexChanged.connect(lambda i: self.settings.setValue("direction", i))
        tb.addWidget(self.direction)
        self.act_detect = act("Nhận diện chữ", self.run_detect, "F5")
        self.act_boxes = act("Hiện khung", self.toggle_boxes, "B")
        self.act_boxes.setCheckable(True)
        self.act_boxes.setChecked(True)
        act("Vừa khung", self.view.fit_width, "Ctrl+0")
        tb.addSeparator()
        # Công cụ vẽ vùng mới: kéo chuột trên ảnh.
        tools = QActionGroup(self)
        self.act_rect = act("▭ Khung chữ nhật", lambda: self.view.set_tool(TOOL_RECT), "R")
        self.act_lasso = act("➰ Lasso", lambda: self.view.set_tool(TOOL_LASSO), "L")
        for a, tip in ((self.act_rect, "Kéo chuột để vẽ khung chữ nhật (R)"),
                       (self.act_lasso, "Kéo chuột để khoanh tự do quanh chữ/SFX; toàn bộ phần khoanh sẽ bị xoá (L)")):
            a.setCheckable(True)
            a.setToolTip(tip)
            tools.addAction(a)
        self.act_rect.setChecked(True)
        self.view.set_tool(TOOL_RECT)
        tb.addSeparator()
        act("Xuất JPEG", self.export_image, "Ctrl+S")
        act("Xuất PSD", self.export_psd_page, "Ctrl+Shift+S").setToolTip(
            "Xuất trang này ra PSD có lớp (có Photoshop: lớp chữ sửa được)")
        tb.addSeparator()
        self.act_process_all = act("Xử lý cả chương", self.process_chapter)
        self.act_process_all.setToolTip("Nhận diện, tự điền câu dịch theo trang, xoá chữ và đặt chữ cho mọi trang")
        self.act_export_all = act("Xuất cả chương", self.export_psd_chapter)
        self.act_export_all.setToolTip("Xuất mọi trang đã mở ra PSD (raw / Redraw / lớp chữ) và/hoặc JPEG")

    def _build_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)

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
        for b in (self.btn_process, btn_clean, btn_reset, btn_del):
            row.addWidget(b)
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
        lay.addWidget(g_text)

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
        lay.addWidget(g_font)
        self._load_favorites()
        self._load_presets()

        QShortcut(QKeySequence("Ctrl+Return"), self, self.process_selected)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self.process_selected)
        QShortcut(QKeySequence("Ctrl+Z"), self, self.reset_selected)
        QShortcut(QKeySequence("Ctrl+Shift+R"), self, self.open_ai_bridge)
        QShortcut(QKeySequence("Ctrl+Shift+A"), self, self.select_translated)
        QShortcut(QKeySequence(Qt.Key_Delete), self.view, self.delete_selected)
        QShortcut(QKeySequence(Qt.Key_Tab), self.view, lambda: self._step_region(1))
        QShortcut(QKeySequence("Ctrl+A"), self.view, self.select_all)
        for key, dx, dy in ((Qt.Key_Left, -1, 0), (Qt.Key_Right, 1, 0), (Qt.Key_Up, 0, -1), (Qt.Key_Down, 0, 1)):
            QShortcut(QKeySequence(key), self.view, lambda dx=dx, dy=dy: self._nudge(dx, dy))
            QShortcut(QKeySequence(Qt.SHIFT | key), self.view, lambda dx=dx, dy=dy: self._nudge(dx * 10, dy * 10))
        return panel

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
        for r in self.selected_regions:
            if r.cleaned or r.text.strip():
                r.style = style.copy()
                changed = changed or r.cleaned
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
            r.manual_rect = tuple(int(v) for v in rect)
            self.refresh()

    def _nudge(self, dx: int, dy: int):
        r = self.region
        if r is None or not r.done:
            return
        x1, y1, x2, y2 = r.text_rect
        r.manual_rect = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
        self.refresh()

    def _reset_text_rect(self):
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
        self.clean_worker = CleanWorker(self, lambda: self.page.clean_many(regions, fit))
        self.clean_worker.failed.connect(lambda msg: QMessageBox.warning(self, "Lỗi khi xoá chữ", msg))

        def finished():
            self.clean_worker = None
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
        self.view.set_image(self.page.render(), fit=True)
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
        n = len(self.page.regions)
        k = self.worker.cleaned if self.worker else 0
        self.statusBar().showMessage(f"Tìm thấy {n} vùng chữ, đã tự xoá chữ {k} bóng thoại / SFX nhỏ. "
                                     "Chọn khung rồi gõ bản dịch; chỗ còn lại: Xử lý (Ctrl+Enter) hoặc upload bản redraw.")
        self._auto_fill()
        self.refresh()
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
        self.page.add_region(box)
        self.select_region(len(self.page.regions) - 1)

    def add_lasso(self, points):
        if self.page is None:
            return
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
        self.refresh()

    def refresh(self):
        if self.page is None:
            return
        self.view.set_image(self.page.render())
        self._draw_overlays()
        self._update_page_list()

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
                if not page.detected:
                    page.detect(self.detector, rtl)
                    page.inpainter, small, _ = auto
                    page.auto_clean(small, fit)
                    page.inpainter = inpainter
                if path not in ch.filled and ch.lines_for(path):
                    ch.auto_fill(path, style)
                regs = [r for r in page.regions if r.text.strip() and not r.cleaned]
                page.clean_many(regs, fit)

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

        self._run_batch("Đang đặt chữ lên bản redraw:", job, msg + " Câu dịch đã có được đặt lại trên nền mới.")

    def _export_pages(self, pages: list[Page], out_dir: str, psd: bool = True, jpeg: bool = False):
        def job(report):
            exporter = PhotoshopExporter() if psd and photoshop_available() else None
            for i, page in enumerate(pages):
                report(f"{i + 1}/{len(pages)} ({os.path.basename(page.path)})…")
                base = os.path.join(out_dir, os.path.splitext(os.path.basename(page.path))[0])
                if jpeg:
                    page.export(base + ".jpg")
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
            self.page.export(path)
            self.statusBar().showMessage(f"Đã lưu: {path}")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Lỗi", str(e))
