import math
import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtGui import QFontDatabase, QGuiApplication

from app.core.assign import apply_plan, assign_lines
from app.core.clean import lobe_shape
from app.core.detect import TextBox, sort_reading_order
from app.core.page import MIN_READABLE, Page, Region, _lobe_center
from app.core.typeset import TextStyle, block_box, layout

FONT = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "arial.ttf")
_CAP = Page.auto_cap


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
            type(page).auto_cap = _CAP
        self.assertGreaterEqual(_size(r), MIN_READABLE * 40)

    def test_joined_balloons_give_each_line_its_own_lobe(self):
        img = np.full((700, 900, 3), 120, np.uint8)
        for c in ((280, 330), (600, 330)):  # hai bóng tròn dính nhau qua một chỗ thắt hẹp
            cv2.circle(img, c, 150, (255, 255, 255), -1)
        cv2.rectangle(img, (400, 305), (480, 355), (255, 255, 255), -1)
        page = _page(img)
        a = Region((560, 260, 640, 400), text="Cậu đến muộn quá đấy, tớ đợi mãi.", cleaned=True)
        b = Region((240, 260, 320, 400), text="Xin lỗi nhé, tàu điện bị trễ mất rồi.", cleaned=True)
        page.regions = [a, b]
        type(page).auto_cap = property(lambda self: 40.)
        try:
            for r in (a, b):
                r.style = TextStyle(max_size=40)
                page.clean(r)
        finally:
            type(page).auto_cap = _CAP
        for r, (lo, hi) in ((a, (440, 760)), (b, (120, 440))):
            self.assertIsNotNone(r.text_shape)
            x1, _, x2, _ = r.text_rect
            self.assertTrue(lo - 5 <= x1 and x2 <= hi + 5, (r.text, r.text_rect))  # chữ nằm gọn trong bóng của nó
            self.assertGreaterEqual(_size(r), 30)

    def test_tight_shared_balloons_keep_big_text(self):
        long = Region((0, 0, 10, 10), text="nãy giờ ông cứ ngó qua ngó lại", style=TextStyle(fit_scale=.88), tight=True)
        self.assertEqual(long.effective_style.fit_scale, 1.0)  # chỗ chật: không thu cỡ thêm
        self.assertTrue(long.effective_style.tight)
        short = Region((0, 0, 10, 10), text="uầy~", style=TextStyle(fit_scale=.88), tight=True)
        self.assertFalse(short.effective_style.tight)  # câu ngắn thì không cần
        text = "rồi đi lang thang khắp nơi suốt cả buổi chiều nay"
        big, _ = layout(text, TextStyle(max_size=60, tight=True), 120, 400)
        small, _ = layout(text, TextStyle(max_size=60), 120, 400)
        self.assertGreaterEqual(big.size, small.size)  # chữ to, dòng ngắn hơn là chữ bé dòng đều

    def test_text_centers_on_the_lobe_not_on_its_tail(self):
        # Thuỳ tròn tâm (300, 300) bán kính 150, kèm một đuôi dài chĩa xuống (chỗ nối sang thuỳ kia).
        mask = np.zeros((900, 600), np.uint8)
        cv2.circle(mask, (300, 300), 150, 255, -1)
        mask[400:850, 380:440] = 255
        rows = []
        for y in range(mask.shape[0]):
            xs = np.flatnonzero(mask[y])
            if len(xs):
                rows.append((y, int(xs[0]), int(xs[-1]) + 1))
        x, y, radius = _lobe_center(rows)
        self.assertLess(math.hypot(x - 300, y - 300), 10)  # giữa thuỳ tròn, không bị dải thừa kéo lệch
        self.assertGreater(radius, 140)
        self.assertGreater((rows[0][0] + rows[-1][0]) / 2, 400)  # cách cũ (giữa hàng trên – hàng dưới) bị đuôi kéo lệch

    def test_dialogue_on_a_page_shares_one_size(self):
        img = np.full((1400, 1400, 3), 120, np.uint8)
        centers = ((250, 300, 120), (650, 300, 120), (1050, 300, 120), (700, 950, 300))  # bóng cuối to gấp đôi
        text = "Hôm nay trời đẹp quá nên mình đi dạo một chút nhé"
        page = _page(img)
        for x, y, r in centers:
            cv2.circle(img, (x, y), r, (255, 255, 255), -1)
            page.regions.append(Region((x - 30, y - 60, x + 30, y + 60), text=text, cleaned=True))
        page.working = img.copy()
        type(page).auto_cap = property(lambda self: 80.)
        try:
            page.typeset_profile = ""
            page._balloon_cache = {(r.uid, "kind"): "normal" for r in page.regions}
            import app.core.typeset_profiles as tp
            kind = tp.region_kind
            tp.region_kind = lambda page, r: "normal"
            try:
                page.clean_many(page.regions)
            finally:
                tp.region_kind = kind
            sizes = [_size(r) for r in page.regions]
        finally:
            type(page).auto_cap = _CAP
        self.assertLessEqual(sizes[3], 1.1 * sorted(sizes)[1] + 1, sizes)  # bóng to: lề rộng, không phóng chữ theo bóng

    def test_staggered_lines_in_one_balloon_do_not_touch(self):
        img = np.full((1400, 1400, 3), 120, np.uint8)
        cv2.ellipse(img, (480, 300), (190, 230), 0, 0, 360, (255, 255, 255), -1)  # thuỳ trên phải
        cv2.ellipse(img, (300, 480), (190, 230), 0, 0, 360, (255, 255, 255), -1)  # thuỳ dưới trái, dính liền
        page = _page(img)
        a = Region((500, 150, 560, 400), text="nãy giờ ông cứ ngó qua ngó lại rồi đi lang thang khắp nơi", cleaned=True)
        b = Region((250, 420, 310, 640), text="vậy nên, rõ ràng ông là du khách Nhật", cleaned=True)
        page.regions = [a, b]
        type(page).auto_cap = property(lambda self: 40.)
        try:
            for r in (a, b):
                r.style = TextStyle(max_size=40)
                page.clean(r)
            boxes = [block_box(r.text, r.effective_style, r.text_rect) for r in (a, b)]
        finally:
            type(page).auto_cap = _CAP
        (*_, sa, la), (*_, sb, lb) = boxes
        gap = 0.4 * 40 - 1
        for p in la:  # không dòng nào của câu này chạm dòng của câu kia
            for q in lb:
                self.assertTrue(q[2] + gap <= p[0] or p[2] + gap <= q[0] or p[3] + gap <= q[1] or q[3] + gap <= p[1],
                                (p, q))
        self.assertLess(boxes[0][1], boxes[1][1])  # bậc thang: câu trước cao hơn câu sau
        self.assertLessEqual(abs(sa - sb), 0.15 * max(sa, sb), boxes)  # cỡ chữ hai câu gần bằng nhau

    def test_a_big_middle_lobe_does_not_swallow_a_small_end_lobe(self):
        img = np.full((600, 1100, 3), 120, np.uint8)
        for c, r in (((120, 300), 60), ((400, 300), 150), ((800, 300), 150)):
            cv2.circle(img, c, r, (255, 255, 255), -1)
        cv2.rectangle(img, (170, 290), (260, 310), (255, 255, 255), -1)  # chỗ thắt mảnh A–B
        cv2.rectangle(img, (540, 230), (660, 370), (255, 255, 255), -1)  # chỗ thắt dày hơn B–C
        a, b, c = (95, 255, 145, 345), (370, 250, 430, 350), (770, 250, 830, 350)
        mid = lobe_shape(img, b, [a, c])
        self.assertIsNotNone(mid)
        self.assertGreater(min(r[1] for r in mid[2]), 180)  # thuỳ giữa không lấn sang bóng nhỏ bên trái
        self.assertLess(max(r[2] for r in mid[2]), 700)
        small = lobe_shape(img, a, [b, c])
        self.assertIsNotNone(small)
        self.assertLess(max(r[2] for r in small[2]), 260)

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
            type(page).auto_cap = _CAP
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
            type(page).auto_cap = _CAP
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
