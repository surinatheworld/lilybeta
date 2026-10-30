"""Offline regression checks for compositing, incremental drawing and edit history."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QToolBar

from app.core import project
from app.core.document import BLENDS, Document, _blend, _checker, doc_for
from app.core.page import Page, Region
from app.ui.image_view import CanvasImageItem
from app.ui.main_window import MainWindow
from app.ui.theme import apply_dark
from tools.benchmark_editor import load_offscreen_fonts


def image_array(image):
    image = image.convertToFormat(QImage.Format_RGB888)
    return np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())[
        :, :image.width() * 3].reshape(image.height(), image.width(), 3).copy()


def reference_composite(doc, box=None):
    """Original general compositor, kept as a pixel-level oracle for fast paths."""
    w, h = doc.size
    x1, y1, x2, y2 = box or (0, 0, w, h)
    out = _checker(x1, y1, x2, y2)
    bg = doc.layers[0]
    if bg.visible and bg.opacity > 0:
        src = doc.page.original[y1:y2, x1:x2].astype(np.float32)
        out += (src - out) * bg.opacity
    for layer in doc.layers[1:]:
        if not layer.visible or layer.opacity <= 0:
            continue
        if layer.kind == "text":
            if layer.uid != doc.editing_uid:
                doc._paint_text(out, (x1, y1, x2, y2), layer)
            continue
        if layer.kind == "redraw":
            src = doc.page.working[y1:y2, x1:x2].astype(np.float32)
            diff = np.abs(src.astype(np.int16) - doc.page.original[y1:y2, x1:x2].astype(np.int16)).max(axis=2) > 0
            alpha = (diff | (doc.page.paint_mask[y1:y2, x1:x2] > 0)).astype(np.float32)
        else:
            src = layer.rgba[y1:y2, x1:x2, :3].astype(np.float32)
            alpha = layer.rgba[y1:y2, x1:x2, 3].astype(np.float32) / 255
        out += (_blend(out, src, layer.blend) - out) * (alpha * layer.opacity)[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_offscreen_fonts()
        apply_dark(cls.app)
        scratch = Path(__file__).resolve().parents[1] / "build"
        scratch.mkdir(exist_ok=True)
        cls.temp = tempfile.TemporaryDirectory(dir=scratch)
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls.temp.name)

    @classmethod
    def tearDownClass(cls):
        cls.app.processEvents()
        cls.temp.cleanup()

    def setUp(self):
        rng = np.random.default_rng(7)
        image = rng.integers(0, 256, (480, 640, 3), dtype=np.uint8)
        path = str(Path(self.temp.name) / "page.png")
        cv2.imencode(".png", image)[1].tofile(path)
        self.page = Page(path)
        self.page.detected = True
        self.page.working[30:60, 40:90] = 255
        self.page.paint_mask[80:100, 40:90] = 255
        for box, text in (((80, 80, 250, 220), "Xin chào Việt Nam"),
                          ((340, 250, 560, 430), "Một trang truyện")):
            self.page.regions.append(Region(box, text=text, cleaned=True, manual_rect=box))
        self.doc = doc_for(self.page)
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.view.end_text_edit()
            self.window.deleteLater()
        self.app.processEvents()

    def editor(self):
        self.window = MainWindow()
        self.window._show_page(self.page)
        self.window.select_region(0)
        return self.window

    def assert_canvas(self):
        np.testing.assert_array_equal(image_array(self.window.view._pixmap.image), image_array(self.doc.render()))

    def test_compositor_matches_general_path(self):
        pixel = self.doc.add_pixel_layer()
        rng = np.random.default_rng(11)
        pixel.rgba[:] = rng.integers(0, 256, pixel.rgba.shape, dtype=np.uint8)
        pixel.opacity = 0.63
        for mode in BLENDS:
            pixel.blend = mode
            for bg_visible, bg_opacity, redraw_visible, redraw_opacity in (
                (True, 1, True, 1), (True, 1, False, 1), (True, 0.5, True, 0.4),
                (False, 1, True, 1), (False, 0, False, 0), (True, 1, True, 0.6),
            ):
                self.doc.layers[0].visible = bg_visible
                self.doc.layers[0].opacity = bg_opacity
                self.doc.layers[1].visible = redraw_visible
                self.doc.layers[1].opacity = redraw_opacity
                for redraw_blend in ("normal", "multiply"):
                    self.doc.layers[1].blend = redraw_blend
                    for box in (None, (17, 23, 333, 399)):
                        with self.subTest(mode=mode, bg=bg_visible, redraw=redraw_blend, box=box):
                            np.testing.assert_array_equal(self.doc.composite(box), reference_composite(self.doc, box))

    def test_render_bgr_channels_and_partial_stride(self):
        box = (3, 7, 240, 155)
        np.testing.assert_array_equal(image_array(self.doc.render_box(box)), self.doc.composite(box)[..., ::-1])

    def test_patch_owns_buffer_and_preserves_surrounding_pixels(self):
        source = QImage(640, 480, QImage.Format_RGB32)
        source.fill(QColor("red"))
        item = CanvasImageItem()
        item.set_image(source)
        patch_image = QImage(11, 13, QImage.Format_RGB32)
        patch_image.fill(QColor("blue"))
        for _ in range(10):
            item.update_patch(21, 23, patch_image)
        self.assertEqual(source.pixelColor(21, 23), QColor("red"))
        self.assertEqual(item.image.pixelColor(20, 23), QColor("red"))
        self.assertEqual(item.image.pixelColor(21, 23), QColor("blue"))
        self.assertEqual(item.image.pixelColor(32, 23), QColor("red"))

    def test_text_edits_erase_previous_ink_without_full_render(self):
        window = self.editor()
        with patch.object(self.doc, "render", side_effect=AssertionError("unexpected full render")):
            window.txt.setPlainText("Dòng mới có dấu: ư ơ ệ")
            window._nudge(110, -30)
            window._text_rect_dragged((180, 20, 440, 180))
            window._text_rotated(37)
        self.assert_canvas()
        window.txt.setPlainText("")
        self.assert_canvas()
        self.assertIsNone(self.doc.text_layer(self.page.regions[0]))
        window.undo()
        self.assert_canvas()
        window.redo()
        self.assert_canvas()

    def test_multi_style_shadow_and_geometry_damage(self):
        window = self.editor()
        window.select_all()
        with patch.object(self.doc, "render", side_effect=AssertionError("unexpected full render")):
            window.stroke_box.setValue(2)
            window.rotate_box.setValue(12)
        self.assertTrue(all(r.style.stroke_width == 2 and r.style.rotation == 12 for r in self.page.regions))
        self.assert_canvas()
        regions = self.page.regions
        before = self.doc.text_bounds(regions)
        with self.doc.step("Effects"):
            for region in regions:
                region.style.rotation = -28
                region.style.shadow = True
                region.style.shadow_distance = 30
                region.style.shadow_size = 12
                region.style.stroke_width = 3
        window._refresh_text(regions, before)
        self.assert_canvas()
        before = self.doc.text_bounds(regions)
        with self.doc.step("Move"):
            regions[0].manual_rect = (320, 10, 630, 250)
        window._refresh_text(regions, before)
        self.assert_canvas()

    def test_inline_typing_does_not_recompose_page(self):
        window = self.editor()
        with patch.object(self.doc, "render", side_effect=AssertionError("unexpected full render")):
            window._inline_edit()
            self.assertEqual(self.doc.editing_uid, self.page.regions[0].uid)
            with patch.object(self.doc, "render_box", side_effect=AssertionError("canvas changed while typing")):
                window._inline_text_changed("Gõ ngay trên ảnh")
            window.view.end_text_edit()
        self.assertEqual(self.page.regions[0].text, "Gõ ngay trên ảnh")
        self.assert_canvas()

    def test_layers_reuse_items_and_skip_pixel_thumbnails_on_text_edit(self):
        window = self.editor()
        item = window.layers_panel.list.item(0)
        with patch.object(self.doc, "thumbnail", side_effect=AssertionError("unnecessary thumbnail")):
            window.select_region(1)
            window.txt.setPlainText("Tên layer mới")
            self.assertIs(window.layers_panel.list.item(0), item)
            self.assertEqual(item.text(), "Tên layer mới")
        item.setText("T   tên giữ nguyên")
        self.assertEqual(self.doc.layers[-1].name, "T   tên giữ nguyên")

    def test_saved_state_remains_distinct_from_merged_typing(self):
        region = self.page.regions[0]
        with self.doc.step("Text", merge="text"):
            region.text = "A"
        self.doc.saved_index = self.doc.index
        with self.doc.step("Text", merge="text"):
            region.text = "B"
        self.assertTrue(self.doc.dirty)
        self.doc.undo()
        self.assertFalse(self.doc.dirty)
        self.assertEqual(self.page.regions[0].text, "A")

    def test_branch_at_saved_index_is_dirty(self):
        for value in ("A", "B"):
            with self.doc.step("Text"):
                self.page.regions[0].text = value
        self.doc.saved_index = self.doc.index
        self.doc.undo()
        with self.doc.step("Text"):
            self.page.regions[0].text = "C"
        self.assertTrue(self.doc.dirty)

    def test_project_roundtrip_preserves_render_and_text(self):
        path = str(Path(self.temp.name) / "roundtrip.lzp")
        project.save(path, [self.page])
        loaded = project.load(path)["pages"][0]
        np.testing.assert_array_equal(doc_for(loaded).composite(), self.doc.composite())
        self.assertEqual(loaded.regions[0].text, self.page.regions[0].text)

    def test_selection_animation_only_runs_with_selection(self):
        window = self.editor()
        self.assertFalse(window.view._ant_timer.isActive())
        self.doc.select_rect((10, 20, 100, 110))
        window._after_selection()
        self.assertTrue(window.view._ant_timer.isActive())
        window.deselect()
        self.assertFalse(window.view._ant_timer.isActive())

    def test_workspace_restores_tools_and_small_window_fits(self):
        window = self.editor()
        window.resize(1280, 720)
        window.show()
        self.app.processEvents()
        self.assertEqual(window.width(), 1280)
        window.d_layers.hide()
        window._reset_workspace()
        self.app.processEvents()
        self.assertTrue(window.d_layers.isVisible())
        self.assertTrue(window.findChild(QToolBar, "tools").isVisible())
        self.assertTrue(window.findChild(QToolBar, "toolOptions").isVisible())
        window.d_text.raise_()
        window.resizeDocks([window.d_text], [360], Qt.Horizontal)
        self.app.processEvents()
        viewport = window.d_text.widget().viewport()
        panel = window.text_panel
        for control in (panel.leading_box, panel.tracking_box, panel.hscale_box, panel.color_btn, panel.aa_box):
            right = control.mapTo(viewport, QPoint(control.width(), 0)).x()
            self.assertLessEqual(right, viewport.width(), control.toolTip())


if __name__ == "__main__":
    unittest.main()
