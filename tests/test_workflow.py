import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtGui import QGuiApplication

from app.core import ai, workflow
from app.core.workflow import Project
try:
    from tests.test_photoshop import BOXES, _fake_detection, _page_image
except ImportError:  # chạy bằng "unittest discover tests"
    from test_photoshop import BOXES, _fake_detection, _page_image


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _reply(data):
    return _Response(json.dumps(data).encode())


class AIClientTests(unittest.TestCase):
    """Mỗi nhà cung cấp: gửi đúng dạng đầu ra có cấu trúc, đọc đúng câu trả lời, báo lỗi rõ ràng."""

    def _ask(self, provider, reply):
        sent = {}

        def fake(req, timeout):
            sent["url"], sent["headers"], sent["body"] = req.full_url, dict(req.header_items()), json.loads(req.data)
            return _reply(reply)

        with patch("app.core.ai.secrets.get_key", return_value="k"), patch("urllib.request.urlopen", fake):
            out = ai.ask_json(ai.AIConfig(provider=provider), "detect", "sys", "hello", [b"jpeg"], ai.MATCH_SCHEMA, "assign_lines")
        return out, sent

    def test_gemini_uses_json_schema_and_reads_text(self):
        out, sent = self._ask("gemini", {"candidates": [{"content": {"parts": [{"text": '{"assignments": []}'}]}}]})
        self.assertEqual(out, {"assignments": []})
        self.assertIn(":generateContent", sent["url"])
        self.assertEqual(sent["body"]["generationConfig"]["responseJsonSchema"], ai.MATCH_SCHEMA)
        self.assertEqual(sent["headers"]["X-goog-api-key"], "k")

    def test_openai_uses_strict_json_schema(self):
        out, sent = self._ask("openai", {"choices": [{"message": {"content": '{"assignments": []}'}, "finish_reason": "stop"}]})
        self.assertEqual(out, {"assignments": []})
        fmt = sent["body"]["response_format"]
        self.assertEqual((fmt["type"], fmt["json_schema"]["strict"]), ("json_schema", True))
        self.assertEqual(sent["body"]["messages"][1]["content"][1]["type"], "image_url")

    def test_anthropic_forces_one_tool_with_the_schema(self):
        out, sent = self._ask("anthropic", {"content": [{"type": "tool_use", "input": {"assignments": []}}],
                                            "stop_reason": "tool_use"})
        self.assertEqual(out, {"assignments": []})
        self.assertEqual(sent["body"]["tool_choice"], {"type": "tool", "name": "assign_lines"})
        self.assertEqual(sent["body"]["tools"][0]["input_schema"], ai.MATCH_SCHEMA)

    def test_errors_are_explained(self):
        def denied(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 401, "x", {}, io.BytesIO(b'{"error": {"message": "bad key"}}'))

        with patch("app.core.ai.secrets.get_key", return_value="k"), patch("urllib.request.urlopen", denied):
            with self.assertRaisesRegex(ai.AIError, "401.*API key sai(.|\n)*bad key"):
                ai.ask_json(ai.AIConfig(provider="openai"), "detect", "s", "p", [], ai.MATCH_SCHEMA, "x")
        with patch("app.core.ai.secrets.get_key", return_value=""), patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            with self.assertRaisesRegex(ai.AIError, "Chưa có API key"):
                ai.ask_json(ai.AIConfig(provider="gemini"), "detect", "s", "p", [], ai.MATCH_SCHEMA, "x")
        with patch("app.core.ai.secrets.get_key", return_value="k"), \
             patch("urllib.request.urlopen", lambda req, timeout: _reply({"choices": [{"message": {"content": "xin chào"}}]})):
            with self.assertRaisesRegex(ai.AIError, "không đúng JSON"):
                ai.ask_json(ai.AIConfig(provider="openai"), "detect", "s", "p", [], ai.MATCH_SCHEMA, "x")

    def test_free_gemini_moves_to_the_next_model_when_one_is_out_of_quota(self):
        tried = []

        def fake(req, timeout):
            model = req.full_url.split("/models/")[1].split(":")[0]
            tried.append(model)
            if len(tried) == 1:
                raise urllib.error.HTTPError(req.full_url, 429, "x", {}, io.BytesIO(b'{"error": {"message": "slow down"}}'))
            return _reply({"candidates": [{"content": {"parts": [{"text": '{"assignments": []}'}]}}]})

        with patch("app.core.ai.secrets.get_key", return_value="k"), patch("urllib.request.urlopen", fake):
            out = ai.ask_json(ai.AIConfig(provider="gemini"), "detect", "s", "p", [], ai.MATCH_SCHEMA, "x")
        self.assertEqual(out, {"assignments": []})
        self.assertEqual(tried, list(ai.FREE_MODELS[:2]))  # không chờ thử lại model đang hết hạn mức

        def always_busy(req, timeout):
            raise urllib.error.HTTPError(req.full_url, 503, "x", {}, io.BytesIO(b'{"error": {"message": "busy"}}'))

        with patch("app.core.ai.secrets.get_key", return_value="k"), patch("urllib.request.urlopen", always_busy):
            with self.assertRaisesRegex(ai.AIError, "Mọi model Gemini miễn phí"):
                ai.ask_json(ai.AIConfig(provider="gemini"), "detect", "s", "p", [], ai.MATCH_SCHEMA, "x")

    def test_answers_are_checked_against_region_ids(self):
        out = ai.parse_assignments({"assignments": [
            {"line": 1, "region": "R01", "confidence": .9, "note": ""},
            {"line": 1, "region": "R02", "confidence": .9, "note": ""},  # trùng câu: giữ lần đầu
            {"line": 2, "region": "R09", "confidence": .9, "note": ""},  # vùng không có
            {"line": 7, "region": "R01", "confidence": .9, "note": ""}]}, 3, ["R01", "R02"])
        self.assertEqual(out["assign"], {0: ("R01", .9, "")})
        self.assertEqual(out["unplaced"], [1, 2])
        self.assertTrue(any("R09" in w for w in out["warnings"]) and any("3" in w for w in out["warnings"]))
        with self.assertRaises(ai.AIError):
            ai.parse_assignments({"nothing": 1}, 3, ["R01"])
        cls = ai.parse_classification({"regions": [{"id": "R01", "bubble": "cloud", "order": 2, "confidence": .5,
                                                    "note": "?"}],
                                       "missed": [{"box": [0, 0, 500, 500], "bubble": "square", "note": ""}]},
                                      ["R01", "R02"], (200, 100))
        self.assertEqual(cls["regions"]["R01"]["bubble"], "cloud")
        self.assertEqual(cls["missed"][0]["box"], [0, 0, 100, 50])
        self.assertTrue(any("R02" in w for w in cls["warnings"]))


