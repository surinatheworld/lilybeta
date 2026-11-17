"""Independent PP-OCR source evidence using explicit local ONNX sessions only.

PP-OCRv5 weights: PaddleOCR/RapidOCR, Apache-2.0. Recognition reads cropped
Japanese columns, not the user's translation; nothing here knows a page number.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import re
from collections import Counter
from functools import lru_cache

import cv2
import numpy as np

from .paths import model_path
from .ocr import normalize, coverage


@lru_cache(maxsize=4096)
def known_sound(text):
    from .match_language import SOUNDS, lexical
    return 2<=len(normalize(text))<12 and any(lexical(name,text)>=6 for name in SOUNDS)


def session(path):
    from .detect import gpu_session
    result = gpu_session(str(path))
    if result is None:
        import onnxruntime as ort
        opts = ort.SessionOptions(); opts.intra_op_num_threads = 4; opts.inter_op_num_threads = 1
        result = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
    return result


def crop_polygon(image, polygon):
    points = np.asarray(polygon, np.float32)
    width = max(2, int(max(np.linalg.norm(points[0]-points[1]), np.linalg.norm(points[2]-points[3]))))
    height = max(2, int(max(np.linalg.norm(points[0]-points[3]), np.linalg.norm(points[1]-points[2]))))
    dst = np.asarray([[0,0], [width,0], [width,height], [0,height]], np.float32)
    crop = cv2.warpPerspective(image, cv2.getPerspectiveTransform(points, dst), (width, height), borderMode=cv2.BORDER_REPLICATE)
    return np.rot90(crop) if height >= 1.5*width else crop


def bounds(polygon):
    points = np.asarray(polygon)
    return [int(points[:,0].min()), int(points[:,1].min()), int(points[:,0].max()), int(points[:,1].max())]


def caption_continuations(readings):
    """Join a horizontal/rotated caption's verb ending using its actual line geometry."""
    result=[]
    for tail in readings:
        if tail['confidence']<.8 or normalize(tail['text']) not in ('しました','しています','します','はじめます','でした'):continue
        b=np.asarray(tail['polygon'],np.float32);edge=b[1]-b[0]
        width=float(np.linalg.norm(edge));height=float(np.linalg.norm(b[3]-b[0]))
        if width<1.3*height:continue
        u=edge/max(1.,width);v=np.asarray([-u[1],u[0]]);options=[]
        for head in readings:
            if head is tail or head['confidence']<.8 or len(normalize(head['text']))<3:continue
            a=np.asarray(head['polygon'],np.float32);e=a[1]-a[0];w=float(np.linalg.norm(e));h=float(np.linalg.norm(a[3]-a[0]))
            if w<1.3*h or float(e@u)/max(1.,w)<.98:continue
            separation=float((b.mean(0)-a.mean(0))@v)
            overlap=min(float((a@u).max()),float((b@u).max()))-max(float((a@u).min()),float((b@u).min()))
            if not (.5*min(h,height)<separation<2.5*max(h,height) and overlap>.5*min(w,width)):continue
            options.append((separation,head))
        if options:
            head=min(options,key=lambda pair:pair[0])[1]
            box=bounds(np.asarray(head['polygon']+tail['polygon']))
            points=np.asarray(head['polygon']+tail['polygon'],np.float32)
            direction=np.asarray(head['polygon'][1])-np.asarray(head['polygon'][0])
            direction=direction/max(1.,float(np.linalg.norm(direction)))
            normal=np.asarray([-direction[1],direction[0]])
            along,across=points@direction,points@normal
            margin=.5*(height+float(np.linalg.norm(np.asarray(head['polygon'][3])-head['polygon'][0])))
            extents=np.asarray([[along.min()-.4*margin,across.min()-.1*margin],
                                [along.max()+.2*margin,across.min()-.1*margin],
                                [along.max()+.2*margin,across.max()+.1*margin],
                                [along.min()-.4*margin,across.max()+.1*margin]])
            reading_polygon=extents[:,0,None]*direction+extents[:,1,None]*normal
            result.append({'box':box,'polygon':[[box[0],box[1]],[box[2],box[1]],[box[2],box[3]],[box[0],box[3]]],
                           'text':head['text']+tail['text'],'confidence':min(head['confidence'],tail['confidence']),
                           'anchor_box':head.get('box') or bounds(head['polygon']),
                           'reading_polygon':reading_polygon.tolist(),'continuation':True})
    return result


