import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from types import SimpleNamespace

import cv2
import numpy as np

from app.core.balloon_layout import constrain_lobes, constrain_panel_rects
from app.core.page import Region,Page
from app.core.typeset import block_box
from app.core.psd_export import text_spec


class BalloonLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def fixture(self):
        r=Region((130,40,165,185),text='Bình tĩnh nào, để xem thử xem.',cleaned=True)
        polygon=cv2.ellipse2Poly((150,120),(60,100),0,0,360,5)
        owner={'id':'A','src':'detect','bubble':'round',
               'balloon':{'split':True,'peers':['A','B'],'outline':polygon.tolist()}}
        page=SimpleNamespace(regions=[r],size=(400,400),auto_cap=26,typeset_profile='')
        return page,r,owner,polygon

    def test_actual_text_lines_and_export_remain_inside_the_assigned_lobe(self):
        page,r,owner,polygon=self.fixture()
        constrain_lobes(page,{r.uid:owner})
        self.assertIsNotNone(r.manual_rect)
        for x,y,X,Y in block_box(r.text,r.effective_style,r.text_rect)[5]:
            for point in ((x,y),(X,y),(x,Y),(X,Y)):
                self.assertGreaterEqual(cv2.pointPolygonTest(polygon.astype(np.float32),point,True),-1.)
        spec=text_spec(r)
        self.assertGreaterEqual(spec['left'],90)
        self.assertLessEqual(spec['left']+spec['width'],210)

    def test_stale_geometry_cannot_pull_a_moved_region_back_to_its_old_balloon(self):
        page,r,owner,polygon=self.fixture();r.box=(250,250,300,350)
        constrain_lobes(page,{r.uid:owner})
        self.assertIsNone(r.manual_rect)

    def test_panel_border_stops_a_lobe_mask_that_leaks_into_the_page_gutter(self):
        page,r,owner,polygon=self.fixture()
        page.original=np.full((400,400,3),255,np.uint8)
        cv2.line(page.original,(230,0),(230,399),(0,0,0),4)
        r.box=(180,80,220,180)
        owner['balloon']['outline']=[[120,20],[350,20],[350,250],[120,250]]
        constrain_lobes(page,{r.uid:owner})
        self.assertIsNotNone(r.manual_rect)
        spec=text_spec(r)
        self.assertLess(spec['left']+spec['width'],230)

    def test_user_geometry_and_multiple_rendered_parts_are_preserved(self):
        page,r,owner,polygon=self.fixture();owner['src']='manual'
        constrain_lobes(page,{r.uid:owner});self.assertIsNone(r.manual_rect)
        owner['src']='detect'
        second=Region((130,130,165,180),text='Câu thứ hai.',group=r.uid)
        page.regions.append(second)
        constrain_lobes(page,{r.uid:owner})
        self.assertIsNone(r.manual_rect);self.assertIsNone(second.manual_rect)

    def test_lobe_evidence_from_separate_detector_boxes_is_used(self):
        page, r, owner, _ = self.fixture()
        owner["balloon"].update(split=False, parts=2)
        constrain_lobes(page, {r.uid: owner})
        self.assertIsNotNone(r.manual_rect)

    def test_lone_lobe_leaking_to_page_edge_cannot_fit_over_neighboring_art(self):
        page, r, owner, _ = self.fixture()
        r.box = (90, 145, 162, 230)
        page.working = np.full((400, 400, 3), 255, np.uint8)
        cv2.rectangle(page.working, (99, 85), (112, 285), (0, 0, 0), -1)
        owner['balloon'] = {'parts': 3, 'peers': ['A'], 'outline': [[0, 72], [177, 72], [177, 301], [0, 301]]}
        constrain_lobes(page, {r.uid: owner})
        self.assertIsNotNone(r.manual_rect)
        self.assertTrue(all(bounds[0] > 113 for bounds in block_box(r.text, r.effective_style, r.text_rect)[5]))

    def test_text_in_a_tall_caption_is_centered_in_the_writing_area(self):
        page, r, owner, _ = self.fixture()
        r.box = (130, 68, 165, 143)
        owner["bubble"] = "square"
        owner["balloon"] = {"split": False, "parts": 1, "shape": "square",
                            "outline": [[103, 42], [210, 42], [210, 336], [103, 336]]}
        constrain_lobes(page, {r.uid: owner})
        self.assertIsNotNone(r.manual_rect)
        x, y, right, bottom, _, _ = block_box(r.text, r.effective_style, r.text_rect)
        self.assertAlmostEqual((y + bottom) / 2, 189, delta=12)

    def test_clipped_short_lobe_stays_near_its_source_and_does_not_oversize(self):
        short = Region((278, 99, 300, 193), text="Xin lỗi,", cleaned=True)
        long = Region((181, 143, 220, 259), text="Tôi được lệnh không nói chuyện với anh.", cleaned=True)
        page = SimpleNamespace(regions=[short, long], size=(400, 400), auto_cap=27, typeset_profile="")
        owners = {}
        outlines = [[[259, 0], [322, 0], [322, 263], [273, 245], [266, 118]],
                    [[151, 105], [253, 105], [257, 270], [226, 304], [161, 295], [140, 226]]]
        for i, (region, outline) in enumerate(zip(page.regions, outlines), start=1):
            owners[region.uid] = {"id": str(i), "src": "detect", "bubble": "round",
                                  "balloon": {"parent": 1, "part": i, "parts": 2, "outline": outline}}
        constrain_lobes(page, owners)
        first, second = [block_box(r.text, r.effective_style, r.text_rect) for r in page.regions]
        self.assertAlmostEqual((first[1] + first[3]) / 2, 146, delta=20)
        self.assertLessEqual(first[4], max(second[4] * 1.08, round(.85 * page.auto_cap)))

    def test_automatic_redraw_does_not_invent_a_rectangular_mask(self):
        from app.core.clean import build_text_mask

        mask = np.zeros((100, 100), np.float32)
        self.assertFalse(build_text_mask(mask, (15, 20, 70, 80)).any())
        mask[39:48, 37:43] = .8
        strokes = build_text_mask(mask, (15, 20, 70, 80))
        self.assertTrue(strokes[43, 41])
        self.assertFalse(strokes[31, 55])

    def test_local_redraw_only_changes_selected_pixels(self):
        from unittest.mock import patch
        from app.core.local_redraw import redraw_local

        page = np.full((90, 140, 3), 219, np.uint8)
        mask = np.zeros(page.shape[:2], np.uint8)
        mask[34:60, 32:55] = 255
        model = SimpleNamespace(run=lambda *_: [np.full((1, 3, 512, 512), .25, np.float32)])
        with patch("app.core.local_redraw._session", return_value=model):
            redraw_local(page, mask)
        self.assertTrue(np.all(page[mask == 0] == 219))
        self.assertTrue(np.all(page[mask > 0] == 63))

    def test_free_text_within_a_panel_does_not_extend_across_its_border(self):
        page,r,owner,_=self.fixture()
        page.original=np.full((400,400,3),255,np.uint8)
        cv2.line(page.original,(230,0),(230,399),(0,0,0),4)
        r.box=(182,80,220,180)
        r.text_rects=[(100,80,275,180)]
        owner.update(bubble='free',balloon=None)
        constrain_panel_rects(page,{r.uid:owner})
        self.assertLess(r.text_rect[2],230)
        self.assertLess(text_spec(r)['left']+text_spec(r)['width'],230)

    def test_rejoining_cramped_parts_keeps_the_source_layer_id_and_font_override(self):
        a=Region((10,10,40,60),text='Tên của tôi.',cleaned=True,group=77,typeset_kind='thought',override={'family':'Arial'})
        b=Region((50,10,80,60),text='Thông tin khác.',cleaned=True,group=77)
        page=SimpleNamespace(regions=[a,b],auto_cap=30,
                             _fit_size=lambda r:1 if r is a or r is b else 5,clean=lambda *args:None)
        Page._rejoin_cramped(page,False)
        self.assertEqual(len(page.regions),1)
        merged=page.regions[0]
        self.assertEqual(merged.group,77)
        self.assertEqual(merged.typeset_kind,'thought')
        self.assertEqual(merged.override,{'family':'Arial'})


if __name__=='__main__':unittest.main()
