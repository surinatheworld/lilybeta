"""Đọc file bản dịch (.docx / .txt) thành danh sách câu.

Định dạng theo trang: một dòng chỉ có số (có thể kèm "Trang"/"Page") là tên trang, các dòng sau
là câu dịch của trang đó theo thứ tự đọc:

    1
    Câu dịch 1
    Câu dịch 2
    2
    …
"""
from __future__ import annotations

import os
import re

PAGE_HEADER = re.compile(r"^\s*(?:trang|page|p\.?|tr\.?)?\s*#?\s*(\d{1,4})\s*[:.\-–]?\s*$", re.IGNORECASE)
# Ghi chú của nhóm dịch, không phải thoại: "T/N: …", "(cái này xoá luôn cũng được, không có sfx …)".
NOTE = re.compile(r"^\s*(?:t\s*/\s*n|n\s*/\s*t|tl\s*note|note|ghi chú)\s*[:：]", re.IGNORECASE)
NOTE_WORDS = re.compile(r"\b(?:sfx|xoá|xóa|typer|typeset|redraw|clean|edit)\b", re.IGNORECASE)
# Nhãn chỉ chỗ đặt chữ: "chữ dọc: …", "vé tàu:", "chữ trên gáy 3 cuốn sách: hồi ức" → chỉ giữ phần chữ.
PLACE_LABEL = re.compile(r"^\s*(?:chữ|bảng|vé|biển|bìa|nhãn|gáy)(?:\s+[^\s:!?.…]+){0,6}\s*:\s*", re.IGNORECASE)


def clean_line(line: str) -> str:
    """Câu dịch thật trong một dòng bản dịch; "" nếu dòng chỉ là ghi chú / nhãn của nhóm dịch."""
    line = line.strip()
    if NOTE.match(line) or (line.startswith("(") and line.endswith(")") and NOTE_WORDS.search(line)):
        return ""
    return PLACE_LABEL.sub("", line, count=1).strip()


def load_pages(path: str) -> dict[int, list[str]]:
    """{số trang: [các câu]}. File không có dòng tên trang thì mọi câu thuộc trang 0."""
    pages: dict[int, list[str]] = {}
    cur = 0
    for line in load_lines(path):
        m = PAGE_HEADER.match(line)
        if m:
            cur = int(m.group(1))
            pages.setdefault(cur, [])
            continue
        pages.setdefault(cur, []).append(line)
    if 0 in pages and not pages[0] and len(pages) > 1:
        del pages[0]
    return pages


def page_number(filename: str) -> int | None:
    """Số trang từ tên file ảnh: 001.jpg → 1, page_12.png → 12 (lấy cụm số cuối cùng)."""
    nums = re.findall(r"\d+", os.path.splitext(os.path.basename(filename))[0])
    return int(nums[-1]) if nums else None


def load_lines(path: str) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        raw = _docx_lines(path)
    else:
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            raw = f.read().splitlines()
    out = []
    for line in raw:
        line = line.strip()
        if line and not line.startswith("//"):
            line = clean_line(line)
            if line:
                out.append(line)
    return out


def _docx_lines(path: str) -> list[str]:
    import docx

    doc = docx.Document(path)
    lines = []
    for para in doc.paragraphs:
        # Xuống dòng mềm (Shift+Enter) trong Word giữ thành "\n" trong cùng một câu.
        lines.append(para.text.replace("\v", "\n"))
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                lines.extend(p.text for p in cell.paragraphs)
    return lines
