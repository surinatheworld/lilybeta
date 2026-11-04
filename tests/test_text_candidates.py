import unittest

import cv2
import numpy as np

from app.core.detect import TextBox
from app.core.text_candidates import filter_decorations


class CandidateFilterTests(unittest.TestCase):
    def check(self,image,coords,expected,protected=()):
        mask = np.zeros(image.shape,np.float32)
        x1,y1,x2,y2 = coords
        mask[y1:y2,x1:x2] = cv2.dilate((image[y1:y2,x1:x2]<220).astype(np.float32),np.ones((3,3),np.uint8))
        box = TextBox(*coords,.5,'sfx')
        original,original_mask = image.copy(),mask.copy()
        got = filter_decorations(image,mask,[box],protected=protected)
        np.testing.assert_array_equal(image,original)
        np.testing.assert_array_equal(mask,original_mask)
        if expected:
            self.assertEqual(got,[box],'real or manually protected lettering was removed')
        else:
            self.assertEqual(got,[],'decoration was retained as lettering')

    def test_repeated_tone_grid_is_not_an_sfx(self):
        image = np.full((640,640),255,np.uint8)
        for x in range(200,281,30):
            for y in range(200,321,30):
                cv2.circle(image,(x,y),6,0,-1)
        self.check(image,(220,218,250,312),False)

    def test_ellipsis_on_plain_paper_is_preserved(self):
        image = np.full((640,640),255,np.uint8)
        for y in (200,230,260):
            cv2.circle(image,(240,y),4,0,-1)
        self.check(image,(230,190,251,271),True)

    def test_verified_border_corner_and_star_field_are_removed(self):
        image = np.full((640,640),255,np.uint8)
        cv2.line(image,(590,20),(590,350),0,4)
        cv2.line(image,(535,460),(590,460),0,5)
        cv2.line(image,(590,460),(590,515),0,5)
        self.check(image,(525,450,605,530),False)
        image[:]=255
        for x,y,r in ((200,180,7),(200,207,5),(220,210,3),(200,225,2)):
            cv2.circle(image,(x,y),r,0,1 if r>=3 else -1)
        self.check(image,(188,166,232,236),False)

    def test_handwritten_and_outlined_lettering_remain_candidates(self):
        for thickness in (1,3):
            image = np.full((640,640),255,np.uint8)
            cv2.putText(image,'SFX!',(200,260),cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,1.2,0,thickness)
            self.check(image,(192,220,310,275),True)
        image[:]=255
        cv2.putText(image,'Aa',(200,260),cv2.FONT_HERSHEY_SIMPLEX,1.5,0,7)
        cv2.putText(image,'Aa',(200,260),cv2.FONT_HERSHEY_SIMPLEX,1.5,255,2)
        self.check(image,(190,220,280,275),True)

    def test_white_lettering_on_a_small_dark_label_is_kept(self):
        image = np.full((640,640),255,np.uint8)
        cv2.rectangle(image,(200,200),(310,235),0,-1)
        cv2.putText(image,'UNDER',(203,225),cv2.FONT_HERSHEY_SIMPLEX,.6,255,1)
        self.check(image,(202,201,309,234),True)

    def test_protected_candidate_and_regular_detector_box_are_not_filtered(self):
        image = np.full((640,640),255,np.uint8)
        for x in range(200,281,30):
            for y in range(200,321,30):
                cv2.circle(image,(x,y),6,0,-1)
        coords=(220,218,250,312)
        self.check(image,coords,True,protected=[coords])
        box=TextBox(*coords,.9,'text')
        self.assertEqual(filter_decorations(image,np.ones(image.shape,np.float32),[box]),[box])
