"""Giao diện tối kiểu Photoshop 2023, icon công cụ nét đơn sắc và icon ký tự có cache."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap

BG = "#323232"  # nền panel
BG_DARK = "#252525"  # nền quanh ảnh, ô nhập
BG_LIGHT = "#3d3d3d"
BORDER = "#1e1e1e"
TEXT = "#d6d6d6"
TEXT_DIM = "#8f8f8f"
ACCENT = "#378ef0"

QSS = f"""
QWidget {{ background:{BG}; color:{TEXT}; font-family:'Segoe UI'; font-size:12px; }}
QLabel {{ background:transparent; }}
QMainWindow::separator {{ background:{BORDER}; width:3px; height:3px; }}
QMenuBar {{ background:{BG}; border-bottom:1px solid {BORDER}; }}
QMenuBar::item {{ padding:4px 10px; background:transparent; }}
QMenuBar::item:selected, QMenu::item:selected {{ background:{ACCENT}; color:white; }}
QMenu {{ background:{BG_LIGHT}; border:1px solid {BORDER}; padding:4px 0; }}
QMenu::item {{ padding:4px 28px 4px 22px; }}
QMenu::item:disabled {{ color:{TEXT_DIM}; }}
QMenu::separator {{ height:1px; background:{BORDER}; margin:4px 8px; }}
QToolBar {{ background:{BG}; border:none; spacing:2px; padding:2px; }}
QToolBar#tools {{ background:#292929; border-right:1px solid {BORDER}; padding:5px 3px; spacing:3px; }}
QToolBar#tools QToolButton {{ padding:4px; border:1px solid transparent; border-radius:2px; }}
QToolBar#tools QToolButton:checked {{ background:#454545; border-color:#686868; }}
QToolBar#toolOptions {{ min-height:34px; border-bottom:1px solid {BORDER}; padding:3px 8px; }}
QToolButton:hover {{ background:#444; }}
QDockWidget {{ titlebar-close-icon:none; }}
QDockWidget::title {{ background:{BG_LIGHT}; padding:4px 8px; border-bottom:1px solid {BORDER}; }}
QTabBar::tab {{ background:{BG_DARK}; color:{TEXT_DIM}; padding:4px 12px; border:1px solid {BORDER}; border-bottom:none; }}
QTabBar::tab:selected {{ background:{BG}; color:{TEXT}; }}
QTabBar::tab:hover:!selected {{ background:{BG_LIGHT}; }}
QTabBar#documentTabs {{ background:#202020; border-bottom:1px solid {BORDER}; }}
QTabBar#documentTabs::tab {{ padding:8px 18px; background:#292929; border:none; border-right:1px solid {BORDER}; }}
QTabBar#documentTabs::tab:selected {{ background:#3b3b3b; color:#eeeeee; }}
QWidget#welcome {{ background:#252525; }}
QLabel#welcomeTitle {{ font-size:32px; font-weight:600; color:#ededed; }}
QTabWidget::pane {{ border:1px solid {BORDER}; }}
QGroupBox {{ border:1px solid #444; border-radius:3px; margin-top:14px; padding-top:4px; }}
QGroupBox::title {{ subcontrol-origin:margin; left:8px; padding:0 4px; color:{TEXT_DIM}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QFontComboBox, QListWidget {{
    background:{BG_DARK}; border:1px solid #4a4a4a; border-radius:2px; selection-background-color:{ACCENT}; }}
QComboBox QAbstractItemView {{ background:{BG_LIGHT}; selection-background-color:{ACCENT}; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ min-height:23px; padding:0 4px; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color:{ACCENT}; }}
QComboBox::drop-down {{ border:none; width:18px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width:13px; border:none; }}
QPushButton {{ background:#4a4a4a; border:1px solid #2a2a2a; border-radius:3px; padding:4px 10px; }}
QPushButton:hover {{ background:#565656; }}
QPushButton:pressed, QPushButton:checked {{ background:{ACCENT}; color:white; }}
QPushButton:disabled {{ background:#363636; color:{TEXT_DIM}; }}
QListWidget {{ outline:none; }}
QListWidget::item {{ padding:4px 6px; border-bottom:1px solid #2b2b2b; }}
QListWidget::item:selected {{ background:#4b4b4b; color:white; }}
QScrollBar:vertical {{ background:{BG}; width:10px; }}
QScrollBar::handle:vertical {{ background:#5a5a5a; border-radius:4px; min-height:24px; }}
QScrollBar:horizontal {{ background:{BG}; height:10px; }}
QScrollBar::handle:horizontal {{ background:#5a5a5a; border-radius:4px; min-width:24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QStatusBar {{ background:#292929; border-top:1px solid {BORDER}; color:{TEXT_DIM}; }}
QStatusBar::item {{ border:none; }}
QCheckBox::indicator {{ width:13px; height:13px; }}
QToolTip {{ background:#f0f0f0; color:#202020; border:1px solid #888; }}
"""


def apply_dark(app) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))
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


@lru_cache(maxsize=128)
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


@lru_cache(maxsize=32)
def tool_icon(tool: str, size: int = 22) -> QIcon:
    """Consistent monochrome tool outlines, independent of emoji/font fallback."""
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size * 2 / 24, size * 2 / 24)
    pen = QPen(QColor(TEXT), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)

    def line(x1, y1, x2, y2):
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    def path(points, closed=False):
        shape = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            shape.lineTo(QPointF(*point))
        if closed:
            shape.closeSubpath()
        p.drawPath(shape)

    if tool == "move":
        line(4, 12, 20, 12)
        line(12, 4, 12, 20)
        for points in ([(4, 12), (7, 9)], [(4, 12), (7, 15)], [(20, 12), (17, 9)],
                       [(20, 12), (17, 15)], [(12, 4), (9, 7)], [(12, 4), (15, 7)],
                       [(12, 20), (9, 17)], [(12, 20), (15, 17)]):
            path(points)
    elif tool == "marquee":
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        p.drawRect(QRectF(4, 5, 16, 14))
    elif tool in ("lasso", "lasso_sel"):
        shape = QPainterPath(QPointF(8, 17))
        shape.cubicTo(0, 15, 3, 4, 13, 4)
        shape.cubicTo(24, 4, 23, 15, 12, 17)
        shape.cubicTo(4, 20, 6, 11, 10, 15)
        shape.cubicTo(13, 18, 9, 22, 6, 21)
        p.drawPath(shape)
        if tool == "lasso":
            line(17, 18, 21, 18)
            line(19, 16, 19, 20)
    elif tool == "wand":
        path([(5, 20), (17, 8), (15, 6), (3, 18)], True)
        for x, y in ((19, 4), (8, 4), (20, 13)):
            line(x - 2, y, x + 2, y)
            line(x, y - 2, x, y + 2)
    elif tool in ("brush", "heal"):
        path([(9, 16), (18, 4), (21, 7), (12, 18)], True)
        shape = QPainterPath(QPointF(9, 15))
        shape.cubicTo(3, 14, 8, 20, 3, 21)
        shape.cubicTo(11, 23, 14, 18, 9, 15)
        p.drawPath(shape)
        if tool == "heal":
            line(4, 6, 10, 6)
            line(7, 3, 7, 9)
    elif tool == "clone":
        path([(5, 17), (5, 20), (20, 20), (20, 17), (15, 15), (14, 10)])
        path([(10, 10), (9, 15), (5, 17), (20, 17)])
        p.drawEllipse(QRectF(9, 3, 6, 8))
    elif tool == "eraser":
        path([(4, 14), (13, 4), (21, 11), (12, 21), (10, 21)], True)
        line(8, 10, 16, 17)
        line(10, 21, 21, 21)
    elif tool == "text":
        pen.setWidthF(2)
        p.setPen(pen)
        line(5, 5, 19, 5)
        line(5, 5, 5, 8)
        line(19, 5, 19, 8)
        line(12, 5, 12, 20)
        line(9, 20, 15, 20)
    elif tool == "typeset":
        path([(5, 5), (19, 5), (19, 15), (12, 15), (8, 19), (8, 15), (5, 15)], True)
        line(8, 8, 16, 8)
        line(8, 11, 13, 11)
    elif tool == "rect":
        p.drawRect(QRectF(4, 4, 16, 16))
        line(8, 8, 16, 8)
        line(12, 8, 12, 16)
    elif tool == "hand":
        path([(7, 13), (7, 6), (9, 5), (10, 12), (10, 4), (12, 3), (13, 12),
              (14, 5), (16, 5), (16, 13), (18, 8), (20, 9), (19, 17), (16, 21),
              (10, 21), (4, 15), (4, 12), (7, 13)], True)
    elif tool == "zoom":
        p.drawEllipse(QRectF(3, 3, 13, 13))
        line(15, 15, 21, 21)
        line(7, 9.5, 12, 9.5)
        line(9.5, 7, 9.5, 12)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)
