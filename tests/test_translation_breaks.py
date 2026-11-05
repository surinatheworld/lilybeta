from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from app.core import ai, workflow
from app.core.docs import TYPE_TAG, translation_units


class TranslationBreakTests(unittest.TestCase):
    def test_soft_breaks_become_separate_matchable_units_without_losing_style(self):
        self.assertEqual(translation_units(["Chuẩn bị chu đáo... \nCảm ơn quý khách!", " \n"]),
                         ["Chuẩn bị chu đáo...", "Cảm ơn quý khách!"])
        self.assertEqual(translation_units(["⟦thought⟧Câu một\nCâu hai"]),
                         ["⟦thought⟧Câu một", "⟦thought⟧Câu hai"])
        tagged=TYPE_TAG.format("normal","全文")+"Một bản dịch\nđược ngắt dòng"
        self.assertEqual(translation_units([tagged]),[tagged])

    def test_file_and_manual_page_lines_use_the_same_units(self):
        with tempfile.TemporaryDirectory() as folder:
            p=workflow.Project(Path(folder)/"project")
            p.sync_documents([{"id":1,"name":"012.png"}])
            page=p.page("012.png")
            with patch.object(p,"translation",return_value={12:["Một câu\nCâu khác"]}):
                self.assertEqual(p.page_lines(page),["Một câu","Câu khác"])
            page.update(lines_manual=True,lines=["Một câu\nCâu khác"])
            self.assertEqual(p.page_lines(page),["Một câu","Câu khác"])

    def test_multiline_manual_assignment_is_not_duplicated_into_an_unlocked_region(self):
        with tempfile.TemporaryDirectory() as folder:
            p=workflow.Project(folder);p.sync_documents([{"id":1,"name":"012.png"}]);page=p.page("012.png")
            page.update(lines_manual=True,lines=["Một câu\nCâu khác"])
            a,b=workflow.new_region("R01",[10,10,40,40]),workflow.new_region("R02",[50,10,80,40])
            a.update(lines=["Một câu\nCâu khác"],lines_src="manual")
            b.update(ja="文字",ja_src="manual")
            page["regions"]=[a,b]
            workflow.imwrite(p.file(page,"work.png"),np.full((100,100,3),255,np.uint8))
            with patch("app.core.ai.ask_json",side_effect=AssertionError("nothing left to assign")):
                workflow.match(p,page,ai.AIConfig())
            self.assertEqual(a["lines"],["Một câu\nCâu khác"])
            self.assertEqual(b["lines"],[])
            self.assertEqual(page["unplaced"],[])


if __name__=="__main__":
    unittest.main()
