"""Meaning checks are separate from draft placement and precede all exports."""
from pathlib import Path
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import copy
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np

from app.core import semantic_review as meaning, workflow


class MeaningGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = workflow.Project(temp.name)
        self.project.sync_documents([{"id":1,"name":"001.png"}])
        self.page = self.project.pages[0]
        self.page.update(lines_manual=True, lines=["Hôm nay tôi thực sự rất vui.", "Ngày mai chúng ta cùng đi nhé."])
        self.page["regions"] = [workflow.new_region("R01",[1,1,40,40]), workflow.new_region("R02",[50,1,90,40])]
        for r, ja, text in zip(self.page["regions"], ["今日は本当に楽しかったです", "明日は一緒に行きましょう"], self.page["lines"]):
            r.update(ja=ja, ja_src="ocr", lines=[text])
        workflow.imwrite(self.project.file(self.page,"raw.png"), np.full((100,100,3),255,np.uint8))
        self.models = SimpleNamespace(compare=lambda ja,vi: {name:np.array([[.95,.1],[.1,.95]]) for name in meaning.MODEL_FILES})

    def verify(self, models=None):
        return meaning.verify_page(self.project,self.page,models=models or self.models)

    def test_consensus_allows_export_gate_and_records_actual_pairs(self):
        self.assertEqual(self.verify(),[])
        with patch.object(meaning,"local_models",side_effect=AssertionError("reuse valid check")):
            meaning.require_verified(self.project,self.page)
        saved = workflow.Project(self.project.folder).pages[0]["regions"][0]["meaning_review"]
        self.assertEqual(saved["checks"][0]["text"],self.page["lines"][0])
        self.assertEqual(saved["state"],"passed")

    def test_reversed_assignments_and_disagreement_cannot_pass(self):
        wrong = SimpleNamespace(compare=lambda ja,vi:{name:np.array([[.1,.95],[.95,.1]]) for name in meaning.MODEL_FILES})
        self.assertEqual(self.verify(wrong),["R01","R02"])
        split = SimpleNamespace(compare=lambda ja,vi:{"e5":np.eye(2)*.99,"minilm":np.fliplr(np.eye(2))*.99})
        self.assertEqual(self.verify(split),["R01","R02"])

    def test_missing_model_and_invalid_model_outputs_fail_closed(self):
        for fake in (SimpleNamespace(compare=lambda *a: {}),
                     SimpleNamespace(compare=lambda *a: {name:np.full((2,2),np.nan) for name in meaning.MODEL_FILES})):
            self.assertEqual(self.verify(fake),["R01","R02"])
        with patch.object(meaning,"local_models",side_effect=FileNotFoundError("missing model")):
            self.assertEqual(meaning.verify_page(self.project,self.page),["R01","R02"])
            with self.assertRaisesRegex(ValueError,"Dừng trước typeset"):
                meaning.require_verified(self.project,self.page)

    def test_short_replies_and_repeated_ocr_require_review_even_with_high_scores(self):
        self.page["regions"][0]["ja"] = "はい"
        self.page["regions"][1]["ja"] = "わ"*30
        self.assertEqual(self.verify(),["R01","R02"])

    def test_source_tags_do_not_substitute_for_meaning_or_a_missing_raw(self):
        r = self.page["regions"][0]
        r["lines"] = ["⟦dialogue|"+r["ja"]+"⟧Một câu sai nghĩa ở vùng khác."]
        wrong = SimpleNamespace(compare=lambda ja,vi:{name:np.fliplr(np.eye(2))*.99 for name in meaning.MODEL_FILES})
        self.assertIn("R01",self.verify(wrong))
        self.project.file(self.page,"raw.png").unlink()
        self.assertEqual(self.verify(),["R01","R02"])

    def test_manual_assignment_is_not_confirmation_and_failed_gate_does_not_draw(self):
        self.page["regions"][0].update(lines_src="manual",ja="はい")
        target = self.project.file(self.page,"typeset.json")
        target.write_bytes(b"previous export")
        with patch.object(meaning,"local_models",return_value=self.models), patch("app.core.assign.apply_plan") as draw:
            with self.assertRaisesRegex(ValueError,"Dừng trước typeset"):
                workflow.typeset(self.project,self.page)
        draw.assert_not_called()
        self.assertEqual(target.read_bytes(),b"previous export")
        self.assertFalse(self.project.has(self.page,"preview.png"))

    def test_explicit_confirmation_is_bound_to_source_text_image_and_boxes(self):
        r = self.page["regions"][0]
        mutations = [lambda: r["lines"].append("Đổi thoại"), lambda: r.update(ja="違う文字です"),
                     lambda: r["box"].__setitem__(0,3), lambda: self.page["lines"].append("Câu mới"),
                     lambda: self.page["regions"][1].update(ja="別の文字です"),
                     lambda: self.project.file(self.page,"raw.png").write_bytes(b"changed raw"),
                     lambda: self.project.data.update(source="redraw")]
        for change in mutations:
            meaning.confirm_region(self.project,self.page,r)
            self.assertEqual(meaning.current_review(self.project,self.page,r)["state"],"confirmed")
            change()
            self.assertIsNone(meaning.current_review(self.project,self.page,r))

    def test_confirmations_do_not_hide_missing_duplicate_or_unplaced_input(self):
        for r in self.page["regions"]:
            meaning.confirm_region(self.project,self.page,r)
        self.page["unplaced"] = [{"text":"Câu chưa đặt"}]
        with self.assertRaisesRegex(ValueError,"chưa có vùng"):
            meaning.require_verified(self.project,self.page)
        self.page["unplaced"] = []
        self.page["lines"].append("Câu bị thiếu")
        for r in self.page["regions"]:
            meaning.confirm_region(self.project,self.page,r)
        with self.assertRaisesRegex(ValueError,"thiếu, trùng"):
            meaning.require_verified(self.project,self.page)

    def test_quick_mode_uses_real_gate_before_export(self):
        from app import photoshop
        from app.core.page import Region
        region = Region((1,1,40,40))
        page = SimpleNamespace(regions=[region], original=np.full((100,100,3),255,np.uint8),
                               _ocr_cache={region.box:"はい"})
        with patch.object(meaning,"local_models",side_effect=FileNotFoundError("missing")):
            with self.assertRaisesRegex(ValueError,"Dừng trước typeset"):
                photoshop._verify_quick_plan(page,[(region,["Vâng"])],["Vâng"],Path(self.project.folder),"",None)
        review = workflow.Project(self.project.folder/"meaning-review")
        self.assertEqual(review.pages[0]["regions"][0]["meaning_review"]["state"],"review")
        self.assertFalse((self.project.folder/"preview.png").exists())

    def test_unverified_assignments_render_as_draft_without_approving_or_exporting_layers(self):
        p,page=self.project,self.page
        np.savez_compressed(p.file(page,"mask.npz"),mask=np.zeros((100,100),np.float32))
        p.file(page,"typeset.json").write_bytes(b"old layer export")
        p.file(page,"preview.png").write_bytes(b"old approved preview")
        with patch.object(meaning,"local_models",side_effect=FileNotFoundError("missing models")):
            meaning.verify_page(p,page)
            before=copy.deepcopy(page)
            with patch("app.core.ai.ask_json",side_effect=AssertionError("no API")) as api:
                path,warnings=workflow.preview_assignments(p,page)
            api.assert_not_called()
            self.assertEqual(page,before)
            self.assertEqual(Path(path).name,"assignment-preview.png")
            image=workflow.imread(path)
            self.assertTrue(np.any(image<200),"Actual text should be visible on the white page")
            self.assertTrue(any("Bản gán thử" in w for w in warnings))
            with self.assertRaisesRegex(ValueError,"Dừng trước typeset"):
                workflow.typeset(p,page)
        self.assertEqual(p.file(page,"typeset.json").read_bytes(),b"old layer export")
        self.assertEqual(p.file(page,"preview.png").read_bytes(),b"old approved preview")

    def test_panel_step_seven_renders_draft_without_calling_photoshop(self):
        from app.panel import Runner
        from unittest.mock import Mock
        ps=Mock()
        runner=Runner(SimpleNamespace(project=self.project,ps=ps,ai_config=SimpleNamespace()))
        with patch("app.core.workflow.match",return_value=["Chưa xác minh nghĩa"]), \
             patch("app.core.workflow.preview_assignments",return_value=("draft.png",["Bản gán thử"])) as preview:
            warnings=runner.run_step("match",self.page,lambda s:None)
        preview.assert_called_once()
        ps.call.assert_not_called()
        self.assertEqual(warnings,["Chưa xác minh nghĩa","Bản gán thử"])


if __name__ == "__main__":
    unittest.main()
