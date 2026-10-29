"""Khung xem ảnh: zoom, kéo, hiển thị vùng chữ, vẽ vùng mới."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
)

COLOR_PENDING = QColor(230, 60, 60)
COLOR_DONE = QColor(40, 170, 80)
COLOR_SELECTED = QColor(30, 120, 255)


class ImageView(QGraphicsView):
    regionClicked = Signal(int)
    regionDrawn = Signal(tuple)

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
        self._draw_start: QPointF | None = None
        self._rubber: QGraphicsRectItem | None = None
        self._pan_from = None
        self.show_boxes = True

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

    def set_regions(self, regions, selected: int) -> None:
        for item in self._overlays:
            self.scene().removeItem(item)
        self._overlays.clear()
        if not self.show_boxes:
            return
        font = QFont()
        font.setPixelSize(18)
        font.setBold(True)
        for i, r in enumerate(regions):
            x1, y1, x2, y2 = r.box
            color = COLOR_SELECTED if i == selected else (COLOR_DONE if r.done else COLOR_PENDING)
            rect = QGraphicsRectItem(QRectF(x1, y1, x2 - x1, y2 - y1))
            pen = QPen(color, 3 if i == selected else 2)
            pen.setCosmetic(True)
            rect.setPen(pen)
            fill = QColor(color)
            fill.setAlpha(45 if i == selected else 18)
            rect.setBrush(QBrush(fill))
            rect.setData(0, i)
            rect.setCursor(Qt.PointingHandCursor)
            self.scene().addItem(rect)

            label = QGraphicsSimpleTextItem(str(i + 1))
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
            self._overlays += [rect, bg]

    # --- chuột --------------------------------------------------------------
    def wheelEvent(self, e):
        # Lăn chuột = cuộn trang (tiện cho truyện dài), Ctrl + lăn = phóng to/thu nhỏ.
        if e.modifiers() & Qt.ControlModifier:
            f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.scale(f, f)
        else:
            super().wheelEvent(e)

    def mousePressEvent(self, e):
        if e.button() in (Qt.MiddleButton, Qt.RightButton):
            self._pan_from = e.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        if e.button() == Qt.LeftButton:
            pos = self.mapToScene(e.position().toPoint())
            for item in self.items(e.position().toPoint()):
                idx = item.data(0)
                if idx is None and item.parentItem() is not None:
                    idx = item.parentItem().data(0)
                if idx is not None:
                    self.regionClicked.emit(int(idx))
                    return
            # Bấm vào chỗ trống: kéo để vẽ vùng mới.
            self._draw_start = pos
            self._rubber = QGraphicsRectItem()
            pen = QPen(COLOR_SELECTED, 2, Qt.DashLine)
            pen.setCosmetic(True)
            self._rubber.setPen(pen)
            self.scene().addItem(self._rubber)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan_from is not None:
            d = e.position() - self._pan_from
            self._pan_from = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        if self._draw_start is not None and self._rubber is not None:
            pos = self.mapToScene(e.position().toPoint())
            self._rubber.setRect(QRectF(self._draw_start, pos).normalized())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan_from is not None:
            self._pan_from = None
            self.unsetCursor()
            return
        if self._draw_start is not None and self._rubber is not None:
            r = self._rubber.rect()
            self.scene().removeItem(self._rubber)
            self._rubber, self._draw_start = None, None
            if r.width() > 6 and r.height() > 6:
                self.regionDrawn.emit((int(r.left()), int(r.top()), int(r.right()), int(r.bottom())))
            return
        super().mouseReleaseEvent(e)
