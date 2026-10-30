import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import cv2
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QFontDatabase, QGuiApplication, QImage, QPainter

from app.core import project
from app.core.clean import text_area
from app.core.page import Page, Region
from app.core.typeset import TextStyle, draw_text, layout, place_in_shape
from app.core.vi_words import joined

NINE = "Cứ như này thì mình sẽ mất kiểm soát mất…"
FONT = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", "arial.ttf")


def _app():
    app = QGuiApplication.instance() or QGuiApplication([])
    QFontDatabase.addApplicationFont(FONT)  # Qt offscreen không thấy font Windows: nạp thẳng Arial
    return app


def _widths(lines, m):
    return [m.adv(ln) for ln, _, _ in lines]


def _bubble(w=800, h=700, center=(200, 180), axes=(110, 150), tail=True):
    img = np.full((h, w, 3), 255, np.uint8)
    img[:] = 120  # nền tranh xám
    cv2.ellipse(img, center, axes, 0, 0, 360, (255, 255, 255), -1)
    if tail:  # đuôi bóng chĩa xuống phải
        cv2.fillPoly(img, [np.array([(center[0] + 40, center[1] + 120), (center[0] + 120, h - 5),
                                     (center[0] + 70, center[1] + 110)], np.int32)], (255, 255, 255))
    cv2.ellipse(img, center, axes, 0, 0, 360, (0, 0, 0), 3)
    return img


class LineShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def test_round_shape_puts_long_lines_in_the_middle(self):
        m, lines = layout(NINE, TextStyle(max_size=20), 260, 260)
        widths = _widths(lines, m)
        self.assertGreaterEqual(len(lines), 4)
        mid = len(widths) // 2
        self.assertLess(widths[0], max(widths) * .8)  # dòng đầu, dòng cuối ngắn
        self.assertLess(widths[-1], max(widths) * .8)
        self.assertGreaterEqual(max(widths[mid - 1:mid + 2]), max(widths))  # dòng dài nhất ở giữa

    def test_fill_shape_keeps_greedy_wrapping(self):
        m, lines = layout(NINE, TextStyle(max_size=20, shape=""), 260, 260)
        self.assertEqual(len(lines), 2)  # lấp đầy: ít dòng nhất

    def test_even_shape_balances_lines(self):
        m, lines = layout(NINE, TextStyle(max_size=20, shape="even"), 260, 260)
        widths = _widths(lines, m)
        self.assertGreater(min(widths), max(widths) * .45)  # không có dòng 1 chữ như dáng bo tròn

    def test_compound_words_are_not_split(self):
        text = "Em nhất định sẽ trân trọng nó."
        for w, h in ((200, 200), (150, 260), (280, 170)):
            _, lines = layout(text, TextStyle(max_size=26), w, h)
            ends = [ln.split()[-1] + " " + nxt.split()[0] for (ln, _, _), (nxt, _, _) in zip(lines, lines[1:])]
            self.assertFalse([e for e in ends if joined(*e.split())], (w, h, lines))

    def test_manual_line_breaks_are_kept(self):
        _, lines = layout("Cảm ơn\nanh rất nhiều.", TextStyle(max_size=20), 300, 300)
        self.assertEqual(lines[0][0], "Cảm ơn")


class BubbleShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def test_bubble_center_ignores_the_tail(self):
        cands, shape = text_area(_bubble(), (180, 150, 220, 210))
        self.assertTrue(cands)
        cx, cy, rows = shape
        self.assertLess(abs(cx - 200), 6)
        self.assertLess(abs(cy - 180), 8)
        widest = max(r - l for _, l, r in rows)
        self.assertGreater(widest, 190)  # ~ bề ngang lòng bóng (220) trừ lề

    def test_text_follows_the_bubble_and_sits_in_its_center(self):
        _, shape = text_area(_bubble(tail=False), (180, 150, 220, 210))
        cx, cy, rows = shape
        style = TextStyle(max_size=24)
        rect = place_in_shape(NINE, style, shape)
        self.assertIsNotNone(rect)
        x1, y1, x2, y2 = rect
        # Căn theo nét chữ thật: khoảng hở tới mép bóng (elip tâm 200,180) bằng nhau trên/dưới, trái/phải.
        img = QImage(800, 700, QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        draw_text(p, NINE, style, QRectF(x1, y1, x2 - x1, y2 - y1))
        p.end()
        alpha = np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(700, img.bytesPerLine())[:, :3200]
        ys, xs = np.nonzero(alpha.reshape(700, 800, 4)[..., 3] > 60)
        dx, dy = (xs - 200) / 110, (ys - 180) / 150
        top = (ys - (180 - 150 * np.sqrt(1 - dx ** 2))).min()
        bottom = ((180 + 150 * np.sqrt(1 - dx ** 2)) - ys).min()
        left = (xs - (200 - 110 * np.sqrt(1 - dy ** 2))).min()
        right = ((200 + 110 * np.sqrt(1 - dy ** 2)) - xs).min()
        self.assertLess(abs(top - bottom), 4, (top, bottom))
        self.assertLess(abs(left - right), 4, (left, right))
        # Mọi nơi dàn lại theo khung này (vẽ, xuất PSD/Photoshop) đều ra đúng bố cục theo dáng bóng.
        m, lines = layout(NINE, style, x2 - x1, y2 - y1)
        half = {y: min(cx - l, r - cx) for y, l, r in rows}
        top = (y1 + y2) / 2 - len(lines) * m.lh / 2
        for i, w in enumerate(_widths(lines, m)):
            band = [half.get(int(top + (i + f) * m.lh), 0) for f in (.15, .5, .85)]
            self.assertLessEqual(w / 2, min(band) + 4, (i, lines))
        widths = _widths(lines, m)
        self.assertLess(widths[0], max(widths))
        self.assertLess(widths[-1], max(widths))

    def test_short_text_grows_above_the_page_size_in_a_big_bubble(self):
        _, shape = text_area(_bubble(tail=False), (180, 150, 220, 210))
        style = TextStyle(max_size=20)
        x1, y1, x2, y2 = place_in_shape("Ừ.", style, shape)
        size = layout("Ừ.", style, x2 - x1, y2 - y1)[0].size
        self.assertGreater(size, 20)  # "Ừ." không lọt thỏm giữa bóng to…
        self.assertLessEqual(size, 30)  # …nhưng không to quá 1,5 lần cỡ chuẩn

    def test_narrow_bubble_keeps_a_readable_size(self):
        img = _bubble(axes=(55, 80), tail=False)
        _, shape = text_area(img, (185, 160, 215, 200))
        style = TextStyle(max_size=20)
        text = "Em nhất định sẽ trân trọng nó."
        x1, y1, x2, y2 = place_in_shape(text, style, shape)
        self.assertGreaterEqual(layout(text, style, x2 - x1, y2 - y1)[0].size, 15)

    def test_round_block_is_balanced_top_and_bottom(self):
        _, shape = text_area(_bubble(tail=False), (180, 150, 220, 210))
        style = TextStyle(max_size=24)
        x1, y1, x2, y2 = place_in_shape(NINE, style, shape)
        m, lines = layout(NINE, style, x2 - x1, y2 - y1)
        widths = _widths(lines, m)
        self.assertLess(abs(widths[0] - widths[-1]), max(widths) * .35, lines)

    def test_shape_layout_is_bigger_than_a_rectangle_in_an_oval(self):
        page = Page.__new__(Page)
        page.original = _bubble(tail=False)
        page.working = page.original.copy()
        page.text_mask = np.zeros(page.original.shape[:2], np.float32)
        page.regions = []
        page.typeset_profile = ""
        r = Region((180, 150, 220, 210), text=NINE, cleaned=True)
        page.regions.append(r)
        page.clean(r)
        self.assertIsNotNone(r.text_shape)
        x1, y1, x2, y2 = r.text_rect
        shaped = layout(r.text, r.effective_style, x2 - x1, y2 - y1)[0].size
        shape, r.text_shape = r.text_shape, None
        x1, y1, x2, y2 = r.text_rect
        boxed = layout(r.text, r.effective_style, x2 - x1, y2 - y1)[0].size
        self.assertGreaterEqual(shaped, boxed)
        r.text_shape = shape
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "p.lzp")
            cv2.imencode(".png", page.original)[1].tofile(os.path.join(folder, "p.png"))
            page.path = os.path.join(folder, "p.png")
            page.redrawn, page.paint_mask, page.detected = None, np.zeros(page.original.shape[:2], np.uint8), True
            project.save(path, [page])
            loaded = project.load(path)["pages"][0]
            self.assertEqual(loaded.regions[0].text_shape, shape)
            self.assertEqual(loaded.regions[0].text_rect, r.text_rect)


if __name__ == "__main__":
    unittest.main()
