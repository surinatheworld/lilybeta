"""Hộp thoại Key và token: xem key đang dùng, token còn lại, dán key mới (nạp thêm)."""
from __future__ import annotations

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox, QPlainTextEdit, QVBoxLayout)

from ..core import quota


class KeyDialog(QDialog):
    def __init__(self, parent=None, reason: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Key và token")
        self.resize(460, 300)
        lay = QVBoxLayout(self)
        if reason:
            why = QLabel(reason)
            why.setWordWrap(True)
            why.setStyleSheet("color: #e0a040;")
            lay.addWidget(why)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        costs = QLabel(f"Mỗi trang tự type: {quota.PAGE_COST} token · mỗi khung xử lý tay: {quota.REGION_COST} token · "
                       f"mỗi lần AI vẽ nền: {quota.AI_COST} token.")
        costs.setWordWrap(True)
        lay.addWidget(costs)
        lay.addWidget(QLabel("Dán key mới (nạp thêm token hoặc đổi key):"))
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(quota.PREFIX + "…")
        lay.addWidget(self.edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.activate = buttons.addButton("Kích hoạt", QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        self.activate.clicked.connect(self._activate)
        lay.addWidget(buttons)
        self._show_status()

    def _show_status(self):
        lic, _ = quota.status()
        text = "<b>" + quota.describe() + "</b>"
        if lic is not None:
            text += f"<br>Mã người dùng: {lic.user} · cấp ngày {lic.issued}"
        self.status.setText(text)

    def _activate(self):
        try:
            lic = quota.install(self.edit.toPlainText())
        except quota.TokenError as e:
            QMessageBox.warning(self, "Key", str(e))
            return
        self._show_status()
        QMessageBox.information(self, "Key", f"Đã kích hoạt key của {lic.name or lic.user}.\n{quota.describe()}")
        self.accept()
