"""Một chương truyện: thư mục ảnh raw + bản dịch chia theo trang."""
from __future__ import annotations

import os
import re

from .assign import assign_lines
from .docs import load_pages, page_number
from .page import Page
from .typeset import TextStyle

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


class Chapter:
    def __init__(self, folder: str):
        self.folder = folder
        self.paths = sorted((os.path.join(folder, f) for f in os.listdir(folder)
                             if f.lower().endswith(IMAGE_EXTS)), key=lambda p: _natural(os.path.basename(p)))
        if not self.paths:
            raise ValueError(f"Thư mục không có ảnh: {folder}")
        self.pages: dict[str, Page] = {}
        self.translations: dict[int, list[str]] = {}
        self.filled: set[str] = set()  # trang đã tự điền câu dịch

    def page(self, path: str) -> Page:
        if path not in self.pages:
            self.pages[path] = Page(path)
        return self.pages[path]

    def load_translations(self, docs_path: str) -> dict[int, list[str]]:
        self.translations = load_pages(docs_path)
        self.filled.clear()
        return self.translations

    def lines_for(self, path: str) -> list[str]:
        n = page_number(path)
        if n is not None and n in self.translations:
            return self.translations[n]
        if len(self.paths) == 1 and 0 in self.translations:
            return self.translations[0]
        return []

    def auto_fill(self, path: str, style: TextStyle | None = None) -> list:
        """Ghép câu dịch của trang vào các vùng chữ (chưa xoá chữ). Trả về [(vùng, [câu])]."""
        page = self.page(path)
        lines = self.lines_for(path)
        free = [r for r in page.regions if not r.cleaned]
        plan = assign_lines(page, free, lines)
        for r in free:
            r.text = ""
        for r, ls in plan:
            r.text = "\n".join(ls)
            if style is not None:
                r.style = style.copy()
        self.filled.add(path)
        return plan
