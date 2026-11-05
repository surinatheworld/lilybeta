import unittest

import cv2
import numpy as np

from app.core.balloons import refine, _partition_lettering
from app.core.detect import TextBox
from tests.test_balloons import fixture, open_fixture


def spanning(mask):
    ys, xs = np.nonzero(mask)
    return TextBox(int(xs.min())-2, int(ys.min())-2,
                   int(xs.max())+3, int(ys.max())+3, .9)


def covered(mask, boxes):
    result = np.zeros(mask.shape, bool)
    for b in boxes:
        result[b.y1:b.y2, b.x1:b.x2] = True
    return not np.any((mask > 0) & ~result)


class BalloonEdgeTests(unittest.TestCase):
    def test_closed_panel_around_an_open_balloon_does_not_hide_the_missing_arc(self):
        image, mask, boxes = open_fixture()
        image[np.all(image == 220, axis=2)] = 255
        cv2.rectangle(image, (50, 40), (650, 580), (0, 0, 0), 3)
        cv2.rectangle(image, (500, 400), (600, 540), (0, 0, 0), -1)
        got, _ = refine(image, mask, boxes)
        self.assertEqual(len(got), 2)
        self.assertTrue(covered(mask, got))

    def test_downscaling_does_not_erase_a_small_low_probability_stroke(self):
        image, mask, _ = fixture([(160, 230), (325, 230)])
        image = cv2.resize(image, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        mask = cv2.resize(mask, (image.shape[1], image.shape[0]), interpolation=cv2.INTER_NEAREST)
        mask[690, 350] = .31
        image[690, 350] = 0
        original = mask.copy()
        got, _ = refine(image, mask, [spanning(mask)])
        self.assertEqual(len(got), 2)
        self.assertTrue(covered(mask, got))
        np.testing.assert_array_equal(mask, original)

    def test_short_interjection_is_not_discarded_beside_a_long_paragraph(self):
        image, mask, _ = fixture([(160, 230), (325, 230)])
        image[mask > 0] = 255
        mask[:] = 0
        for y in range(165, 290, 20):
            for x in range(125, 195, 16):
                mask[y:y+14, x:x+9] = 1
        mask[224:238, 320:329] = 1  # 2.8% of the parent's lettering
        image[mask > 0] = 0
        for rotation in range(4):
            for scale in (.7, 1., 2.6):
                with self.subTest(rotation=rotation, scale=scale):
                    im = cv2.resize(np.rot90(image, rotation), None, fx=scale, fy=scale)
                    pred = cv2.resize(np.rot90(mask, rotation), (im.shape[1], im.shape[0]),
                                      interpolation=cv2.INTER_NEAREST)
                    original = im.copy()
                    boxes, meta = refine(im, pred, [spanning(pred)])
                    self.assertEqual(len(boxes), 2)
                    self.assertTrue(covered(pred, boxes))
                    self.assertTrue(all(m['parts'] == 2 for m in meta.values()))
                    np.testing.assert_array_equal(im, original)

    def test_broad_diagonal_neck_splits_but_single_oval_columns_stay_together(self):
        image, mask, boxes = fixture([(190, 240), (330, 270)], [(100, 140)]*2, True)
        for rotation in range(4):
            im, pred = np.rot90(image, rotation).copy(), np.rot90(mask, rotation).copy()
            got, _ = refine(im, pred, [spanning(pred)])
            self.assertEqual(len(got), 2)
            self.assertTrue(covered(pred, got))
        image, mask, boxes = fixture([(280, 280)], [(180, 220)])
        got, _ = refine(image, mask, boxes)
        self.assertEqual(got, boxes)

    def test_word_near_a_geometric_divider_keeps_its_edge_stroke(self):
        ink = np.zeros((130, 130), np.uint8)
        ink[12:35, 24:40] = 1
        ink[65:90, 76:92] = 1
        ink[58:72, 76:87] = 1  # same lower word, crossing the proposed divider
        owners = np.ones(ink.shape, np.int32)
        owners[60:] = 2
        parts = _partition_lettering(ink, owners, [1, 2])
        self.assertIsNotNone(parts)
        assigned = dict(parts)
        self.assertTrue(np.all(assigned[2][58:72, 76:87]))
        self.assertTrue(np.array_equal(assigned[1] | assigned[2], ink > 0))
        # A single continuous stroke equally spans the two lobes: keep the box.
        ink[30:90, 112:118] = 1
        self.assertIsNone(_partition_lettering(ink, owners, [1, 2]))

    def test_short_front_balloon_separated_by_a_visible_arc_is_preserved(self):
        image, mask, _ = fixture([(210, 240), (340, 280)])
        cv2.ellipse(image, (340, 280), (100, 110), 0, 0, 360, (0, 0, 0), 3)
        image[mask > 0] = 255
        mask[:] = 0
        for y in range(170, 290, 18):
            mask[y:y+12, 175:200] = 1
        mask[274:286, 336:344] = 1
        image[mask > 0] = 0
        got, _ = refine(image, mask, [spanning(mask)])
        self.assertEqual(len(got), 2)
        self.assertTrue(covered(mask, got))

    def test_thought_tail_is_not_confused_with_round_decorations_inside_a_panel(self):
        image, mask, boxes = fixture([(230, 250)], [(100, 130)])
        for center, radius in (((316, 350), 12), ((337, 375), 8)):
            cv2.circle(image, center, radius, (0, 0, 0), 2)
            cv2.circle(image, center, radius-2, (255, 255, 255), -1)
        got, meta = refine(image, mask, boxes)
        self.assertEqual(len(got), 1)
        self.assertEqual(next(iter(meta.values()))['shape'], 'cloud')

        panel = np.full((640, 720, 3), 255, np.uint8)
        cv2.rectangle(panel, (50, 50), (650, 590), (0, 0, 0), 3)
        cv2.circle(panel, (530, 300), 30, (0, 0, 0), 2)
        cv2.circle(panel, (560, 370), 18, (0, 0, 0), 2)
        panel[mask > 0] = 0
        got, meta = refine(panel, mask, boxes)
        self.assertEqual(got, boxes)
        self.assertEqual(next(iter(meta.values()))['shape'], 'free')
        self.assertTrue(next(iter(meta.values()))['uncertain'])

    def test_open_radiating_thought_outline_requires_spokes_around_the_text(self):
        image = np.full((500, 500, 3), 255, np.uint8)
        mask = np.zeros((500, 500), np.float32)
        for y in range(210, 280, 20):
            mask[y:y+12, 225:238] = 1
            mask[y:y+12, 252:265] = 1
        box = spanning(mask)
        for angle in np.linspace(0, 2*np.pi, 32, endpoint=False):
            a = np.array([245, 245]) + np.array([50*np.cos(angle), 70*np.sin(angle)])
            b = np.array([245, 245]) + np.array([70*np.cos(angle), 98*np.sin(angle)])
            cv2.line(image, tuple(a.astype(int)), tuple(b.astype(int)), (0, 0, 0), 2)
        image[mask > 0] = 0
        _, meta = refine(image, mask, [box])
        self.assertEqual(next(iter(meta.values()))['shape'], 'cloud')
        image[:, :250] = 255
        image[mask > 0] = 0  # only one side has radial strokes now
        _, meta = refine(image, mask, [box])
        self.assertEqual(next(iter(meta.values()))['shape'], 'free')

    def test_white_lettering_on_black_art_does_not_invent_a_caption(self):
        image = np.full((500, 500, 3), 255, np.uint8)
        cv2.fillPoly(image, [np.array([[40, 30], [450, 40], [400, 480], [230, 440], [30, 320]])], (0, 0, 0))
        mask = np.zeros((500, 500), np.float32)
        for y in range(180, 320, 25):
            mask[y:y+14, 240:250] = 1
        image[mask > 0] = 255
        box = spanning(mask)
        _, meta = refine(image, mask, [box])
        self.assertEqual(next(iter(meta.values()))['shape'], 'free')
        image[:] = 255
        cv2.rectangle(image, (190, 130), (310, 370), (0, 0, 0), -1)
        image[mask > 0] = 255
        _, meta = refine(image, mask, [box])
        self.assertEqual(next(iter(meta.values()))['shape'], 'square')


if __name__ == '__main__':
    unittest.main()
