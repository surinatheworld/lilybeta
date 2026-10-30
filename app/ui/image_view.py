"""Khung xem ảnh: zoom, kéo, hiển thị vùng chữ, vẽ vùng mới (khung chữ nhật hoặc lasso),
chọn nhiều vùng và kéo/đổi cỡ khung chữ để tự căn chỉnh."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsPolygonItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

COLOR_PENDING = QColor(230, 60, 60)
COLOR_SFX = QColor(245, 150, 20)
COLOR_DONE = QColor(40, 170, 80)
COLOR_REDRAW = QColor(235, 200, 0)  # đã đặt chữ, nền khó để người dùng tự redraw
COLOR_SELECTED = QColor(30, 120, 255)
COLOR_EDIT = QColor(200, 40, 200)

TOOL_RECT = "rect"
TOOL_LASSO = "lasso"
CLICK_TOLERANCE = 5  # px màn hình: kéo ít hơn thế coi là bấm chọn
HANDLE = 9  # px màn hình: cỡ tay nắm ở góc khung chữ


class ImageView(QGraphicsView):
    regionClicked = Signal(int, int)  # chỉ số vùng, phím bổ trợ (Ctrl/Shift) dạng int
    regionDrawn = Signal(tuple)
    lassoDrawn = Signal(list)
    textRectChanged = Signal(tuple)  # khung chữ mới (x1, y1, x2, y2) sau khi kéo/đổi cỡ

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(60, 60, 64))
        self._pixmap = QGraphicsPixmapItem()
        self._pixmap.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self._pixmap)
        self._overlays: list = []
        self._press_pos = None  # vị trí bấm chuột (toạ độ màn hình)
        self._press_region: int | None = None
        self._press_mods = 0
        self._points: list[QPointF] = []
        self._preview = None
        self._pan_from = None
        self.show_boxes = True
        self.tool = TOOL_RECT
        # Khung chữ đang chỉnh (vùng chính đã xử lý)
        self._edit_rect: QRectF | None = None
        self._edit_item: QGraphicsRectItem | None = None
        self._edit_mode: str | None = None  # "move" | "tl" | "tr" | "bl" | "br"
        self._edit_start: QPointF | None = None
        self._edit_orig: QRectF | None = None
        self._handles: list = []

    # --- ảnh & vùng -------------------------------------------------------
    def set_image(self, img: QImage, fit: bool = False) -> None:
        self._pixmap.setPixmap(QPixmap.fromImage(img))
        self.scene().setSceneRect(QRectF(img.rect()))
        if fit:
            self.fit_width()

    def fit_width(self) -> None:
        r = self.scene().sceneRect()
        if r.isEmpty():
            return
        self.resetTransform()
        s = (self.viewport().width() - 20) / r.width()
        self.scale(s, s)
        self.centerOn(r.center().x(), 0)

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.viewport().setCursor(Qt.CrossCursor)

    def set_regions(self, regions, selection: list[int], edit_rect=None) -> None:
        """Vẽ các khung; `selection` = các vùng đang chọn; `edit_rect` = khung chữ cho phép kéo chỉnh."""
        for item in self._overlays:
            self.scene().removeItem(item)
        self._overlays.clear()
        self._edit_item = None
        self._edit_rect = QRectF(*edit_rect[:2], edit_rect[2] - edit_rect[0], edit_rect[3] - edit_rect[1]) \
            if edit_rect else None
        if self.show_boxes:
            self._draw_regions(regions, set(selection))
        if self._edit_rect is not None:
            self._draw_edit_frame()

    def _draw_regions(self, regions, selected: set[int]) -> None:
        font = QFont()
        font.setPixelSize(18)
        font.setBold(True)
        for i, r in enumerate(regions):
            x1, y1, x2, y2 = r.box
            if i in selected:
                color = COLOR_SELECTED
            elif r.needs_redraw:
                color = COLOR_REDRAW
            elif r.done:
                color = COLOR_DONE
            else:
                color = COLOR_SFX if r.kind == "sfx" else COLOR_PENDING
            if r.polygon:
                shape = QGraphicsPolygonItem(QPolygonF([QPointF(x, y) for x, y in r.polygon]))
            else:
                shape = QGraphicsRectItem(QRectF(x1, y1, x2 - x1, y2 - y1))
            pen = QPen(color, 3 if i in selected else 2)
            if (r.kind == "sfx" and not r.done) or r.needs_redraw:
                pen.setStyle(Qt.DashLine)
            pen.setCosmetic(True)
            shape.setPen(pen)
            fill = QColor(color)
            fill.setAlpha(45 if i in selected else 18)
            shape.setBrush(QBrush(fill))
            shape.setData(0, i)
            shape.setCursor(Qt.PointingHandCursor)
            self.scene().addItem(shape)

            label = QGraphicsSimpleTextItem(f"{i + 1} SFX" if r.kind == "sfx" else str(i + 1))
            label.setFont(font)
            label.setBrush(QBrush(Qt.white))
            br = label.boundingRect()
            bg = QGraphicsRectItem(QRectF(0, 0, br.width() + 8, br.height() + 2))
            bg.setBrush(QBrush(color))
            bg.setPen(QPen(Qt.NoPen))
            bg.setPos(x1, y1 - br.height() - 2 if y1 > br.height() else y1)
            bg.setFlag(QGraphicsRectItem.ItemIgnoresTransformations)
            label.setParentItem(bg)
            label.setPos(4, 1)
            bg.setData(0, i)
            self.scene().addItem(bg)
            self._overlays += [shape, bg]

    def _draw_edit_frame(self) -> None:
        r = self._edit_rect
        frame = QGraphicsRectItem(r)
        pen = QPen(COLOR_EDIT, 1.5, Qt.DashLine)
        pen.setCosmetic(True)
        frame.setPen(pen)
        frame.setZValue(10)
        self.scene().addItem(frame)
        self._overlays.append(frame)
        self._edit_item = frame
        self._handles = []
        for pt in self._corners(r):
            # Giữ nguyên cỡ trên màn hình dù phóng to/thu nhỏ.
            h = QGraphicsRectItem(QRectF(-HANDLE / 2, -HANDLE / 2, HANDLE, HANDLE))
            h.setFlag(QGraphicsRectItem.ItemIgnoresTransformations)
            h.setPos(pt)
            h.setBrush(QBrush(Qt.white))
            hp = QPen(COLOR_EDIT, 1.5)
            hp.setCosmetic(True)
            h.setPen(hp)
            h.setZValue(11)
            self.scene().addItem(h)
            self._overlays.append(h)
            self._handles.append(h)

    @staticmethod
    def _corners(r: QRectF):
        return (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight())

    def _edit_hit(self, scene_pos: QPointF) -> str | None:
        """Bấm trúng tay nắm góc / bên trong khung chữ đang chỉnh?"""
        r = self._edit_rect
        if r is None:
            return None
        tol = HANDLE / max(self.transform().m11(), 1e-6)
        corners = {"tl": r.topLeft(), "tr": r.topRight(), "bl": r.bottomLeft(), "br": r.bottomRight()}
        for name, pt in corners.items():
            if abs(scene_pos.x() - pt.x()) <= tol and abs(scene_pos.y() - pt.y()) <= tol:
                return name
        return "move" if r.contains(scene_pos) else None

    # --- chuột --------------------------------------------------------------
    def wheelEvent(self, e):
        # Lăn chuột = cuộn trang (tiện cho truyện dài), Ctrl + lăn = phóng to/thu nhỏ.
        if e.modifiers() & Qt.ControlModifier:
            f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.scale(f, f)
        else:
            super().wheelEvent(e)

    def _region_at(self, pos) -> int | None:
        for item in self.items(pos):
            idx = item.data(0)
            if idx is None and item.parentItem() is not None:
                idx = item.parentItem().data(0)
            if idx is not None:
                return int(idx)
        return None

    def mousePressEvent(self, e):
        if e.button() in (Qt.MiddleButton, Qt.RightButton):
            self._pan_from = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.LeftButton:
            pos = self.mapToScene(e.position().toPoint())
            mods = e.modifiers()
            hit = None if mods & (Qt.ControlModifier | Qt.ShiftModifier) else self._edit_hit(pos)
            if hit:
                self._edit_mode, self._edit_start, self._edit_orig = hit, pos, QRectF(self._edit_rect)
                return
            # Bấm rồi thả tại chỗ = chọn vùng; bấm rồi kéo = vẽ vùng mới (kể cả đè lên vùng cũ).
            self._press_pos = e.position()
            self._press_region = self._region_at(e.position().toPoint())
            self._press_mods = int(mods.value) if hasattr(mods, "value") else int(mods)
            self._points = [pos]
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan_from is not None:
            d = e.position() - self._pan_from
            self._pan_from = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        pos = self.mapToScene(e.position().toPoint())
        if self._edit_mode is not None:
            self._drag_edit(pos)
            return
        if self._press_pos is not None:
            if self._preview is None and (e.position() - self._press_pos).manhattanLength() < CLICK_TOLERANCE:
                return
            if self.tool == TOOL_LASSO:
                if (pos - self._points[-1]).manhattanLength() >= 2 / max(self.transform().m11(), 1e-6):
                    self._points.append(pos)
                path = QPainterPath(self._points[0])
                for p in self._points[1:]:
                    path.lineTo(p)
                path.closeSubpath()
                self._ensure_preview(QGraphicsPathItem).setPath(path)
            else:
                self._ensure_preview(QGraphicsRectItem).setRect(QRectF(self._points[0], pos).normalized())
            return
        # Đổi con trỏ khi rê qua khung chữ đang chỉnh.
        hit = self._edit_hit(pos)
        cursors = {"move": Qt.SizeAllCursor, "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
                   "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor}
        self.viewport().setCursor(cursors.get(hit, Qt.CrossCursor))
        super().mouseMoveEvent(e)

    def _drag_edit(self, pos: QPointF) -> None:
        d = pos - self._edit_start
        r = QRectF(self._edit_orig)
        if self._edit_mode == "move":
            r.translate(d)
        else:
            if "l" in self._edit_mode:
                r.setLeft(min(r.left() + d.x(), r.right() - 10))
            if "r" in self._edit_mode:
                r.setRight(max(r.right() + d.x(), r.left() + 10))
            if "t" in self._edit_mode:
                r.setTop(min(r.top() + d.y(), r.bottom() - 10))
            if "b" in self._edit_mode:
                r.setBottom(max(r.bottom() + d.y(), r.top() + 10))
        self._edit_rect = r
        if self._edit_item is not None:
            self._edit_item.setRect(r)
            for h, pt in zip(self._handles, self._corners(r)):
                h.setPos(pt)

    def _ensure_preview(self, cls):
        if self._preview is None:
            self._preview = cls()
            pen = QPen(COLOR_SELECTED, 2, Qt.DashLine)
            pen.setCosmetic(True)
            self._preview.setPen(pen)
            fill = QColor(COLOR_SELECTED)
            fill.setAlpha(30)
            self._preview.setBrush(QBrush(fill))
            self.scene().addItem(self._preview)
        return self._preview

    def mouseReleaseEvent(self, e):
        if self._pan_from is not None:
            self._pan_from = None
            self.viewport().setCursor(Qt.CrossCursor)
            return
        if self._edit_mode is not None:
            self._edit_mode = None
            r = self._edit_rect
            if r != self._edit_orig:
                self.textRectChanged.emit((int(r.left()), int(r.top()), int(r.right()), int(r.bottom())))
            return
        if self._press_pos is None:
            return super().mouseReleaseEvent(e)
        preview, self._preview = self._preview, None
        self._press_pos = None
        if preview is None:
            if self._press_region is not None:
                self.regionClicked.emit(self._press_region, self._press_mods)
            return
        self.scene().removeItem(preview)
        if self.tool == TOOL_LASSO:
            pts = [(int(p.x()), int(p.y())) for p in self._points]
            br = QPolygonF(self._points).boundingRect()
            if len(pts) >= 3 and br.width() > 6 and br.height() > 6:
                self.lassoDrawn.emit(pts)
        else:
            r = preview.rect()
            if r.width() > 6 and r.height() > 6:
                self.regionDrawn.emit((int(r.left()), int(r.top()), int(r.right()), int(r.bottom())))
