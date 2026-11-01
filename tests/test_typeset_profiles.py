import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.core.typeset_profiles import (CUSTOM, FONTS, GAI_BANG, ISSHIKI, ISSHIKI_FONTS, balloon_shape, classify_balloon, flustered,
                                      lettering_features, profile_style, region_kind)
from app.core.page import Region
from app.core.typeset import TextStyle


class BalloonTests(unittest.TestCase):
    box = (255, 285, 385, 355)

    @classmethod
    def balloon(cls, kind):
        image = np.full((640, 640, 3), 225, np.uint8)
        if kind == "normal":
            cv2.ellipse(image, (320, 320), (160, 120), 0, 0, 360, (255, 255, 255), -1)
            cv2.ellipse(image, (320, 320), (160, 120), 0, 0, 360, (0, 0, 0), 3)
        elif kind == "square":
            cv2.rectangle(image, (150, 190), (490, 450), (255, 255, 255), -1)
            cv2.rectangle(image, (150, 190), (490, 450), (0, 0, 0), 3)
        elif kind == "cloud":
            angles = np.linspace(0, 2 * np.pi, 360, endpoint=False)
            radius = 1 + .10 * np.cos(12 * angles)
            points = np.column_stack([320 + 160 * radius * np.cos(angles), 320 + 120 * radius * np.sin(angles)]).astype(np.int32)
            cv2.fillPoly(image, [points], (255, 255, 255))
            cv2.polylines(image, [points], True, (0, 0, 0), 3)
        elif kind == "rays":
            for angle in np.linspace(0, 2 * np.pi, 24, endpoint=False):
                a = (int(320 + 145 * np.cos(angle)), int(320 + 100 * np.sin(angle)))
                b = (int(320 + 200 * np.cos(angle)), int(320 + 160 * np.sin(angle)))
                cv2.line(image, a, b, (0, 0, 0), 3)
        cv2.putText(image, "TEXT", (260, 330), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 3)
        return image

    def test_round_square_cloud_and_open_rays(self):
        for shape, expected in (("normal", "normal"), ("square", "square"), ("cloud", "thought"), ("rays", "thought")):
            with self.subTest(shape=shape):
                self.assertEqual(classify_balloon(self.balloon(shape), self.box), expected)

    def test_input_unchanged_and_blank_defaults_to_dialogue(self):
        image = self.balloon("normal")
        original = image.copy()
        classify_balloon(image, self.box)
        np.testing.assert_array_equal(image, original)
        self.assertEqual(classify_balloon(np.full_like(image, 255), self.box), "normal")

    def test_profile_fonts_and_italic_are_independent_per_region(self):
        class Page:
            typeset_profile = GAI_BANG
            original = BalloonTests.balloon("normal")
        base = TextStyle(family="Arial", bold=True, italic=True, size=28, color="#123456", stroke_width=2)
        for kind, family in FONTS.items():
            region = Region(self.box, typeset_kind=kind)
            style = profile_style(Page(), region, base)
            self.assertEqual(style.family, family)
            self.assertEqual(style.italic, kind == "thought")
            self.assertFalse(style.bold)
            self.assertEqual(style.size, 28)
            self.assertEqual(style.color, base.color)
            self.assertEqual(style.stroke_width, 2)
        self.assertEqual(base.family, "Arial")

    def test_sfx_and_user_override_take_priority_over_shape(self):
        class Page:
            original = BalloonTests.balloon("square")
        region = Region(self.box, kind="sfx")
        self.assertEqual(region_kind(Page(), region), "sfx")
        region.typeset_kind = "thought"
        self.assertEqual(region_kind(Page(), region), "thought")

    def test_scaled_and_rotated_balloons_and_invalid_boxes(self):
        image = self.balloon("square")
        matrix = cv2.getRotationMatrix2D((320, 320), 17, 1)
        rotated = cv2.warpAffine(image, matrix, (640, 640), borderValue=(225, 225, 225))
        self.assertEqual(classify_balloon(rotated, self.box), "square")
        enlarged = cv2.resize(self.balloon("normal"), (2560, 2560))
        self.assertEqual(classify_balloon(enlarged, tuple(v * 4 for v in self.box)), "normal")
        self.assertEqual(classify_balloon(image, (30, 30, 30, 40)), "normal")

    def test_shape_is_cached_between_keystrokes_and_overrides_skip_classification(self):
        class Page:
            original = BalloonTests.balloon("cloud")
        page, region = Page(), Region(self.box)
        with patch("app.core.typeset_profiles.classify_balloon", wraps=classify_balloon) as classify:
            for _ in range(10):
                self.assertEqual(region_kind(page, region), "thought")
            self.assertEqual(classify.call_count, 1)
            region.typeset_kind = "square"
            self.assertEqual(region_kind(page, region), "square")
            self.assertEqual(classify.call_count, 1)


