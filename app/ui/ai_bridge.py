"""Hộp thoại redraw qua ChatGPT / Gemini bằng copy–dán (dùng gói Plus/Pro trên web, không cần API)."""
from __future__ import annotations

import os

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..core import manual_ai

STEPS = (
    "<b>1.</b> Bấm <b>Copy ảnh</b> rồi dán (Ctrl+V) vào ChatGPT hoặc Gemini, kèm lời nhắc bên dưới "
    "(bấm <b>Copy lời nhắc</b>).<br>"
    "<b>2.</b> Khi AI trả ảnh: chuột phải vào ảnh → <i>Copy image</i> (hoặc tải về).<br>"
    "<b>3.</b> Bấm <b>Dán kết quả</b> (hoặc mở/kéo thả file ảnh vào đây). Tool tự co, căn khớp và chỉ thay "
    "phần trong viền đỏ."
)


def to_qimage(bgr: np.ndarray) -> QImage:
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def from_qimage(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(h, img.bytesPerLine())
    return cv2.cvtColor(arr[:, :w * 3].reshape(h, w, 3).copy(), cv2.COLOR_RGB2BGR)


class AiBridgeDialog(QDialog):
    """Không chặn cửa sổ chính, để chuyển qua lại với trình duyệt."""

    about_to_apply = Signal()  # ngay trước khi dán kết quả vào trang (để ghi bước hoàn tác)
    applied = Signal(dict)

    def __init__(self, job: manual_ai.Job, fit_bubble: bool, parent=None):
        super().__init__(parent)
        self.job, self.fit_bubble = job, fit_bubble
        self.setWindowTitle("Redraw bằng ChatGPT / Gemini")
        self.setModal(False)
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        steps = QLabel(STEPS)
        steps.setWordWrap(True)
        lay.addWidget(steps)

        w, h = job.size
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignCenter)
        pm = QPixmap.fromImage(to_qimage(manual_ai.preview(job)))
        self.preview.setPixmap(pm.scaled(420, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lay.addWidget(self.preview)
        info = QLabel(f"Vùng gửi đi: {w}x{h}px · {len(job.regions)} vùng chữ · viền đỏ = phần sẽ được thay")
        info.setStyleSheet("color:#888;")
        lay.addWidget(info)

        self.prompt = QPlainTextEdit(manual_ai.PROMPT)
        self.prompt.setFixedHeight(84)
        lay.addWidget(self.prompt)

        row1 = QHBoxLayout()
        for text, slot in (("Copy ảnh", self.copy_image), ("Copy lời nhắc", self.copy_prompt),
                           ("Lưu ảnh ra file…", self.save_image)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row1.addWidget(b)
        lay.addLayout(row1)
        row2 = QHBoxLayout()
        self.btn_paste = QPushButton("Dán kết quả (Ctrl+V)")
        self.btn_paste.setStyleSheet("font-weight:bold; padding:6px;")
        self.btn_paste.clicked.connect(self.paste_result)
        self.btn_paste.setShortcut("Ctrl+V")
        b_open = QPushButton("Mở file kết quả…")
        b_open.clicked.connect(self.open_result)
        b_close = QPushButton("Đóng")
        b_close.clicked.connect(self.close)
        row2.addWidget(self.btn_paste, 1)
        row2.addWidget(b_open)
        row2.addWidget(b_close)
        lay.addLayout(row2)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

    # --- gửi đi ------------------------------------------------------------------------
    def copy_image(self):
        QApplication.clipboard().setImage(to_qimage(self.job.crop))
        self.status.setText("Đã copy ảnh. Dán (Ctrl+V) vào ChatGPT/Gemini.")

    def copy_prompt(self):
        QApplication.clipboard().setText(self.prompt.toPlainText())
        self.status.setText("Đã copy lời nhắc.")

    def save_image(self):
        base = os.path.splitext(self.job.page.path)[0] + "_redraw.png"
        path, _ = QFileDialog.getSaveFileName(self, "Lưu ảnh gửi cho AI", base, "PNG (*.png)")
        if path:
            cv2.imencode(".png", self.job.crop)[1].tofile(path)
            self.status.setText(f"Đã lưu: {path}")

    # --- nhận về ------------------------------------------------------------------------
    def paste_result(self):
        cb = QApplication.clipboard()
        md = cb.mimeData()
        img = cb.image()
        if img.isNull() and md.hasUrls():
            paths = [u.toLocalFile() for u in md.urls() if u.isLocalFile()]
            if paths:
                return self.use_file(paths[0])
        if img.isNull():
            QMessageBox.information(self, "Chưa có ảnh", "Clipboard chưa có ảnh. Trong ChatGPT/Gemini: chuột phải "
                                    "vào ảnh kết quả → Copy image, rồi bấm lại.")
            return
        self.use_result(from_qimage(img))

    def open_result(self):
        path, _ = QFileDialog.getOpenFileName(self, "Ảnh kết quả từ AI", os.path.expanduser("~/Downloads"),
                                              "Ảnh (*.png *.jpg *.jpeg *.webp)")
        if path:
            self.use_file(path)

    def use_file(self, path: str):
        img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            QMessageBox.warning(self, "Lỗi", f"Không đọc được ảnh: {path}")
            return
        self.use_result(img)

    def use_result(self, img: np.ndarray):
        warn = manual_ai.aspect_warning(self.job, img)
        self.about_to_apply.emit()
        res = manual_ai.apply(self.job, img, self.fit_bubble)
        msg = "Đã dán kết quả. Không ưng thì bấm Hoàn tác (Ctrl+Z) rồi thử lại."
        if res["mismatch"] > 25:
            warn = (warn + "\n" if warn else "") + ("Tranh quanh chữ khác ảnh gốc khá nhiều – AI có thể đã vẽ lại "
                                                    "cả bố cục; nên kiểm tra kỹ.")
        if warn:
            QMessageBox.warning(self, "Lưu ý", warn)
        self.applied.emit(res)
        self.status.setText(msg)
        self.close()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasImage():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasImage():
            self.use_result(from_qimage(QImage(md.imageData())))
        elif md.hasUrls() and md.urls()[0].isLocalFile():
            self.use_file(md.urls()[0].toLocalFile())
