import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from types import SimpleNamespace

import cv2
import numpy as np

from app.core.balloon_layout import constrain_lobes
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