class IsshikiTests(unittest.TestCase):
    box = BalloonTests.box

    @staticmethod
    def page(image, thickness=1):
        """Ảnh bóng thoại + mask chữ như detector trả về (vùng quanh nét chữ)."""
        image = image.copy()
        cv2.rectangle(image, (250, 295), (390, 345), (255, 255, 255), -1)
        text = np.full_like(image, 255)
        cv2.putText(text, "TEXT", (260, 330), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 1)
        text = cv2.erode(text, np.ones((thickness, thickness), np.uint8))  # nét dày (OpenCV 5 giới hạn thickness)
        image = np.minimum(image, text)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        mask = cv2.dilate((gray < 110).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32)
        mask[:, :250] = mask[:, 390:] = 0
        mask[:280] = mask[360:] = 0

        class Page:
            typeset_profile = ISSHIKI
            original = image
            text_mask = mask
        return Page()

    def kind(self, page, text=""):
        return region_kind(page, Region(self.box, text=text))

    def test_every_kind_gets_its_font_and_thoughts_are_italic(self):
        page = self.page(BalloonTests.balloon("normal"))
        for kind, family in ISSHIKI_FONTS.items():
            style = profile_style(page, Region(self.box, typeset_kind=kind), TextStyle(family="Arial", bold=True))
            self.assertEqual(style.family, family)
            self.assertEqual(style.italic, kind in ("thought", "square"))
            self.assertFalse(style.bold)
        self.assertEqual(ISSHIKI_FONTS["normal"], "SJ Chanighter Handwriting")
        self.assertEqual(ISSHIKI_FONTS["bold"], "000 ObelixPro TB")
        self.assertEqual(ISSHIKI_FONTS["free"], "SJ SS Rosehip")
        self.assertEqual(ISSHIKI_FONTS["flustered"], "SJ CCComicraft")

    def test_balloon_shapes(self):
        # Tia quanh chữ mà lòng bóng hở: ở bộ này thường là bóng thoại hở viền, không phải suy nghĩ.
        for shape, expected in (("normal", "normal"), ("square", "square"), ("cloud", "thought"), ("rays", "normal")):
            with self.subTest(shape=shape):
                self.assertEqual(self.kind(self.page(BalloonTests.balloon(shape))), expected)

    @staticmethod
    def outline(points_list):
        image = np.full((640, 640, 3), 225, np.uint8)
        for pts in points_list:
            cv2.polylines(image, [pts], True, (0, 0, 0), 3)
        for pts in points_list:
            cv2.fillPoly(image, [pts], (255, 255, 255))  # bỏ nét viền bên trong chỗ hai bóng chồng nhau
        for pts in points_list:
            cv2.polylines(image, [pts], True, (0, 0, 0), 3)
        for pts in points_list:
            inner = (pts - pts.mean(0)) * .96 + pts.mean(0)
            cv2.fillPoly(image, [inner.astype(np.int32)], (255, 255, 255))
        return image

    @staticmethod
    def ellipse(cx, cy, ax, ay, wave=0., lobes=0):
        t = np.linspace(0, 2 * np.pi, 360, endpoint=False)
        r = 1 + wave * np.cos(lobes * t)
        return np.column_stack([cx + ax * r * np.cos(t), cy + ay * r * np.sin(t)]).astype(np.int32)

    def test_two_touching_balloons_are_dialogue_not_a_thought_cloud(self):
        merged = self.outline([self.ellipse(320, 320, 110, 90, .03, 20), self.ellipse(480, 420, 110, 90, .03, 20)])
        self.assertEqual(classify_balloon(merged, self.box), "thought")  # Gái Băng giữ luật cũ (nhiều chỗ lõm)
        self.assertEqual(self.kind(self.page(merged)), "normal")

    def test_trembling_outline_is_dialogue(self):
        wobbly = self.outline([self.ellipse(320, 320, 160, 120, .015, 40)])
        self.assertEqual(balloon_shape(wobbly, self.box, outline=True), "wobbly")
        self.assertEqual(self.kind(self.page(wobbly)), "normal")

    def test_lettering_without_a_balloon_is_a_free_thought(self):
        art = np.full((640, 640, 3), 225, np.uint8)
        self.assertEqual(balloon_shape(art, self.box), "free")
        self.assertEqual(classify_balloon(art, self.box), "normal")  # Gái Băng: vẫn là thoại thường
        self.assertEqual(self.kind(self.page(art)), "free")

    def test_bold_raw_lettering_uses_the_bold_font(self):
        thin = self.page(BalloonTests.balloon("normal"), 1)
        thick = self.page(BalloonTests.balloon("normal"), 5)
        f_thin = lettering_features(thin.original, thin.text_mask, self.box)
        f_thick = lettering_features(thick.original, thick.text_mask, self.box)
        self.assertGreater(f_thick["stroke"], f_thin["stroke"] * 1.5)
        self.assertEqual(self.kind(thin), "normal")
        self.assertEqual(self.kind(thick), "bold")

    def test_white_text_in_a_black_box_is_a_square_thought(self):
        image = np.full((640, 640, 3), 225, np.uint8)
        cv2.rectangle(image, (200, 250), (440, 390), (0, 0, 0), -1)
        cv2.putText(image, "TEXT", (260, 330), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        mask = np.zeros(image.shape[:2], np.float32)
        mask[300:340, 258:382] = 1

        class Page:
            typeset_profile = ISSHIKI
            original = image
            text_mask = mask
        self.assertEqual(self.kind(Page()), "square")

    def test_stammering_lines_are_flustered(self):
        for text in ("e- em ổn", "a- anh xin lỗi!", "Ri- Rinna..", "đ– đừng mà"):
            self.assertTrue(flustered(text), text)
        for text in ("Ri-", "e-mail của em", "khoan! không phải vì em sợ", "đừng-"):
            self.assertFalse(flustered(text), text)
        page = self.page(BalloonTests.balloon("normal"))
        self.assertEqual(self.kind(page, "e- em ổn"), "flustered")
        self.assertEqual(self.kind(page, "em ổn"), "normal")
        region = Region(self.box, text="e- em ổn", typeset_kind="normal")
        self.assertEqual(region_kind(page, region), "normal")  # người dùng chọn tay vẫn ưu tiên

    def test_gai_bang_ignores_the_isshiki_rules(self):
        page = self.page(BalloonTests.balloon("normal"), 5)
        page.typeset_profile = GAI_BANG
        self.assertEqual(self.kind(page, "e- em ổn"), "normal")


class CustomFontTests(unittest.TestCase):
    def test_user_fonts_per_balloon_kind(self):
        page = IsshikiTests.page(BalloonTests.balloon("normal"))
        page.typeset_profile = CUSTOM
        page.custom_fonts = {"normal": {"family": "Comic Sans MS", "style": "Bold", "italic": True},
                             "sfx": {"family": "Impact", "style": "Regular"}}
        style = profile_style(page, Region(IsshikiTests.box), TextStyle(family="Arial"))
        self.assertEqual((style.family, style.style_name, style.italic), ("Comic Sans MS", "Bold", True))
        sfx = profile_style(page, Region(IsshikiTests.box, kind="sfx"), TextStyle(family="Arial"))
        self.assertEqual((sfx.family, sfx.style_name, sfx.italic), ("Impact", "", False))
        # loại chưa chọn font: dùng font mặc định
        thought = profile_style(page, Region(IsshikiTests.box, typeset_kind="thought"), TextStyle(family="Arial"))
        self.assertEqual(thought.family, FONTS["thought"])


if __name__ == "__main__":
    unittest.main()
