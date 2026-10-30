import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.core.typeset_profiles import FONTS, GAI_BANG, classify_balloon, profile_style, region_kind
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


if __name__ == "__main__":
    unittest.main()
