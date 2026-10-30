"""State retained independently for each open document tab."""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF
from PySide6.QtGui import QTransform

from ..core.chapter import Chapter
from ..core.page import Page
from ..core.typeset import TextStyle


@dataclass(eq=False)
class OpenDocument:
    page: Page
    chapter: Chapter | None = None
    project_path: str | None = None
    psd_path: str | None = None
    source_path: str | None = None
    selected: int = -1
    selection: list[int] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    used_lines: set[int] = field(default_factory=set)
    doc_pages: dict = field(default_factory=dict)
    doc_flat: list[str] | None = None
    page_lines: bool = False
    docs_label: str = "Chưa có file bản dịch (.docx / .txt)."
    direction: int = 0
    style: TextStyle | None = None
    transform: QTransform | None = None
    center: QPointF | None = None
    clone_src: tuple | None = None
    clone_offset: tuple | None = None
