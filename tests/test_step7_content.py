import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from app.core import ai, match_text, workflow


class StepSevenContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = workflow.Project(Path(self.tmp.name))
        self.project.sync_documents([{"id":1,"name":"013.jpg"}])
        self.page = self.project.page("013.jpg")
        self.page.update(lines=["Không có gì mà.", "Cảm ơn anh nhiều."], lines_manual=True)
        self.page["regions"] = [workflow.new_region("R01", [20,20,80,110]),
                                workflow.new_region("R02", [130,20,190,110])]
        for i,r in enumerate(self.page["regions"],1):
            r["order"] = i
        workflow.imwrite(self.project.file(self.page,"work.png"), np.full((200,220,3),255,np.uint8))
        workflow.imwrite(self.project.file(self.page,"raw.png"), np.full((200,220,3),123,np.uint8))
        self.cfg = ai.AIConfig(send_image_on_match=False)

    def reply(self):
        return {"assignments":[{"line":1,"region":"R02","confidence":.96,"note":""},
                               {"line":2,"region":"R01","confidence":.95,"note":""}],
                "readings":[{"region":"R01","ja":"ありがとうございます","vi":"Cảm ơn anh nhiều.",
                             "confidence":.92,"note":""},
                            {"region":"R02","ja":"どういたしまして","vi":"Không có gì mà.",
                             "confidence":.93,"note":""}]}

    def test_redetection_invalidates_auto_japanese_when_the_text_box_changes(self):
        from app.core.detect import TextBox

        for source in ("ocr","manual"):
            with self.subTest(source=source):
                r=workflow.new_region("R01",[20,20,80,110])
                r.update(ja="古い文字",ja_src=source)
                self.page["regions"]=[r]
                boxes=[TextBox(22,20,82,110,.9)]
                detector=SimpleNamespace(detect=lambda im:(boxes,np.zeros(im.shape[:2],np.float32)))
                with patch("app.core.balloons.refine",return_value=(boxes,{})):
                    workflow.detect(self.project,self.page,detector,ai.AIConfig(provider="none"))
                self.assertEqual(r["ja"],"古い文字" if source=="manual" else "")

    def test_auto_ocr_reads_raw_and_matches_reordered_vietnamese_without_rewriting_it(self):
        calls = []
        def read(img, box):
            self.assertTrue(np.all(img == 123))
            calls.append(box)
            return "ありがとうございます" if box[0] == 20 else "どういたしまして"
        def request(cfg, step, system, prompt, images, schema, name):
            self.assertIn("faithful Vietnamese reference", system)
            self.assertIn("ありがとうございます", prompt)
            self.assertIn("どういたしまして", prompt)
            self.assertEqual(schema["required"], ["assignments","readings"])
            self.assertEqual(images, [])
            return self.reply()
        progress = []
        with patch("app.core.ocr.reader", return_value=SimpleNamespace(read=read)), \
                patch("app.core.ai.ask_json", side_effect=request) as api:
            warnings = workflow.match(self.project, self.page, self.cfg, progress.append)
            self.assertTrue(any("Chưa xác minh nghĩa" in w for w in warnings))
            self.assertEqual(api.call_count,1)  # translation and assignment in one request
            workflow.match(self.project, self.page, self.cfg)
        self.assertEqual(len(calls),2)  # Japanese is reused on rerun
        a,b = self.page["regions"]
        self.assertEqual((a["lines"],b["lines"]),(["Cảm ơn anh nhiều."],["Không có gì mà."]))
        self.assertEqual(match_text.reference(a)["text"],"Cảm ơn anh nhiều.")
        reopened = workflow.Project(self.project.folder)
        self.assertEqual(match_text.reference(reopened.pages[0]["regions"][1])["text"],"Không có gì mà.")
        self.assertTrue(any("dịch Nhật" in p for p in progress))

    def test_manual_assignments_and_japanese_are_never_replaced(self):
        a,b = self.page["regions"]
        a.update(ja="ありがとうございます", ja_src="manual")
        b.update(lines=["Không có gì mà."], lines_src="manual", ja="Giữ tay", ja_src="manual")
        def request(*args):
            prompt=args[3]
            self.assertNotIn("R02",prompt)
            self.assertNotIn("Không có gì mà.",prompt)
            return {"assignments":[{"line":1,"region":"R01","confidence":.9,"note":""}],
                    "readings":[self.reply()["readings"][0]]}
        with patch("app.core.ocr.reader", side_effect=AssertionError("manual Japanese is authoritative")), \
                patch("app.core.ai.ask_json", side_effect=request):
            workflow.match(self.project,self.page,self.cfg)
        self.assertEqual((a["ja"],a["ja_src"]),("ありがとうございます","manual"))
        self.assertEqual((b["lines"],b["lines_src"],b["ja"]),(["Không có gì mà."],"manual","Giữ tay"))

    def test_redraw_without_raw_is_not_sent_to_local_ocr(self):
        self.project.data["source"]="redraw"
        self.project.file(self.page,"raw.png").unlink()
        with patch("app.core.ocr.reader", side_effect=AssertionError("not an original")), \
                patch("app.core.ai.ask_json", return_value=self.reply()):
            warnings=workflow.match(self.project,self.page,self.cfg)
        self.assertTrue(any("thiếu Raw" in w for w in warnings))
        self.assertEqual(self.page["regions"][0]["ja_src"],"ai")

    def test_failed_request_preserves_old_assignments_and_saves_new_ocr(self):
        self.page["regions"][0].update(lines=["Kết quả cũ"],lines_src="ai")
        with patch("app.core.ocr.reader", return_value=SimpleNamespace(read=lambda im,b:"文字")), \
                patch("app.core.ai.ask_json", side_effect=ai.AIError("service unavailable")):
            with self.assertRaises(ai.AIError):
                workflow.match(self.project,self.page,self.cfg)
        saved=json.loads(self.project.path.read_text(encoding="utf-8"))["pages"][0]["regions"][0]
        self.assertEqual(saved["lines"],["Kết quả cũ"])
        self.assertEqual((saved["ja"],saved["ja_src"]),("文字","ocr"))

    def test_source_or_geometry_change_invalidates_cached_reference(self):
        a=self.page["regions"][0];a["ja"]="ありがとうございます"
        match_text.save_references([a],{"R01":self.reply()["readings"][0]})
        self.assertIsNotNone(match_text.reference(a))
        a["ja"]="違います"
        self.assertIsNone(match_text.reference(a))
        a["ja"]="ありがとうございます";a["box"][0]+=1
        self.assertIsNone(match_text.reference(a))
        with patch("app.core.ocr.reader",return_value=None):
            match_text.read_missing(self.project,self.page,[a],np.zeros((200,220,3),np.uint8))
        self.assertEqual(a["ja"],"")  # stale OCR is not sent to AI if rereading is unavailable

    def test_cached_vietnamese_anchors_work_offline_even_when_order_is_wrong(self):
        for r,item in zip(self.page["regions"],self.reply()["readings"]):
            r["ja"]=item["ja"]
            match_text.save_references([r],{r["id"]:item})
        with patch("app.core.ai.ask_json", side_effect=AssertionError("offline")), \
                patch("app.core.ocr.reader", side_effect=AssertionError("already read")):
            warnings=workflow.match(self.project,self.page,ai.AIConfig(provider="none"))
        self.assertEqual(self.page["regions"][0]["lines"],["Cảm ơn anh nhiều."])
        self.assertEqual(self.page["regions"][1]["lines"],["Không có gì mà."])
        self.assertTrue(any("không tạo bản dịch" in w for w in warnings))

    def test_repeated_short_replies_are_not_given_false_unique_anchors(self):
        for r in self.page["regions"]:
            r["ja"]="はい"
            match_text.save_references([r],{r["id"]:{"ja":"はい","vi":"Ừ.","confidence":.95,"note":""}})
        self.assertFalse(match_text.reference_anchors(self.page["regions"],["Ừ","Ừ."]))
        self.assertFalse(match_text.reference_anchors(self.page["regions"],["Ừ."]))

    def test_conflicting_manual_japanese_lowers_reference_and_assignment_confidence(self):
        for r in self.page["regions"]:
            r.update(ja="全然違います",ja_src="manual")
        with patch("app.core.ai.ask_json", return_value=self.reply()):
            workflow.match(self.project,self.page,self.cfg)
        self.assertTrue(all(r["uncertain"] and r["confidence"]<=.5 for r in self.page["regions"]))
        self.assertTrue(all(r["ja"]=="全然違います" for r in self.page["regions"]))
        self.assertFalse(match_text.reference_anchors(self.page["regions"],self.page["lines"]))

    def test_empty_manually_entered_japanese_remains_empty(self):
        r=self.page["regions"][0];r["ja_src"]="manual"
        with patch("app.core.ocr.reader", side_effect=AssertionError("manual empty")):
            match_text.read_missing(self.project,self.page,[r],np.zeros((200,220,3),np.uint8))
        match_text.save_references([r],{"R01":self.reply()["readings"][0]})
        self.assertEqual(r["ja"],"")
        self.assertIsNone(match_text.reference(r))


