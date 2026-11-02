import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtGui import QGuiApplication

from app.photoshop import load_request, main, process_job
from app.core.detect import TextBox


class PhotoshopBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_request_validation_and_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            image = folder / "ảnh.png"
            image.write_bytes(b"image")
            job = folder / "request.json"
            value = {"protocol":1, "image":str(image), "lines":["Cảm ơn anh."], "engine":"opencv"}
            job.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8-sig")
            self.assertEqual(load_request(job), value)
            for change in ({"protocol":2}, {"engine":"cloud"}, {"lines":[]}, {"lines":[42]}):
                job.write_text(json.dumps({**value, **change}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_request(job)

    def test_backend_outputs_text_specs_transparent_redraw_and_editable_project(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            image = np.full((480, 500, 3), 210, np.uint8)
            cv2.ellipse(image, (350, 140), (90, 100), 0, 0, 360, (255, 255, 255), -1)
            cv2.ellipse(image, (350, 140), (90, 100), 0, 0, 360, (0, 0, 0), 2)
            cv2.rectangle(image, (330, 100), (370, 180), (0, 0, 0), -1)
            cv2.circle(image, (140, 390), 8, (0, 0, 0), 2)
            path = folder / "ảnh.png"
            cv2.imencode(".png", image)[1].tofile(path)
            mask = np.zeros((480, 500), np.float32)
            mask[100:181, 330:371] = 1
            mask[380:401, 130:151] = 1
            boxes = [TextBox(330, 100, 371, 181, 1), TextBox(130, 380, 151, 401, 1, "sfx")]
            model = folder / "fake-model"
            model.touch()
            job = folder / "request.json"
            job.write_text(json.dumps({"protocol":1, "image":str(path), "size":[500,480],
                                       "lines":["Cảm ơn anh."], "engine":"opencv"}), encoding="utf-8")
            with patch("app.photoshop.model_path", return_value=str(model)), \
                 patch("app.photoshop.files_for", return_value=["font.ttf"]), \
                 patch("app.photoshop.register_fonts"), \
                 patch("app.photoshop.TextDetector") as detector:
                detector.return_value.detect.return_value = boxes, mask
                result = process_job(job)
            self.assertTrue(result["ok"])
            self.assertEqual(result["size"], [500, 480])
            self.assertEqual(len(result["layers"]), 1)
            self.assertEqual(result["layers"][0]["source_box"], [330, 100, 371, 181])
            self.assertEqual(result["layers"][0]["contents"].replace("\r", " ").replace("\u0003", " "), "Cảm ơn anh.")
            redraw = cv2.imdecode(np.fromfile(result["redraw"], np.uint8), cv2.IMREAD_UNCHANGED)
            self.assertEqual(redraw.shape, (480, 500, 4))
            self.assertTrue(redraw[..., 3].any())
            self.assertFalse(redraw[370:410, 120:160, 3].any())
            self.assertTrue((folder / "preview.png").is_file())
            self.assertTrue((folder / "editable.lzp").is_file())

    def test_failure_is_reported_atomically_without_partial_result(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "request.json"
            with patch("app.photoshop.process_job", side_effect=ValueError("Lỗi thử nghiệm")):
                self.assertEqual(main(["--job", str(path)]), 1)
            data = json.loads((path.parent / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(data["error"], "Lỗi thử nghiệm")
            self.assertFalse(data["ok"])
            self.assertFalse((path.parent / "result.json.tmp").exists())


def _page_image():
    """Bóng thoại trắng có chữ + chữ trên nền tranh (lưới chấm) + SFX."""
    image = np.full((480, 500, 3), 255, np.uint8)
    image[:, :250] = np.where((np.indices((480, 250)).sum(axis=0) % 6) < 3, 90, 200)[..., None]  # tranh lưới chấm
    cv2.ellipse(image, (380, 140), (90, 100), 0, 0, 360, (255, 255, 255), -1)
    cv2.ellipse(image, (380, 140), (90, 100), 0, 0, 360, (0, 0, 0), 2)
    cv2.rectangle(image, (360, 100), (400, 180), (0, 0, 0), -1)  # chữ trong bóng
    cv2.rectangle(image, (100, 100), (140, 180), (0, 0, 0), -1)  # chữ đè lên tranh
    cv2.rectangle(image, (330, 330), (450, 400), (0, 0, 0), -1)  # SFX
    return image


def _fake_detection(boxes_kinds):
    mask = np.zeros((480, 500), np.float32)
    boxes = []
    for (x1, y1, x2, y2), kind in boxes_kinds:
        mask[y1:y2, x1:x2] = 1
        boxes.append(TextBox(x1, y1, x2, y2, 1, kind))
    return boxes, mask


BOXES = [((360, 100, 401, 181), "text"), ((100, 100, 141, 181), "text"), ((330, 330, 451, 401), "sfx")]


class PhotoshopModesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def _run(self, folder, request):
        job = folder / "request.json"
        job.write_text(json.dumps({"protocol": 1, **request}, ensure_ascii=False), encoding="utf-8")
        model = folder / "fake-model"
        model.touch()
        with patch("app.photoshop.model_path", return_value=str(model)), \
             patch("app.photoshop.files_for", return_value=["font.ttf"]), \
             patch("app.photoshop.register_fonts"), \
             patch("app.photoshop.assign_lines", lambda page, regions, lines: [(r, [s]) for r, s in zip(regions, lines)]), \
             patch("app.photoshop.TextDetector") as detector:
            # Ghép câu cố định mỗi khung một câu: các test này kiểm luật xoá chữ / chế độ, không kiểm bộ ghép câu.
            detector.return_value.detect.return_value = _fake_detection(BOXES)
            return process_job(job)

    def test_only_easy_parts_are_cleaned_sfx_and_art_keep_the_original(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            path = folder / "013.png"
            cv2.imencode(".png", _page_image())[1].tofile(path)
            result = self._run(folder, {"image": str(path), "size": [500, 480],
                                        "lines": ["Cảm ơn anh nhiều.", "Không có gì mà.", "Ôm chặt"]})
            by_box = {tuple(spec["source_box"]): spec for spec in result["layers"]}
            self.assertEqual(set(by_box), {box for box, _ in BOXES})
            self.assertFalse(by_box[(360, 100, 401, 181)]["redraw"])  # bóng thoại: xoá được
            self.assertTrue(by_box[(100, 100, 141, 181)]["redraw"])  # chữ trên tranh: chữ đè lên bản gốc
            self.assertTrue(by_box[(330, 330, 451, 401)]["redraw"])  # SFX: chữ đè lên bản gốc
            self.assertEqual(result["needs_redraw"], 2)
            redraw = cv2.imdecode(np.fromfile(result["redraw"], np.uint8), cv2.IMREAD_UNCHANGED)
            self.assertTrue(redraw[110:170, 365:395, 3].all())  # bóng thoại: đã xoá
            self.assertFalse(redraw[100:181, 100:141, 3].any())  # chữ trên tranh: giữ nguyên
            self.assertFalse(redraw[330:401, 330:451, 3].any())  # SFX: giữ nguyên

    def test_batch_uses_page_numbers_and_reports_missing_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            pages = []
            for name in ("001.png", "002.png", "005.png"):
                path = folder / name
                cv2.imencode(".png", _page_image())[1].tofile(path)
                pages.append({"image": str(path), "size": [500, 480], "doc_name": name.replace(".png", ".jpg"), "doc_id": len(pages)})
            text = "1\nCảm ơn anh nhiều.\nKhông có gì mà.\n2\nTrở nên trân trọng.\nKhông có gì mà.\nÔm chặt\n"
            result = self._run(folder, {"mode": "batch", "pages": pages, "translation_text": text})
            self.assertEqual(result["mode"], "batch")
            ok = [p for p in result["pages"] if p["ok"]]
            self.assertEqual([p["doc_name"] for p in ok], ["001.jpg", "002.jpg"])
            self.assertEqual(len(ok[1]["layers"]), 3)
            self.assertIn("5", result["pages"][2]["error"])
            self.assertTrue(Path(ok[0]["redraw"]).is_file() and Path(ok[1]["redraw"]).is_file())
            self.assertNotEqual(ok[0]["redraw"], ok[1]["redraw"])

    def test_sfx_redraw_keeps_only_the_changed_area_even_when_shifted_and_noisy(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            base = _page_image()
            redrawn = base.copy()
            redrawn[330:401, 330:451] = 255  # SFX đã xoá
            redrawn = np.roll(redrawn, 2, axis=1)  # AI làm lệch 2px
            ok, jpg = cv2.imencode(".jpg", redrawn, [cv2.IMWRITE_JPEG_QUALITY, 85])  # nhiễu nén JPG
            (folder / "redrawn.jpg").write_bytes(jpg.tobytes())
            cv2.imencode(".png", base)[1].tofile(folder / "base.png")
            result = self._run(folder, {"mode": "sfx", "image": str(folder / "base.png"),
                                        "redrawn": str(folder / "redrawn.jpg"), "doc_name": "013.jpg"})
            layer = cv2.imdecode(np.fromfile(result["layer"], np.uint8), cv2.IMREAD_UNCHANGED)
            self.assertEqual(layer.shape, (480, 500, 4))
            self.assertTrue(layer[340:390, 340:440, 3].min() > 200)  # chỗ SFX: lấy ảnh redraw
            outside = layer[..., 3].copy()
            outside[315:416, 315:466] = 0
            self.assertLess((outside > 0).mean(), 0.002)  # gần như không lấy gì ngoài SFX
            self.assertTrue(layer[360, 380, :3].min() > 240)  # đúng màu đã xoá (trắng)