def _classify_reply(bubbles):
    def fake(cfg, step, system, prompt, images, schema, name):
        assert step == "detect" and images and schema is ai.CLASSIFY_SCHEMA
        return {"regions": [{"id": rid, "bubble": b, "order": i + 1, "confidence": .95, "note": ""}
                            for i, (rid, b) in enumerate(bubbles.items())], "missed": []}
    return fake


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.project = Project(self.folder / "proj")
        self.project.sync_documents([{"id": 1, "name": "013.jpg"}])
        self.page = self.project.page("013.jpg")
        workflow.imwrite(self.project.file(self.page, "work.png"), _page_image())
        self.detector = type("D", (), {"detect": lambda self, img: _fake_detection(BOXES)})()

    def tearDown(self):
        self.tmp.cleanup()

    def _detect(self, bubbles):
        with patch("app.core.ai.ask_json", _classify_reply(bubbles)):
            return workflow.detect(self.project, self.page, self.detector, ai.AIConfig())

    def test_full_flow_keeps_ids_and_manual_edits(self):
        p, page = self.project, self.page
        workflow.copy_image(p.file(page, "work.png"), p.file(page, "raw.png"))
        self._detect({"R01": "round", "R02": "free", "R03": "sfx"})
        self.assertEqual([r["id"] for r in page["regions"]], ["R01", "R02", "R03"])
        self.assertEqual([r["bubble"] for r in page["regions"]], ["round", "free", "sfx"])
        self.assertEqual(page["regions"][0]["bubble_src"], "ai")
        # Sửa tay loại bóng, nhận diện lại: ID và phần sửa tay giữ nguyên.
        p.region(page, "R02")["bubble"], p.region(page, "R02")["bubble_src"] = "square", "manual"
        self._detect({"R01": "cloud", "R02": "free", "R03": "sfx"})
        self.assertEqual([r["id"] for r in page["regions"]], ["R01", "R02", "R03"])
        self.assertEqual((p.region(page, "R01")["bubble"], p.region(page, "R02")["bubble"]), ("cloud", "square"))
        p.region(page, "R01")["bubble"] = "round"
        p.region(page, "R02")["bubble"] = "free"

        layer, warnings = workflow.clean(p, page)  # chỉ bóng kín (R01) được xoá chữ
        alpha = cv2.imdecode(np.fromfile(layer, np.uint8), cv2.IMREAD_UNCHANGED)[..., 3]
        self.assertTrue(alpha[110:170, 365:395].all())
        self.assertFalse(alpha[100:181, 100:141].any() or alpha[330:401, 330:451].any())
        self.assertEqual([r["cleaned"] for r in page["regions"]], [True, False, False])

        tl = self.folder / "tl.txt"
        tl.write_text("13\nCảm ơn anh nhiều.\nKhông có gì mà.\nÔm chặt\nBÙM\n", encoding="utf-8")
        p.data["translation_file"] = str(tl)
        p.region(page, "R03")["lines"], p.region(page, "R03")["lines_src"] = ["BÙM"], "manual"
        sent = {}

        def fake_match(cfg, step, system, prompt, images, schema, name):
            sent["prompt"] = prompt
            return {"assignments": [{"line": 1, "region": "R01", "confidence": .9, "note": ""},
                                    {"line": 2, "region": "R01", "confidence": .5, "note": "có thể là R02"},
                                    {"line": 3, "region": "", "confidence": .2, "note": ""}]}

        with patch("app.core.ai.ask_json", fake_match):
            warnings = workflow.match(p, page, ai.AIConfig())
        self.assertNotIn("BÙM", sent["prompt"])  # câu đã gán tay không gửi lại cho AI
        self.assertNotIn("R03", sent["prompt"])
        r1 = p.region(page, "R01")
        self.assertEqual(r1["lines"], ["Cảm ơn anh nhiều.", "Không có gì mà."])
        self.assertTrue(r1["uncertain"])
        self.assertEqual(p.region(page, "R03")["lines"], ["BÙM"])
        self.assertEqual([u["text"] for u in page["unplaced"]], ["Ôm chặt"])
        self.assertTrue(warnings)

        p.region(page, "R03")["size"] = 30
        # Placement and manual text edits alone do not authorize typesetting.
        with self.assertRaisesRegex(ValueError, "Dừng trước typeset"):
            workflow.typeset(p, page)
        self.assertFalse(p.has(page, "typeset.json"))
        # Explicitly choose the three lines for this layout fixture and review them.
        from app.core.semantic_review import confirm_region
        page.update(lines_manual=True, lines=["Cảm ơn anh nhiều.", "Không có gì mà.", "BÙM"], unplaced=[])
        for r in (r1, p.region(page, "R03")):
            confirm_region(p, page, r)
        path, warnings = workflow.typeset(p, page)
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        ids = [layer["id"] for layer in data["layers"]]
        self.assertEqual(sorted(set(ids)), ["R01", "R03"])
        sfx = next(layer for layer in data["layers"] if layer["id"] == "R03")
        self.assertTrue(sfx["redraw"] and sfx["name"].startswith("R03 · "))
        self.assertEqual(data["redraw"], "")
        self.assertTrue(p.has(page, "preview.png"))

    def test_ai_failure_keeps_detected_regions_and_reports(self):
        workflow.copy_image(self.project.file(self.page, "work.png"), self.project.file(self.page, "raw.png"))
        with patch("app.core.ai.ask_json", side_effect=ai.AIError("Gemini báo lỗi 429")):
            with self.assertRaisesRegex(ai.AIError, "429"):
                workflow.detect(self.project, self.page, self.detector, ai.AIConfig())
        saved = json.loads(self.project.path.read_text(encoding="utf-8"))["pages"][0]["regions"]
        self.assertEqual(len(saved), 3)
        self.assertEqual(saved[0]["bubble_src"], "auto")

    def test_redraw_marks_unerased_text_and_finds_missed_erased_text(self):
        p, page = self.project, self.page
        p.data["source"] = "redraw"
        raw = _page_image()
        cv2.rectangle(raw, (300, 20), (480, 50), (0, 0, 0), -1)  # băng tên chương: model nhận diện bỏ sót
        redraw = raw.copy()
        redraw[95:186, 355:406] = 255  # bản redraw xoá chữ trong bóng…
        redraw[15:56, 295:486] = 255  # …và băng tên chương; chữ đè tranh, SFX giữ nguyên
        workflow.imwrite(p.file(page, "raw.png"), raw)
        workflow.imwrite(p.file(page, "work.png"), redraw)
        warnings = self._detect({"R01": "round", "R02": "free", "R03": "sfx", "R04": "square"})
        regions = {r["id"]: r for r in page["regions"]}
        self.assertTrue(regions["R01"]["erased"])
        self.assertFalse(regions["R02"]["erased"])
        diff = [r for r in page["regions"] if r["src"] == "diff"]
        self.assertEqual(len(diff), 1)
        x1, y1, x2, y2 = diff[0]["box"]
        self.assertTrue(x1 <= 310 and y1 <= 30 and x2 >= 470 and y2 >= 45)
        layer, warnings = workflow.clean(p, page)
        self.assertIsNone(layer)  # bản redraw: không xoá chữ

    def test_without_ai_steps_two_and_seven_run_on_the_machine(self):
        p, page = self.project, self.page
        workflow.copy_image(p.file(page, "work.png"), p.file(page, "raw.png"))
        off = ai.AIConfig(provider="none")
        with patch("app.core.ai.ask_json", side_effect=AssertionError("không được gọi AI")):
            warnings = workflow.detect(p, page, self.detector, off)
            self.assertTrue(any("Không dùng AI" in w for w in warnings))
            self.assertEqual([r["bubble_src"] for r in page["regions"]], ["auto"] * 3)
            tl = self.folder / "tl.txt"
            tl.write_text("13\nCảm ơn anh nhiều.\nKhông có gì mà.\nBÙM\n", encoding="utf-8")
            p.data["translation_file"] = str(tl)
            workflow.match(p, page, off)
        placed = [s for r in page["regions"] for s in r["lines"]] + [u["text"] for u in page["unplaced"]]
        self.assertEqual(sorted(placed), sorted(["Cảm ơn anh nhiều.", "Không có gì mà.", "BÙM"]))
        self.assertTrue(all(r["confidence"] is None for r in page["regions"]))
        self.assertTrue(any(r["lines"] for r in page["regions"]))

    def test_spread_takes_both_raws_right_to_left(self):
        folder = self.folder / "raw"
        folder.mkdir()
        for n in (2, 3, 4, 5):
            (folder / f"{n:03d}.jpg").write_bytes(b"x")
        files = workflow.raw_files_for("03-04.jpg", str(folder))
        self.assertEqual([Path(f).name for f in files], ["004.jpg", "003.jpg"])
        self.assertEqual([Path(f).name for f in workflow.raw_files_for("05.png", str(folder))], ["005.jpg"])
        self.assertEqual(workflow.raw_files_for("09.png", str(folder)), [])


if __name__ == "__main__":
    unittest.main()
