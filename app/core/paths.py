"""Đường dẫn dùng chung: chạy từ mã nguồn hay từ bản Lilyzono.exe đã đóng gói."""
from __future__ import annotations

import os
import sys


def app_dir() -> str:
    """Thư mục gốc của tool: cạnh Lilyzono.exe khi đã đóng gói, thư mục dự án khi chạy mã nguồn."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def model_path(name: str) -> str:
    return os.path.join(app_dir(), "models", name)