class SourceOCR:
    def __init__(self, folder=None):
        from .translation_weights import OCR_FILES
        root = Path(folder or model_path("source-ocr"))
        for name, expected in OCR_FILES.items():
            path = root/name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Thiếu/sai model OCR bổ sung; chạy tools/install_translation_models.py: " + name)
        self.det = session(root/"ch_PP-OCRv5_det_mobile.onnx")
        self.rec = session(root/"ch_PP-OCRv5_rec_server.onnx")
        self.characters = [""] + self.rec.get_modelmeta().custom_metadata_map["character"].splitlines() + [" "]

    def recognize(self, crops):
        answer = []
        for crop in crops:
            h, w = crop.shape[:2]
            resized_width = min(1600, max(1, math.ceil(w/h*48)))
            width = max(320,resized_width)
            image = cv2.resize(crop, (resized_width, 48)).astype(np.float32)/127.5-1
            tensor = np.zeros((1,3,48,width),np.float32)
            tensor[0,:,:,:resized_width] = image.transpose(2,0,1)
            output = self.rec.run(None, {self.rec.get_inputs()[0].name:tensor})[0][0]
            indices = output.argmax(-1); probabilities = output.max(-1)
            keep = (indices != 0) & np.r_[True, indices[1:] != indices[:-1]]
            text = "".join(self.characters[i] for i in indices[keep])
            answer.append((text, float(probabilities[keep].mean()) if keep.any() else 0.))
        return answer

    def detect(self, image):
        h, w = image.shape[:2]; scale = min(1., 1600/max(h,w))
        H, W = max(32, round(h*scale/32)*32), max(32, round(w*scale/32)*32)
        resized = cv2.resize(image, (W,H)).astype(np.float32)/255
        # PP-OCRv5 uses mean/std .5, unlike earlier ImageNet-normalized DB models.
        tensor = ((resized-.5)/.5).transpose(2,0,1)[None].astype(np.float32)
        prob = self.det.run(None, {self.det.get_inputs()[0].name:tensor})[0].squeeze()
        contours, _ = cv2.findContours((prob>.3).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        polygons = []
        for contour in contours[:1000]:
            rect = cv2.minAreaRect(contour)
            if min(rect[1]) < 3: continue
            x,y,X,Y = bounds(contour.reshape(-1,2)); mask = np.zeros((Y-y+1,X-x+1),np.uint8)
            cv2.fillPoly(mask, [contour-[x,y]], 1)
            if float(prob[y:Y+1,x:X+1][mask.astype(bool)].mean()) < .45: continue
            # Expand the DB core into a text crop, preserving its rotation.
            a,b = rect[1]; d = 1.5*a*b/max(1,2*(a+b))
            expanded = (rect[0], (a+2*d,b+2*d), rect[2])
            pts = cv2.boxPoints(expanded)
            pts = pts[np.argsort(pts[:,0])]; left=pts[:2][np.argsort(pts[:2,1])]; right=pts[2:][np.argsort(pts[2:,1])]
            polygon = np.asarray([left[0],right[0],right[1],left[1]])
            polygon[:,0] = np.clip(polygon[:,0]*w/prob.shape[1],0,w-1)
            polygon[:,1] = np.clip(polygon[:,1]*h/prob.shape[0],0,h-1)
            polygons.append(polygon)
        readings = self.recognize([crop_polygon(image,p) for p in polygons])
        return [{"polygon":p.tolist(), "box":bounds(p), "text":t, "confidence":c}
                for p,(t,c) in zip(polygons,readings)]


_OCR = None


def source_ocr():
    global _OCR
    if _OCR is None: _OCR = SourceOCR()
    return _OCR


def prepare(project, page, image, report=None):
    """Add independent readings and recover clear missed text, respecting every manual field."""
    from .workflow import new_region
    from .semantic_review import _unreliable_japanese
    from .ocr import reader

    if project.uses_redraw(page) and not project.has(page,"raw.png"):
        return ["Thiếu Raw; không đọc chữ từ redraw."]
    digest = hashlib.sha256(image.tobytes()).hexdigest()
    cache = page.get("source_evidence",{})
    if cache.get("image") == digest and cache.get('version')==7:
        readings = cache["readings"]
    else:
        if report: report("Bước 7 · OCR độc lập kiểm tra lại chữ Nhật…")
        readings = source_ocr().detect(image)
        from .match_language import category, SOUNDS, shape
        if any(category(t) in SOUNDS or shape(t) for t in project.page_lines(page)):
            # Halftone lettering and white-on-dark SFX disappear in the normal
            # detector input. Both views are made from Raw, without VI labels.
            gray=cv2.cvtColor(cv2.GaussianBlur(image,(5,5),0),cv2.COLOR_BGR2GRAY)
            solid=cv2.cvtColor(np.where(gray<230,0,255).astype(np.uint8),cv2.COLOR_GRAY2BGR)
            for view in (solid,255-solid):
                for item in source_ocr().detect(view):
                    if known_sound(item['text']) and item['confidence']>=.6 and not any(_overlap(item['box'],old['box'])>.6 for old in readings):
                        item['contrast_view']=True;readings.append(item)
        readings.extend(caption_continuations(readings))
        page["source_evidence"] = {"version":7,"image":digest,"readings":readings}
    read = reader() if any(r.get('ja_src')!='manual' and r.get('lines_src')!='manual' for r in page['regions']) else None
    covered = set()
    for region in page["regions"]:
        if region["bubble"] == "skip" or region.get('lines_src')=='manual': continue
        if region.get('ja_image') not in (None,digest) and region.get('ja_src')!='manual':
            region['ja']='';region.pop('ja_variants',None);region.pop('ocr_checked',None)
        if region.get('ja_src')!='manual' and region.get('src')!='manual':
            for item in readings:
                if not item.get('continuation') or _overlap(item.get('anchor_box',item['box']),region['box'])<.5:continue
                if read and item.get('reading_polygon') and not item.get('caption_ja'):
                    crop=crop_polygon(image,item['reading_polygon'])
                    text=read.read(crop,(0,0,crop.shape[1],crop.shape[0]))
                    if not _unreliable_japanese(text):item['caption_ja']=text
                caption=item.get('caption_ja') or item['text']
                a,b,A,B=item['box'];x,y,X,Y=region['box']
                merged=[min(a,x),min(b,y),max(A,X),max(B,Y)]
                if (merged[2]-merged[0])*(merged[3]-merged[1])>2.5*max(1,(X-x)*(Y-y)):continue
                if any(r is not region and (r.get('src')=='manual' or r.get('lines_src')=='manual') and _overlap(merged,r['box'])>.2 for r in page['regions']):continue
                if merged!=region['box']:
                    # Automatic readings and clean flags belong to their crop.
                    # An expanded caption must not keep the old truncated OCR.
                    region['box']=merged;region['ja']=caption;region['ja_src']='ocr-caption'
                    region['ja_variants']=[];region['cleaned']=False
                    region['source_ocr_confidence']=item['confidence']
                    region.pop('ocr_checked',None)
                else:
                    region['ja']=caption;region['ja_src']='ocr-caption'
                    region['ja_variants']=list(dict.fromkeys([*region.get('ja_variants',[]),caption]))
        x,y,X,Y = region["box"]
        columns = []
        for j, item in enumerate(readings):
            a,b,A,B = item.get("box") or bounds(item["polygon"])
            if x-4 < (a+A)/2 < X+4 and y-4 < (b+B)/2 < Y+4:
                covered.add(j)
                if item["confidence"] >= .65 or (item['confidence']>=.6 and known_sound(item['text'])): columns.append(item)
        columns.sort(key=lambda t: -(t.get("box") or bounds(t["polygon"]))[0] if Y-y > X-x else (t.get("box") or bounds(t["polygon"]))[1])
        variants = list(region.get("ja_variants",[])) + ([" ".join(t["text"] for t in columns)] if columns else [])
        retried = region.get("ocr_checked") != [4,region["box"]]
        if region.get("ja_src") != "manual" and read and retried:
            # A tight crop often repairs kana preceding punctuation or rotated captions.
            for pad in (0.,.12):
                ex=max(2,round((X-x)*pad));ey=max(2,round((Y-y)*pad))
                crop=image[max(0,y-ey):min(image.shape[0],Y+ey),max(0,x-ex):min(image.shape[1],X+ex)]
                variants.append(read.read(crop,[0,0,crop.shape[1],crop.shape[0]]).strip())
            region["ocr_checked"] = [4,list(region["box"])]
        if region.get("ja_src") == "manual":
            region.pop("ja_variants",None)
            continue
        if len(normalize(region.get('ja','')))<8 and region.get('short_ocr_box')!=[2,region['box']]:
            crop=image[max(0,y-5):min(image.shape[0],Y+5),max(0,x-5):min(image.shape[1],X+5)]
            solid=np.where(cv2.cvtColor(cv2.GaussianBlur(crop,(5,5),0),cv2.COLOR_BGR2GRAY)[...,None]<230,0,255).astype(np.uint8).repeat(3,2)
            results=source_ocr().recognize([np.rot90(v,k) for v in (crop,solid) for k in range(4)])
            variants.extend(t for t,c in results if c>=.7 and re.search(r'[ぁ-ゖァ-ヺ一-鿿]',t))
            region['short_ocr_box']=[2,list(region['box'])]
        variants = [t for t in dict.fromkeys(variants) if t and not _unreliable_japanese(t)]
        if variants:
            old = region.get("ja", "")
            region["ja_variants"] = variants
            # Only replace clearly corrupt primary OCR. Other readings remain alternatives.
            votes=Counter(normalize(t) for t in variants)
            best,count=votes.most_common(1)[0]
            compatible=len(normalize(old))<8 or len(best)>=.45*len(normalize(old))
            if _unreliable_japanese(old) or (region.get('ja_src')!='ocr-caption' and retried and compatible and count>=2 and normalize(old)!=best):
                region["ja"] = next(t for t in variants if normalize(t)==best)
                region["ja_src"] = "ocr-local"
            # Punctuation-only crop differences must not multiply translation
            # candidates or create a spurious semantic advantage.
            unique={}
            for text in variants:unique.setdefault(normalize(text),text)
            primary=normalize(region.get('ja',''))
            alternatives=[t for key,t in unique.items() if key!=primary and
                          (len(primary)<8 or .45*len(primary)<=len(key)<=2.2*len(primary))]
            alternatives.sort(key=lambda t: votes[normalize(t)],reverse=True)
            region['ja_variants']=alternatives[:2]
        region['ja_image']=digest
    for j,item in enumerate(readings):
        sound=known_sound(item['text'])
        if j in covered or item["confidence"] < (.6 if sound else .92): continue
        text = item["text"].strip(); box = item.get("box") or bounds(item["polygon"])
        if len(normalize(text))<(2 if sound else 4) or not re.search(r"[ぁ-ゖァ-ヺ]",text): continue
        if any(_overlap(box,r["box"])>.25 for r in page["regions"]): continue
        region = new_region(project.next_id(page),box)
        region["ja"],region["ja_src"] = text,"ocr-local"
        region["bubble"] = "sfx" if sound else "free"
        region['source_ocr_confidence']=item['confidence']
        region["order"] = max([r["order"] for r in page["regions"]] or [0])+1
        region["note"] = "Chữ Nhật được OCR bổ sung tìm lại trên Raw"
        page["regions"].append(region)
    project.save()
    return []


def _overlap(a,b):
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return inter/max(1,min((a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1])))
