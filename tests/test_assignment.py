import unittest
from types import SimpleNamespace

import numpy as np

from app.core.assign import _just_a_name, assign_lines, skip_cost, text_clusters
from app.core.detect import TextBox, sort_reading_order
from app.core.page import Page, Region


class ReadingOrderTests(unittest.TestCase):
    def test_staggered_speech_and_narration_share_a_reading_row(self):
        left = TextBox(540, 340, 600, 415, 1)
        right = TextBox(650, 390, 710, 480, 1)
        below = TextBox(270, 675, 296, 732, 1)
        self.assertEqual(sort_reading_order([left, below, right]), [right, left, below])
        self.assertEqual(sort_reading_order([left, below, right], False), [left, right, below])

    def test_staggered_boxes_far_apart_are_separate_beats(self):
        # 18.1 trang 3: bóng nhỏ "Dạ…" (phải, cao hơn) và bóng "Cháu cũng…" (trái, thấp hơn) cách nhau ~200px, giữa có
        # SFX: đọc từ trên xuống như hai nhịp, không gộp hàng (gộp làm lệch ghép thoại cả trang).
        small_right = TextBox(374, 423, 392, 488, 1)
        far_left = TextBox(101, 458, 176, 573, 1)
        self.assertEqual(sort_reading_order([far_left, small_right]), [small_right, far_left])
        self.assertEqual(sort_reading_order([far_left, small_right], False), [small_right, far_left])

    def test_lower_right_balloon_is_read_before_a_far_left_sfx_with_nothing_between(self):
        # SFX ở trên bên trái, bóng thoại ở dưới bên phải, giữa hai vùng là khoảng trống.
        sfx_left = TextBox(41, 856, 121, 988, 1)
        bubble_right = TextBox(353, 940, 427, 1047, 1)
        self.assertEqual(sort_reading_order([sfx_left, bubble_right]), [bubble_right, sfx_left])

    def test_two_texts_in_one_balloon_are_read_together_before_a_nearby_sfx(self):
        # Hai cụm chữ chung một bóng. SFX ở ngoài bóng và cao hơn cụm thứ hai.
        image = np.full((400, 400), 120, np.uint8)
        import cv2
        cv2.ellipse(image, (200, 200), (120, 80), 0, 0, 360, 255, -1)
        first = TextBox(220, 140, 280, 200, 1)
        second = TextBox(150, 215, 175, 250, 1)
        sfx = TextBox(40, 150, 75, 190, 1, "sfx")
        self.assertEqual(sort_reading_order([sfx, second, first], img=image), [first, second, sfx])

    def test_panels_split_by_a_slanted_gutter_read_right_panel_first(self):
        # chap 23 trang 21: khe chéo giữa hai panel dưới; ô trái cao hơn ô phải nhưng panel phải đọc trước.
        import cv2
        image = np.full((400, 400), 255, np.uint8)
        cv2.fillPoly(image, [np.array([(10, 10), (190, 10), (140, 390), (10, 390)])], 120)
        cv2.fillPoly(image, [np.array([(210, 10), (390, 10), (390, 390), (160, 390)])], 120)
        left_high = TextBox(40, 40, 80, 120, 1)
        left_low = TextBox(40, 250, 80, 330, 1)
        right = TextBox(300, 250, 340, 330, 1)
        self.assertEqual(sort_reading_order([left_high, left_low, right], img=image), [right, left_high, left_low])

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


class LineMatchingRuleTests(unittest.TestCase):
    def test_a_stepped_box_with_two_text_clusters_counts_two_blocks(self):
        page = SimpleNamespace(text_mask=np.zeros((300, 300), np.float32))
        page.text_mask[20:120, 200:230] = 1  # cụm trên phải
        page.text_mask[150:260, 60:90] = 1  # cụm dưới trái, cách xa
        self.assertEqual(text_clusters(page, Region((50, 10, 240, 270)), 30), 2)
        page.text_mask[150:260, 60:90] = 0
        page.text_mask[20:120, 160:190] = 1  # hai cột sát nhau: một cụm
        self.assertEqual(text_clusters(page, Region((50, 10, 240, 270)), 30), 1)

    def test_a_name_called_out_is_not_an_sfx_translation(self):
        words = ["Nagi", "Nagi", "Giật", "Anh", "yêu", "Nagi"]
        self.assertTrue(_just_a_name("Nagi", words))
        self.assertTrue(_just_a_name("Souta-kun…?", words))
        self.assertFalse(_just_a_name("Giật", words))
        self.assertFalse(_just_a_name("Ôm", words))


