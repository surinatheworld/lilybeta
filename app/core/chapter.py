"""Một chương truyện: thư mục ảnh raw + bản dịch chia theo trang."""
from __future__ import annotations

import os
import re

from .assign import apply_plan, assign_lines
from .docs import load_pages, page_number
from .page import Page
from .typeset import TextStyle

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def list_images(folder: str) -> list[str]:
    return sorted((os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(IMAGE_EXTS)),
                  key=lambda p: _natural(os.path.basename(p)))


class Chapter:
    def __init__(self, folder: str):
        self.folder = folder
        self.paths = list_images(folder)
        if not self.paths:
            raise ValueError(f"Thư mục không có ảnh: {folder}")
        self.pages: dict[str, Page] = {}
        self.translations: dict[int, list[str]] = {}
        self.filled: set[str] = set()  # trang đã tự điền câu dịch
        self.redrawn: dict[str, str] = {}  # ảnh raw → ảnh đã redraw nạp từ ngoài

    def page(self, path: str) -> Page:
        if path not in self.pages:
            page = self.pages[path] = Page(path)
            if path in self.redrawn:
                page.set_redrawn(self.redrawn[path])
        return self.pages[path]

    def load_redrawn(self, folder: str) -> dict[str, str]:
        """Ghép ảnh đã redraw trong `folder` với từng trang: theo số trang trong tên file (001.png ↔ 001.jpg),
        không khớp được thì theo thứ tự nếu hai thư mục cùng số ảnh. Trả về {ảnh raw: ảnh redraw}."""
        files = [f for f in list_images(folder) if os.path.normcase(f) not in map(os.path.normcase, self.paths)]
        by_num: dict[int, str] = {}
        for f in files:
            n = page_number(f)
            if n is not None:
                by_num.setdefault(n, f)
        matched = {p: by_num[page_number(p)] for p in self.paths if page_number(p) in by_num}
        if len(matched) < len(self.paths) and len(files) == len(self.paths):
            matched = dict(zip(self.paths, files))
        self.redrawn = matched
        for path, page in self.pages.items():
            page.set_redrawn(matched.get(path))
        return matched

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
        apply_plan(page, plan, style)
        self.filled.add(path)
        return [(r, r.text.split("\n")) for r in page.regions if r.text and not r.cleaned]
