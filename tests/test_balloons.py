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


def open_fixture():
    image, mask, boxes = fixture([(335,190),(225,370)],[(80,120),(85,140)],merged=True)
    # A breathing SFX interrupts the outer arc. The white interior now leaks into
    # the surrounding panel; a normal 3px closing cannot recover its two lobes.
    cv2.rectangle(image,(402,145),(425,223),(255,255,255),-1)
    return image, mask, boxes


class ConjoinedBalloonTests(unittest.TestCase):
    def test_mixed_enclosed_and_exterior_text_is_partitioned_without_losing_ink(self):
        for reversed_text in (False,True):
            with self.subTest(reversed=reversed_text):
                image,mask,boxes = fixture([(160,160),(310,300)],merged=True)
                # The detector rectangle contains two lobes, a caption in a
                # concave corner, and another caption beyond the parent's bbox.
                for x,y in ((290,145),(440,220)):
                    if reversed_text:
                        cv2.rectangle(image,(x-4,y-4),(x+29,y+24),(0,0,0),-1)
                    for dx in (0,14):
                        mask[y:y+18,x+dx:x+dx+8] = 1
                        image[y:y+18,x+dx:x+dx+8] = 255 if reversed_text else 0
                boxes[0].x2 = 470
                original,original_mask = image.copy(),mask.copy()
                got,evidence = refine(image,mask,boxes)
                self.assertEqual(sum(m['parts']==2 for m in evidence.values()),2)
                self.assertGreaterEqual(sum(m['shape']=='free' for m in evidence.values()),1)
                covered = np.zeros(mask.shape,bool)
                for b in got:
                    covered[b.y1:b.y2,b.x1:b.x2] = True
                self.assertFalse(np.any((mask > 0) & ~covered))
                np.testing.assert_array_equal(image,original)
                np.testing.assert_array_equal(mask,original_mask)

    def test_manual_mixed_region_is_never_partitioned(self):
        image,mask,boxes = fixture([(160,160),(310,300)],merged=True)
        mask[145:165,290:320] = 1
        image[mask > 0] = 0
        b = boxes[0]
        got,_ = refine(image,mask,boxes,protected=[(b.x1,b.y1,b.x2,b.y2)])
        self.assertEqual(got,boxes)

    def test_single_cloud_and_caption_use_the_outline_despite_heavy_lettering(self):
        for kind in ('cloud','square','round'):
            with self.subTest(kind=kind):
                image = np.full((640,640,3),220,np.uint8)
                body = np.zeros((640,640),np.uint8)
                if kind=='cloud':
                    t = np.linspace(0,2*np.pi,720,endpoint=False)
                    radius = 1+.13*np.cos(10*t)
                    pts = np.column_stack([320+130*radius*np.cos(t),320+200*radius*np.sin(t)]).astype(np.int32)
                    cv2.fillPoly(body,[pts],1)
                    # One deep indentation alongside the other cloud scallops.
                    cv2.fillPoly(body,[np.array([[440,290],[405,310],[450,335]],np.int32)],0)
                elif kind=='square':
                    cv2.rectangle(body,(160,100),(450,510),1,-1)
                    cv2.fillPoly(body,[np.array([[450,420],[480,450],[450,455]],np.int32)],1)
                else:
                    cv2.ellipse(body,(310,180),(150,200),0,0,360,1,-1)
                    body[:100] = 0
                    body[:,360:] = 0  # an oval clipped by two frame edges
                image[body>0] = 255
                cv2.drawContours(image,cv2.findContours(body,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0],-1,(0,0,0),3)
                mask = np.zeros(body.shape,np.float32)
                if kind=='round':
                    coords=(240,140,320,300)
                elif kind=='cloud':
                    coords=(260,195,370,415)
                else:
                    coords=(245,170,380,450)
                x1,y1,x2,y2=coords
                for x in range(x1,x2-10,30):
                    for y in range(y1,y2-15,30):
                        mask[y:y+17,x:x+13]=1
                image[mask>0]=0
                got,meta=refine(image,mask,[TextBox(*coords,.9)])
                self.assertEqual(len(got),1)
                self.assertEqual(meta[coords]['shape'],kind)
                self.assertEqual(meta[coords]['parts'],1)

    def test_missing_outer_arc_recovers_two_text_bearing_lobes_in_rotated_pages(self):
        image, mask, boxes = open_fixture()
        for rotation in range(4):
            with self.subTest(rotation=rotation):
                rotated = np.rot90(image,rotation).copy()
                pred = np.rot90(mask,rotation).copy()
                ys,xs = np.nonzero(pred)
                box = TextBox(int(xs.min())-2,int(ys.min())-2,int(xs.max())+3,int(ys.max())+3,.9)
                original = rotated.copy()
                got,evidence = refine(rotated,pred,[box])
                self.assertEqual(len(got),2)
                self.assertEqual({m["shape"] for m in evidence.values()},{"round"})
                self.assertTrue(all(m["split"] and not m["uncertain"] for m in evidence.values()))
                covered = np.zeros(pred.shape,bool)
                for b in got:
                    covered[b.y1:b.y2,b.x1:b.x2] = True
                self.assertFalse(np.any((pred > 0) & ~covered))
                np.testing.assert_array_equal(rotated,original)

    def test_open_single_oval_and_missing_large_outline_are_not_invented_as_two_balloons(self):
        image,mask,boxes = fixture([(230,250)],[(120,180)])
        cv2.rectangle(image,(335,210),(360,285),(255,255,255),-1)
        got,meta=refine(image,mask,boxes)
        self.assertEqual(got,boxes)
        self.assertTrue(all(m['parts']==1 for m in meta.values()))
        image,mask,boxes = open_fixture()
        cv2.rectangle(image,(360,80),(450,320),(255,255,255),-1)
        got,meta=refine(image,mask,boxes)
        self.assertEqual(got,boxes)
        self.assertTrue(all(m['parts']==1 and m['uncertain'] for m in meta.values()))

    def test_manual_open_spanning_region_is_preserved(self):
        image,mask,boxes = open_fixture()
        b = boxes[0]
        self.assertEqual(refine(image,mask,boxes,protected=[(b.x1,b.y1,b.x2,b.y2)]),(boxes,{}))

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
        self.assertTrue(all(m['parts']==1 and m['shape']=='round' for m in evidence.values()))

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
        got,meta=refine(image,mask,boxes)
        self.assertEqual(got,boxes)
        self.assertTrue(all(m['shape']=='free' and m['uncertain'] for m in meta.values()))

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
        got,meta=refine(image,mask,boxes)
        self.assertEqual(got,boxes)
        self.assertTrue(all(m['shape']=='free' and m['parts']==1 for m in meta.values()))

    def test_screentone_art_background_does_not_become_a_thought_balloon(self):
        image, mask, boxes = fixture([(160,230),(325,230)])
        image[np.all(image == 255,axis=2)] = 225
        image[:60] = 255  # paper-white reference outside the gray art region
        got,meta=refine(image,mask,boxes)
        self.assertEqual(got,boxes)
        self.assertTrue(all(m['shape']=='free' and m['parts']==1 for m in meta.values()))

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
    def test_bold_lettering_is_not_used_to_invent_a_ray_balloon(self):
        image=np.full((400,400,3),255,np.uint8)
        cv2.rectangle(image,(60,40),(340,360),(0,0,0),3)
        cv2.putText(image,'BOLD',(100,220),cv2.FONT_HERSHEY_SIMPLEX,1.6,(0,0,0),7)
        mask=np.zeros((400,400),np.float32)
        mask[170:235,95:280]=1
        self.assertEqual(workflow.heuristic_bubble(image,mask,(95,170,280,235),'text'),'square')
        self.assertEqual(workflow.heuristic_bubble(image,mask,(95,170,280,235),'sfx'),'sfx')

    def test_open_balloon_replaces_a_merged_region_without_changing_the_sfx(self):
        image,mask,boxes = open_fixture()
        sfx = TextBox(430,145,450,223,.9,"sfx")
        boxes.append(sfx)
        with tempfile.TemporaryDirectory() as folder:
            project = workflow.Project(Path(folder)/"project")
            project.sync_documents([{"id":1,"name":"open.jpg"}])
            page = project.page("open.jpg")
            workflow.imwrite(project.file(page,"work.png"),image)
            detector = type("Detector",(),{"detect":lambda self,image:(boxes,mask)})()
            workflow.detect(project,page,detector,ai.AIConfig(provider="none"))
            children = [r for r in page["regions"] if r.get("balloon")]
            self.assertEqual(len(children),2)
            self.assertEqual({r["bubble"] for r in children},{"round"})
            other = [r for r in page["regions"] if r["det_kind"] == "sfx"]
            self.assertEqual([r["box"] for r in other],[[430,145,450,223]])
            ids = [r["id"] for r in page["regions"]]
            workflow.detect(project,page,detector,ai.AIConfig(provider="none"))
            self.assertEqual([r["id"] for r in page["regions"]],ids)

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

    def test_style_only_parent_splits_but_hand_entered_text_keeps_its_box(self):
        image, mask, boxes = fixture([(160,230),(325,230)], merged=True)
        b = boxes[0]
        box = [b.x1,b.y1,b.x2,b.y2]
        for locked in (False, True):
            with self.subTest(locked=locked), tempfile.TemporaryDirectory() as folder:
                project = workflow.Project(folder)
                project.sync_documents([{"id":1,"name":"001.png"}])
                page = project.pages[0]
                parent = workflow.new_region(project.next_id(page), box)
                parent.update(bubble="round", bubble_src="manual", style_kind="normal", font={"family":"Arial"},
                              size=18, ja="結合文字", ja_src="ocr", lines=["Câu cũ"],
                              lines_src="manual" if locked else "auto", cleaned=True)
                page["regions"] = [parent]
                workflow.imwrite(project.file(page,"work.png"), image)
                detector = type("Detector",(),{"detect":lambda self, image:(boxes,mask)})()
                workflow.detect(project,page,detector,ai.AIConfig(provider="none"))
                rows = page["regions"]
                if locked:
                    self.assertEqual([(r["box"],r["lines"]) for r in rows], [(box,["Câu cũ"])])
                else:
                    self.assertEqual(len(rows),2)
                    self.assertEqual(rows[0]["id"], parent["id"])
                    for r in rows:
                        self.assertEqual((r["style_kind"],r["font"],r["size"],r["bubble_src"]),
                                         ("normal",{"family":"Arial"},18,"manual"))
                        self.assertEqual((r["lines"],r["ja"],r["cleaned"]),([],"",False))
                    self.assertIsNot(rows[0]["font"],rows[1]["font"])

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
