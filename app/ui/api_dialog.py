"""Hộp thoại nhập API key cho AI vẽ nền trên mạng (Gemini, OpenAI)."""
from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..core import cloud, secrets

PROVIDERS = {
    "gemini": {
        "title": "Google Gemini",
        "models": [cloud.GEMINI_DEFAULT_MODEL, "gemini-3.1-flash-image", "gemini-3.1-flash-lite-image",
                   "gemini-3-pro-image", "gemini-2.5-flash-image"],
        "help": 'Lấy key tại <a href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a>. '
                "Model sửa ảnh cần tài khoản đã bật thanh toán (gói miễn phí có hạn mức 0).",
        "check": cloud.gemini_check,
    },
    "openai": {
        "title": "OpenAI (ChatGPT)",
        "models": [cloud.OPENAI_DEFAULT_MODEL, "gpt-image-2.5-flare", "gpt-image-2", "gpt-image-1.5"],
        "help": 'Lấy key tại <a href="https://platform.openai.com/api-keys">platform.openai.com/api-keys</a>. '
                "Tính phí theo từng ảnh.",
        "check": cloud.openai_check,
    },
}


def model_for(settings: QSettings, provider: str) -> str:
    return settings.value(f"ai/{provider}_model", PROVIDERS[provider]["models"][0])


class ApiDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API key cho AI vẽ nền")
        self.setMinimumWidth(520)
        self.settings = settings
        self.fields: dict[str, tuple[QLineEdit, QComboBox]] = {}

        lay = QVBoxLayout(self)
        note = QLabel("Chỉ gửi một ô ảnh nhỏ quanh chữ cần xoá (không gửi cả trang); mỗi vùng = 1 lần gọi API "
                      "(tính phí theo nhà cung cấp, mất khoảng 10–30 giây). Key được cất trong Windows Credential "
                      "Manager, không ghi ra file.")
        note.setWordWrap(True)
        lay.addWidget(note)

        for pid, info in PROVIDERS.items():
            box = QGroupBox(info["title"])
            fl = QFormLayout(box)
            key = QLineEdit(secrets.get_key(pid))
            key.setEchoMode(QLineEdit.Password)
            key.setPlaceholderText("Dán API key vào đây")
            show = QCheckBox("Hiện")
            show.toggled.connect(lambda on, k=key: k.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
            row = QHBoxLayout()
            row.addWidget(key, 1)
            row.addWidget(show)
            fl.addRow("API key", row)
            model = QComboBox()
            model.setEditable(True)  # tên model đổi thường xuyên nên cho gõ tay
            model.addItems(info["models"])
            model.setCurrentText(model_for(settings, pid))
            test = QPushButton("Kiểm tra key")
            test.clicked.connect(lambda _=False, p=pid: self._check(p))
            mrow = QHBoxLayout()
            mrow.addWidget(model, 1)
            mrow.addWidget(test)
            fl.addRow("Model", mrow)
            help_ = QLabel(info["help"])
            help_.setOpenExternalLinks(True)
            help_.setWordWrap(True)
            help_.setStyleSheet("color:#888;")
            fl.addRow(help_)
            lay.addWidget(box)
            self.fields[pid] = (key, model)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _check(self, pid: str):
        key, model = self.fields[pid]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            name = PROVIDERS[pid]["check"](key.text().strip(), model.currentText().strip())
            QMessageBox.information(self, "Kiểm tra key", f"Key hợp lệ, dùng được model: {name}\n\n"
                                    "(Bước này chỉ kiểm tra key, chưa kiểm tra hạn mức/thanh toán.)")
        except cloud.CloudError as e:
            QMessageBox.warning(self, "Kiểm tra key", str(e))
        finally:
            QApplication.restoreOverrideCursor()

    def _save(self):
        try:
            for pid, (key, model) in self.fields.items():
                secrets.set_key(pid, key.text().strip())
                self.settings.setValue(f"ai/{pid}_model", model.currentText().strip())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Lỗi", f"Không lưu được key: {e}")
            return
        self.accept()
