import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
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

    def test_only_compatible_provider_uses_custom_url(self):
        custom = "http://localhost:11434/v1/"
        for provider in ("gemini", "openai", "anthropic", "none"):
            with self.subTest(provider=provider):
                self.assertEqual(ai.AIConfig(provider=provider, base_url=custom).url(), ai.PROVIDERS[provider]["url"])
        self.assertEqual(ai.AIConfig(provider="compatible", base_url=custom).url(), custom.rstrip("/"))

    def test_api_credentials_are_single_request_headers(self):
        for header in ("Authorization", "X-api-key", "X-goog-api-key"):
            with self.subTest(header=header):
                req = urllib.request.Request("https://example.invalid/", headers={header: "test"})
                with patch("urllib.request.urlopen") as transport:
                    ai.secrets.open_api_request(req, timeout=1)
                    transport.assert_called_once_with(req, timeout=1)
                self.assertEqual(req.get_header(header), "test")
                self.assertNotIn(header, req.headers)
                self.assertEqual(req.unredirected_hdrs[header], "test")

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

    def test_text_splits_validate_all_parts_and_parent_ids(self):
        part = {"box": [600, 100, 700, 200], "bubble": "round", "order": 1, "confidence": .9, "note": ""}
        outside = {**part, "box": [400, 300, 500, 400], "bubble": "free", "order": 2}
        data = {"regions": [], "missed": [], "splits": [{"id": "R01", "parts": [outside, part]}]}
        out = ai.parse_classification(data, ["R01"], (500, 480))
        self.assertEqual([p["bubble"] for p in out["splits"]["R01"]], ["round", "free"])
        self.assertEqual(out["splits"]["R01"][0]["box"], [300, 48, 350, 96])
        for bad in ({**outside, "box": [0, 0, 0, 0]}, {**outside, "box": [0, 0, float("nan"), 100]},
                    {**outside, "confidence": "sure"}, {**outside, "bubble": "unknown"}):
            data["splits"] = [{"id": "R01", "parts": [part, bad]}]
            out = ai.parse_classification(data, ["R01"], (500, 480))
            self.assertFalse(out["splits"])
            self.assertTrue(any("R01" in w for w in out["warnings"]))
        data["splits"] = [{"id": "R99", "parts": [part, outside]}]
        out = ai.parse_classification(data, ["R01"], (500, 480))
        self.assertFalse(out["splits"])
        self.assertTrue(any("R99" in w for w in out["warnings"]))


