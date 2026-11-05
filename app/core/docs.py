"""Đọc file bản dịch (.docx / .txt / .xlsx) thành danh sách câu.

Định dạng theo trang: một dòng chỉ có số (có thể kèm "Trang"/"Page") là tên trang, các dòng sau
là câu dịch của trang đó theo thứ tự đọc:

    1
    Câu dịch 1
    Câu dịch 2
    2
    …

Bảng script .xlsx (nhóm dịch tiếng Anh): mỗi dòng một câu, các cột Page / Type / English (hoặc Translation).
Cột Type (vd "Snow_CuteHappy") ghi loại thoại: câu được gắn nhãn TYPE_TAG để chọn đúng font khi type.
"""
from __future__ import annotations

import os
import re

PAGE_HEADER = re.compile(r"^\s*(?:trang|page|p\.?|tr\.?)?\s*#?\s*(\d{1,4})\s*[:.\-–]?\s*$", re.IGNORECASE)
# Ghi chú của nhóm dịch, không phải thoại: "T/N: …", "(cái này xoá luôn cũng được, không có sfx …)".
NOTE = re.compile(r"^\s*(?:t\s*/\s*n|n\s*/\s*t|tl\s*note|trans\s*note[^:：]*|note|ghi chú)\s*[:：]", re.IGNORECASE)
# "(bỏ)", "(bỏ hết)": nhóm dịch dặn bỏ câu ở bóng đó.
SKIP = re.compile(r"^\(\s*(?:bỏ|bỏ hết|bỏ qua|skip|không dịch)\s*\)$", re.IGNORECASE)
NOTE_WORDS = re.compile(r"\b(?:sfx|xoá|xóa|typer|typeset|redraw|clean|edit)\b", re.IGNORECASE)
# Nhãn chỉ chỗ đặt chữ: "chữ dọc: …", "vé tàu:", "chữ trên gáy 3 cuốn sách: hồi ức" → chỉ giữ phần chữ.
PLACE_LABEL = re.compile(r"^\s*(?:chữ|bảng|vé|biển|bìa|nhãn|gáy)(?:\s+[^\s:!?.…]+){0,6}\s*:\s*", re.IGNORECASE)


def clean_line(line: str) -> str:
    """Câu dịch thật trong một dòng bản dịch; "" nếu dòng chỉ là ghi chú / nhãn của nhóm dịch."""
    line = line.strip()
    if NOTE.match(line) or SKIP.match(line) or (line.startswith("(") and line.endswith(")") and NOTE_WORDS.search(line)):
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
    if ext == ".xlsx":
        return [line for page, lines in _xlsx_pages(path).items() for line in [str(page), *lines]]
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


TYPE_TAG = "⟦{}|{}⟧"  # ⟦loại|câu gốc⟧ đầu câu, từ cột Type / Japanese của script (bỏ đi khi type)
TAG = re.compile(r"^⟦(\w*)(?:\|([^⟧]*))?⟧")


def source_text(line: str) -> str:
    """Câu gốc (tiếng Nhật, bỏ dấu cách) ghi trong nhãn của script, "" nếu không có."""
    m = TAG.match(line)
    return m.group(2) or "" if m else ""


def source_length(line: str) -> int | None:
    """Số ký tự câu gốc ghi trong nhãn của script, None nếu không có."""
    return len(source_text(line)) or None


def translation_units(lines: list[str]) -> list[str]:
    """Treat explicit soft line breaks as independently assignable dialogue.

    A DOCX paragraph can contain several balloons separated with Shift+Enter.
    Regions may still receive several units. A Japanese-tagged script row stays
    together because its source column describes that complete translation.
    """
    out = []
    for line in lines:
        if source_text(line):
            if line.strip():
                out.append(line.strip())
            continue
        tag = TAG.match(line)
        prefix = tag.group(0) if tag else ""
        text = line[len(prefix):]
        out.extend(prefix + part.strip() for part in text.splitlines() if part.strip())
    return out
# Tên loại trong cột Type (bỏ tiền tố "Snow_", bỏ dấu cách / gạch) → loại khung của bộ font.
TYPE_KINDS = {"dialogue": "normal", "speech": "normal", "thought": "thought", "bolderthought": "bold_thought",
              "boldthought": "bold_thought", "narration": "square", "box": "square", "shoutangry": "bold", "shout": "bold",
              "angry": "bold", "writing": "writing", "writting": "writing", "outsidetext": "free", "outside": "free",
              "nervouscrying": "flustered", "nervous": "flustered", "crying": "flustered", "cutehappy": "cute",
              "cute": "cute", "happy": "cute", "chaptertitle": "title", "title": "title", "phonetext": "phone",
              "phone": "phone", "sfx": "sfx"}


def type_kind(name: str) -> str:
    key = re.sub(r"[^a-z]", "", (name or "").split("_")[-1].lower())
    return TYPE_KINDS.get(key, "")


def _xlsx_pages(path: str) -> dict[int, list[str]]:
    """Script .xlsx: {trang: [câu đã gắn nhãn loại]} theo thứ tự dòng (đọc thẳng XML, không cần thư viện Excel)."""
    import zipfile
    import xml.etree.ElementTree as ET

    m = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(t.text or "" for t in si.iter(m + "t"))
                      for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(m + "si")]
        sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
        rows = []
        for r in ET.fromstring(z.read(sheet)).iter(m + "row"):
            cells = {}
            for c in r.iter(m + "c"):
                v = c.find(m + "v")
                if c.get("t") == "s" and v is not None:
                    text = shared[int(v.text)]
                elif c.get("t") == "inlineStr":
                    text = "".join(t.text or "" for t in c.iter(m + "t"))
                else:
                    text = v.text if v is not None else ""
                cells[re.sub(r"\d", "", c.get("r", ""))] = (text or "").strip()
            rows.append(cells)
    if not rows:
        return {}
    header = {v.lower(): k for k, v in rows[0].items() if v}
    col = lambda *names: next((header[n] for n in names if n in header), None)  # noqa: E731
    page_col = col("page", "trang")
    text_col = col("english", "translation", "vietnamese", "tiếng việt", "bản dịch", "dịch")
    type_col = col("type", "loại")
    source_col = col("japanese", "raw", "jp", "original", "tiếng nhật")
    if page_col is None or text_col is None:
        raise ValueError("Script .xlsx cần cột Page và English (hoặc Translation).")
    pages: dict[int, list[str]] = {}
    for r in rows[1:]:
        text = clean_line(r.get(text_col, ""))
        try:
            page = int(float(r.get(page_col, "")))
        except ValueError:
            continue
        if text:
            kind = type_kind(r.get(type_col, "")) if type_col else ""
            source = "".join((r.get(source_col, "") if source_col else "").split()).replace("⟧", "").replace("|", "")
            tag = TYPE_TAG.format(kind, source) if kind or source else ""
            pages.setdefault(page, []).append(tag.replace("|⟧", "⟧") + text)
    return pages


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
