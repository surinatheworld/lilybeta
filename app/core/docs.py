"""Đọc file bản dịch (.docx / .txt) thành danh sách câu."""
from __future__ import annotations

import os


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