def _classify_reply(bubbles):
    def fake(cfg, step, system, prompt, images, schema, name):
        assert step == "detect" and images and schema is ai.CLASSIFY_SCHEMA
        return {"regions": [{"id": rid, "bubble": b, "order": i + 1, "confidence": .95, "note": ""}
                            for i, (rid, b) in enumerate(bubbles.items())], "missed": [], "splits": []}
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

    def test_clean_panel_restores_screentone_on_enabled_sfx(self):
        p, page = self.project, self.page
        self._detect({"R01": "round", "R02": "free", "R03": "sfx"})
        sfx = p.region(page, "R03")
        x, y, right, bottom = sfx["box"]
        yy, xx = np.indices((500, 500))
        tone = np.where((xx % 8 - 4) ** 2 + (yy % 8 - 4) ** 2 <= 4, 30, 248).astype(np.uint8)
        original = cv2.cvtColor(tone, cv2.COLOR_GRAY2BGR)
        mask = np.zeros(tone.shape, np.float32)
        mask[y + 8:bottom - 8, x + 12:right - 12] = 1
        raw = original.copy()
        raw[mask > 0] = 0
        workflow.imwrite(p.file(page, "work.png"), raw)
        np.savez_compressed(p.file(page, "mask.npz"), mask=mask)
        p.data.update(clean_kinds=["sfx"], restore_screentone=True)

        layer, warnings = workflow.clean(p, page)

        self.assertEqual(warnings, [])
        self.assertTrue(sfx["cleaned"])
        np.testing.assert_array_equal(workflow.imread(p.file(page, "cleaned.png")), original)
        patch = cv2.imdecode(np.fromfile(layer, np.uint8), cv2.IMREAD_UNCHANGED)
        visible = patch[..., 3] == 255
        np.testing.assert_array_equal(np.where(visible[..., None], patch[..., :3], raw), original)
        self.assertFalse(visible[:y].any() or visible[bottom:].any())

    def test_cleanup_after_matching_preserves_original_unassigned_lettering(self):
        p, page = self.project, self.page
        self._detect({"R01": "round", "R02": "free", "R03": "sfx"})
        raw = workflow.imread(p.file(page, "work.png"))
        assigned, unassigned = page["regions"][:2]
        assigned["lines"] = ["Chữ đã ghép."]
        page["assignment_units"] = []
        p.data["clean_kinds"] = ["round", "free", "sfx"]
        workflow.clean(p, page)
        result = workflow.imread(p.file(page, "cleaned.png"))
        x, y, right, bottom = unassigned["box"]
        np.testing.assert_array_equal(result[y:bottom, x:right], raw[y:bottom, x:right])
        self.assertTrue(assigned["cleaned"])
        self.assertFalse(unassigned["cleaned"])

    def test_inpaint_padding_cannot_erase_neighboring_unassigned_ink(self):
        p, page = self.project, self.page
        raw = np.full((160, 200, 3), 255, np.uint8)
        raw[50:85, 60:83] = 30
        raw[50:85, 85:98] = 45
        workflow.imwrite(p.file(page, "work.png"), raw)
        np.savez(p.file(page, "mask.npz"), mask=np.zeros(raw.shape[:2], np.float32))
        assigned = workflow.new_region("R01", [60, 50, 83, 85])
        assigned.update(bubble="round", lines=["Chữ đã ghép."])
        neighbor = workflow.new_region("R02", [85, 50, 98, 85])
        neighbor.update(bubble="sfx")
        page["regions"] = [assigned, neighbor]
        page["assignment_units"] = []

        def broad_inpaint(pg, region):
            pg.working[42:93, 52:103] = 255

        with patch("app.core.page.Page._clean_pixels", broad_inpaint):
            workflow.clean(p, page)
        result = workflow.imread(p.file(page, "cleaned.png"))
        np.testing.assert_array_equal(result[48:87, 83:100], raw[48:87, 83:100])
        self.assertTrue((result[50:85, 60:83] == 255).all())

    def test_photoshop_typeset_replaces_stale_cleanup_after_verification(self):
        from app.panel import Runner

        p, page = self.project, self.page
        self._detect({"R01": "round", "R02": "free", "R03": "sfx"})
        page["regions"][0]["lines"] = ["Chữ đã ghép."]
        p.set_status(page, "clean", "done")
        p.invalidate(page, "match")
        self.assertEqual(p.status(page, "clean")["state"], "stale")
        calls = []
        runner = Runner(SimpleNamespace(project=p, ps=SimpleNamespace(call=lambda command, **_: calls.append(command))))
        with patch("app.core.semantic_review.require_verified", side_effect=lambda *_: calls.append("verify")), \
                patch.object(workflow, "clean", return_value=("clean.png", [])), \
                patch.object(workflow, "typeset", return_value=("layers.json", [])):
            runner.run_step("typeset", page, lambda _: None)
        self.assertEqual(calls, ["verify", "place_layer", "typeset"])
        self.assertEqual(p.status(page, "clean")["state"], "done")

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
        self.assertFalse(sfx["visible"])
        self.assertEqual(data["redraw"], "")
        self.assertTrue(p.has(page, "preview.png"))

    def test_typeset_repairs_a_missed_white_balloon_and_attaches_a_redraw_patch(self):
        from app.core.semantic_review import confirm_region

        p, page = self.project, self.page
        self._detect({"R01": "free", "R02": "free", "R03": "sfx"})
        page.update(lines_manual=True, lines=["Cảm ơn anh."])
        row = p.region(page, "R01")
        row.update(lines=["Cảm ơn anh."], lines_src="manual")
        confirm_region(p, page, row)

        path, _ = workflow.typeset(p, page)

        result = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertTrue(result["layers"][0]["visible"])
        self.assertFalse(p.has(page, "cleaned.png"))
        patch = cv2.imdecode(np.fromfile(result["redraw"], np.uint8), cv2.IMREAD_UNCHANGED)
        self.assertTrue((patch[112:165, 371:390] == 255).all())
        self.assertFalse(patch[340:400, 330:451, 3].any())

    def test_ai_failure_keeps_detected_regions_and_reports(self):
        workflow.copy_image(self.project.file(self.page, "work.png"), self.project.file(self.page, "raw.png"))
        with patch("app.core.ai.ask_json", side_effect=ai.AIError("Gemini báo lỗi 429")):
            with self.assertRaisesRegex(ai.AIError, "429"):
                workflow.detect(self.project, self.page, self.detector, ai.AIConfig())
        saved = json.loads(self.project.path.read_text(encoding="utf-8"))["pages"][0]["regions"]
        self.assertEqual(len(saved), 3)
        self.assertEqual(saved[0]["bubble_src"], "auto")

    def test_merged_inside_and_outside_text_split_and_keep_ids_on_redetection(self):
        p, page = self.project, self.page
        merged = [((100, 100, 401, 181), "text"), BOXES[2]]
        detector = type("D", (), {"detect": lambda self, img: _fake_detection(merged)})()
        parts = [{"box": ai._norm(b, (500, 480)), "bubble": bubble, "order": i + 1,
                  "confidence": .95, "note": ""}
                 for i, (b, bubble) in enumerate([(BOXES[0][0], "round"), (BOXES[1][0], "free")])]

        def classify(cfg, step, system, prompt, images, schema, name):
            self.assertEqual(len(images), 2)  # ảnh có ID + ảnh gốc để nhìn viền bóng
            reply = _classify_reply({"R01": "round", "R02": "sfx"})(cfg, step, system, prompt, images, schema, name)
            reply["regions"][1]["order"] = 3
            reply["splits"] = [{"id": "R01", "parts": parts}]
            return reply

        with patch("app.core.ai.ask_json", classify):
            warnings = workflow.detect(p, page, detector, ai.AIConfig())
        self.assertEqual(warnings, [])
        self.assertEqual([(r["id"], r["bubble"], r["box"]) for r in page["regions"]],
                         [("R01", "round", list(BOXES[0][0])), ("R03", "free", list(BOXES[1][0])),
                          ("R02", "sfx", list(BOXES[2][0]))])
        self.assertFalse(p.region(page, "R01")["cleaned"])
        p.region(page, "R03")["ja"] = "Giữ chỉnh tay"
        p.region(page, "R03")["ja_src"] = "manual"
        # Cùng một khung gộp từ detector vẫn khớp đúng từng ID đã tách, kể cả khi mở lại dự án.
        p.save()
        reopened = Project(p.folder)
        with patch("app.core.ai.ask_json", _classify_reply({"R01": "round", "R03": "free", "R02": "sfx"})):
            workflow.detect(reopened, reopened.pages[0], detector, ai.AIConfig())
        self.assertEqual([r["id"] for r in reopened.pages[0]["regions"]], ["R01", "R03", "R02"])
        self.assertEqual(reopened.region(reopened.pages[0], "R03")["ja"], "Giữ chỉnh tay")

    def test_shared_balloon_keeps_separate_text_ids_and_protects_manual_edits(self):
        p, page = self.project, self.page
        parts = [{"box": [360, 100, 401, 130], "bubble": "round", "order": 1, "confidence": .9, "note": ""},
                 {"box": [360, 150, 401, 181], "bubble": "round", "order": 2, "confidence": .9, "note": ""}]
        r = workflow.new_region("R01", BOXES[0][0])
        page["regions"] = [r]
        _, mask = _fake_detection(BOXES)
        for field, value in (("ja_src", "manual"), ("bubble_src", "manual"), ("lines_src", "manual"), ("src", "manual")):
            r[field] = value
            warnings = workflow._split_text_regions(p, page, page["regions"], {"R01": parts}, None, mask)
            self.assertEqual(len(page["regions"]), 1)
            self.assertTrue(warnings)
            r[field] = "detect" if field == "src" else "auto"
        self.assertEqual(workflow._split_text_regions(p, page, page["regions"], {"R01": parts}, None, mask), [])
        self.assertEqual([(r["id"], r["box"], r["bubble"]) for r in page["regions"]],
                         [("R01", parts[0]["box"], "round"), ("R02", parts[1]["box"], "round")])

    def test_invalid_split_geometry_leaves_original_region_intact(self):
        p, page = self.project, self.page
        _, mask = _fake_detection(BOXES)
        for second in ([360, 120, 401, 181], [20, 150, 401, 181]):
            r = workflow.new_region("R01", BOXES[0][0])
            page["regions"] = [r]
            parts = [{"box": b, "bubble": "round", "order": i + 1, "confidence": .9, "note": ""}
                     for i, b in enumerate(([360, 100, 401, 140], second))]
            warnings = workflow._split_text_regions(p, page, page["regions"], {"R01": parts}, None, mask)
            self.assertTrue(warnings)
            self.assertEqual(len(page["regions"]), 1)
            self.assertEqual(r["box"], list(BOXES[0][0]))

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
            from tests.test_semantic_matching import Scores
            japanese=["ありがとうございます","どういたしまして","爆発しました"]
            translated=["Cảm ơn anh nhiều.","Không có gì mà.","BÙM"]
            for r,ja in zip(page['regions'],japanese):r.update(ja=ja,ja_src='manual')
            scoring=Scores({(ja,vi):4 for ja,vi in zip(japanese,translated)})
            with patch('app.core.source_ocr.prepare',return_value=[]), \
                 patch('app.core.semantic_match.Scorer',return_value=scoring):
                workflow.match(p, page, off)
        placed = [s for r in page["regions"] for s in r["lines"]] + [u["text"] for u in page["unplaced"]]
        self.assertEqual(sorted(placed), sorted(["Cảm ơn anh nhiều.", "Không có gì mà.", "BÙM"]))
        self.assertTrue(all(r["confidence"] is None for r in page["regions"]))
        # Local evidence may abstain; every input must remain either assigned
        # or explicitly unplaced, without an inference service.
        self.assertTrue(page["unplaced"] or any(r["lines"] for r in page["regions"]))

    def test_spread_takes_both_raws_right_to_left(self):
        folder = self.folder / "raw"
        folder.mkdir()
        for n in (2, 3, 4, 5):
            (folder / f"{n:03d}.jpg").write_bytes(b"x")
        files = workflow.raw_files_for("03-04.jpg", str(folder))
        self.assertEqual([Path(f).name for f in files], ["004.jpg", "003.jpg"])
        self.assertEqual([Path(f).name for f in workflow.raw_files_for("05.png", str(folder))], ["005.jpg"])
        self.assertEqual(workflow.raw_files_for("09.png", str(folder)), [])

    def test_redraw_upload_matches_ranges_and_rejects_ambiguous_images(self):
        pages = [{"id": 1, "name": "15.psd"}, {"id": 2, "name": "03-04.psd"},
                 {"id": 3, "name": "016.psd"}, {"id": 4, "name": "16 copy.psd"}]
        jobs, rejected = workflow.redraw_targets(
            ["015.jpg", "03-04.jpg", "04.jpg", "16.jpg", "42.jpg"], pages, active="15.psd")
        self.assertEqual([(d["id"], name) for d, name in jobs], [(1, "015.jpg"), (2, "03-04.jpg")])
        self.assertEqual(len(rejected), 3)
        self.assertFalse(workflow.redraw_targets(["15.jpg", "015.png"], pages)[0])
        self.assertEqual(workflow.redraw_targets(["redraw.jpg"], pages, active="15.psd")[0], [(pages[0], "redraw.jpg")])
        self.assertFalse(workflow.redraw_targets(["42.jpg"], pages, active="15.psd")[0])

    def test_redraw_upload_preserves_verified_raw_and_updates_only_matched_pages(self):
        from app.panel import PhotoshopError, Runner

        p, page = self.project, self.page
        docs = [{"id": 1, "name": page["key"]}, {"id": 2, "name": "14.jpg"}]
        p.sync_documents(docs)
        second = p.page("14.jpg")
        original = workflow.imread(p.file(page, "work.png"))
        raw = p.file(page, "raw.png")
        workflow.imwrite(raw, original)
        raw_bytes = raw.read_bytes()
        r = workflow.new_region("R01", BOXES[0][0])
        r.update(ja="ありがとう", ja_src="manual", lines=["Cảm ơn"], lines_src="manual")
        page["regions"] = [r]
        for step in ("match", "typeset"):
            p.set_status(page, step, "done")
        for name in ("cleaned.png", "assignment-preview.png", "preview.png"):
            p.file(page, name).write_bytes(b"old")

        def call(command, *, layer, folder, id, name):
            self.assertEqual(command, "replace_redraw")
            if id == 2:
                raise PhotoshopError("ảnh sai tỉ lệ")
            self.assertEqual((name, layer), (page["key"], "13.jpg"))
            folder = Path(folder)
            inputs = {"raw": folder / "raw.png", "work": folder / "work.png", "preview": folder / "preview.png"}
            cv2.imencode(".png", original, [cv2.IMWRITE_PNG_COMPRESSION, 9])[1].tofile(inputs["raw"])
            workflow.imwrite(inputs["work"], np.full_like(original, 255))
            workflow.imwrite(inputs["preview"], np.full_like(original, 200))
            return {**inputs, "size": [original.shape[1], original.shape[0]]}

        runner = Runner(SimpleNamespace(project=p, ps=SimpleNamespace(call=call)))
        runner.redraw_done = []
        runner._run_redraw([(docs[0], "13.jpg"), (docs[1], "14.png")])
        self.assertEqual(runner.redraw_done, [page["key"]])
        self.assertTrue(p.uses_redraw(page))
        self.assertFalse(p.uses_redraw(second))
        self.assertEqual(raw.read_bytes(), raw_bytes)
        self.assertEqual(page["regions"], [r])
        self.assertEqual(p.status(page, "match")["state"], "done")
        self.assertEqual(p.status(page, "typeset")["state"], "done")
        self.assertTrue(np.all(workflow.imread(p.file(page, "work.png")) == 255))
        self.assertTrue(np.all(workflow.imread(p.file(page, "preview.png")) == 200))
        self.assertFalse(p.has(page, "cleaned.png"))
        self.assertFalse(p.has(page, "assignment-preview.png"))


if __name__ == "__main__":
    unittest.main()
