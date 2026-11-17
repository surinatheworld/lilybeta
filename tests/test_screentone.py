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
