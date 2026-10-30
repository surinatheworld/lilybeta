"""Giao diện tối kiểu trình sửa ảnh chuyên nghiệp (bố cục quen thuộc của Photoshop 2023) và icon vẽ bằng ký tự."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap

BG = "#323232"  # nền panel
BG_DARK = "#262626"  # nền quanh ảnh, ô nhập
BG_LIGHT = "#3d3d3d"
BORDER = "#1e1e1e"
TEXT = "#d4d4d4"
TEXT_DIM = "#8f8f8f"
ACCENT = "#378ef0"

QSS = f"""
QWidget {{ background:{BG}; color:{TEXT}; font-size:12px; }}
QMainWindow::separator {{ background:{BORDER}; width:3px; height:3px; }}
QMenuBar {{ background:{BG}; border-bottom:1px solid {BORDER}; }}
QMenuBar::item {{ padding:4px 10px; background:transparent; }}
QMenuBar::item:selected, QMenu::item:selected {{ background:{ACCENT}; color:white; }}
QMenu {{ background:{BG_LIGHT}; border:1px solid {BORDER}; padding:4px 0; }}
QMenu::item {{ padding:4px 28px 4px 22px; }}
QMenu::item:disabled {{ color:{TEXT_DIM}; }}
QMenu::separator {{ height:1px; background:{BORDER}; margin:4px 8px; }}
QToolBar {{ background:{BG}; border:none; spacing:2px; padding:2px; }}
QToolBar#tools QToolButton {{ padding:3px; border:1px solid transparent; border-radius:3px; }}
QToolBar#tools QToolButton:checked {{ background:{BG_DARK}; border-color:#555; }}
QToolButton:hover {{ background:#444; }}
QDockWidget {{ titlebar-close-icon:none; }}
QDockWidget::title {{ background:{BG_LIGHT}; padding:4px 8px; border-bottom:1px solid {BORDER}; }}
QTabBar::tab {{ background:{BG_DARK}; color:{TEXT_DIM}; padding:4px 12px; border:1px solid {BORDER}; border-bottom:none; }}
QTabBar::tab:selected {{ background:{BG}; color:{TEXT}; }}
QTabWidget::pane {{ border:1px solid {BORDER}; }}
QGroupBox {{ border:1px solid #444; border-radius:3px; margin-top:14px; padding-top:4px; }}
QGroupBox::title {{ subcontrol-origin:margin; left:8px; padding:0 4px; color:{TEXT_DIM}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QFontComboBox, QListWidget {{
    background:{BG_DARK}; border:1px solid #4a4a4a; border-radius:2px; selection-background-color:{ACCENT}; }}
QComboBox QAbstractItemView {{ background:{BG_LIGHT}; selection-background-color:{ACCENT}; }}
QPushButton {{ background:#4a4a4a; border:1px solid #2a2a2a; border-radius:3px; padding:4px 10px; }}
QPushButton:hover {{ background:#565656; }}
QPushButton:pressed, QPushButton:checked {{ background:{ACCENT}; color:white; }}
QListWidget::item:selected {{ background:#4b5d73; color:white; }}
QScrollBar:vertical {{ background:{BG}; width:10px; }}
QScrollBar::handle:vertical {{ background:#5a5a5a; border-radius:4px; min-height:24px; }}
QScrollBar:horizontal {{ background:{BG}; height:10px; }}
QScrollBar::handle:horizontal {{ background:#5a5a5a; border-radius:4px; min-width:24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QStatusBar {{ background:{BG}; border-top:1px solid {BORDER}; color:{TEXT_DIM}; }}
QCheckBox::indicator {{ width:13px; height:13px; }}
QToolTip {{ background:#f0f0f0; color:#202020; border:1px solid #888; }}
"""


def apply_dark(app) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in ((QPalette.Window, BG), (QPalette.Base, BG_DARK), (QPalette.AlternateBase, BG_LIGHT),
                        (QPalette.Button, "#4a4a4a"), (QPalette.Text, TEXT), (QPalette.WindowText, TEXT),
                        (QPalette.ButtonText, TEXT), (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"),
                        (QPalette.ToolTipBase, "#f0f0f0"), (QPalette.ToolTipText, "#202020")):
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor(TEXT_DIM))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(TEXT_DIM))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


def glyph_icon(glyph: str, size: int = 22, color: str = TEXT, font: str = "Segoe UI Symbol", scale: float = 0.78,
               bold: bool = False) -> QIcon:
    """Icon vẽ từ một ký tự (không dùng hình của phần mềm khác)."""
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    f = QFont(font)
    f.setPixelSize(int(size * 2 * scale))
    f.setBold(bold)
    p.setFont(f)
    p.setPen(QColor(color))
    p.drawText(QRectF(0, 0, size * 2, size * 2), Qt.AlignCenter, glyph)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)
