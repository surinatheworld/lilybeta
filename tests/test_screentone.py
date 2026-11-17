"""Verify phase restoration and the unchanged-pixel guarantee on unsafe fills."""
import unittest

import cv2
import numpy as np

from app.core.clean import clean_local, screentone_only
from app.core.screentone import restore_screentone


class ScreentoneTests(unittest.TestCase):
    def setUp(self):
        self.y, self.x = np.indices((180, 220))
        self.mask = np.zeros(self.x.shape, np.uint8)
        cv2.putText(self.mask, "SFX", (67, 99), cv2.FONT_HERSHEY_SIMPLEX, 1.3, 255, 6)

    def image(self, dot):
        return cv2.cvtColor(np.where(dot, 30, 248).astype(np.uint8), cv2.COLOR_GRAY2BGR)

    def test_restores_dot_phase_with_horizontal_and_diagonal_periods(self):
        x, y = self.x, self.y
        for dot in ((x % 8 - 4) ** 2 + (y % 8 - 4) ** 2 <= 5,
                    ((x + y) % 12 - 6) ** 2 + ((x - y) % 12 - 6) ** 2 <= 10):
            with self.subTest():
                original = self.image(dot)
                page = original.copy()
                page[self.mask > 0] = 0
                self.assertTrue(restore_screentone(page, self.mask))
                np.testing.assert_array_equal(page, original)

    def test_rejects_mask_crossing_two_different_backgrounds(self):
        page = self.image((self.x % 8 == 4) & (self.y % 8 == 4))
        page[:, 106:] = 180
        page[self.mask > 0] = 0
        before = page.copy()
        self.assertFalse(restore_screentone(page, self.mask))
        np.testing.assert_array_equal(page, before)

    def test_recovers_resampled_screentone_with_fractional_spacing(self):
        y, x = np.indices((260, 320))
        dots = np.where((x % 11 - 5) ** 2 + (y % 11 - 5) ** 2 <= 7, 20, 250).astype(np.uint8)
        original = cv2.cvtColor(cv2.resize(dots, (259, 224), interpolation=cv2.INTER_AREA), cv2.COLOR_GRAY2BGR)
        mask = np.zeros(original.shape[:2], np.uint8)
        cv2.putText(mask, "FX", (53, 125), cv2.FONT_HERSHEY_SIMPLEX, 1.7, 255, 8)
        page = original.copy()
        page[mask > 0] = 0
        smooth = cv2.inpaint(page, mask, 5, cv2.INPAINT_TELEA)

        self.assertTrue(restore_screentone(page, mask))

        error = lambda image: np.abs(image[mask > 0].astype(float) - original[mask > 0]).mean()
        self.assertLess(error(page), 16)
        self.assertLess(error(page), error(smooth) / 3)
        np.testing.assert_array_equal(page[mask == 0], original[mask == 0])

        # Leave the cutout alone if a clearly visible ink line crosses it.
        cv2.line(original, (60, 50), (105, 172), (0, 0, 0), 5)
        original[mask > 0] = 0
        before = original.copy()
        self.assertFalse(restore_screentone(original, mask))
        np.testing.assert_array_equal(original, before)

    def test_clean_local_uses_screentone_for_sfx_and_leaves_unsupported_art_pending(self):
        box = (55, 57, 160, 109)
        mask = self.mask.astype(np.float32) / 255
        original = self.image((self.x % 8 == 4) & (self.y % 8 == 4))
        page = original.copy()
        page[self.mask > 0] = 0
        self.assertIsNone(clean_local(page, mask, box, inpainter=screentone_only, sfx=True))
        np.testing.assert_array_equal(page, original)

        page = np.random.default_rng(7).integers(0, 256, page.shape, dtype=np.uint8)
        before = page.copy()
        self.assertIsNotNone(clean_local(page, mask, box, inpainter=screentone_only, sfx=True))
        np.testing.assert_array_equal(page, before)

    def test_sfx_on_solid_panel_clears_the_lettering_without_changing_art(self):
        box = (55, 57, 160, 109)
        original = np.full((*self.x.shape, 3), (125, 123, 119), np.uint8)
        cv2.line(original, (170, 56), (198, 132), (0, 0, 0), 4)
        page = original.copy()
        page[self.mask > 0] = 255

        self.assertIsNone(clean_local(page, self.mask.astype(np.float32) / 255, box,
                                     inpainter=screentone_only, sfx=True))
        np.testing.assert_array_equal(page, original)