class MatchingReplyTests(unittest.TestCase):
    def test_japanese_from_an_earlier_step_is_reused_without_loading_ocr(self):
        from app.core.assign import assign_lines
        from app.core.docs import TYPE_TAG
        from app.core.page import Region

        regions=[Region((10,10,50,100)),Region((100,10,150,100))]
        page=SimpleNamespace(_ocr_cache={tuple(regions[0].box):"ありがとうございます",
                                        tuple(regions[1].box):"どういたしまして"})
        lines=[TYPE_TAG.format("normal","どういたしまして")+"Không có gì.",
               TYPE_TAG.format("normal","ありがとうございます")+"Cảm ơn."]
        with patch("app.core.ocr.reader",side_effect=AssertionError("cached Japanese needs no reader")):
            plan=assign_lines(page,regions,lines)
        self.assertEqual({id(r):ls for r,ls in plan},{id(regions[0]):[lines[1]],id(regions[1]):[lines[0]]})

    def test_invalid_reference_ids_and_confidences_cannot_poison_the_cache(self):
        item={"region":"R01","ja":"はい","vi":"Vâng","confidence":.9,"note":""}
        for bad in ({**item,"region":"R99"},{**item,"confidence":float("nan")},
                    {**item,"confidence":1.1},{**item,"vi":42}):
            readings,warnings=ai.parse_readings({"readings":[bad]},["R01"])
            self.assertFalse(readings)
            self.assertTrue(warnings)

    def test_fractional_indices_and_nonfinite_assignment_confidence_stay_unplaced(self):
        for line,conf in ((1.5,.9),(True,.9),(float("nan"),.9),(1,float("nan")),(1,2)):
            out=ai.parse_assignments({"assignments":[{"line":line,"region":"R01","confidence":conf}]},1,["R01"])
            self.assertFalse(out["assign"])
            self.assertEqual(out["unplaced"],[0])

    def test_legacy_response_remains_usable_but_reports_missing_references(self):
        with patch("app.core.ai.ask_json", return_value={"assignments":[]}):
            out=ai.match(ai.AIConfig(send_image_on_match=False),
                         [{"id":"R01","box":[0,0,10,10],"ja":"はい"}],["Vâng"],(100,100))
        self.assertFalse(out["readings"])
        self.assertTrue(any("bản Việt tham chiếu" in w for w in out["warnings"]))


if __name__ == "__main__":
    unittest.main()
