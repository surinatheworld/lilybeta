import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from app.core.balloons import refine
from app.core.detect import TextBox
from app.core import ai, workflow


def fixture(centers, axes=None, merged=False):
    image = np.full((640, 720, 3), 220, np.uint8)
    body = np.zeros(image.shape[:2], np.uint8)
    for center, radius in zip(centers, axes or [(100, 110)] * len(centers)):
        cv2.ellipse(body, center, radius, 0, 0, 360, 1, -1)
    image[body > 0] = 255
    contours, _ = cv2.findContours(body, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(image, contours, -1, (0, 0, 0), 3)
    mask = np.zeros(image.shape[:2], np.float32)
    boxes = []
    for cx, cy in centers:
        box = (cx-26, cy-25, cx+27, cy+26)
        boxes.append(TextBox(*box, .9))
        for dx in (-20, 0, 20):
            for dy in (-18, 2):
                cv2.rectangle(mask, (cx+dx, cy+dy), (cx+dx+6, cy+dy+12), 1, -1)
    image[mask > 0] = 0
    if merged:
        boxes = [TextBox(min(b.x1 for b in boxes), min(b.y1 for b in boxes),
                         max(b.x2 for b in boxes), max(b.y2 for b in boxes), .9)]
    return image, mask, boxes


class ConjoinedBalloonTests(unittest.TestCase):
    def test_two_existing_text_ids_get_distinct_lobes_and_round_outlines(self):
        image, mask, boxes = fixture([(160, 230), (325, 230)])
        original, original_mask = image.copy(), mask.copy()
        got, evidence = refine(image, mask, boxes)
        self.assertEqual(got, boxes)
        metas = [evidence[(b.x1,b.y1,b.x2,b.y2)] for b in got]
        self.assertEqual({m["parts"] for m in metas}, {2})
        self.assertEqual({m["part"] for m in metas}, {1,2})
        self.assertEqual({m["shape"] for m in metas}, {"round"})
        np.testing.assert_array_equal(image, original)
        np.testing.assert_array_equal(mask, original_mask)

    def test_merged_box_is_split_horizontally_vertically_and_diagonally(self):
        for centers in ([(160,230),(325,230)], [(160,230),(300,230)],
                        [(230,160),(230,345)], [(160,160),(310,300)]):
            with self.subTest(centers=centers):
                image, mask, boxes = fixture(centers, merged=True)
                got, evidence = refine(image, mask, boxes)
                self.assertEqual(len(got), 2)
                self.assertTrue(all(evidence[(b.x1,b.y1,b.x2,b.y2)]["split"] for b in got))
                covered = np.zeros(mask.shape, bool)
                for b in got:
                    covered[b.y1:b.y2,b.x1:b.x2] = True
                self.assertFalse(np.any((mask > 0) & ~covered))

    def test_triplet_and_unequal_lobes(self):
        for centers, axes, count in (([(140,250),(305,250),(470,250)],None,3),
                                     ([(180,230),(345,230)],[(110,120),(80,90)],2)):
            with self.subTest(centers=centers):
                image, mask, boxes = fixture(centers, axes, merged=True)
                got, _ = refine(image, mask, boxes)
                self.assertEqual(len(got), count)

    def test_one_oval_multiple_columns_is_not_split(self):
        image, mask, boxes = fixture([(230,250)], [(160,180)])
        got, evidence = refine(image, mask, boxes)
        self.assertEqual(got, boxes)
        self.assertFalse(evidence)

    def test_manual_spanning_box_and_ink_crossing_the_neck_are_not_split(self):
        image, mask, boxes = fixture([(160,230),(325,230)], merged=True)
        coords = (boxes[0].x1,boxes[0].y1,boxes[0].x2,boxes[0].y2)
        got, evidence = refine(image, mask, boxes, protected=[coords])
        self.assertEqual(got, boxes)
        self.assertTrue(evidence[coords]["uncertain"])
        mask[225:234, 160:326] = 1
        image[mask > 0] = 0
        got, evidence = refine(image, mask, boxes)
        self.assertEqual(got, boxes)
        self.assertTrue(evidence[coords]["uncertain"])

    def test_sfx_and_unbounded_white_background_are_unchanged(self):
        image, mask, boxes = fixture([(160,230),(325,230)], merged=True)
        boxes[0].kind = "sfx"
        self.assertEqual(refine(image, mask, boxes), (boxes, {}))
        image[:] = 255
        image[mask > 0] = 0
        boxes[0].kind = "text"
        self.assertEqual(refine(image, mask, boxes), (boxes, {}))

    def test_visible_border_in_overlapping_balloons_separates_a_spanning_box(self):
        image, mask, boxes = fixture([(210,240),(340,280)], merged=True)
        # Rear balloon outline is occluded by the front balloon, whose visible
        # arc separates the two white interiors. The text box still spans both.
        cv2.ellipse(image,(340,280),(100,110),0,0,360,(0,0,0),3)
        image[mask > 0] = 0
        got, evidence = refine(image, mask, boxes)
        self.assertEqual(len(got), 2)
        self.assertEqual({evidence[(b.x1,b.y1,b.x2,b.y2)]["parts"] for b in got}, {2})

    def test_a_locked_child_does_not_remerge_the_whole_parent(self):
        image, mask, boxes = fixture([(160,230),(325,230)], merged=True)
        children, _ = refine(image,mask,boxes)
        child = children[0]
        got, _ = refine(image,mask,boxes,protected=[(child.x1,child.y1,child.x2,child.y2)])
        self.assertEqual(got,children)

    def test_empty_lobe_does_not_override_the_single_text_classifier(self):
        image, mask, boxes = fixture([(160,230),(325,230)])
        mask[:,280:] = 0
        image[200:260,280:375] = 255
        got, evidence = refine(image,mask,boxes[:1])
        self.assertEqual(got,boxes[:1])
        self.assertFalse(evidence)

    def test_panel_background_winding_around_art_is_not_a_balloon_group(self):
        image = np.full((640,720,3),220,np.uint8)
        cv2.rectangle(image,(50,50),(500,550),(255,255,255),-1)
        cv2.rectangle(image,(50,50),(500,550),(0,0,0),3)
        # Alternating intrusions leave white connected around characters/art,
        # creating several broad maxima but an implausibly winding outline.
        for k in range(4):
            x1,x2 = (50,390) if k%2==0 else (160,500)
            cv2.rectangle(image,(x1,130+k*95),(x2,170+k*95),(0,0,0),-1)
        mask = np.zeros((640,720),np.float32)
        boxes = [TextBox(100,70,130,100,.9),TextBox(410,485,440,515,.9)]
        for b in boxes:
            mask[b.y1:b.y2,b.x1:b.x2] = 1
        image[mask>0] = 0
        self.assertEqual(refine(image,mask,boxes),(boxes,{}))

    def test_screentone_art_background_does_not_become_a_thought_balloon(self):
        image, mask, boxes = fixture([(160,230),(325,230)])
        image[np.all(image == 255,axis=2)] = 225
        image[:60] = 255  # paper-white reference outside the gray art region
        self.assertEqual(refine(image,mask,boxes),(boxes,{}))

    def test_unusable_tiny_child_keeps_all_text_and_is_flagged_for_review(self):
        image, mask, boxes = fixture([(160,230),(325,230)], merged=True)
        image[mask > 0] = 255
        mask[:] = 0
        mask[220:240,155:165] = 1
        mask[205:255,325] = 1
        image[mask > 0] = 0
        got, evidence = refine(image,mask,boxes)
        self.assertEqual(got,boxes)
        self.assertTrue(next(iter(evidence.values()))["uncertain"])


class StepTwoBalloonTests(unittest.TestCase):
    def test_split_ids_manual_edits_and_geometry_survive_redetection(self):
        image, mask, boxes = fixture([(160,230),(325,230)], merged=True)
        with tempfile.TemporaryDirectory() as folder:
            project = workflow.Project(Path(folder)/"project")
            project.sync_documents([{"id":1,"name":"001.jpg"}])
            page = project.page("001.jpg")
            workflow.imwrite(project.file(page,"work.png"),image)
            detector = type("Detector",(),{"detect":lambda self, image:(boxes,mask)})()
            workflow.detect(project,page,detector,ai.AIConfig(provider="none"))
            self.assertEqual(len(page["regions"]),2)
            first, second = page["regions"]
            ids = [r["id"] for r in page["regions"]]
            self.assertEqual(first["balloon"]["group"], second["balloon"]["group"])
            self.assertNotEqual(first["balloon"]["part"], second["balloon"]["part"])
            first.update(lines=["Sửa tay"],lines_src="manual",bubble="square",bubble_src="manual")
            workflow.detect(project,page,detector,ai.AIConfig(provider="none"))
            self.assertEqual([r["id"] for r in page["regions"]],ids)
            self.assertEqual(page["regions"][0]["lines"],["Sửa tay"])
            self.assertEqual(page["regions"][0]["bubble"],"square")
            self.assertTrue(all(r["balloon"] for r in page["regions"]))

    def test_ai_receives_per_lobe_evidence_and_original_input_is_unchanged(self):
        image, mask, boxes = fixture([(160,230),(325,230)])
        with tempfile.TemporaryDirectory() as folder:
            project = workflow.Project(Path(folder)/"project")
            project.sync_documents([{"id":1,"name":"001.jpg"}])
            page = project.page("001.jpg")
            path = project.file(page,"work.png")
            workflow.imwrite(path,image)
            original = path.read_bytes()
            detector = type("Detector",(),{"detect":lambda self, image:(boxes,mask)})()
            def reply(cfg,step,system,prompt,images,schema,name):
                self.assertIn("connected_parent=B-R01",prompt)
                self.assertIn("lobe=",prompt)
                self.assertIn("NOT a thought cloud",system)
                return {"regions":[{"id":f"R0{i}","bubble":"round","order":i,"confidence":.9,"note":""}
                                    for i in (1,2)],"missed":[],"splits":[]}
            with patch("app.core.ai.ask_json",reply):
                workflow.detect(project,page,detector,ai.AIConfig())
            self.assertEqual(path.read_bytes(),original)
            self.assertTrue(all("thuỳ" in r["note"] for r in page["regions"]))
