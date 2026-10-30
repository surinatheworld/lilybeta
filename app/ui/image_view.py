"""Khung vẽ (canvas): zoom, kéo, các công cụ kiểu Photoshop (di chuyển, vùng chọn, cọ, tẩy, clone, healing, chữ,
khung chữ), hiển thị vùng chữ, vùng chọn ("kiến bò") và kéo/đổi cỡ khung chữ để tự căn chỉnh."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QGraphicsEllipseItem,
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

# Công cụ
TOOL_MOVE = "move"
TOOL_MARQUEE = "marquee"
TOOL_LASSO_SEL = "lasso_sel"
TOOL_WAND = "wand"
TOOL_HEAL = "heal"
TOOL_BRUSH = "brush"
TOOL_CLONE = "clone"
TOOL_ERASER = "eraser"
TOOL_TEXT = "text"
TOOL_RECT = "rect"  # vẽ khung chữ (Lilyzono) hình chữ nhật
TOOL_LASSO = "lasso"  # vẽ khung chữ (Lilyzono) tự do
TOOL_HAND = "hand"
TOOL_ZOOM = "zoom"
PAINT_TOOLS = (TOOL_BRUSH, TOOL_ERASER, TOOL_CLONE, TOOL_HEAL)
REGION_TOOLS = (TOOL_MOVE, TOOL_TEXT, TOOL_RECT, TOOL_LASSO)  # công cụ bấm chọn được khung chữ

CLICK_TOLERANCE = 5  # px màn hình: kéo ít hơn thế coi là bấm chọn
HANDLE = 9  # px màn hình: cỡ tay nắm ở góc khung chữ


def _mods(e) -> int:
    m = e.modifiers()
    return int(m.value) if hasattr(m, "value") else int(m)


class ImageView(QGraphicsView):
    regionClicked = Signal(int, int)  # chỉ số vùng, phím bổ trợ (Ctrl/Shift) dạng int
    regionDrawn = Signal(tuple)
    lassoDrawn = Signal(list)
    textRectChanged = Signal(tuple)  # khung chữ mới (x1, y1, x2, y2) sau khi kéo/đổi cỡ
    selectionMade = Signal(str, object, str)  # "rect" | "poly" | "wand" | "none", dữ liệu, chế độ new/add/sub/inter
    strokeBegan = Signal(float, float, int)
    strokeMoved = Signal(float, float)
    strokeEnded = Signal()
    cloneSourceSet = Signal(float, float)
    textClicked = Signal(float, float)  # công cụ chữ bấm vào chỗ trống
    textBoxDrawn = Signal(tuple)  # công cụ chữ kéo một khung đoạn văn
    zoomChanged = Signal(float)
    cursorMoved = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(40, 40, 40))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
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
        self._space = False
        self._stroking = False
        self.show_boxes = True
        self.tool = TOOL_MOVE
        self.sel_mode = "new"
        self.brush_size = 20.0
        # Khung chữ đang chỉnh (vùng chính đã xử lý)
        self._edit_rect: QRectF | None = None
        self._edit_item: QGraphicsRectItem | None = None
        self._edit_mode: str | None = None  # "move" | "tl" | "tr" | "bl" | "br"
        self._edit_start: QPointF | None = None
        self._edit_orig: QRectF | None = None
        self._handles: list = []
        # Vùng chọn: viền trắng + viền đen nét đứt chạy ("kiến bò")
        self._ants = [QGraphicsPathItem(), QGraphicsPathItem()]
        for i, item in enumerate(self._ants):
            pen = QPen(Qt.white if i == 0 else Qt.black, 1)
            pen.setCosmetic(True)
            if i:
                pen.setDashPattern([4, 4])
            item.setPen(pen)
            item.setZValue(20)
            self.scene().addItem(item)
        self._ant_offset = 0
        self._ant_timer = QTimer(self)
        self._ant_timer.timeout.connect(self._march)
        self._ant_timer.start(120)
        # Con trỏ cọ (vòng tròn đúng cỡ) và nét Spot Healing đang tô
        self._brush_cursor = QGraphicsEllipseItem()
        bp = QPen(QColor(255, 255, 255, 200), 1)
        bp.setCosmetic(True)
        self._brush_cursor.setPen(bp)
        self._brush_cursor.setZValue(30)
        self._brush_cursor.hide()
        self.scene().addItem(self._brush_cursor)
        self._heal_item = QGraphicsPathItem()
        self._heal_item.setZValue(15)
        self.scene().addItem(self._heal_item)

    # --- ảnh & vùng -------------------------------------------------------
    def set_image(self, img: QImage, fit: bool = False) -> None:
        self._pixmap.setPixmap(QPixmap.fromImage(img))
        self.scene().setSceneRect(QRectF(img.rect()).adjusted(-200, -200, 200, 200))
        if fit:
            self.fit_page()

    def update_patch(self, x: int, y: int, img: QImage) -> None:
        """Vẽ đè một mảnh ảnh (nét cọ đang vẽ) mà không dựng lại cả trang."""
        pm = self._pixmap.pixmap()
        p = QPainter(pm)
        p.drawImage(x, y, img)
        p.end()
        self._pixmap.setPixmap(pm)

    def image_rect(self) -> QRectF:
        return self._pixmap.boundingRect()

    def fit_width(self) -> None:
        r = self.image_rect()
        if r.isEmpty():
            return
        self.resetTransform()
        s = (self.viewport().width() - 20) / r.width()
        self.scale(s, s)
        self.centerOn(r.center().x(), 0)
        self.zoomChanged.emit(self.zoom)

    def fit_page(self) -> None:
        r = self.image_rect()
        if r.isEmpty():
            return
        self.resetTransform()
        s = min((self.viewport().width() - 30) / r.width(), (self.viewport().height() - 30) / r.height())
        self.scale(s, s)
        self.centerOn(r.center())
        self.zoomChanged.emit(self.zoom)

    @property
    def zoom(self) -> float:
        return self.transform().m11()

    def zoom_by(self, f: float) -> None:
        z = self.zoom * f
        if 0.02 <= z <= 32:
            self.scale(f, f)
            self.zoomChanged.emit(self.zoom)
            self._update_brush_cursor()

    def zoom_to(self, z: float) -> None:
        self.zoom_by(z / max(self.zoom, 1e-6))

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self._update_cursor()

    def set_brush_size(self, size: float) -> None:
        self.brush_size = size
        self._update_brush_cursor()

    def _update_cursor(self) -> None:
        if self._space or self.tool == TOOL_HAND:
            cur = Qt.OpenHandCursor
        elif self.tool in PAINT_TOOLS:
            cur = Qt.CrossCursor
        elif self.tool == TOOL_TEXT:
            cur = Qt.IBeamCursor
        elif self.tool == TOOL_MOVE:
            cur = Qt.ArrowCursor
        else:
            cur = Qt.CrossCursor
        self.viewport().setCursor(cur)
        self._brush_cursor.setVisible(self.tool in PAINT_TOOLS and not self._space)

    def _update_brush_cursor(self, pos: QPointF | None = None) -> None:
        if pos is None:
            pos = self._brush_cursor.rect().center()
        r = self.brush_size / 2
        self._brush_cursor.setRect(QRectF(pos.x() - r, pos.y() - r, 2 * r, 2 * r))

    def set_selection_outline(self, contours) -> None:
        path = QPainterPath()
        for c in contours:
            path.moveTo(float(c[0][0]), float(c[0][1]))
            for x, y in c[1:]:
                path.lineTo(float(x), float(y))
            path.closeSubpath()
        for item in self._ants:
            item.setPath(path)

    def _march(self) -> None:
        if self._ants[1].path().isEmpty():
            return
        self._ant_offset = (self._ant_offset + 1) % 8
        pen = self._ants[1].pen()
        pen.setDashOffset(self._ant_offset)
        self._ants[1].setPen(pen)

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
        font.setPixelSize(14)
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
            pen = QPen(color, 2 if i in selected else 1.5)
            if (r.kind == "sfx" and not r.done) or r.needs_redraw:
                pen.setStyle(Qt.DashLine)
            pen.setCosmetic(True)
            shape.setPen(pen)
            fill = QColor(color)
            fill.setAlpha(40 if i in selected else 12)
            shape.setBrush(QBrush(fill))
            shape.setData(0, i)
            self.scene().addItem(shape)

            label = QGraphicsSimpleTextItem(f"{i + 1} SFX" if r.kind == "sfx" else str(i + 1))
            label.setFont(font)
            label.setBrush(QBrush(Qt.white))
            br = label.boundingRect()
            bg = QGraphicsRectItem(QRectF(0, 0, br.width() + 6, br.height() + 2))
            bg.setBrush(QBrush(color))
            bg.setPen(QPen(Qt.NoPen))
            bg.setPos(x1, y1)
            bg.setFlag(QGraphicsRectItem.ItemIgnoresTransformations)
            label.setParentItem(bg)
            label.setPos(3, 1)
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
        if r is None or self.tool not in REGION_TOOLS:
            return None
        tol = HANDLE / max(self.zoom, 1e-6)
        corners = {"tl": r.topLeft(), "tr": r.topRight(), "bl": r.bottomLeft(), "br": r.bottomRight()}
        for name, pt in corners.items():
            if abs(scene_pos.x() - pt.x()) <= tol and abs(scene_pos.y() - pt.y()) <= tol:
                return name
        return "move" if r.contains(scene_pos) else None

    # --- bàn phím --------------------------------------------------------------
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True
            self._update_cursor()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False
            self._update_cursor()
            return
        super().keyReleaseEvent(e)

    def enterEvent(self, e):
        self.setFocus()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._brush_cursor.hide()
        super().leaveEvent(e)

    # --- chuột --------------------------------------------------------------
    def wheelEvent(self, e):
        # Lăn chuột = cuộn trang (tiện cho truyện dài); Ctrl/Alt + lăn = phóng to/thu nhỏ.
        if e.modifiers() & (Qt.ControlModifier | Qt.AltModifier):
            dy = e.angleDelta().y() or e.angleDelta().x()
            self.zoom_by(1.15 if dy > 0 else 1 / 1.15)
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

    def _sel_mode(self, mods: int) -> str:
        shift, alt = bool(mods & int(Qt.ShiftModifier.value)), bool(mods & int(Qt.AltModifier.value))
        if shift and alt:
            return "inter"
        if shift:
            return "add"
        if alt:
            return "sub"
        return self.sel_mode

    def mousePressEvent(self, e):
        pan = e.button() in (Qt.MiddleButton, Qt.RightButton) or (
            e.button() == Qt.LeftButton and (self._space or self.tool == TOOL_HAND))
        if pan:
            self._pan_from = e.position()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            return
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        pos = self.mapToScene(e.position().toPoint())
        mods = e.modifiers()
        if self.tool == TOOL_ZOOM:
            self.centerOn(pos)
            self.zoom_by(1 / 1.5 if mods & Qt.AltModifier else 1.5)
            return
        if self.tool in PAINT_TOOLS:
            if self.tool == TOOL_CLONE and mods & Qt.AltModifier:
                self.cloneSourceSet.emit(pos.x(), pos.y())
                return
            self._stroking = True
            if self.tool == TOOL_HEAL:
                path = QPainterPath(pos)
                path.lineTo(pos + QPointF(0.01, 0.01))
                pen = QPen(QColor(230, 40, 40, 110), self.brush_size, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                self._heal_item.setPen(pen)
                self._heal_item.setPath(path)
            self.strokeBegan.emit(pos.x(), pos.y(), _mods(e))
            return
        if self.tool == TOOL_WAND:
            self.selectionMade.emit("wand", (int(pos.x()), int(pos.y())), self._sel_mode(_mods(e)))
            return
        hit = None if mods & (Qt.ControlModifier | Qt.ShiftModifier) else self._edit_hit(pos)
        if hit:
            self._edit_mode, self._edit_start, self._edit_orig = hit, pos, QRectF(self._edit_rect)
            return
        # Bấm rồi thả tại chỗ = chọn; bấm rồi kéo = vẽ (khung chữ, vùng chọn, khung đoạn văn).
        self._press_pos = e.position()
        self._press_region = self._region_at(e.position().toPoint()) if self.tool in REGION_TOOLS else None
        self._press_mods = _mods(e)
        self._points = [pos]

    def mouseMoveEvent(self, e):
        pos = self.mapToScene(e.position().toPoint())
        self.cursorMoved.emit(int(pos.x()), int(pos.y()))
        if self.tool in PAINT_TOOLS:
            self._update_brush_cursor(pos)
            self._brush_cursor.setVisible(not self._space)
        if self._pan_from is not None:
            d = e.position() - self._pan_from
            self._pan_from = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        if self._stroking:
            if self.tool == TOOL_HEAL:
                path = self._heal_item.path()
                path.lineTo(pos)
                self._heal_item.setPath(path)
            self.strokeMoved.emit(pos.x(), pos.y())
            return
        if self._edit_mode is not None:
            self._drag_edit(pos)
            return
        if self._press_pos is not None and self.tool != TOOL_MOVE:
            if self._preview is None and (e.position() - self._press_pos).manhattanLength() < CLICK_TOLERANCE:
                return
            if self.tool in (TOOL_LASSO, TOOL_LASSO_SEL):
                if (pos - self._points[-1]).manhattanLength() >= 2 / max(self.zoom, 1e-6):
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
        if hit:
            cursors = {"move": Qt.SizeAllCursor, "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
                       "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor}
            self.viewport().setCursor(cursors[hit])
        elif not self._space:
            self._update_cursor()
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
            pen = QPen(COLOR_SELECTED if self.tool in (TOOL_RECT, TOOL_LASSO, TOOL_TEXT) else Qt.white, 1.5,
                       Qt.DashLine)
            pen.setCosmetic(True)
            self._preview.setPen(pen)
            if self.tool in (TOOL_RECT, TOOL_LASSO):
                fill = QColor(COLOR_SELECTED)
                fill.setAlpha(30)
                self._preview.setBrush(QBrush(fill))
            self._preview.setZValue(25)
            self.scene().addItem(self._preview)
        return self._preview

    def mouseReleaseEvent(self, e):
        if self._pan_from is not None:
            self._pan_from = None
            self._update_cursor()
            return
        if self._stroking:
            self._stroking = False
            self._heal_item.setPath(QPainterPath())
            self.strokeEnded.emit()
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
        mode = self._sel_mode(self._press_mods)
        if preview is None:  # bấm tại chỗ
            if self._press_region is not None:
                self.regionClicked.emit(self._press_region, self._press_mods)
            elif self.tool in (TOOL_MARQUEE, TOOL_LASSO_SEL) and mode == "new":
                self.selectionMade.emit("none", None, "new")  # bấm ra ngoài = bỏ chọn (như Photoshop)
            elif self.tool == TOOL_TEXT:
                p = self._points[0]
                self.textClicked.emit(p.x(), p.y())
            return
        self.scene().removeItem(preview)
        if self.tool in (TOOL_LASSO, TOOL_LASSO_SEL):
            pts = [(int(p.x()), int(p.y())) for p in self._points]
            br = QPolygonF(self._points).boundingRect()
            if len(pts) >= 3 and br.width() > 2 and br.height() > 2:
                if self.tool == TOOL_LASSO:
                    self.lassoDrawn.emit(pts)
                else:
                    self.selectionMade.emit("poly", pts, mode)
        else:
            r = preview.rect()
            box = (int(r.left()), int(r.top()), int(r.right()), int(r.bottom()))
            if r.width() > 2 and r.height() > 2:
                if self.tool == TOOL_MARQUEE:
                    self.selectionMade.emit("rect", box, mode)
                elif self.tool == TOOL_TEXT and r.width() > 6 and r.height() > 6:
                    self.textBoxDrawn.emit(box)
                elif self.tool == TOOL_RECT and r.width() > 6 and r.height() > 6:
                    self.regionDrawn.emit(box)
