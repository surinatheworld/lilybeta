"""Offline regression checks for compositing, incremental drawing and edit history."""
from __future__ import annotations

import os
import gc
from pathlib import Path
import tempfile
import threading
import time
import unittest
import weakref
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QCloseEvent, QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolBar, QMessageBox

from app.core import project
from app.core.chapter import Chapter
from app.core.document import BLENDS, Document, _blend, _checker, doc_for
from app.core.page import Page, Region
from app.core.typeset import TextStyle
from app.core.typeset_profiles import FONTS, GAI_BANG, ISSHIKI, ISSHIKI_FONTS, ISSHIKI_KINDS
from app.ui.image_view import CanvasImageItem
from app.ui.main_window import DetectWorker, MainWindow
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
        QSettings("Lilyzono", "Lilyzono").setValue("typeset/profile", "")
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

    def other_page(self, name="other.png", detected=True):
        path = str(Path(self.temp.name) / name)
        cv2.imencode(".png", self.page.original)[1].tofile(path)
        page = Page(path)
        page.detected = detected
        page.regions = [Region((100, 100, 300, 250), text="Trang thứ hai", cleaned=True)]
        return page

    def drain_jobs(self):
        deadline = time.monotonic() + 10
        while self.window.batch is not None or self.window.worker is not None or self.window._detect_queue:
            self.assertLess(time.monotonic(), deadline, "background job timed out")
            QTest.qWait(20)

    def test_open_multiple_files_preserves_unsaved_document_and_deduplicates(self):
        window = self.editor()
        window.txt.setPlainText("Chưa lưu ở ảnh 1")
        original = window._active_document
        other = self.other_page()
        with patch.object(window, "_confirm_discard", side_effect=AssertionError("opening must not discard")), \
             patch.object(window, "run_detect"):
            window.load_image(other.path)
            window.load_image(other.path)
        self.assertEqual(window.document_tab.count(), 2)
        self.assertIsNot(window.page, self.page)
        window._activate_document(original)
        self.assertEqual(window.txt.toPlainText(), "Chưa lưu ở ảnh 1")
        self.assertTrue(self.doc.dirty)
        self.assert_canvas()

    def test_tabs_restore_zoom_selection_history_and_translation_context(self):
        window = self.editor()
        window.show()
        self.app.processEvents()
        first = window._active_document
        window.select_region(1)
        window.view.zoom_to(2)
        window.txt.setPlainText("Chỉ đổi ảnh 1")
        window.lines = ["Bản dịch riêng ảnh 1"]
        window._doc_flat = list(window.lines)
        self.doc.select_rect((20, 30, 70, 90))
        window._show_page(self.other_page())
        second = window._active_document
        window.txt.setPlainText("Chỉ đổi ảnh 2")
        window.view.zoom_to(0.5)
        window._activate_document(first)
        self.assertEqual(window.selected, 1)
        self.assertEqual(window.view.zoom, 2)
        self.assertEqual(window.lines, ["Bản dịch riêng ảnh 1"])
        self.assertEqual(window.txt.toPlainText(), "Chỉ đổi ảnh 1")
        self.assertIsNotNone(window.doc.selection)
        window.undo()
        self.assertEqual(window.page.regions[1].text, "Một trang truyện")
        window._activate_document(second)
        self.assertEqual(window.txt.toPlainText(), "Chỉ đổi ảnh 2")
        self.assertEqual(window.view.zoom, 0.5)
        self.assertIsNone(window.doc.selection)

    def test_unprocessed_region_keeps_typed_translation_when_switching_tabs(self):
        window = self.editor()
        first = window._active_document
        self.page.regions[0].cleaned = False
        window.select_region(0)
        window.txt.setPlainText("Bản dịch chưa xoá nền")
        window._show_page(self.other_page())
        window._activate_document(first)
        self.assertEqual(window.txt.toPlainText(), "Bản dịch chưa xoá nền")
        self.assertFalse(window.region.cleaned)
        self.assertTrue(window.doc.dirty)
        self.assertFalse(window.save_document(wait=True))
        self.assertIn("chưa xử lý", window.statusBar().currentMessage())

    def test_close_tab_cancel_save_cancel_discard_and_welcome(self):
        window = self.editor()
        window.txt.setPlainText("Chưa lưu")
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.Cancel):
            window.close_document(0)
        self.assertEqual(window.document_tab.count(), 1)
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.Save), \
             patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=("", "")):
            window.close_document(0)
        self.assertEqual(window.document_tab.count(), 1)
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.Discard):
            window.close_document(0)
        self.assertEqual(window.document_tab.count(), 0)
        self.assertIsNone(window.page)
        self.assertEqual(window.canvas_stack.currentIndex(), 0)
        window._show_page(self.other_page())
        self.assertEqual(window.document_tab.count(), 1)

    def test_closing_app_checks_every_dirty_tab_and_honors_cancel(self):
        window = self.editor()
        window.txt.setPlainText("Chưa lưu 1")
        window._show_page(self.other_page())
        window.txt.setPlainText("Chưa lưu 2")
        event = QCloseEvent()
        with patch("app.ui.main_window.QMessageBox.question", side_effect=[QMessageBox.Discard, QMessageBox.Cancel]) as question:
            window.closeEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertEqual(question.call_count, 2)
        self.assertEqual(window.document_tab.count(), 2)

    def test_psd_default_save_reuses_path_and_save_as_keeps_other_tab_independent(self):
        window = self.editor()
        first = window._active_document
        path = str(Path(self.temp.name) / "default-save.psd")
        window.txt.setPlainText("Đã lưu PSD")
        with patch("app.ui.main_window.photoshop_available", return_value=False), \
             patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(path, "Photoshop (*.psd)")) as dialog:
            self.assertTrue(window.save_document(wait=True))
            self.assertEqual(dialog.call_args.args[3], "Photoshop (*.psd)")
        self.assertEqual(Path(path).read_bytes()[:4], b"8BPS")
        self.assertFalse(window.doc.dirty)
        self.assertEqual(first.psd_path, path)
        window.txt.setPlainText("Lưu lại PSD")
        with patch("app.ui.main_window.photoshop_available", return_value=False), \
             patch("app.ui.main_window.QFileDialog.getSaveFileName", side_effect=AssertionError("Save must reuse path")):
            self.assertTrue(window.save_document(wait=True))
        window._show_page(self.other_page())
        self.assertIsNone(window._active_document.psd_path)
        window.document_tab.moveTab(0, 1)
        window._activate_document(first)
        self.assertIn("default-save.psd", window.document_tab.tabText(window.document_tab.currentIndex()))
        copied = str(Path(self.temp.name) / "copy-save.psd")
        with patch("app.ui.main_window.photoshop_available", return_value=False), \
             patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(copied, "")):
            self.assertTrue(window.save_document(ask=True, wait=True))
        self.assertTrue(Path(copied).exists())
        self.assertEqual(first.psd_path, copied)

    def test_failed_psd_save_keeps_original_file_and_unsaved_state(self):
        window = self.editor()
        path = Path(self.temp.name) / "failed-save.psd"
        path.write_bytes(b"previous PSD")
        window._active_document.psd_path = str(path)
        window.txt.setPlainText("Chưa lưu do lỗi ghi")

        def broken_export(page, destination):
            Path(destination).write_bytes(b"partial PSD")
            raise OSError("disk error")

        with patch("app.ui.main_window.photoshop_available", return_value=False), \
             patch("app.ui.main_window.export_without_photoshop", side_effect=broken_export), \
             patch("app.ui.main_window.QMessageBox.warning"):
            self.assertFalse(window.save_document(wait=True))
        self.assertEqual(path.read_bytes(), b"previous PSD")
        self.assertTrue(window.doc.dirty)
        self.assertIn("disk error", window.statusBar().currentMessage())

    def test_detection_queue_completes_on_its_own_tab(self):
        window = self.editor()
        first, second = self.other_page("detect1.png", False), self.other_page("detect2.png", False)
        gate = threading.Event()
        processed = []

        def fake_detect(worker):
            if worker.page is first:
                gate.wait(5)
            worker.page.detected = True
            processed.append(worker.page)
            worker.finished_ok.emit()

        with patch.object(DetectWorker, "run", fake_detect):
            window._show_page(first)
            window._show_page(second)
            self.assertEqual(window.document_tab.count(), 3)
            self.assertEqual(len(window._detect_queue), 1)
            gate.set()
            self.drain_jobs()
        self.assertEqual(processed, [first, second])
        self.assertIs(window.page, second)
        self.assertTrue(window.view.isEnabled())

    def test_file_dialog_opens_every_selected_file(self):
        window = self.editor()
        second = self.other_page("dialog2.png")
        third = self.other_page("dialog3.png")
        with patch("app.ui.main_window.QFileDialog.getOpenFileNames", return_value=([second.path, third.path], "")), \
             patch.object(window, "run_detect"):
            window.open_image()
        self.assertEqual(window.document_tab.count(), 3)
        self.assertEqual(window.page.path, third.path)

    def test_project_and_psd_open_without_replacing_other_tabs(self):
        window = self.editor()
        window.txt.setPlainText("Ảnh đang sửa")
        first = window._active_document
        other = self.other_page("project2.png")
        project_path = str(Path(self.temp.name) / "tabs.lzp")
        project.save(project_path, [other])
        window.open_project(project_path)
        second = window._active_document
        psd_page = self.other_page("psd-background.png")
        psd_path = str(Path(self.temp.name) / "opened.psd")
        with patch("app.core.psd_import.open_psd", return_value=(psd_page, [])):
            window.load_image(psd_path)
        self.assertEqual(window.document_tab.count(), 3)
        self.assertEqual(window._active_document.psd_path, psd_path)
        self.assertEqual(second.project_path, project_path)
        window._activate_document(first)
        self.assertIsNone(window.project_path)
        self.assertEqual(window.txt.toPlainText(), "Ảnh đang sửa")
        self.assertTrue(window.doc.dirty)

    def test_close_save_waits_for_success_before_removing_tab(self):
        window = self.editor()
        window.txt.setPlainText("Lưu trước khi đóng")
        path = str(Path(self.temp.name) / "close-save.psd")
        with patch("app.ui.main_window.QMessageBox.question", return_value=QMessageBox.Save), \
             patch("app.ui.main_window.photoshop_available", return_value=False), \
             patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(path, "")):
            window.close_document(0)
        self.assertEqual(window.document_tab.count(), 0)
        self.assertEqual(Path(path).read_bytes()[:4], b"8BPS")

    def test_closed_saved_tab_is_released_after_worker_finishes(self):
        window = self.editor()
        reference = weakref.ref(window._active_document)
        path = str(Path(self.temp.name) / "released-tab.psd")
        with patch("app.ui.main_window.photoshop_available", return_value=False), \
             patch("app.ui.main_window.QFileDialog.getSaveFileName", return_value=(path, "")):
            self.assertTrue(window.save_document(wait=True))
        window.close_document(0)
        QTest.qWait(30)
        gc.collect()
        self.assertIsNone(reference())

    def test_chapter_navigation_uses_tabs_and_preserves_other_workspaces(self):
        window = self.editor()
        folder = Path(self.temp.name) / "chapter"
        folder.mkdir(exist_ok=True)
        chapter = Chapter.__new__(Chapter)
        chapter.folder = str(folder)
        first, second = self.other_page("chapter1.png"), self.other_page("chapter2.png")
        chapter.paths = [first.path, second.path]
        chapter.pages = {first.path: first, second.path: second}
        chapter.translations, chapter.filled, chapter.redrawn = {}, set(), {}
        window.load_chapter(chapter.folder, chapter)
        window._page_row_changed(1)
        self.assertEqual(window.document_tab.count(), 3)
        self.assertIs(window.page, second)
        window._page_row_changed(0)
        self.assertEqual(window.document_tab.count(), 3)
        self.assertIs(window.page, first)
        window._activate_document(window.documents[0])
        self.assertIsNone(window.chapter)

    def enable_series_profile(self):
        self.page.regions = [Region((40 + i * 145, 100, 170 + i * 145, 230), text="Bản dịch",
                                    cleaned=True, typeset_kind=kind) for i, kind in enumerate(FONTS)]
        self.doc.sync()
        window = self.editor()
        window.typeset_mode.setCurrentIndex(window.typeset_mode.findData(GAI_BANG))
        return window

    def test_isshiki_mode_offers_its_own_kinds_and_fonts(self):
        self.page.regions = [Region((40 + i * 100, 100, 130 + i * 100, 230), text="Bản dịch",
                                    cleaned=True, typeset_kind=kind) for i, kind in enumerate(ISSHIKI_FONTS)]
        self.doc.sync()
        window = self.editor()
        window.typeset_mode.setCurrentIndex(window.typeset_mode.findData(ISSHIKI))
        for i, (kind, family) in enumerate(ISSHIKI_FONTS.items()):
            window.select_region(i)
            self.assertEqual([window.typeset_kind.itemData(k) for k in range(window.typeset_kind.count())],
                             list(ISSHIKI_KINDS))
            self.assertEqual(window.typeset_kind.currentData(), kind)
            self.assertEqual(window.region.style.family, family)
            self.assertEqual(window.region.style.italic, kind in ("thought", "square"))
        window.typeset_mode.setCurrentIndex(window.typeset_mode.findData(GAI_BANG))
        self.assertNotIn("bold", [window.typeset_kind.itemData(k) for k in range(window.typeset_kind.count())])

    def test_gai_bang_applies_all_font_rules_and_preserves_them_during_typing_and_batch(self):
        window = self.enable_series_profile()
        for i, (kind, family) in enumerate(FONTS.items()):
            window.select_region(i)
            self.assertEqual(window.current_style().family, family)
            window.txt.setPlainText(f"Gõ thêm {i}")
            self.assertEqual(window.region.style.family, family)
            self.assertEqual(window.region.style.italic, kind == "thought")
        window.select_all()
        window.process_selected()
        for region, (kind, family) in zip(self.page.regions, FONTS.items()):
            self.assertEqual(region.style.family, family)
            self.assertEqual(region.style.italic, kind == "thought")

    def test_profile_and_balloon_override_support_undo_and_project_roundtrip(self):
        window = self.enable_series_profile()
        window.select_region(0)
        window.typeset_kind.setCurrentIndex(window.typeset_kind.findData("thought"))
        self.assertEqual(window.region.style.family, FONTS["thought"])
        window.undo()
        self.assertEqual(window.region.typeset_kind, "normal")
        self.assertEqual(window.region.style.family, FONTS["normal"])
        window.redo()
        self.assertTrue(window.region.style.italic)
        path = str(Path(self.temp.name) / "gai-bang.lzp")
        project.save(path, [self.page])
        loaded = project.load(path)["pages"][0]
        self.assertEqual(loaded.typeset_profile, GAI_BANG)
        self.assertEqual(loaded.regions[0].typeset_kind, "thought")
        self.assertEqual(loaded.regions[0].style.family, FONTS["thought"])
        window.typeset_mode.setCurrentIndex(0)
        self.assertEqual(self.page.typeset_profile, "")
        window.undo()
        self.assertEqual(self.page.typeset_profile, GAI_BANG)
        self.assertEqual(window.typeset_mode.currentData(), GAI_BANG)

    def test_gai_bang_inline_text_uses_the_thought_font_and_italic(self):
        window = self.enable_series_profile()
        window.select_region(1)
        window._inline_edit()
        window._inline_text_changed("Đang suy nghĩ")
        window.view.end_text_edit()
        self.assertEqual(window.region.style.family, FONTS["thought"])
        self.assertTrue(window.region.style.italic)
        self.assert_canvas()

    def test_auto_fill_honors_the_profile_and_mode_is_independent_per_tab(self):
        from app.core.assign import apply_plan

        window = self.enable_series_profile()
        first = window._active_document
        apply_plan(self.page, [(region, ["Tự điền"]) for region in self.page.regions], TextStyle(family="Arial"))
        self.assertEqual([r.style.family for r in self.page.regions], list(FONTS.values()))
        window._show_page(self.other_page())
        self.assertEqual(window.typeset_mode.currentData(), "")
        window._activate_document(first)
        self.assertEqual(window.typeset_mode.currentData(), GAI_BANG)

    def test_chapter_processing_applies_each_region_font_on_every_page(self):
        import copy

        window = self.enable_series_profile()
        first = self.other_page("series-ch1.png")
        first.regions = copy.deepcopy(self.page.regions)
        first.typeset_profile = GAI_BANG
        second = self.other_page("series-ch2.png")
        second.regions[0].kind = "sfx"
        chapter = Chapter.__new__(Chapter)
        chapter.folder, chapter.paths = self.temp.name, [first.path, second.path]
        chapter.pages = {first.path: first, second.path: second}
        chapter.translations, chapter.filled, chapter.redrawn, chapter.typeset_profile = {}, set(), {}, GAI_BANG
        window.load_chapter(chapter.folder, chapter)
        window.detector = object()  # both fixture pages have already been detected
        window.process_chapter()
        self.drain_jobs()
        self.assertEqual([r.style.family for r in first.regions], list(FONTS.values()))
        self.assertEqual(second.regions[0].style.family, FONTS["sfx"])
        self.assertEqual(second.typeset_profile, GAI_BANG)


if __name__ == "__main__":
    unittest.main()
