"""Bảng điều khiển Lilyzono: cửa sổ chạy cạnh Photoshop, làm từng bước của quy trình typeset theo ID vùng thoại.

Script Lilyzono.jsx trong Photoshop mở cửa sổ này (LilyzonoPhotoshop.py --panel --script <đường dẫn jsx>). Cửa sổ
điều khiển Photoshop qua scripting (COM): chụp ảnh trang, gắn raw, tạo layer xoá chữ và layer chữ. Mọi kết quả nằm
trong thư mục dự án (app.core.workflow), chỉnh tay trong bảng thoại được giữ khi chạy lại bước khác.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPen, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem, QGraphicsView, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QPushButton, QSpinBox, QSplitter, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .core import ai, secrets, workflow
from .core.semantic_review import TypesetBlocked
from .core.workflow import BUBBLES, STEPS, STATES, Project

INSTANCE = "LilyzonoPanel-" + hashlib.md5(os.environ.get("USERNAME", "").encode()).hexdigest()[:8]
ICONS = {"done": "✓", "warn": "!", "error": "✗", "stale": "↻", "pending": "·", "running": "…", "blocked": "!"}
COLORS = {"done": "#3fb950", "warn": "#d29922", "error": "#f85149", "stale": "#58a6ff", "pending": "#8b949e",
          "running": "#c9d1d9", "blocked": "#d29922"}
BUBBLE_COLORS = {"round": "#2f81f7", "square": "#a371f7", "ray": "#f0883e", "cloud": "#3fb950", "sfx": "#db61a2",
                 "free": "#e3b341", "skip": "#6e7681"}
REQUIRES = {"detect": ["open"], "setup": ["detect"], "raw": ["open"], "ocr": ["raw", "detect"],
            "clean": ["detect"], "match": ["detect"], "typeset": ["detect"]}
COLS = ["ID", "Loại bóng", "Kiểu chữ", "Font", "Cỡ", "Chữ Nhật (OCR)", "Việt tham chiếu (AI)",
        "Bản dịch (mỗi câu một dòng)", "Đối chiếu nghĩa", "Tin cậy", "Ghi chú"]
C_ID, C_BUBBLE, C_KIND, C_FONT, C_SIZE, C_JA, C_VI, C_LINES, C_MEANING, C_CONF, C_NOTE = range(len(COLS))


def _batch_status(names: list[str]) -> str:
    complete = names.count("done") + names.count("warn")
    details = [f"Xong {complete}/{len(names)} trang"]
    for state, label in (("blocked", "cần đối chiếu"), ("error", "lỗi"), ("stale", "cần chạy lại"),
                         ("running", "đang chạy"), ("pending", "chưa chạy"), ("warn", "có cảnh báo")):
        if count := names.count(state):
            details.append(f"{count} {label}")
    return " · ".join(details)


# --- Photoshop qua COM ----------------------------------------------------------------------------
class PhotoshopError(Exception):
    pass


class Photoshop:
    """Gọi LilyzonoPS.bridge trong Photoshop đang mở. Photoshop bận (đang có hộp thoại) thì thử lại tới 60 giây."""

    def __init__(self, script: str):
        self.script = script
        self._local = threading.local()

    def call(self, command: str, /, wait: bool = True, **args):
        import pythoncom
        import pywintypes
        import win32com.client

        if not getattr(self._local, "com", False):
            pythoncom.CoInitialize()
            self._local.com = True
        if not self.script or not Path(self.script).is_file():
            raise PhotoshopError("Không tìm thấy Lilyzono.jsx: mở bảng điều khiển từ Photoshop (File › Scripts).")
        code = ("var LILYZONO_PS_NO_DIALOG = true; $.evalFile(new File(%s)); LilyzonoPS.bridge(%s, %s);"
                % (json.dumps(self.script), json.dumps(command), json.dumps(json.dumps(args))))
        start = time.time()
        while True:
            try:
                ps = win32com.client.GetActiveObject("Photoshop.Application")
                out = ps.DoJavaScript(code)
                break
            except pywintypes.com_error as exc:
                busy = exc.args and exc.args[0] in (-2147417846, -2147418111)  # RPC_E_SERVERCALL_RETRYLATER / CALL_REJECTED
                if busy and wait and time.time() - start < 60:
                    time.sleep(0.5)
                    continue
                if exc.args and exc.args[0] == -2147221021:  # MK_E_UNAVAILABLE
                    raise PhotoshopError("Photoshop chưa mở.") from None
                raise PhotoshopError(f"Photoshop không nhận lệnh “{command}”"
                                     f"{' (đang bận – đóng hộp thoại đang mở trong Photoshop)' if busy else ''}: {exc}") from None
        try:
            data = json.loads(out)
        except (TypeError, ValueError):
            raise PhotoshopError(f"Photoshop trả về dữ liệu lạ cho “{command}”: {str(out)[:200]}") from None
        if "error" in data:
            raise PhotoshopError(data["error"])
        return data.get("result")


# --- thiết lập AI --------------------------------------------------------------------------------
def load_ai_config() -> ai.AIConfig:
    s = QSettings("Lilyzono", "Panel")
    provider = s.value("ai/provider", "none")
    return ai.AIConfig(provider=provider if provider in ai.PROVIDERS else "none", model=s.value("ai/model", ""),
                       match_model=s.value("ai/match_model", ""), base_url=s.value("ai/base_url", ""),
                       send_image_on_match=s.value("ai/match_image", "true") == "true")


def save_ai_config(cfg: ai.AIConfig) -> None:
    s = QSettings("Lilyzono", "Panel")
    s.setValue("ai/provider", cfg.provider)
    s.setValue("ai/model", cfg.model)
    s.setValue("ai/match_model", cfg.match_model)
    s.setValue("ai/base_url", cfg.base_url)
    s.setValue("ai/match_image", "true" if cfg.send_image_on_match else "false")


class AIDialog(QDialog):
    """Nhà cung cấp, model bước 2 / bước 7, địa chỉ máy chủ, API key (lưu trong Windows Credential Manager)."""

    def __init__(self, cfg: ai.AIConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cấu hình AI")
        self.resize(560, 0)
        form = QFormLayout(self)
        self.provider = QComboBox()
        for key, info in ai.PROVIDERS.items():
            self.provider.addItem(info["title"], key)
        self.provider.setCurrentIndex(max(0, self.provider.findData(cfg.provider)))
        self.model = QComboBox(editable=True)
        self.match_model = QComboBox(editable=True)
        self.base_url = QLineEdit(cfg.base_url)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.image = QCheckBox("Gửi kèm ảnh trang khi ghép bản dịch (chính xác hơn, tốn thêm token)")
        self.image.setChecked(cfg.send_image_on_match)
        self.info = QLabel()
        self.info.setWordWrap(True)
        load = self._load_btn = QPushButton("Tải danh sách model")
        test = self._test_btn = QPushButton("Kiểm tra kết nối")
        form.addRow("Nhà cung cấp", self.provider)
        form.addRow("Model bước 2 (đọc ảnh)", self.model)
        form.addRow("Model bước 7 (trống = như bước 2)", self.match_model)
        form.addRow("Địa chỉ API (trống = mặc định)", self.base_url)
        form.addRow("API key", self.key)
        row = QHBoxLayout()
        row.addWidget(load)
        row.addWidget(test)
        form.addRow("", row)
        form.addRow(self.image)
        form.addRow(self.info)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        form.addRow(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        load.clicked.connect(self._load_models)
        test.clicked.connect(self._test)
        self._provider_changed()
        self.model.setCurrentText(cfg.model or ai.PROVIDERS[cfg.provider]["model"])
        self.match_model.setCurrentText(cfg.match_model)

    def _provider_changed(self):
        key = self.provider.currentData()
        info = ai.PROVIDERS[key]
        on = key != "none"
        for w in (self.model, self.match_model, self.base_url, self.key, self.image, self._load_btn, self._test_btn):
            w.setEnabled(on)
        for box in (self.model, self.match_model):
            box.clear()
            if key == "gemini":  # model có gói miễn phí lên đầu danh sách
                box.addItems(([""] if box is self.match_model else []) + [ai.GEMINI_FREE, *ai.FREE_MODELS])
        self.model.setCurrentText(info["model"])
        if not on:
            self.info.setText("Không gọi API. Bước 7 đề xuất ghép trên máy rồi đối chiếu nghĩa Nhật–Việt bằng model "
                              "cục bộ. Bước 8 dừng nếu còn vùng chưa xác minh hoặc câu chưa đặt. "
                              "Câu ngắn/OCR lỗi cần xem Raw và xác nhận ở bảng thoại; không tự tạo bản dịch mới.")
            return
        self.base_url.setPlaceholderText(info["url"])
        saved = secrets.get_key(key)
        self.key.setText("")
        self.key.setPlaceholderText("đã lưu – để trống để giữ" if saved else
                                    ("không bắt buộc" if key == "compatible" else f"hoặc biến môi trường {info['env']}"))
        self.info.setText("Bước 7 tự đọc chữ Nhật còn thiếu, dịch Việt tham chiếu và ghép câu của bạn theo nghĩa. "
                         "Key lưu trong Windows Credential Manager, không ghi ra file." +
                          (f" “{ai.GEMINI_FREE}”: dùng lần lượt các model có gói miễn phí ({', '.join(ai.FREE_MODELS)}), "
                           "model nào hết hạn mức / quá tải thì tự chuyển sang model sau." if key == "gemini" else "") +
                          (" Máy chủ cục bộ (Ollama / LM Studio): điền địa chỉ …/v1 và tên model đọc được ảnh."
                           if key == "compatible" else ""))

    def config(self) -> ai.AIConfig:
        return ai.AIConfig(provider=self.provider.currentData(), model=self.model.currentText().strip(),
                           match_model=self.match_model.currentText().strip(), base_url=self.base_url.text().strip(),
                           send_image_on_match=self.image.isChecked())

    def _store_key(self):
        if self.key.text().strip():
            secrets.set_key(self.provider.currentData(), self.key.text().strip())
            self.key.setText("")
            self._provider_changed_keep()

    def _provider_changed_keep(self):
        self.key.setPlaceholderText("đã lưu – để trống để giữ")

    def _load_models(self):
        self._store_key()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            models = ai.list_models(self.config())
        except ai.AIError as exc:
            QMessageBox.warning(self, "Cấu hình AI", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        for box in (self.model, self.match_model):
            text = box.currentText()
            box.clear()
            box.addItems(([""] if box is self.match_model else []) + models)
            box.setCurrentText(text)
        self.info.setText(f"{len(models)} model.")

    def _test(self):
        self._store_key()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            cfg = self.config()
            data = ai.ask_json(cfg, "detect", "Reply with the requested JSON.", 'Return {"ok": true}.', [],
                               {"type": "object", "additionalProperties": False, "required": ["ok"],
                                "properties": {"ok": {"type": "boolean"}}}, "ping")
            QMessageBox.information(self, "Cấu hình AI", f"Kết nối được {cfg.title} ({cfg.model_for('detect')}): {data}")
        except ai.AIError as exc:
            QMessageBox.warning(self, "Cấu hình AI", str(exc))
        finally:
            QApplication.restoreOverrideCursor()

    def accept(self):
        self._store_key()
        super().accept()


# --- chọn font trong danh sách font của Photoshop ---------------------------------------------------------
class FontPicker(QDialog):
    def __init__(self, fonts: list[dict], current: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chọn font (danh sách của Photoshop)")
        self.resize(420, 480)
        self.fonts, self.choice = fonts, None
        lay = QVBoxLayout(self)
        self.search = QLineEdit(current)
        self.search.setPlaceholderText("Gõ để lọc…")
        self.list = QListWidget()
        lay.addWidget(self.search)
        lay.addWidget(self.list)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        clear = buttons.addButton("Bỏ font riêng", QDialogButtonBox.ResetRole)
        lay.addWidget(buttons)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        clear.clicked.connect(self._clear)
        self.list.itemDoubleClicked.connect(lambda _: self._ok())
        self.search.textChanged.connect(self._fill)
        self._fill()

    def _fill(self):
        q = self.search.text().lower()
        self.list.clear()
        for f in self.fonts:
            if not q or q in f["name"].lower() or q in f["postscript"].lower():
                item = QListWidgetItem(f["name"])
                item.setData(Qt.UserRole, f)
                self.list.addItem(item)
                if self.list.count() >= 400:
                    break
        if self.list.count():
            self.list.setCurrentRow(0)

    def _ok(self):
        item = self.list.currentItem()
        if item:
            f = item.data(Qt.UserRole)
            self.choice = {"family": f["family"], "style": f["style"], "postscript": f["postscript"]}
            self.accept()

    def _clear(self):
        self.choice = {}
        self.accept()


def font_label(font: dict) -> str:
    if not font or not font.get("family"):
        return ""
    style = font.get("style", "")
    return font["family"] + ("" if style.lower() in ("", "regular", "normal", "roman") else " " + style)


# --- loại bóng → kiểu chữ / font, loại được xoá chữ ---------------------------------------------------------
class KindsDialog(QDialog):
    def __init__(self, project: Project, fonts_source, parent=None):
        from .core.typeset_profiles import fonts_for, kinds_for

        super().__init__(parent)
        self.setWindowTitle("Loại bóng → kiểu chữ, font")
        self.project, self.fonts_source = project, fonts_source
        kinds = kinds_for(project.data["profile"])
        defaults = {**workflow.default_kind_map(kinds), **project.data.get("kind_map", {})}
        self.fonts = {k: dict(v) for k, v in project.data.get("kind_fonts", {}).items()}
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Kiểu chữ là loại font của preset đang chọn. Font riêng (tuỳ chọn) thay font của preset "
                             "cho mọi vùng thuộc loại bóng đó; từng vùng vẫn chỉnh riêng được trong bảng thoại."))
        grid = QGridLayout()
        for c, title in enumerate(["Loại bóng", "Kiểu chữ (preset)", "Font riêng", "Nghiêng", "Xoá chữ (bước 6)"]):
            grid.addWidget(QLabel(f"<b>{title}</b>"), 0, c)
        self.rows = {}
        clean = set(project.data.get("clean_kinds") or workflow.CLEAN_KINDS)
        for i, (bubble, label) in enumerate((k, v) for k, v in BUBBLES.items() if k != "skip"):
            kind = QComboBox()
            for k, text in kinds.items():
                if k != "auto":
                    kind.addItem(f"{text} · {fonts_for(project.data['profile']).get(k, '')}", k)
            kind.setCurrentIndex(max(0, kind.findData(defaults.get(bubble))))
            font = QPushButton(font_label(self.fonts.get(bubble, {})) or "theo preset")
            font.clicked.connect(lambda _=False, b=bubble, btn=font: self._pick(b, btn))
            italic = QComboBox()
            italic.addItems(["theo preset", "có", "không"])
            it = self.fonts.get(bubble, {}).get("italic")
            italic.setCurrentIndex(0 if it is None else (1 if it else 2))
            cl = QCheckBox()
            cl.setChecked(bubble in clean)
            for c, w in enumerate([QLabel(label), kind, font, italic, cl]):
                grid.addWidget(w, i + 1, c)
            self.rows[bubble] = (kind, italic, cl)
        lay.addLayout(grid)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        lay.addWidget(buttons)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

    def _pick(self, bubble, button):
        fonts = self.fonts_source()
        if fonts is None:
            return
        dlg = FontPicker(fonts, font_label(self.fonts.get(bubble, {})), self)
        if dlg.exec() and dlg.choice is not None:
            self.fonts[bubble] = dlg.choice
            button.setText(font_label(dlg.choice) or "theo preset")

    def apply(self) -> None:
        kind_map, clean = {}, []
        for bubble, (kind, italic, cl) in self.rows.items():
            kind_map[bubble] = kind.currentData()
            f = dict(self.fonts.get(bubble, {}))
            if italic.currentIndex():
                f["italic"] = italic.currentIndex() == 1
            else:
                f.pop("italic", None)
            if f:
                self.fonts[bubble] = f
            else:
                self.fonts.pop(bubble, None)
            if cl.isChecked():
                clean.append(bubble)
        self.project.data.update(kind_map=kind_map, kind_fonts=self.fonts, clean_kinds=clean)


# --- xem trang -----------------------------------------------------------------------------------
class PageView(QGraphicsView):
    regionClicked = Signal(str)
    boxDrawn = Signal(list)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.adding = False
        self._start = None
        self._rubber = None
        self._items = {}

    def show_page(self, image_path: str | None, regions: list[dict], selected: str = ""):
        scene = self.scene()
        scene.clear()
        self._items = {}
        self._rubber = None
        if not image_path or not Path(image_path).exists():
            scene.addText("Chưa có ảnh (chạy bước 1).")
            return
        pix = QPixmap(image_path)
        scene.addPixmap(pix)
        scene.setSceneRect(QRectF(pix.rect()))
        width = max(2.0, pix.width() / 400)
        font = QFont()
        font.setPixelSize(max(12, int(pix.width() / 45)))
        font.setBold(True)
        for r in regions:
            x1, y1, x2, y2 = r["box"]
            color = QColor(BUBBLE_COLORS.get(r["bubble"], "#2f81f7"))
            pen = QPen(QColor("#ff4d4f") if r["id"] == selected else color, width * (2 if r["id"] == selected else 1))
            if r["uncertain"]:
                pen.setStyle(Qt.DashLine)
            rect = QGraphicsRectItem(x1, y1, x2 - x1, y2 - y1)
            rect.setPen(pen)
            rect.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 30)))
            rect.setData(0, r["id"])
            rect.setToolTip(f"{r['id']} · {BUBBLES[r['bubble']]}\n{r['ja']}\n" + "\n".join(r["lines"]))
            scene.addItem(rect)
            label = QGraphicsSimpleTextItem(r["id"] + (" ?" if r["uncertain"] else ""))
            label.setFont(font)
            label.setBrush(QBrush(QColor("#ffffff")))
            bg = QGraphicsRectItem(label.boundingRect())
            bg.setBrush(QBrush(color))
            bg.setPen(QPen(Qt.NoPen))
            bg.setPos(x1, max(0, y1 - label.boundingRect().height()))
            label.setParentItem(bg)
            bg.setData(0, r["id"])
            scene.addItem(bg)
            self._items[r["id"]] = rect
        self.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.scene().items():
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)

    def wheelEvent(self, event):
        f = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(f, f)

    def mousePressEvent(self, event):
        pos = self.mapToScene(event.position().toPoint())
        if self.adding and event.button() == Qt.LeftButton:
            self._start = pos
            self._rubber = QGraphicsRectItem(QRectF(pos, pos))
            self._rubber.setPen(QPen(QColor("#ff4d4f"), 3, Qt.DashLine))
            self.scene().addItem(self._rubber)
            return
        for item in self.scene().items(pos):
            rid = item.data(0)
            if rid:
                self.regionClicked.emit(rid)
                break
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._rubber is not None and self._start is not None:
            self._rubber.setRect(QRectF(self._start, self.mapToScene(event.position().toPoint())).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._rubber is not None and self._start is not None:
            rect = self._rubber.rect()
            self.scene().removeItem(self._rubber)
            self._rubber, self._start = None, None
            if rect.width() > 8 and rect.height() > 8:
                self.boxDrawn.emit([int(rect.left()), int(rect.top()), int(rect.right()), int(rect.bottom())])
            return
        super().mouseReleaseEvent(event)


class LinesDelegate(QStyledItemDelegate):
    """Ô bản dịch: sửa nhiều dòng (mỗi câu một dòng)."""

    def createEditor(self, parent, option, index):
        editor = QPlainTextEdit(parent)
        editor.setMinimumHeight(90)
        return editor

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data(Qt.UserRole) or "")

    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText(), Qt.UserRole)  # chữ hiển thị được vẽ lại khi bảng nạp lại

    def updateEditorGeometry(self, editor, option, index):
        r = option.rect
        editor.setGeometry(r.x(), r.y(), max(r.width(), 320), max(r.height(), 110))


# --- chạy bước ở luồng riêng -------------------------------------------------------------------------
class Signals(QObject):
    log = Signal(str, str)  # (mức: info / warn / error / ok, nội dung)
    progress = Signal(str)
    page = Signal(str)  # trang vừa đổi trạng thái
    finished = Signal()


class Runner:
    def __init__(self, window: "PanelWindow"):
        self.w = window
        self.signals = Signals()
        self.cancel = False
        self.thread = None
        self._detector = None

    @property
    def busy(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self, steps: list[str], keys: list[str]) -> None:
        self.cancel = False
        self.steps = list(steps)
        self.keys = list(keys)
        self.thread = threading.Thread(target=self._run, args=(steps, keys), daemon=True)
        self.thread.start()

    def start_redraw(self, jobs) -> None:
        self.cancel = False
        self.steps = ["replace_redraw"]
        self.keys = [doc["name"] for doc, _ in jobs]
        self.redraw_done = []
        self.thread = threading.Thread(target=self._run_redraw, args=(jobs,), daemon=True)
        self.thread.start()

    def _run_redraw(self, jobs) -> None:
        project = self.w.project
        try:
            for doc, path in jobs:
                if self.cancel:
                    break
                key = doc["name"]
                page = project.page(key)
                self.signals.progress.emit(f"{key} · Đang thay bản redraw…")
                try:
                    with tempfile.TemporaryDirectory() as folder:
                        out = self.w.ps.call("replace_redraw", layer=path, folder=folder, id=doc.get("id"), name=key)
                        self.redraw_done.append(key)
                        page["has_redraw"] = True
                        for name in ("cleaned.png", "assignment-preview.png", "preview.png"):
                            project.file(page, name).unlink(missing_ok=True)
                        raw_changed = out["raw"] and workflow.copy_image(
                            out["raw"], project.file(page, "raw.png"), compare_pixels=True)
                        if raw_changed:
                            project.invalidate(page, "open")
                        workflow.copy_image(out["work"], project.file(page, "work.png"))
                        if out.get("preview"):
                            workflow.copy_image(out["preview"], project.file(page, "preview.png"))
                        page["size"] = out["size"]
                        project.set_status(page, "open", "done")
                    self.signals.log.emit("ok", f"{key} · Đã thay bản redraw.")
                except (PhotoshopError, OSError, ValueError) as exc:
                    label = "Đã thay redraw, chưa cập nhật ảnh xem trước" if key in self.redraw_done else "Chưa thay redraw"
                    self.signals.log.emit("error", f"{key} · {label}. {exc}")
                self.signals.page.emit(key)
        finally:
            project.save()
            self.signals.finished.emit()

    def detector(self):
        if self._detector is None:
            from .core.detect import TextDetector

            self.signals.progress.emit("Nạp model nhận diện chữ…")
            self._detector = TextDetector()
        return self._detector

    def _run(self, steps, keys):
        p = self.w.project
        try:
            for step in steps:
                for key in keys:
                    if self.cancel:
                        self.signals.log.emit("warn", "Đã dừng.")
                        return
                    page = p.page(key)
                    missing = [s for s in REQUIRES.get(step, []) if p.status(page, s)["state"] not in ("done", "warn", "stale")]
                    if missing:
                        if len(steps) > 1 and p.status(page, missing[0])["state"] == "error":
                            continue  # chạy toàn bộ: trang đã lỗi ở bước trước thì bỏ qua, lỗi đã báo
                        msg = "Chưa chạy bước " + ", ".join(f"{_num(s)} {workflow.STEP_NAMES[s]}" for s in missing)
                        p.set_status(page, step, "error", msg)
                        self.signals.log.emit("error", f"{key} · {_num(step)} {workflow.STEP_NAMES[step]}: {msg}")
                        self.signals.page.emit(key)
                        continue
                    self._one(step, page)
        finally:
            p.save()
            self.signals.finished.emit()

    def _one(self, step, page):
        p, key, name = self.w.project, page["key"], f"{_num(step)} {workflow.STEP_NAMES[step]}"
        p.set_status(page, step, "running")
        self.signals.page.emit(key)
        self.signals.progress.emit(f"{key}: {name}…")

        def report(text):
            self.signals.progress.emit(f"{key}: {text}")

        try:
            warnings = self.run_step(step, page, report) or []
            p.set_status(page, step, "warn" if warnings else "done", "\n".join(warnings))
            for wmsg in warnings:
                self.signals.log.emit("warn", f"{key} · {name}: {wmsg}")
            self.signals.log.emit("ok", f"{key} · {name}: xong")
        except TypesetBlocked as exc:
            p.set_status(page, step, "blocked", str(exc))
            self.signals.log.emit("warn", f"{key} · {name}: {exc}")
        except (ai.AIError, PhotoshopError, ValueError, OSError) as exc:
            p.set_status(page, step, "error", str(exc))
            self.signals.log.emit("error", f"{key} · {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - lỗi lạ: báo kèm chỗ lỗi
            p.set_status(page, step, "error", f"{type(exc).__name__}: {exc}")
            self.signals.log.emit("error", f"{key} · {name}: {type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}")
        p.save()
        self.signals.page.emit(key)

    def run_step(self, step, page, report):
        p, ps = self.w.project, self.w.ps
        doc = {"id": page.get("doc_id"), "name": page.get("doc_name") or page["key"]}
        if step == "open":
            with tempfile.TemporaryDirectory() as tmp:
                out = ps.call("snapshot", folder=tmp, **doc)
                changed = workflow.copy_image(out["work"], p.file(page, "work.png"))
                raw_src = out["raw"] if p.uses_redraw(page) else out["work"]
                if page.get("has_redraw"):
                    changed = False  # chỉ thay nền, vẫn dùng cùng ảnh Nhật để đối chiếu
                if raw_src:
                    changed |= workflow.copy_image(raw_src, p.file(page, "raw.png"), compare_pixels=bool(page.get("has_redraw")))
            page["size"] = out["size"]
            if changed:
                p.invalidate(page, "open")
            return [] if changed else ["Ảnh không đổi so với lần trước"]
        if step == "raw":
            if not p.uses_redraw(page):
                workflow.copy_image(p.file(page, "work.png"), p.file(page, "raw.png"))
                return ["Ảnh đang mở là raw: dùng luôn ảnh này"]
            files = workflow.raw_files_for(page["key"], p.data.get("raw_folder", ""), p.data["right_to_left"])
            if not files:
                if p.has(page, "raw.png"):
                    return ["Dùng layer raw đã gắn trong tài liệu"]
                raise ValueError("Chưa có raw cho trang này: bấm “Chọn folder raw…” (tên ảnh raw có số trang).")
            if files == page.get("raw_files") and p.has(page, "raw.png"):
                return []
            report("Gắn raw vào tài liệu Photoshop…")
            ps.call("attach_raw", raws=files, **doc)
            with tempfile.TemporaryDirectory() as tmp:
                out = ps.call("snapshot", folder=tmp, **doc)
                if not out["raw"]:
                    raise PhotoshopError("Đã gắn raw nhưng không đọc lại được layer raw.")
                changed = workflow.copy_image(out["raw"], p.file(page, "raw.png"))
            page["raw_files"] = files
            if changed:
                p.invalidate(page, "raw")
                if p.status(page, "detect")["state"] in ("done", "warn"):
                    p.set_status(page, "detect", "stale", "Vừa nạp raw: nhận diện lại trên raw")
            return []
        if step == "detect":
            out = workflow.detect(p, page, self.detector(), self.w.ai_config, report)
        elif step == "setup":
            out = workflow.setup(p, page)
        elif step == "ocr":
            out = workflow.ocr(p, page, report)
        elif step == "clean":
            layer, out = workflow.clean(p, page)
            if layer:
                report("Tạo layer xoá chữ trong Photoshop…")
                ps.call("place_layer", png=layer, **doc)
        elif step == "match":
            p.file(page, "assignment-preview.png").unlink(missing_ok=True)
            out = workflow.match(p, page, self.w.ai_config, report)
            if any(r["lines"] and r["bubble"] != "skip" for r in page["regions"]):
                report("Dựng ảnh xem trước: đặt chữ Việt vào các bóng đã gán…")
                _, preview_warnings = workflow.preview_assignments(p, page, report)
                out.extend(preview_warnings)
        elif step == "typeset":
            path, out = workflow.typeset(p, page, report)
            report("Tạo layer chữ trong Photoshop…")
            ps.call("typeset", json=path, **doc)
        else:
            raise ValueError(step)
        p.invalidate(page, step)
        return out


def _num(step: str) -> int:
    return [s for s, _ in STEPS].index(step) + 1


# --- cửa sổ chính ---------------------------------------------------------------------------------
class PanelWindow(QMainWindow):
    def __init__(self, script: str):
        super().__init__()
        from .core.typeset_profiles import PROFILES

        self.setWindowTitle("Lilyzono · Bảng điều khiển typeset")
        self.ps = Photoshop(script)
        self.ai_config = load_ai_config()
        self.settings = QSettings("Lilyzono", "Panel")
        self.project: Project | None = None
        self.current = ""  # trang đang xem
        self.selected = ""  # ID vùng đang chọn
        self._fonts = None
        self._filling = False
        self.runner = Runner(self)
        self.runner.signals.log.connect(self.log)
        self.runner.signals.progress.connect(lambda t: self.progress.setText(t))
        self.runner.signals.page.connect(self._page_changed)
        self.runner.signals.finished.connect(self._finished)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # 1 · ảnh, raw, bản dịch, preset, AI
        inputs = QGroupBox("Ảnh, raw và bản dịch")
        grid = QGridLayout(inputs)
        self.source = QComboBox()
        self.source.addItem("Bản Raw (chữ Nhật còn nguyên)", "raw")
        self.source.addItem("Bản Redraw (chữ đã xoá)", "redraw")
        get_docs = QPushButton("Lấy ảnh đang mở trong Photoshop")
        open_files = QPushButton("Mở ảnh…")
        self.upload_redraw_btn = QPushButton("Nạp bản redraw…")
        self.upload_redraw_btn.setToolTip("Chọn một hoặc nhiều ảnh redraw để thay nền các trang đang mở theo số trang.")
        self.raw_label = QLabel("—")
        pick_raw = QPushButton("Chọn folder raw…")
        self.tl_label = QLabel("—")
        pick_tl = QPushButton("Mở bản dịch (.docx / .txt / .xlsx)…")
        self.numbering = QComboBox()
        self.numbering.addItem("Số trang theo số trong tên ảnh", "name")
        self.numbering.addItem("Số trang theo thứ tự ảnh (1, 2, 3…)", "order")
        self.profile = QComboBox()
        for k, v in PROFILES.items():
            self.profile.addItem(v, k)
        self.rtl = QCheckBox("Đọc phải → trái")
        self.screentone = QCheckBox("Giữ lưới chấm khi xoá chữ (offline)")
        self.screentone.setToolTip("Áp dụng khi chạy Bước 6. Chọn loại bóng được xoá trong “Loại bóng → font…”.")
        kinds_btn = QPushButton("Loại bóng → font…")
        ai_btn = QPushButton("Cấu hình AI…")
        self.ai_label = QLabel()
        self.folder_label = QLabel()
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        open_folder = QPushButton("Mở thư mục dự án")
        grid.addWidget(QLabel("Bản đang mở là"), 0, 0)
        grid.addWidget(self.source, 0, 1)
        grid.addWidget(get_docs, 0, 2)
        grid.addWidget(open_files, 0, 3)
        grid.addWidget(QLabel("Raw"), 1, 0)
        grid.addWidget(self.raw_label, 1, 1, 1, 2)
        grid.addWidget(pick_raw, 1, 3)
        grid.addWidget(QLabel("Bản dịch"), 2, 0)
        grid.addWidget(self.tl_label, 2, 1)
        grid.addWidget(self.numbering, 2, 2)
        grid.addWidget(pick_tl, 2, 3)
        grid.addWidget(QLabel("Preset font"), 3, 0)
        grid.addWidget(self.profile, 3, 1)
        grid.addWidget(self.rtl, 3, 2)
        grid.addWidget(kinds_btn, 3, 3)
        grid.addWidget(QLabel("AI"), 4, 0)
        grid.addWidget(self.ai_label, 4, 1, 1, 2)
        grid.addWidget(ai_btn, 4, 3)
        grid.addWidget(QLabel("Dự án"), 5, 0)
        grid.addWidget(self.folder_label, 5, 1, 1, 2)
        grid.addWidget(open_folder, 5, 3)
        grid.addWidget(self.screentone, 6, 0, 1, 3)
        grid.addWidget(self.upload_redraw_btn, 6, 3)
        grid.setColumnStretch(1, 1)

        # các bước
        steps = QGroupBox("Các bước")
        sgrid = QGridLayout(steps)
        self.scope = QComboBox()
        self.scope.addItem("Trang đang chọn", "page")
        self.scope.addItem("Tất cả trang", "all")
        self.step_status = {}
        for i, (key, name) in enumerate(STEPS):
            sgrid.addWidget(QLabel(f"<b>{i + 1}</b> · {name}"), i, 0)
            lab = QLabel()
            lab.setMinimumWidth(170)
            sgrid.addWidget(lab, i, 1)
            btn = QPushButton("Lưu thiết lập" if key == "setup" else "Chạy")
            btn.clicked.connect(lambda _=False, k=key: self.run([k]))
            sgrid.addWidget(btn, i, 2)
            self.step_status[key] = (lab, btn)
        run_all = QPushButton("▶ Chạy toàn bộ")
        run_all.setStyleSheet("font-weight: bold; padding: 6px;")
        self.stop_btn = QPushButton("Dừng")
        self.stop_btn.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(QLabel("Phạm vi"))
        row.addWidget(self.scope)
        sgrid.addLayout(row, len(STEPS), 0, 1, 3)
        row2 = QHBoxLayout()
        row2.addWidget(run_all)
        row2.addWidget(self.stop_btn)
        sgrid.addLayout(row2, len(STEPS) + 1, 0, 1, 3)
        self.run_all_btn = run_all
        self.progress = QLabel("")
        self.progress.setWordWrap(True)
        sgrid.addWidget(self.progress, len(STEPS) + 2, 0, 1, 3)
        note = QLabel("Bản redraw: “Chạy toàn bộ” làm bước 4 (nạp raw) trước bước 2, vì chữ Nhật trên redraw đã bị xoá.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #8b949e;")
        sgrid.addWidget(note, len(STEPS) + 3, 0, 1, 3)

        top = QHBoxLayout()
        top.addWidget(inputs, 3)
        top.addWidget(steps, 2)
        root.addLayout(top)

        # trang | ảnh | bảng thoại
        split = QSplitter(Qt.Horizontal)
        self.pages = QListWidget()
        self.pages.setMinimumWidth(180)
        split.addWidget(self.pages)
        left = QWidget()
        llay = QVBoxLayout(left)
        llay.setContentsMargins(0, 0, 0, 0)
        vrow = QHBoxLayout()
        self.view_kind = QComboBox()
        self.view_kind.addItem("Ảnh làm việc", "work.png")
        self.view_kind.addItem("Raw", "raw.png")
        self.view_kind.addItem("Đã xoá chữ", "cleaned.png")
        self.view_kind.addItem("Bản gán thử (chữ Việt trong bóng)", "assignment-preview.png")
        self.view_kind.addItem("Kết quả typeset", "preview.png")
        self.add_btn = QPushButton("+ Thêm vùng (kéo trên ảnh)")
        self.add_btn.setCheckable(True)
        vrow.addWidget(self.view_kind)
        vrow.addWidget(self.add_btn)
        llay.addLayout(vrow)
        self.view = PageView()
        llay.addWidget(self.view)
        split.addWidget(left)

        right = QWidget()
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setItemDelegateForColumn(C_LINES, LinesDelegate(self.table))
        hdr = self.table.horizontalHeader()
        for c in (C_ID, C_BUBBLE, C_KIND, C_SIZE, C_CONF):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(C_JA, QHeaderView.Stretch)
        hdr.setSectionResizeMode(C_VI, QHeaderView.Stretch)
        hdr.setSectionResizeMode(C_LINES, QHeaderView.Stretch)
        rlay.addWidget(QLabel("<b>Bảng thoại</b> · ô in đậm là chỉnh tay (giữ nguyên khi chạy lại); dòng vàng: AI chưa chắc"))
        rlay.addWidget(self.table, 3)
        trow = QHBoxLayout()
        up, down = QPushButton("↑"), QPushButton("↓")
        delete = QPushButton("Xoá vùng")
        unlock = QPushButton("Bỏ khoá chỉnh tay")
        confirm_meaning = QPushButton("Xác nhận nghĩa vùng…")
        confirm_meaning.clicked.connect(self.confirm_meaning)
        for b in (up, down, delete, unlock, confirm_meaning):
            trow.addWidget(b)
        trow.addStretch(1)
        rlay.addLayout(trow)
        lines_box = QGroupBox("Câu dịch của trang")
        lb = QGridLayout(lines_box)
        self.tl_page = QSpinBox()
        self.tl_page.setRange(0, 9999)
        self.tl_page.setSpecialValueText("tự động")
        self.lines = QPlainTextEdit()
        self.lines.setPlaceholderText("Mỗi câu một dòng, theo thứ tự đọc.")
        save_lines = QPushButton("Lưu câu dịch của trang")
        reset_lines = QPushButton("Lấy lại từ file")
        self.unplaced = QListWidget()
        assign_btn = QPushButton("Gán câu này vào vùng đang chọn")
        lb.addWidget(QLabel("Trang trong bản dịch"), 0, 0)
        lb.addWidget(self.tl_page, 0, 1)
        lb.addWidget(save_lines, 0, 2)
        lb.addWidget(reset_lines, 0, 3)
        lb.addWidget(self.lines, 1, 0, 1, 2)
        ubox = QVBoxLayout()
        ubox.addWidget(QLabel("Câu chưa có vùng"))
        ubox.addWidget(self.unplaced)
        ubox.addWidget(assign_btn)
        lb.addLayout(ubox, 1, 2, 1, 2)
        rlay.addWidget(lines_box, 2)
        split.addWidget(right)
        split.setSizes([180, 520, 900])
        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(split)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        vsplit.addWidget(self.log_view)
        vsplit.setSizes([700, 140])
        root.addWidget(vsplit, 1)

        # sự kiện
        get_docs.clicked.connect(self.load_documents)
        open_files.clicked.connect(self.open_images)
        self.upload_redraw_btn.clicked.connect(self.upload_redraw)
        pick_raw.clicked.connect(self.pick_raw)
        pick_tl.clicked.connect(self.pick_translation)
        kinds_btn.clicked.connect(self.edit_kinds)
        ai_btn.clicked.connect(self.edit_ai)
        open_folder.clicked.connect(lambda: self.project and os.startfile(self.project.folder))
        self.source.currentIndexChanged.connect(lambda: self._setting("source", self.source.currentData(), ["open"]))
        self.numbering.currentIndexChanged.connect(lambda: self._setting("numbering", self.numbering.currentData(), ["match"]))
        self.profile.currentIndexChanged.connect(lambda: self._setting("profile", self.profile.currentData(), ["setup"]))
        self.rtl.toggled.connect(lambda v: self._setting("right_to_left", v, ["detect"]))
        self.screentone.toggled.connect(lambda v: self._setting("restore_screentone", v, ["clean"]))
        run_all.clicked.connect(lambda: self.run(workflow.RUN_ORDER))
        self.stop_btn.clicked.connect(self.stop)
        self.pages.currentItemChanged.connect(lambda cur, _: cur and self.show_page(cur.data(Qt.UserRole)))
        self.view_kind.currentIndexChanged.connect(lambda: self.refresh_view())
        self.add_btn.toggled.connect(self._toggle_add)
        self.view.regionClicked.connect(self.select_region)
        self.view.boxDrawn.connect(self.add_region)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemSelectionChanged.connect(self._row_selected)
        self.table.cellDoubleClicked.connect(self._cell_double)
        up.clicked.connect(lambda: self.move_region(-1))
        down.clicked.connect(lambda: self.move_region(1))
        delete.clicked.connect(self.delete_region)
        unlock.clicked.connect(self.unlock_region)
        save_lines.clicked.connect(self.save_lines)
        reset_lines.clicked.connect(self.reset_lines)
        assign_btn.clicked.connect(self.assign_unplaced)

        self.resize(1500, 950)
        self._update_ai_label()
        last = self.settings.value("last_project", "")
        if last and Path(last, "state.json").exists():
            self.open_project(Path(last))
        else:
            self._update_inputs()
        self.log("info", "Bắt đầu: bấm “Lấy ảnh đang mở trong Photoshop” (hoặc “Mở ảnh…”), chọn bản dịch, rồi chạy từng "
                         "bước hoặc “Chạy toàn bộ”.")

    # --- dự án ---------------------------------------------------------------------------------
    def open_project(self, folder: Path) -> None:
        self.project = Project(folder)
        self.settings.setValue("last_project", str(folder))
        self._update_inputs()
        self.refresh_pages()

    def _update_inputs(self):
        p = self.project
        self._filling = True
        try:
            for w in (self.source, self.numbering, self.profile, self.rtl, self.screentone):
                w.setEnabled(p is not None)
            if p is None:
                self.folder_label.setText("chưa có – lấy ảnh từ Photoshop để tạo")
                return
            self.source.setCurrentIndex(max(0, self.source.findData(p.data["source"])))
            self.numbering.setCurrentIndex(max(0, self.numbering.findData(p.data["numbering"])))
            self.profile.setCurrentIndex(max(0, self.profile.findData(p.data["profile"])))
            self.rtl.setChecked(p.data["right_to_left"])
            self.screentone.setChecked(bool(p.data.get("restore_screentone")))
            self.raw_label.setText(p.data.get("raw_folder") or ("không cần (ảnh đang mở là raw)"
                                                                 if p.data["source"] == "raw" else "chưa chọn"))
            tl = p.data.get("translation_file")
            self.tl_label.setText(Path(tl).name if tl else "chưa chọn")
            self.tl_label.setToolTip(tl or "")
            self.folder_label.setText(str(p.folder))
        finally:
            self._filling = False

    def _setting(self, key, value, invalidate):
        if self._filling or self.project is None or self.project.data.get(key) == value:
            return
        self.project.data[key] = value
        for page in self.project.pages:
            for step in invalidate:
                if self.project.status(page, step)["state"] in ("done", "warn"):
                    self.project.set_status(page, step, "stale", "Thiết lập đã đổi")
                self.project.invalidate(page, step)
        self.project.save()
        self._update_inputs()
        self.refresh_pages()

    def _project_for(self, docs: list[dict]) -> Path:
        first = docs[0]
        if first.get("path"):
            parent = Path(first["path"]).parent
            name = f"{workflow.slug(parent.name)}-{hashlib.md5(str(parent).lower().encode()).hexdigest()[:6]}"
        else:
            name = workflow.slug(Path(first["name"]).stem)
        return workflow.projects_root() / name

    def load_documents(self) -> list[dict] | None:
        if self.runner.busy:
            return
        try:
            data = self.ps.call("docs")
        except PhotoshopError as exc:
            self.log("error", str(exc))
            return
        docs = data["docs"]
        if not docs:
            self.log("warn", "Photoshop chưa mở ảnh nào: bấm “Mở ảnh…”.")
            return
        folder = self._project_for(docs)
        if self.project is None or self.project.folder != folder:
            self.open_project(folder)
        self.project.sync_documents(docs)
        if all(d["has_raw"] for d in docs) and not any(p["status"] for p in self.project.pages):
            self.project.data["source"] = "redraw"  # tất cả tài liệu đã có raw gắn riêng
        self.project.save()
        self._update_inputs()
        self.refresh_pages()
        self.log("info", f"{len(docs)} ảnh đang mở · dự án {folder}")
        return docs

    def open_images(self) -> None:
        if self.runner.busy:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Mở ảnh (bản Redraw hoặc Raw)", "",
                                                "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.psd)")
        if not files:
            return
        try:
            self.ps.call("open", paths=files)
        except PhotoshopError as exc:
            self.log("error", str(exc))
            return
        self.load_documents()

    def upload_redraw(self) -> None:
        if self.runner.busy:
            return
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn bản redraw để thay layer", "",
                                                "Ảnh (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff)")
        if not files:
            return
        self.commit_editors()
        docs = self.load_documents()
        if not docs:
            return
        jobs, rejected = workflow.redraw_targets(files, docs, self.current)
        for reason in rejected:
            self.log("warn", f"Bỏ qua {reason}.")
        if jobs:
            self._busy(True)
            self.runner.start_redraw(jobs)

    def pick_raw(self) -> None:
        if self.project is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Folder ảnh raw (tên ảnh có số trang)",
                                                  self.project.data.get("raw_folder", ""))
        if folder:
            self.project.data["raw_folder"] = folder
            if self.project.data["source"] == "raw":
                self.project.data["source"] = "redraw"
            missing = [p["key"] for p in self.project.pages if not workflow.raw_files_for(p["key"], folder, self.project.data["right_to_left"])]
            if missing:
                self.log("warn", "Không có raw cho: " + ", ".join(missing))
            for page in self.project.pages:
                if self.project.status(page, "raw")["state"] in ("done", "warn"):
                    self.project.set_status(page, "raw", "stale", "Đổi folder raw")
                page.pop("raw_files", None)
            self.project.save()
            self._update_inputs()
            self.refresh_pages()

    def pick_translation(self) -> None:
        if self.project is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Bản dịch", "", "Bản dịch (*.docx *.txt *.xlsx)")
        if not path:
            return
        self.project.data["translation_file"] = path
        try:
            pages = self.project.translation()
            self.log("info", f"Bản dịch: {len(pages)} trang, {sum(map(len, pages.values()))} câu.")
        except Exception as exc:  # noqa: BLE001
            self.log("error", f"Không đọc được bản dịch: {exc}")
        for page in self.project.pages:
            if self.project.status(page, "match")["state"] in ("done", "warn"):
                self.project.set_status(page, "match", "stale", "Đổi bản dịch")
        self.project.save()
        self._update_inputs()
        self.show_page(self.current)

    def fonts(self):
        if self._fonts is None:
            try:
                self._fonts = self.ps.call("fonts")
            except PhotoshopError as exc:
                QMessageBox.warning(self, "Lilyzono", f"Không lấy được danh sách font của Photoshop: {exc}")
                return None
        return self._fonts

    def edit_kinds(self) -> None:
        if self.project is None:
            return
        dlg = KindsDialog(self.project, self.fonts, self)
        if dlg.exec():
            dlg.apply()
            for page in self.project.pages:
                self.project.invalidate(page, "setup")
            self.project.save()
            self.refresh_pages()

    def edit_ai(self) -> None:
        dlg = AIDialog(self.ai_config, self)
        if dlg.exec():
            self.ai_config = dlg.config()
            save_ai_config(self.ai_config)
            self._update_ai_label()

    def _update_ai_label(self):
        c = self.ai_config
        if not c.enabled:
            self.ai_label.setText(c.title)
            return

        def name(step):
            m = c.model_for(step)
            return "model miễn phí (tự chọn)" if m == ai.GEMINI_FREE else m
        try:
            text = f"{c.title} · bước 2: {name('detect')} · bước 7: {name('match')}"
            c.key()
        except ai.AIError as exc:
            text = f"<span style='color:{COLORS['error']}'>{exc}</span>"
        self.ai_label.setText(text)

    # --- chạy ---------------------------------------------------------------------------------
    def scope_keys(self) -> list[str]:
        if self.project is None:
            return []
        if self.scope.currentData() == "all":
            return [p["key"] for p in self.project.pages if p.get("open", True)]
        return [self.current] if self.current else []

    def run(self, steps: list[str]) -> None:
        if self.runner.busy:
            return
        if self.project is None:
            self.load_documents()
            if self.project is None:
                return
        keys = self.scope_keys()
        if not keys:
            self.log("warn", "Chưa chọn trang nào.")
            return
        self.commit_editors()
        if any(s in steps for s in ("typeset", "setup", "match")):
            self._register_fonts()
        self._busy(True)
        self.log("info", f"Chạy {', '.join(str(_num(s)) for s in steps)} cho {len(keys)} trang…")
        self.runner.start(steps, keys)

    def _register_fonts(self):
        from .core.typeset_profiles import fonts_for
        from .photoshop import register_fonts

        p = self.project
        fams = set(fonts_for(p.data["profile"]).values())
        fams |= {f.get("family", "") for f in p.data.get("kind_fonts", {}).values()}
        fams |= {r["font"].get("family", "") for page in p.pages for r in page["regions"]}
        register_fonts([f for f in fams if f])

    def stop(self):
        self.runner.cancel = True
        self.progress.setText("Đang dừng sau việc hiện tại…")

    def _busy(self, busy: bool):
        for lab, btn in self.step_status.values():
            btn.setEnabled(not busy)
        self.run_all_btn.setEnabled(not busy)
        self.upload_redraw_btn.setEnabled(not busy)
        self.screentone.setEnabled(not busy and self.project is not None)
        self.stop_btn.setEnabled(busy)
        self.table.setEnabled(not busy)

    def _finished(self):
        self._busy(False)
        self.progress.setText("")
        self.refresh_pages()
        page = self.page_obj()
        steps = getattr(self.runner, "steps", [])
        keys = getattr(self.runner, "keys", [])
        if page and page["key"] in getattr(self.runner, "redraw_done", []) and "replace_redraw" in steps:
            name = ("preview.png" if self.project.has(page, "preview.png")
                    and self.project.status(page, "typeset")["state"] in ("done", "warn") else "work.png")
            self.view_kind.setCurrentIndex(self.view_kind.findData(name))
        elif page and page["key"] in keys and ("match" in steps or "typeset" in steps):
            name = ("preview.png" if self.project.status(page, "typeset")["state"] in ("done", "warn")
                    else "assignment-preview.png")
            if self.project.has(page, name):
                self.view_kind.blockSignals(True)
                self.view_kind.setCurrentIndex(self.view_kind.findData(name))
                self.view_kind.blockSignals(False)
        self.show_page(self.current)
        if "replace_redraw" in steps:
            self.log("info", f"Đã thay bản redraw cho {len(self.runner.redraw_done)}/{len(keys)} trang.")
        elif not self.runner.cancel and "typeset" in steps:
            statuses = [self.project.status(self.project.page(key), "typeset")["state"] for key in keys]
            self.log("info", "Bước 8 · " + _batch_status(statuses))
        elif not self.runner.cancel:
            self.log("info", "Xong.")

    def _page_changed(self, key):
        self.refresh_pages(keep_view=True)
        if key == self.current and not self.runner.busy:
            self.show_page(key)
        elif key == self.current:
            self.refresh_view()

    # --- danh sách trang, trạng thái ----------------------------------------------------------------
    def refresh_pages(self, keep_view: bool = False) -> None:
        p = self.project
        self.pages.blockSignals(True)
        self.pages.clear()
        if p is not None:
            for page in p.pages:
                badges = "".join(ICONS[p.status(page, s)["state"]] for s, _ in STEPS)
                item = QListWidgetItem(f"{page['key']}\n{badges}")
                item.setData(Qt.UserRole, page["key"])
                if not page.get("open", True):
                    item.setText(item.text() + "  (đã đóng)")
                    item.setForeground(QColor("#6e7681"))
                issues = [(s, p.status(page, s)) for s, _ in STEPS if p.status(page, s)["state"] in ("error", "blocked")]
                item.setToolTip("\n".join(f"{_num(s)}: {status['msg']}" for s, status in issues)
                                or "1 Mở · 2 Nhận diện · 3 Thiết lập · 4 Raw · 5 OCR · 6 Xoá chữ · 7 Ghép · 8 Typeset")
                if any(status["state"] == "error" for _, status in issues):
                    item.setForeground(QColor(COLORS["error"]))
                elif issues and page.get("open", True):
                    item.setForeground(QColor(COLORS["blocked"]))
                self.pages.addItem(item)
                if page["key"] == self.current:
                    self.pages.setCurrentItem(item)
            if not self.current and p.pages:
                self.pages.setCurrentRow(0)
                self.current = p.pages[0]["key"]
                if not keep_view:
                    self.show_page(self.current)
        self.pages.blockSignals(False)
        self.refresh_steps()

    def refresh_steps(self) -> None:
        p = self.project
        keys = self.scope_keys()
        for step, (lab, btn) in self.step_status.items():
            if p is None or not keys:
                lab.setText("")
                continue
            states = [p.status(p.page(k), step) for k in keys]
            names = [s["state"] for s in states]
            for state in ("running", "error", "blocked", "stale", "warn"):
                if state in names:
                    break
            else:
                state = "done" if all(n == "done" for n in names) else "pending"
            text = _batch_status(names) if len(keys) > 1 else STATES[state]
            lab.setText(f"<span style='color:{COLORS[state]}'>{ICONS[state]} {text}</span>")
            lab.setToolTip("\n".join(f"{k}: {s['msg']}" for k, s in zip(keys, states) if s.get("msg")))

    # --- một trang ---------------------------------------------------------------------------
    def page_obj(self):
        if self.project is None or not self.current:
            return None
        try:
            return self.project.page(self.current)
        except KeyError:
            return None

    def show_page(self, key: str) -> None:
        self.commit_editors()
        self.current = key or ""
        page = self.page_obj()
        self.refresh_steps()
        self.fill_table()
        self.refresh_view()
        self._filling = True
        try:
            if page is None:
                self.lines.setPlainText("")
                self.unplaced.clear()
                return
            self.tl_page.setValue(page.get("tl_page") or 0)
            n = self.project.tl_page(page)
            self.tl_page.setSuffix("" if page.get("tl_page") else f"  (đang dùng: {n if n is not None else '?'})")
            try:
                lines = self.project.page_lines(page)
            except Exception as exc:  # noqa: BLE001
                lines = []
                self.log("error", str(exc))
            self.lines.setPlainText("\n".join(lines))
            self.unplaced.clear()
            for u in page.get("unplaced", []):
                item = QListWidgetItem(u["text"])
                item.setToolTip(u.get("note", ""))
                self.unplaced.addItem(item)
        finally:
            self._filling = False
        if not self.runner.busy:
            try:
                self.ps.call("activate", wait=False, id=page.get("doc_id"), name=page.get("doc_name") or page["key"])
            except PhotoshopError:
                pass

    def refresh_view(self) -> None:
        page = self.page_obj()
        if page is None:
            self.view.show_page(None, [])
            return
        name = self.view_kind.currentData()
        if name == "preview.png" and self.project.status(page, "typeset")["state"] not in ("done", "warn"):
            name = "assignment-preview.png" if self.project.has(page, "assignment-preview.png") else "work.png"
            self.view_kind.blockSignals(True)
            self.view_kind.setCurrentIndex(self.view_kind.findData(name))
            self.view_kind.blockSignals(False)
        path = self.project.file(page, name)
        if not path.exists():
            path = self.project.file(page, "work.png")
        regions = [] if name in ("preview.png", "assignment-preview.png") else page["regions"]
        self.view.show_page(str(path), regions, self.selected)

    def fill_table(self) -> None:
        from .core.match_text import reference
        from .core.semantic_review import current_review
        from .core.typeset_profiles import kinds_for

        page = self.page_obj()
        self._filling = True
        try:
            self.table.setRowCount(0)
            if page is None:
                return
            kinds = kinds_for(self.project.data["profile"])
            bold = QFont()
            bold.setBold(True)
            for row, r in enumerate(page["regions"]):
                self.table.insertRow(row)
                idi = QTableWidgetItem(r["id"])
                idi.setFlags(idi.flags() & ~Qt.ItemIsEditable)
                idi.setToolTip({"detect": "model nhận diện", "diff": "chỗ bản redraw đã xoá chữ", "ai": "AI thêm vào",
                                "manual": "thêm tay"}.get(r["src"], r["src"]))
                self.table.setItem(row, C_ID, idi)
                bubble = QComboBox()
                for k, v in BUBBLES.items():
                    bubble.addItem(v, k)
                bubble.setCurrentIndex(bubble.findData(r["bubble"]))
                if r["bubble_src"] == "manual":
                    bubble.setFont(bold)
                bubble.setToolTip({"ai": "AI phân loại", "auto": "đoán theo hình bóng (chưa có AI)",
                                   "manual": "chỉnh tay"}.get(r["bubble_src"], ""))
                bubble.currentIndexChanged.connect(lambda _=0, rid=r["id"], w=bubble: self._edit(rid, "bubble", w.currentData()))
                self.table.setCellWidget(row, C_BUBBLE, bubble)
                kind = QComboBox()
                auto = self.project.style_kind({**r, "style_kind": ""})
                kind.addItem(f"theo loại bóng ({kinds.get(auto, auto)})", "")
                for k, v in kinds.items():
                    if k != "auto":
                        kind.addItem(v, k)
                kind.setCurrentIndex(max(0, kind.findData(r["style_kind"])))
                if r["style_kind"]:
                    kind.setFont(bold)
                kind.currentIndexChanged.connect(lambda _=0, rid=r["id"], w=kind: self._edit(rid, "style_kind", w.currentData()))
                self.table.setCellWidget(row, C_KIND, kind)
                font = QTableWidgetItem(font_label(r["font"]) or "—")
                font.setFlags(font.flags() & ~Qt.ItemIsEditable)
                font.setToolTip("Nháy đúp để chọn font riêng cho vùng này")
                if r["font"]:
                    font.setFont(bold)
                self.table.setItem(row, C_FONT, font)
                size = QTableWidgetItem(str(r["size"]) if r["size"] else "tự động")
                if r["size"]:
                    size.setFont(bold)
                self.table.setItem(row, C_SIZE, size)
                ja = QTableWidgetItem(r["ja"])
                if r["ja_src"] == "manual":
                    ja.setFont(bold)
                self.table.setItem(row, C_JA, ja)
                ref = reference(r)
                vi = QTableWidgetItem(ref["text"] if ref else "")
                vi.setFlags(vi.flags() & ~Qt.ItemIsEditable)
                vi.setToolTip((f"Dịch tham chiếu từ chữ Nhật · tin cậy {ref['confidence']:.2f}\n"
                               f"{ref['note']}\nBước 7 dùng để ghép; bản dịch đưa vào layer vẫn là câu của bạn.")
                              if ref else "Bước 7 tự đọc chữ còn thiếu và dịch tham chiếu khi bật AI.")
                self.table.setItem(row, C_VI, vi)
                lines = QTableWidgetItem(" ⏎ ".join(r["lines"]))
                lines.setData(Qt.UserRole, "\n".join(r["lines"]))
                lines.setToolTip("\n".join(r["lines"]))
                if r["lines_src"] == "manual":
                    lines.setFont(bold)
                self.table.setItem(row, C_LINES, lines)
                meaning = current_review(self.project, page, r)
                state = (meaning or {}).get("state", "")
                label = {"passed":"Model đã đối chiếu", "confirmed":"Đã xác nhận", "review":"Cần đối chiếu"}.get(state, "Chưa đối chiếu")
                status = QTableWidgetItem(label if r["lines"] else "")
                status.setFlags(status.flags() & ~Qt.ItemIsEditable)
                detail = (meaning or {}).get("reason", "Chạy Bước 7 hoặc Bước 8 để kiểm tra nghĩa Nhật–Việt.")
                for check in (meaning or {}).get("checks", []):
                    detail += "\n\n" + check["text"] + "\n" + check["reason"]
                    detail += "\n" + "\n".join(c["id"]+": "+c["ja"] for c in check.get("candidates", []))
                status.setToolTip(detail)
                self.table.setItem(row, C_MEANING, status)
                conf = QTableWidgetItem("" if r["confidence"] is None else f"{r['confidence']:.2f}")
                conf.setFlags(conf.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, C_CONF, conf)
                note = QTableWidgetItem(r["note"])
                note.setFlags(note.flags() & ~Qt.ItemIsEditable)
                note.setToolTip(r["note"])
                self.table.setItem(row, C_NOTE, note)
                tint = QColor(210, 153, 34, 70) if r["uncertain"] else (QColor(110, 118, 129, 60) if r["bubble"] == "skip" else None)
                if tint is not None:
                    for c in range(len(COLS)):
                        if self.table.item(row, c):
                            self.table.item(row, c).setBackground(tint)
                if r["id"] == self.selected:
                    self.table.selectRow(row)
            self.table.resizeRowsToContents()
        finally:
            self._filling = False

    def commit_editors(self):
        """Ô đang sửa dở (vd bản dịch nhiều dòng) được ghi trước khi đổi trang / chạy bước."""
        if self.table.state() == QAbstractItemView.EditingState:
            self.table.setCurrentItem(None)

    # --- chỉnh tay ---------------------------------------------------------------------------
    def _edit(self, rid: str, field: str, value) -> None:
        if self._filling:
            return
        page = self.page_obj()
        r = self.project.region(page, rid)
        if r[field] == value:
            return
        r[field] = value
        if field == "bubble":
            r["bubble_src"], r["uncertain"] = "manual", False
            self.project.invalidate(page, "setup")
            self.project.invalidate(page, "detect")
        elif field in ("ja",):
            r["ja_src"] = "manual"
            self.project.invalidate(page, "ocr")
        elif field == "lines":
            r["lines_src"] = "manual" if value else ""
            r["uncertain"] = False
            self.project.invalidate(page, "match")
        else:  # style_kind, font, size
            self.project.invalidate(page, "setup")
        self.project.save()
        QTimer.singleShot(0, self._after_edit)  # không dựng lại bảng ngay trong lúc ô đang ghi dữ liệu

    def _after_edit(self):
        self.refresh_pages(keep_view=True)
        self.fill_table()
        self.refresh_view()

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        page = self.page_obj()
        rid = page["regions"][item.row()]["id"]
        c = item.column()
        if c == C_JA:
            self._edit(rid, "ja", item.text().strip())
        elif c == C_LINES:
            text = item.data(Qt.UserRole)
            if text is None:
                text = item.text().replace(" ⏎ ", "\n")
            self._edit(rid, "lines", [s.strip() for s in text.splitlines() if s.strip()])
        elif c == C_SIZE:
            t = item.text().strip()
            self._edit(rid, "size", int(t) if t.isdigit() else 0)

    def _cell_double(self, row, col):
        if col != C_FONT:
            return
        page = self.page_obj()
        r = page["regions"][row]
        fonts = self.fonts()
        if fonts is None:
            return
        dlg = FontPicker(fonts, font_label(r["font"]), self)
        if dlg.exec() and dlg.choice is not None:
            self._edit(r["id"], "font", dlg.choice)

    def _row_selected(self):
        if self._filling:
            return
        rows = self.table.selectionModel().selectedRows()
        page = self.page_obj()
        if rows and page:
            self.selected = page["regions"][rows[0].row()]["id"]
            self.refresh_view()

    def select_region(self, rid: str):
        self.selected = rid
        page = self.page_obj()
        for row, r in enumerate(page["regions"]):
            if r["id"] == rid:
                self.table.selectRow(row)
                self.table.scrollToItem(self.table.item(row, 0))
        self.refresh_view()

    def _toggle_add(self, on: bool):
        self.view.adding = on
        self.view.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.ScrollHandDrag)

    def add_region(self, box: list):
        page = self.page_obj()
        if page is None:
            return
        r = workflow.new_region(self.project.next_id(page), box, "text", "manual")
        r["bubble_src"] = "manual"
        r["order"] = len(page["regions"]) + 1
        page["regions"].append(r)
        self.selected = r["id"]
        self.project.invalidate(page, "detect")
        self.project.save()
        self.add_btn.setChecked(False)
        self.fill_table()
        self.refresh_view()
        self.refresh_pages(keep_view=True)
        self.log("info", f"Thêm vùng {r['id']} (chọn loại bóng, nhập câu dịch trong bảng).")

    def _selected_region(self):
        page = self.page_obj()
        rows = self.table.selectionModel().selectedRows()
        return (page, page["regions"][rows[0].row()]) if page and rows else (page, None)

    def move_region(self, delta: int):
        page, r = self._selected_region()
        if r is None:
            return
        regs = page["regions"]
        i = regs.index(r)
        j = i + delta
        if 0 <= j < len(regs):
            regs[i], regs[j] = regs[j], regs[i]
            for k, x in enumerate(regs):
                x["order"] = k + 1
            self.project.invalidate(page, "detect")
            self.project.save()
            self.fill_table()

    def delete_region(self):
        page, r = self._selected_region()
        if r is None:
            return
        page["regions"].remove(r)
        self.project.invalidate(page, "detect")
        self.project.save()
        self.fill_table()
        self.refresh_view()

    def unlock_region(self):
        """Bỏ dấu chỉnh tay: bước 2 / 5 / 7 chạy lại được ghi đè vùng này."""
        page, r = self._selected_region()
        if r is None:
            return
        for k in ("bubble_src", "ja_src", "lines_src"):
            if r[k] == "manual":
                r[k] = ""
        r.update(style_kind="", font={}, size=0)
        if r["src"] == "manual":
            r["src"] = "detect"
        self.project.save()
        self.fill_table()

    def confirm_meaning(self):
        from .core.semantic_review import confirm_region, current_review

        if self.runner.busy:
            return
        self.commit_editors()
        page, region = self._selected_region()
        if region is None or not region["lines"]:
            self.log("warn", "Chọn vùng có câu dịch và đối chiếu với ảnh Raw trước khi xác nhận.")
            return
        review = current_review(self.project,page,region) or {}
        text = (f"{region['id']}\n\nNhật: {region['ja'] or '(OCR chưa đọc được — xem ảnh Raw)'}\n\n"
                + "Việt: " + "\n".join(region["lines"]) + "\n\n" + review.get("reason", "Chưa có đối chiếu tự động")
                + "\n\nBạn đã xem ảnh Raw và xác nhận các câu Việt này thuộc đúng vùng, đúng nghĩa chưa?")
        if QMessageBox.question(self,"Xác nhận đối chiếu Nhật–Việt",text,
                                QMessageBox.Yes | QMessageBox.No,QMessageBox.No) != QMessageBox.Yes:
            return
        confirm_region(self.project,page,region)
        self.project.invalidate(page,"match")
        self.fill_table()

    def save_lines(self):
        page = self.page_obj()
        if page is None:
            return
        page["tl_page"] = self.tl_page.value() or None
        text = [s.strip() for s in self.lines.toPlainText().splitlines() if s.strip()]
        try:
            from_file = self.project.page_lines({**page, "lines_manual": False})
        except Exception:  # noqa: BLE001
            from_file = []
        page["lines_manual"] = text != from_file
        page["lines"] = text if page["lines_manual"] else []
        self.project.invalidate(page, "ocr")  # → ghép lại
        if self.project.status(page, "match")["state"] in ("done", "warn"):
            self.project.set_status(page, "match", "stale", "Câu dịch đã đổi")
        self.project.save()
        self.show_page(self.current)
        self.refresh_pages(keep_view=True)

    def reset_lines(self):
        page = self.page_obj()
        if page is None:
            return
        page["lines_manual"], page["lines"] = False, []
        self.project.save()
        self.show_page(self.current)

    def assign_unplaced(self):
        page, r = self._selected_region()
        item = self.unplaced.currentItem()
        if r is None or item is None:
            self.log("warn", "Chọn một câu trong “Câu chưa có vùng” và một dòng trong bảng thoại.")
            return
        idx = self.unplaced.row(item)
        u = page["unplaced"].pop(idx)
        self._edit(r["id"], "lines", r["lines"] + [u["text"]])
        self.show_page(self.current)

    # --- nhật ký ------------------------------------------------------------------------------
    def log(self, level: str, text: str) -> None:
        color = {"error": COLORS["error"], "warn": COLORS["warn"], "ok": COLORS["done"]}.get(level, "#c9d1d9")
        stamp = time.strftime("%H:%M:%S")
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        self.log_view.appendHtml(f"<span style='color:#6e7681'>{stamp}</span> <span style='color:{color}'>{safe}</span>")
        if level == "error" and not self.runner.busy and "\n" not in text:
            self.statusBar().showMessage(text, 15000)

    def closeEvent(self, event):
        if self.runner.busy:
            self.runner.cancel = True
        super().closeEvent(event)


def main(script: str) -> int:
    app = QApplication.instance() or QApplication([])
    # Một cửa sổ duy nhất: chạy lại script trong Photoshop chỉ đưa cửa sổ đang mở lên trước.
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        return 0
    try:
        from .ui.theme import apply_dark

        apply_dark(app)
    except Exception:  # noqa: BLE001 - giao diện tối chỉ để đẹp
        pass
    win = PanelWindow(script)
    server = QLocalServer()
    QLocalServer.removeServer(INSTANCE)
    server.listen(INSTANCE)

    def raise_window():
        conn = server.nextPendingConnection()
        if conn:
            conn.close()
        win.setWindowState((win.windowState() & ~Qt.WindowMinimized) | Qt.WindowActive)
        win.show()
        win.raise_()
        win.activateWindow()
        win.load_documents()

    server.newConnection.connect(raise_window)
    win.show()
    QTimer.singleShot(300, win.load_documents)
    return app.exec()