class OneColorBalloonTests(unittest.TestCase):
    def test_only_text_on_pure_white_or_black_is_erased(self):
        from app.core.clean import one_color_around
        text = np.zeros((80, 80), bool)
        text[30:50, 35:45] = True
        white = np.full((80, 80), 255, np.uint8)
        self.assertTrue(one_color_around(white, text, text))  # lòng bóng trắng
        self.assertTrue(one_color_around(np.full((80, 80), 10, np.uint8), text, text))  # ô đen
        ramp = np.tile(np.linspace(225, 255, 80).astype(np.uint8), (80, 1))
        self.assertFalse(one_color_around(ramp, text, text))  # SFX trên mảng trắng chuyển màu của tranh
        art = white.copy()
        art[:, :40] = 170
        self.assertFalse(one_color_around(art, text, text))  # bóng trong suốt, tranh xám lộ qua
        outline = white.copy()
        outline[:, 50:52] = 0
        self.assertTrue(one_color_around(outline, text, text))  # chữ sát viền đen của bóng vẫn xoá được


class ScriptTextMatchingTests(unittest.TestCase):
    """Script có cột tiếng Nhật: đọc chữ trong vùng (manga-ocr) rồi neo câu vào đúng vùng, dù thứ tự đọc khung sai."""

    def test_lines_follow_the_text_read_in_each_region(self):
        from unittest.mock import patch
        from app.core import ocr
        from app.core.docs import TYPE_TAG

        page = SimpleNamespace(size=(800, 600), original=np.full((600, 800, 3), 255, np.uint8),
                               text_mask=np.zeros((600, 800), np.float32))
        regions = [Region((600, 50, 660, 200)), Region((100, 50, 160, 200)), Region((350, 300, 420, 450))]
        read = {(600, 50, 660, 200): "輝かしい装飾品…！", (100, 50, 160, 200): "あれは全て料理屋ですか！？",
                (350, 300, 420, 450): "とっても美味しいです♪"}  # bóng cuối không có trong script
        lines = [TYPE_TAG.format("normal", "あれは全て料理屋ですか！？") + "Are all those restaurants!?",
                 TYPE_TAG.format("bold", "輝かしい装飾品…！") + "Glorious ornaments...!",
                 TYPE_TAG.format("free", "よっす") + "Yo."]  # chữ gốc nhỏ quá, model không thấy vùng
        fake = SimpleNamespace(read=lambda img, box: read[tuple(box)])
        with patch.object(ocr, "reader", lambda: fake):
            plan = assign_lines(page, regions, lines)
        placed = {tuple(r.box): [l.split("⟧")[1] for l in ls] for r, ls in plan if not r.orphan}
        self.assertEqual(placed, {(100, 50, 160, 200): ["Are all those restaurants!?"],
                                  (600, 50, 660, 200): ["Glorious ornaments...!"]})
        orphans = [ls for r, ls in plan if r.orphan]
        self.assertEqual(len(orphans), 1)  # "Yo." thành khung CHƯA ĐẶT, không nhét vào bóng không có trong script
        self.assertTrue(orphans[0][0].endswith("Yo."))

    def test_reading_is_compared_loosely(self):
        from app.core.ocr import coverage
        self.assertEqual(coverage("すみません…お待たせしてしまって…", "すみません...お待たせしてしまって..."), 1.0)
        self.assertGreaterEqual(coverage("おぉ…！", "おお"), 1.0)  # kana nhỏ = kana lớn, bỏ dấu câu
        self.assertLess(coverage("あれっ", "しゃばっ"), 0.5)
