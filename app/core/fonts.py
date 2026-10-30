"""Tra cứu font cài trên máy: file font, tên PostScript (cho Photoshop), và tên dùng được trong Qt.

Nhiều font Việt hoá đặt tên họ có ngoặc vuông, vd "000 CCPascualFerry [TeddyBear]". Qt hiểu phần
"[...]" là tên nhà sản xuất nên không chọn được font đó; ta nạp một bản sao trong bộ nhớ với tên
không có ngoặc (file font gốc không bị đụng tới).
"""
from __future__ import annotations

import io
import os
import re
import sys

_registry: list[tuple[str, str]] | None = None
_aliases: dict[str, str] = {}
_ps_cache: dict[tuple[str, str], str] = {}


def _font_files() -> list[tuple[str, str]]:
    """[(tên hiển thị trong registry, đường dẫn file)] của font cài cho máy và cho người dùng."""
    global _registry
    if _registry is not None:
        return _registry
    out = []
    if sys.platform == "win32":
        import winreg

        for root, base in ((winreg.HKEY_LOCAL_MACHINE, r"C:\Windows\Fonts"),
                           (winreg.HKEY_CURRENT_USER, os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts"))):
            try:
                k = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts")
            except OSError:
                continue
            for i in range(winreg.QueryInfoKey(k)[1]):
                name, value, _ = winreg.EnumValue(k, i)
                path = value if os.path.isabs(value) else os.path.join(base, value)
                if os.path.exists(path):
                    out.append((re.sub(r"\s*\((TrueType|OpenType)\)$", "", name), path))
    _registry = out
    return out


def _names(path: str):
    """Mỗi font trong file: (họ, kiểu, tên PostScript, chỉ số font trong file .ttc)."""
    from fontTools.ttLib import TTCollection, TTFont

    try:
        fonts = TTCollection(path).fonts if path.lower().endswith(".ttc") else [TTFont(path, lazy=True)]
    except Exception:  # noqa: BLE001 - file font hỏng/không đọc được thì bỏ qua
        return []
    out = []
    for i, f in enumerate(fonts):
        n = f["name"]
        fam = n.getDebugName(16) or n.getDebugName(1) or ""
        sub = n.getDebugName(17) or n.getDebugName(2) or "Regular"
        out.append((fam, sub, n.getDebugName(6) or "", i))
    return out


def files_for(family: str) -> list[str]:
    key = family.lower()
    return [p for name, p in _font_files() if name.lower().startswith(key)]


def postscript_name(family: str, style: str = "") -> str:
    """Tên PostScript của font (Photoshop dùng tên này), vd 000WildWords2TB-Roman."""
    ck = (family, style)
    if ck in _ps_cache:
        return _ps_cache[ck]
    faces = [(sub, ps) for p in files_for(family) for _, sub, ps, _ in _names(p) if ps]
    if not faces:  # tên trong registry khác tên họ font: dò bảng tên trong file
        faces = [(sub, ps) for fam, sub, ps in _all_faces() if fam.lower() == family.lower() and ps]
    want = (style or "Regular").lower()
    plain = ("regular", "roman", "normal", "book")
    pick = (next((ps for sub, ps in faces if sub.lower() == want), None)
            or (next((ps for sub, ps in faces if sub.lower() in plain), None) if want in plain else None)
            or (faces[0][1] if faces else family.replace(" ", "")))
    # Photoshop đổi dấu cách trong tên PostScript thành "-" (vd "000 CCBattleCry [TeddyBear]").
    _ps_cache[ck] = pick.replace(" ", "-")
    return _ps_cache[ck]


def family_from_postscript(ps: str) -> tuple[str, str]:
    """(họ font, kiểu) theo tên PostScript đọc từ PSD; không có trên máy thì ("", "")."""
    for fam, sub, name in _all_faces():
        if name and (name == ps or name.replace(" ", "-") == ps):
            return fam, sub
    return "", ""


_faces: list[tuple[str, str, str]] | None = None


def _all_faces():
    """(họ, kiểu, PostScript) của mọi font trên máy – chỉ đọc khi cần, đọc một lần."""
    global _faces
    if _faces is None:
        _faces = [(fam, sub, ps) for _, p in _font_files() for fam, sub, ps, _ in _names(p)]
    return _faces


def qt_family(family: str) -> str:
    """Tên họ font dùng được trong QFont (xử lý tên có ngoặc vuông)."""
    if "[" not in family:
        return family
    if family in _aliases:
        return _aliases[family]
    alias = re.sub(r"\s*\[(.*?)\]", r" \1", family).strip() + " (EM)"
    ok = False
    for path in files_for(family):
        try:
            data = _renamed(path, family, alias)
        except Exception:  # noqa: BLE001
            continue
        from PySide6.QtGui import QFontDatabase

        if QFontDatabase.addApplicationFontFromData(data) >= 0:
            ok = True
    _aliases[family] = alias if ok else family
    return _aliases[family]


def _renamed(path: str, family: str, alias: str) -> bytes:
    from fontTools.ttLib import TTFont

    f = TTFont(path)
    sub = f["name"].getDebugName(2) or "Regular"
    for rec in f["name"].names:
        if rec.nameID in (1, 16):
            rec.string = alias
        elif rec.nameID == 4:
            rec.string = alias if sub.lower() in ("regular", "roman", "normal") else f"{alias} {sub}"
    buf = io.BytesIO()
    f.save(buf)
    return buf.getvalue()
