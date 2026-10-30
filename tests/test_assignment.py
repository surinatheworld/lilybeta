import unittest
from types import SimpleNamespace

import numpy as np

from app.core.assign import assign_lines, skip_cost
from app.core.detect import TextBox, sort_reading_order
from app.core.page import Page, Region


class ReadingOrderTests(unittest.TestCase):
    def test_staggered_speech_and_narration_share_a_reading_row(self):
        left = TextBox(540, 340, 600, 415, 1)
        right = TextBox(650, 390, 710, 480, 1)
        below = TextBox(270, 675, 296, 732, 1)
        self.assertEqual(sort_reading_order([left, below, right]), [right, left, below])
        self.assertEqual(sort_reading_order([left, below, right], False), [left, right, below])

    def test_separate_rows_and_separate_panels_keep_their_order(self):
        top = TextBox(80, 70, 120, 120, 1)
        bottom = TextBox(260, 250, 300, 300, 1)
        self.assertEqual(sort_reading_order([bottom, top]), [top, bottom])
        image = np.zeros((400, 400), np.uint8)
        image[180:205] = 255
        self.assertEqual(sort_reading_order([bottom, top], img=image), [top, bottom])


class AssignmentTests(unittest.TestCase):
    def page(self):
        boxes = [(630, 940, 690, 1040), (400, 870, 570, 995), (294, 962, 340, 1000),
                 (146, 917, 193, 1093), (250, 1030, 288, 1063), (266, 1138, 570, 1178)]
        regions = [Region(box, kind=kind) for box, kind in zip(boxes, ("text", "sfx", "sfx", "text", "sfx", "text"))]
        mask = np.zeros((1200, 836), np.float32)
        for region, ink in zip(regions, (2000, 4500, 340, 2300, 255, 6400)):
            x1, y1, x2, y2 = region.box
            patch = np.zeros((y2-y1, x2-x1), np.float32)
            patch.flat[:ink] = 1
            mask[y1:y2, x1:x2] = patch
        page = SimpleNamespace(size=(836, 1200), original=np.full((1200, 836, 3), 255, np.uint8), text_mask=mask)
        return page, regions

    def test_large_translated_sfx_and_thought_beat_extra_footer(self):
        page, regions = self.page()
        lines = ["Yêu anh lắm, Souta", "Ôm chặt", "Cứ như này thì mình sẽ mất kiểm soát mất…"]
        plan = assign_lines(page, regions, lines)
        self.assertEqual([(regions.index(r), ls) for r, ls in plan], [(0, [lines[0]]), (1, [lines[1]]), (3, [lines[2]])])

    def test_footer_can_still_receive_an_explicit_translation(self):
        page, regions = self.page()
        footer = regions[-1]
        line = "Hẹn gặp lại ở chương sau!"
        self.assertEqual(assign_lines(page, [footer], [line]), [(footer, [line])])
        self.assertLess(skip_cost(page, footer), skip_cost(page, regions[3]))

    def test_auto_clean_can_leave_all_unassigned_sfx_untouched(self):
        page = Page.__new__(Page)
        page.original = np.full((160, 160, 3), 255, np.uint8)
        page.working = page.original.copy()
        page.text_mask = np.zeros((160, 160), np.float32)
        page.regions = [Region((50, 50, 90, 90), kind="sfx")]
        # A closed white shape must also be skipped, not just the inpainting branch.
        self.assertEqual(page.auto_clean(small_sfx=False), 0)
        self.assertFalse(page.regions[0].cleaned)
        np.testing.assert_array_equal(page.working, page.original)
