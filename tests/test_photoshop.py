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
