import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtGui import QFontDatabase, QGuiApplication

from app.core.assign import apply_plan, assign_lines
from app.core.detect import TextBox, sort_reading_order
from app.core.page import MIN_READABLE, Page, Region
from app.core.typeset import TextStyle, layout

FONT = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "arial.ttf")


def _page(image):
    page = Page.__new__(Page)
    page.original = image
    page.working = image.copy()
    page.text_mask = np.zeros(image.shape[:2], np.float32)
    page.paint_mask = np.zeros(image.shape[:2], np.uint8)
    page.regions, page.typeset_profile, page.redrawn = [], "", None
    page.inpainter = None
    return page


def _size(region):
    x1, y1, x2, y2 = region.text_rect
    return layout(region.text, region.effective_style, x2 - x1, y2 - y1)[0].size


class ReadingOrderOnBlackPages(unittest.TestCase):
    def test_black_gutters_split_panels(self):
        img = np.zeros((600, 800, 3), np.uint8)  # trang nền đen, hai panel có tranh cạnh nhau
        img[20:580, 20:390] = 150
        img[20:580, 410:780] = 150
        left_high = TextBox(100, 60, 160, 200, 1.)
        right_low = TextBox(600, 300, 660, 440, 1.)
        order = sort_reading_order([left_high, right_low], True, img)
        self.assertIs(order[0], right_low)  # manga: đọc hết panel phải rồi mới sang panel trái


class LineAssignmentTests(unittest.TestCase):
    def page(self, inks):
        boxes = [(100 + 150 * i, 100, 200 + 150 * i, 300) for i in range(len(inks))]
        mask = np.zeros((600, 836), np.float32)
        for (x1, y1, x2, y2), ink in zip(boxes, inks):
            patch = np.zeros((y2 - y1, x2 - x1), np.float32)
            patch.flat[:ink] = 1
            mask[y1:y2, x1:x2] = patch
        return SimpleNamespace(size=(836, 600), original=np.full((600, 836, 3), 255, np.uint8), text_mask=mask), boxes

    def test_sfx_takes_one_line_and_the_rest_waits_as_an_orphan(self):
        page, boxes = self.page([900])
        sfx = Region(boxes[0], kind="sfx")
        plan = assign_lines(page, [sfx], ["Rầm", "Bịch"])
        placed = [ls for r, ls in plan if r is sfx]
        orphans = [ls for r, ls in plan if r.orphan]
        self.assertEqual(len(placed), 1)
        self.assertEqual(len(placed[0]) + len(orphans), 2)  # SFX nhận một câu, câu kia thành khung "chưa đặt"
        self.assertEqual([ls[0] for _, ls in plan], ["Rầm", "Bịch"])  # vẫn theo thứ tự đọc

    def test_orphans_are_inserted_after_the_previous_region(self):
        page, boxes = self.page([900])
        page.regions = [Region(boxes[0], kind="sfx")]
        page.clean = lambda r: None
        plan = assign_lines(page, page.regions, ["Rầm", "Bịch"])
        apply_plan(page, plan)
        self.assertEqual([r.text for r in page.regions], ["Rầm", "Bịch"])
        orphan = next(r for r in page.regions if r.orphan)
        self.assertTrue(orphan.cleaned)  # khung tạm: không xoá chữ / tranh bên dưới


class ReadableSizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        QFontDatabase.addApplicationFont(FONT)

    def test_long_translation_of_tiny_free_text_grows_its_box(self):
        page = _page(np.full((800, 600, 3), 160, np.uint8))  # chữ nhỏ trên nền tranh, không có bóng
        r = Region((280, 380, 300, 440), text="tôi không nghĩ là được đâu", cleaned=True)
        r.style = TextStyle(max_size=40)
        page.regions = [r]
        type(page).auto_cap = property(lambda self: 40.)
        try:
            page.clean(r)
        finally:
            del type(page).auto_cap
        self.assertGreaterEqual(_size(r), MIN_READABLE * 40)

    def test_two_lines_sharing_a_box_are_stacked_not_squeezed_into_columns(self):
        img = np.full((800, 600, 3), 120, np.uint8)
        cv2.rectangle(img, (200, 200), (400, 600), (255, 255, 255), -1)
        cv2.rectangle(img, (200, 200), (400, 600), (0, 0, 0), 3)
        page = _page(img)
        a = Region((320, 240, 360, 560), text="cô nhóc anh gặp khi đó", cleaned=True)
        b = Region((240, 240, 280, 560), text="là “sư phụ”", cleaned=True)
        page.regions = [a, b]
        type(page).auto_cap = property(lambda self: 40.)
        try:
            for r in (a, b):
                r.style = TextStyle(max_size=40)
                page.clean(r)
        finally:
            del type(page).auto_cap
        self.assertIsNotNone(a.text_shape)
        self.assertLess(a.text_rect[3], b.text_rect[1] + 5)  # câu đầu ở trên, câu sau ở dưới
        self.assertGreaterEqual(min(_size(a), _size(b)), 30)


    def test_text_on_a_dark_banner_turns_white(self):
        img = np.full((800, 600, 3), 255, np.uint8)
        img[300:420, 100:500] = 15  # băng rôn đen (bản redraw đã xoá chữ)
        page = _page(img)
        r = Region((150, 320, 450, 400), text="bảo tàng Sherlock Holmes", kind="sfx", cleaned=True)
        page.regions = [r]
        type(page).auto_cap = property(lambda self: 40.)
        try:
            page.clean(r)
        finally:
            del type(page).auto_cap
        self.assertEqual(r.style.color, "#ffffff")


class OrphanPlacementTests(unittest.TestCase):
    def test_orphans_after_the_same_region_do_not_overlap(self):
        page, boxes = LineAssignmentTests.page(None, [900])
        sfx = Region(boxes[0], kind="sfx")
        plan = assign_lines(page, [sfx], ["Rầm", "tuyệt quá", "sờ mó"])
        orphans = [r.box for r, _ in plan if r.orphan]
        self.assertEqual(len(orphans), 2)
        a, b = orphans
        self.assertFalse(a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


if __name__ == "__main__":
    unittest.main()
