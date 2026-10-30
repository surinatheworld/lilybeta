"""Regression checks for paint-tool hover seams at Windows fractional display scaling."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFocusEvent, QImage, QMouseEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from app.ui.image_view import ImageView, PAINT_TOOLS, TOOL_BRUSH, TOOL_MOVE


def pixels(pixmap):
    image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine()).copy()


def render_probe():
    app = QApplication([])
    view = ImageView()
    view.resize(640, 520)
    yy, xx = np.indices((1600, 1200))
    data = np.repeat((((xx // 7 + yy // 11) % 2) * 255).astype(np.uint8)[..., None], 3, axis=2)
    view.set_image(QImage(data.data, 1200, 1600, 3600, QImage.Format_RGB888).copy())
    view.show()
    app.processEvents()
    results = []
    for tool in PAINT_TOOLS:
        view.set_tool(tool)
        for zoom in (.3, .75, 1., 2.):
            view.zoom_to(zoom)
            for size in (20, 200):
                view.set_brush_size(size)
                for i in range(30):
                    QTest.mouseMove(view.viewport(), QPoint(100 + i * 10, 100 + i * 6))
                    app.processEvents()
                before = view.screen().grabWindow(view.winId())
                if before.isNull():
                    raise RuntimeError("offscreen backing-store capture unavailable")
                view.viewport().update()
                app.processEvents()
                after = view.screen().grabWindow(view.winId())
                changed = int(np.count_nonzero(pixels(before) != pixels(after)))
                results.append({"tool": tool, "zoom": zoom, "size": size, "changed": changed})
    view.deleteLater()
    app.processEvents()
    print(json.dumps(results))
    return 0 if all(row["changed"] == 0 for row in results) else 1


class CursorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = ImageView()
        self.view.resize(500, 400)
        image = QImage(800, 800, QImage.Format_RGB32)
        image.fill(Qt.white)
        self.view.set_image(image, fit=True)
        self.view.show()
        self.app.processEvents()
        self.view.set_tool(TOOL_BRUSH)

    def tearDown(self):
        QTest.mouseRelease(self.view.viewport(), Qt.LeftButton, pos=QPoint(160, 150))
        self.view.deleteLater()
        self.app.processEvents()

    def test_hover_repaints_match_clean_frame_at_150_percent_dpi(self):
        environment = os.environ.copy()
        environment["QT_QPA_PLATFORM"] = "offscreen"
        environment["QT_SCALE_FACTOR"] = "1.5"
        result = subprocess.run([sys.executable, __file__, "--probe"], env=environment,
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(json.loads(result.stdout)), 32)

    def test_missing_release_ends_stroke_without_painting_on_hover(self):
        moved, ended = QSignalSpy(self.view.strokeMoved), QSignalSpy(self.view.strokeEnded)
        QTest.mousePress(self.view.viewport(), Qt.LeftButton, pos=QPoint(130, 130))
        self.assertTrue(self.view._stroking)
        move = QMouseEvent(QEvent.MouseMove, QPointF(160, 150), QPointF(160, 150),
                           Qt.NoButton, Qt.NoButton, Qt.NoModifier)
        QApplication.sendEvent(self.view.viewport(), move)
        self.assertFalse(self.view._stroking)
        self.assertEqual(moved.count(), 0)
        self.assertEqual(ended.count(), 1)

    def test_focus_loss_and_tool_switch_finish_the_stroke_once(self):
        ended = QSignalSpy(self.view.strokeEnded)
        QTest.mousePress(self.view.viewport(), Qt.LeftButton, pos=QPoint(130, 130))
        QApplication.sendEvent(self.view, QFocusEvent(QEvent.FocusOut))
        self.assertFalse(self.view._stroking)
        self.assertEqual(ended.count(), 1)
        QTest.mouseRelease(self.view.viewport(), Qt.LeftButton, pos=QPoint(130, 130))
        self.assertEqual(ended.count(), 1)
        QTest.mousePress(self.view.viewport(), Qt.LeftButton, pos=QPoint(140, 140))
        self.view.set_tool(TOOL_MOVE)
        self.assertFalse(self.view._stroking)
        self.assertEqual(ended.count(), 2)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        raise SystemExit(render_probe())
    unittest.main()
