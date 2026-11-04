"""Conservative visual rejection of unboxed detector noise, not of detected dialogue."""
from __future__ import annotations

import cv2
import numpy as np


def filter_decorations(gray, pred, boxes, scale=1., protected=()):
    """Keep real SFX; reject frame fragments, repeated tone dots and hollow stars.

    Only low-confidence, unboxed SFX candidates are inspected. Manual regions and
    the detector's regular dialogue boxes never pass through this filter.
    """
    if not any(b.kind == "sfx" for b in boxes):
        return list(boxes)
    dark = (gray < 150).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(dark)
    length = max(60,round(min(gray.shape)*.08))
    vertical = cv2.morphologyEx(dark,cv2.MORPH_OPEN,np.ones((length,1),np.uint8))
    horizontal = cv2.morphologyEx(dark,cv2.MORPH_OPEN,np.ones((1,length),np.uint8))
    frame = np.zeros(gray.shape,np.uint8)
    margin = max(40,round(min(gray.shape)*.08))
    for mask,is_vertical in ((vertical,True),(horizontal,False)):
        _,_,st,_ = cv2.connectedComponentsWithStats(mask)
        for x,y,w,h,area in st[1:]:
            if min(w,h) > 7:
                continue
            if is_vertical:
                edge = x < .15*gray.shape[1] or x > .85*gray.shape[1]
                pad = gray.shape[0] if edge and h > .25*gray.shape[0] else margin
                frame[max(0,y-pad):y+h+pad,max(0,x-1):x+w+1] = 1
            else:
                frame[max(0,y-1):y+h+1,max(0,x-margin):x+w+margin] = 1
    component_cache = {}

    def tone_grid(x1,y1,x2,y2):
        patch = dark[max(0,y1-30):y2+30,max(0,x1-30):x2+30]
        patch = cv2.morphologyEx(patch,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        _,_,st,_ = cv2.connectedComponentsWithStats(patch)
        dots = [(x+w/2,y+h/2,w,h) for x,y,w,h,a in st[1:]
                if 7 <= min(w,h) <= max(w,h) <= 20 and min(w,h) >= .55*max(w,h) and a >= .45*w*h]
        if len(dots) < 6:
            return False
        tolerance = max(4,float(np.median([d[2] for d in dots]))*.7)
        def groups(values):
            clusters = []
            for value in sorted(values):
                if not clusters or value-np.mean(clusters[-1]) > tolerance:
                    clusters.append([value])
                else:
                    clusters[-1].append(value)
            return clusters
        columns = groups(d[0] for d in dots)
        rows = groups(d[1] for d in dots)
        return sum(len(c) >= 2 for c in columns) >= 2 and len(rows) >= 3

    def art_component(label):
        if label in component_cache:
            return component_cache[label]
        x,y,w,h,area = map(int,stats[label])
        result = False
        if area < 20000 and max(w,h) >= 12:
            component = labels[y:y+h,x:x+w] == label
            result = np.count_nonzero(component & (frame[y:y+h,x:x+w] > 0)) >= .9*area
            if not result and np.any(component & (frame[y:y+h,x:x+w] > 0)):
                row = int(component.sum(axis=1).argmax())
                col = int(component.sum(axis=0).argmax())
                yy,xx = np.indices(component.shape)
                elbows = (abs(yy-row) <= 5) | (abs(xx-col) <= 5)
                result = (component[row].sum() >= .75*w and component[:,col].sum() >= .75*h
                          and np.count_nonzero(component & elbows) >= .95*area)
        component_cache[label] = result
        return result

    def small_decoration(body):
        contours,hierarchy = cv2.findContours(body,cv2.RETR_CCOMP,cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return True
        contour = max(contours,key=cv2.contourArea)
        area = cv2.contourArea(contour)
        x,y,w,h = cv2.boundingRect(contour)
        if max(w,h) < 9 or max(w,h) > 40:
            return False
        # Hollow star/flower outlines have several deep inward notches. A
        # rectangular kana counter or an outlined sound effect is not a star.
        filled = np.zeros(body.shape,np.uint8)
        cv2.drawContours(filled,[contour],-1,1,-1)
        hollow = int(filled.sum()) > 1.65*int(body.sum())
        hull = cv2.convexHull(contour,returnPoints=False)
        if hollow and len(contour) > len(hull) > 3:
            try:
                defects = cv2.convexityDefects(contour,hull)
            except cv2.error:
                defects = None
            if defects is not None and np.count_nonzero(defects.reshape(-1,4)[:,3]/256 > max(1,min(w,h)*.07)) >= 3:
                return True
        return False

    kept = []
    for box in boxes:
        coords = (box.x1,box.y1,box.x2,box.y2)
        if box.kind != "sfx" or any(
            max(0,min(box.x2,p[2])-max(box.x1,p[0]))*max(0,min(box.y2,p[3])-max(box.y1,p[1]))
            > .5*max(1,(p[2]-p[0])*(p[3]-p[1])) for p in protected):
            kept.append(box)
            continue
        x1,y1,x2,y2 = [round(v*scale) for v in coords]
        cells = pred[y1:y2,x1:x2] > .3
        if (np.median(gray[y1:y2,x1:x2]) < 110
                and np.count_nonzero(cells & (gray[y1:y2,x1:x2] > 220)) > .05*max(1,cells.sum())):
            kept.append(box)
            continue
        local_labels = labels[y1:y2,x1:x2]
        support = (pred[y1:y2,x1:x2] > .3) & (dark[y1:y2,x1:x2] > 0)
        hits = np.bincount(local_labels[support],minlength=n)
        total = int(hits[1:].sum())
        if not total:
            kept.append(box)  # white lettering on dark art is not ruled out
            continue
        huge = sum(hits[g] for g in np.flatnonzero(hits[1:])+1
                   if stats[g,4] > max(8*total,2*(x2-x1)*(y2-y1))
                   and stats[g,2] > 2*(x2-x1) and stats[g,3] > 2*(y2-y1))
        if max(x2-x1,y2-y1) <= 32 and huge >= .8*total:
            continue  # eyebrow/hair: most predicted ink belongs to a large art component
        remaining = dark[y1:y2,x1:x2].copy()
        for g in np.unique(local_labels[support]):
            if g and art_component(int(g)):
                remaining[local_labels == g] = 0
        if np.count_nonzero(support & (remaining == 0)) >= .85*total:
            continue
        star_mask = np.zeros(remaining.shape,np.uint8)
        light_strokes = cv2.morphologyEx((gray[y1:y2,x1:x2] < 220).astype(np.uint8),
                                         cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        ns,ls,sts,_ = cv2.connectedComponentsWithStats(light_strokes)
        rings = []
        specks = []
        for g in range(1,ns):
            x,y,w,h,area = map(int,sts[g])
            body = (ls[y:y+h,x:x+w] == g).astype(np.uint8)
            if 3 <= min(w,h) <= max(w,h) <= 24:
                specks.append((x+w/2,y+h/2,max(w,h)))
                contours = cv2.findContours(body,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
                filled = np.zeros(body.shape,np.uint8)
                cv2.drawContours(filled,contours,-1,1,-1)
                if filled.sum() > 1.65*area:
                    rings.append(g)
            if small_decoration(body):
                star_mask[y:y+h,x:x+w][body > 0] = 1
        if rings and 3 <= len(specks) <= 6 and max(s[2] for s in specks) <= 16:
            points = np.asarray(specks)
            singular = np.linalg.svd(points[:,:2]-points[:,:2].mean(axis=0),compute_uv=False)
            if (singular[1] > max(3,.15*singular[0])
                    and points[:,2].std() > .15*points[:,2].mean()):
                continue  # varied hollow sparkles in two dimensions, not a text line
        remaining[star_mask > 0] = 0
        closed = cv2.morphologyEx(remaining,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        nc, lab, st, _ = cv2.connectedComponentsWithStats(closed)
        useful = 0
        dot_ink = 0
        dots = 0
        for g in range(1,nc):
            x,y,w,h,area = map(int,st[g])
            body = (lab[y:y+h,x:x+w] == g).astype(np.uint8)
            weight = int(np.count_nonzero(support[y:y+h,x:x+w] & (body > 0)))
            if not weight:
                continue
            useful += weight
            contours = cv2.findContours(body,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]
            contour = max(contours,key=cv2.contourArea)
            solidity = cv2.contourArea(contour)/max(1,cv2.contourArea(cv2.convexHull(contour)))
            if area >= .5*w*h and min(w,h) >= .6*max(w,h) and solidity >= .7:
                dots += 1
                dot_ink += weight
        repeated = dots >= 2 and dot_ink >= .75*useful and tone_grid(x1,y1,x2,y2)
        if useful >= max(4,.1*total) and not repeated:
            kept.append(box)
    return kept
