"""Text-seeded analysis of closed and conjoined balloons for workflow step 2.

Each enclosing white component is a parent, not necessarily one balloon. Distance
maxima separated by a genuine narrow neck identify its lobes. Lettering determines
which lobes are relevant and whether a detector's spanning text box may be split.
No image pixels are edited and no additional model/runtime is required.

Research sources and evaluation limits: docs/balloon-research.md.
"""
from __future__ import annotations

import cv2
import numpy as np

from .detect import TextBox

MAX_SIDE = 1600
NECK_RATIO = .84  # step 2 also handles broad overlaps; ink/shape checks guard against false splits
MIN_INK = 18


def _filled(region):
    padded = np.pad(region.astype(np.uint8), 1)
    outside = padded.copy()
    cv2.floodFill(outside, None, (0, 0), 2)
    return (outside[1:-1, 1:-1] != 2).astype(np.uint8)


def _lobes(region):
    """Reject extra maxima of the same oval; accept cores separated by a neck."""
    dist = cv2.distanceTransform(np.pad(region, 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    peak = float(dist.max())
    if peak < 6:
        return np.ones(region.shape, np.int32) * region, False
    radius = max(3, round(peak * .3))
    local = cv2.dilate(dist, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1,) * 2))
    maxima = ((dist >= local - .05) & (dist >= max(6, peak * .28))).astype(np.uint8)
    n, labels = cv2.connectedComponents(maxima)
    candidates = []
    for label in range(1, n):
        ys, xs = np.nonzero(labels == label)
        k = int(np.argmax(dist[ys, xs]))
        candidates.append((int(ys[k]), int(xs[k])))
    candidates.sort(key=lambda p: float(dist[p]), reverse=True)
    poles = []
    for point in candidates[:32]:
        distinct = True
        for old in poles:
            threshold = NECK_RATIO * min(float(dist[old]), float(dist[point]))
            _, joined = cv2.connectedComponents((dist >= threshold).astype(np.uint8), connectivity=4)
            if joined[point] == joined[old]:
                distinct = False
                break
        if distinct:
            poles.append(point)
        if len(poles) == 6:
            break
    if len(poles) < 2:
        return np.ones(region.shape, np.int32) * region, False
    cores = np.zeros(region.shape, np.int32)
    for i, pole in enumerate(poles, 1):
        _, lab = cv2.connectedComponents((dist >= NECK_RATIO * float(dist[pole])).astype(np.uint8), connectivity=4)
        cores[lab == lab[pole]] = i
    _, nearest = cv2.distanceTransformWithLabels((cores == 0).astype(np.uint8), cv2.DIST_L2, 5,
                                                labelType=cv2.DIST_LABEL_PIXEL)
    owners = np.concatenate([[0], cores[cores > 0]])
    return np.where(region, owners[nearest], 0).astype(np.int32), True


def _shape(part):
    contours, _ = cv2.findContours(part.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    area = cv2.contourArea(contour)
    rect = cv2.minAreaRect(contour)
    perimeter = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, .025 * perimeter, True)
    if area / max(1, rect[1][0] * rect[1][1]) >= .88 and 4 <= len(approx) <= 6:
        return "square", contour
    detailed = cv2.approxPolyDP(contour, .004 * perimeter, True).reshape(-1, 2).astype(float)
    orientation = cv2.contourArea(detailed.astype(np.float32), oriented=True)
    sharp = 0
    for i, point in enumerate(detailed):
        a, b = detailed[i - 1] - point, detailed[(i + 1) % len(detailed)] - point
        cosine = float(a @ b) / max(1e-6, np.linalg.norm(a) * np.linalg.norm(b))
        cross = a[0]*b[1] - a[1]*b[0]
        sharp += cosine > .35 and cross*orientation < 0  # outward tips, not inward notches
    if sharp >= 4:
        return "ray", contour
    hull = cv2.convexHull(contour, returnPoints=False)
    if len(contour) > len(hull) > 3:
        try:
            defects = cv2.convexityDefects(contour, hull)
        except cv2.error:
            defects = None
        if defects is not None:
            _, _, w, h = cv2.boundingRect(contour)
            if (defects.reshape(-1, 4)[:, 3] / 256 > max(3, min(w, h) * .035)).sum() >= 4:
                return "cloud", contour
    return "round", contour


def _overlap(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy / max(1, (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - ix*iy)


def _plausible(body, bright=None):
    """White panel background winds around artwork; a balloon is more compact."""
    if bright is not None and np.count_nonzero(body & bright) < .85*np.count_nonzero(body):
        return False  # screentone/art background, not a predominantly plain light interior
    contours, _ = cv2.findContours(body, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False
    contour = max(contours,key=cv2.contourArea)
    hull = cv2.convexHull(contour)
    solidity = cv2.contourArea(contour)/max(1,cv2.contourArea(hull))
    complexity = cv2.arcLength(contour,True)/max(1,cv2.arcLength(hull,True))
    return solidity >= .7 and complexity <= 1.85


def _outlined_children(boxes, labels, stats, ink, bright, scale, protected, size):
    """Visible interior borders already separate overlapping balloons into components.

    A text detector may still span both. Keep complete glyphs and require ample
    enclosed whitespace, so counters/isolated character strokes cannot be children.
    """
    sh, sw = labels.shape
    width, height = size
    out, evidence = [], {}
    for index, box in enumerate(boxes):
        original = (box.x1, box.y1, box.x2, box.y2)
        if box.kind == "sfx" or any(_overlap(original, p) > .5 for p in protected):
            out.append(box)
            continue
        x1, y1 = max(0, round(box.x1*scale)), max(0, round(box.y1*scale))
        x2, y2 = min(sw, round(box.x2*scale)), min(sh, round(box.y2*scale))
        crop = ink[y1:y2,x1:x2]
        hits = np.bincount(labels[y1:y2,x1:x2][crop > 0], minlength=len(stats))
        candidates = []
        for parent in np.flatnonzero(hits >= max(MIN_INK, .15*crop.sum())):
            x, y, w, h, area = map(int, stats[parent])
            if (parent and x > 0 and y > 0 and x+w < sw and y+h < sh
                    and 3*hits[parent] <= area <= .3*sw*sh and min(w,h) >= 12):
                candidates.append(int(parent))
        if not 2 <= len(candidates) <= 6 or sum(hits[p] for p in candidates) < .9*max(1, crop.sum()):
            out.append(box)
            continue
        children = []
        child_evidence = {}
        for part, parent in enumerate(candidates, 1):
            ys, xs = np.nonzero((crop > 0) & (labels[y1:y2,x1:x2] == parent))
            coords = (max(0,int((xs.min()+x1)/scale)-2), max(0,int((ys.min()+y1)/scale)-2),
                      min(width,int(np.ceil((xs.max()+x1+1)/scale))+2), min(height,int(np.ceil((ys.max()+y1+1)/scale))+2))
            x, y, w, h, _ = map(int, stats[parent])
            body = _filled(labels[y:y+h,x:x+w] == parent)
            if not _plausible(body,bright[y:y+h,x:x+w]):
                children = []
                break
            kind, contour = _shape(body)
            contour = cv2.approxPolyDP(contour,max(.8,.003*cv2.arcLength(contour,True)),True).reshape(-1,2)
            outline = [[round((px+x)/scale),round((py+y)/scale)] for px,py in contour]
            children.append(TextBox(*coords,box.score,box.kind))
            child_evidence[coords] = {"parent":len(stats)+index,"part":part,"parts":len(candidates),
                                      "shape":kind,"outline":outline,"split":True,"uncertain":False}
        out.extend(children or [box])
        if children:
            evidence.update(child_evidence)
    return out, evidence


def refine(image, text_mask, boxes, protected=()):
    """Return (text boxes, evidence keyed by box). Ambiguous ink is never cut.

    Evidence is supplementary: open, dark or unbounded balloons keep the existing
    classifier. Multiple text columns in one oval stay one original text box.
    Protected boxes represent manual work and are never automatically divided.
    """
    if not boxes:
        return list(boxes), {}
    h, w = image.shape[:2]
    scale = min(1., MAX_SIDE / max(h, w))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray.copy()
    pred = cv2.resize(text_mask, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    ink = ((pred > .3) & (gray < 190)).astype(np.uint8)
    paper = max(220, min(245, float(np.percentile(gray,95))-10))
    bright = ((gray >= paper) | (ink > 0)).astype(np.uint8)
    # Remove actual predicted lettering, not the whole encompassing rectangle:
    # a spanning box can contain the border separating two overlapping balloons.
    gray[pred > .3] = 255
    walls = cv2.morphologyEx((gray < 190).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(1 - walls, connectivity=4)
    sh, sw = gray.shape
    boxes, evidence = _outlined_children(boxes,labels,stats,ink,bright,scale,protected,(w,h))
    scaled = []
    parents = {}
    for i, box in enumerate(boxes):
        coords = (box.x1, box.y1, box.x2, box.y2)
        x1, y1 = max(0, round(coords[0]*scale)), max(0, round(coords[1]*scale))
        x2, y2 = min(sw, round(coords[2]*scale)), min(sh, round(coords[3]*scale))
        scaled.append((x1, y1, x2, y2))
        if x2 <= x1 or y2 <= y1 or box.kind == "sfx":
            continue
        hits = np.bincount(labels[y1:y2, x1:x2].ravel(), minlength=n)
        hits[0] = 0
        parent = int(hits.argmax())
        if hits[parent] < .4 * (x2-x1)*(y2-y1):
            continue
        x, y, bw, bh, area = map(int, stats[parent])
        if x == 0 or y == 0 or x+bw >= sw or y+bh >= sh or area > .3*sw*sh:
            continue
        parents.setdefault(parent, []).append(i)
    replacements = {}
    for parent, indices in parents.items():
        x, y, bw, bh, area = map(int, stats[parent])
        text_area = sum((scaled[i][2]-scaled[i][0])*(scaled[i][3]-scaled[i][1]) for i in indices)
        if area > 60 * text_area:
            continue
        body = _filled(labels[y:y+bh, x:x+bw] == parent)
        if not _plausible(body,bright[y:y+bh,x:x+bw]):
            continue
        parts, conjoined = _lobes(body)
        if not conjoined:
            continue  # existing single-balloon classifier remains the authority
        occupied = np.bincount(parts[ink[y:y+bh,x:x+bw] > 0], minlength=int(parts.max())+1)
        if np.count_nonzero(occupied[1:] >= MIN_INK) < 2:
            continue  # a wide tail/empty decorative lobe is not a second text-bearing balloon
        part_ids = list(range(1, int(parts.max()) + 1))
        outlines = {}
        for part_id in part_ids:
            kind, contour = _shape(parts == part_id)
            contour = cv2.approxPolyDP(contour, max(.8, .003*cv2.arcLength(contour, True)), True).reshape(-1, 2)
            outline = [[int(round((px+x)/scale)), int(round((py+y)/scale))] for px, py in contour]
            outlines[part_id] = (kind, outline)
        for i in indices:
            box = boxes[i]
            original = (box.x1, box.y1, box.x2, box.y2)
            x1, y1, x2, y2 = scaled[i]
            left, top = max(x1, x)-x, max(y1, y)-y
            right, bottom = min(x2, x+bw)-x, min(y2, y+bh)-y
            if right <= left or bottom <= top:
                continue
            lettering = np.zeros(body.shape, np.uint8)
            lettering[top:bottom, left:right] = ink[y+top:y+bottom, x+left:x+right]
            amounts = np.bincount(parts[lettering > 0], minlength=len(part_ids)+1)
            relevant = [j for j in part_ids if amounts[j] >= max(MIN_INK, .15*lettering.sum())]
            if not relevant:
                continue
            cuts_ink = False
            if len(relevant) > 1:
                nc, glyphs = cv2.connectedComponents(lettering)
                for glyph in range(1, nc):
                    ids, counts = np.unique(parts[glyphs == glyph], return_counts=True)
                    if sum(count >= max(2, .04*counts.sum()) for pid, count in zip(ids, counts) if pid) > 1:
                        cuts_ink = True
                        break
            locked = any(_overlap(original, p) > .5 for p in protected)
            split = len(relevant) > 1 and not locked and not cuts_ink and amounts[0] <= .1*max(1, lettering.sum())
            targets = []
            if split:
                for part_id in relevant:
                    ys, xs = np.nonzero((lettering > 0) & (parts == part_id))
                    coords = (max(0, int((x+xs.min())/scale)-2), max(0, int((y+ys.min())/scale)-2),
                              min(w, int(np.ceil((x+xs.max()+1)/scale))+2), min(h, int(np.ceil((y+ys.max()+1)/scale))+2))
                    if coords[2]-coords[0] < 6 or coords[3]-coords[1] < 6:
                        targets = []
                        break
                    targets.append((TextBox(*coords, box.score, box.kind), part_id))
            if not targets:
                targets = [(box, max(relevant, key=lambda j: amounts[j]))]
            replacements[i] = [b for b, _ in targets]
            for target, part_id in targets:
                kind, outline = outlines[part_id]
                evidence[(target.x1, target.y1, target.x2, target.y2)] = {
                    "parent": parent, "part": part_id, "parts": len(part_ids), "shape": kind,
                    "outline": outline, "split": len(targets) > 1,
                    "uncertain": len(relevant) > 1 and len(targets) == 1,
                }
    return [b for i, box in enumerate(boxes) for b in replacements.get(i, [box])], evidence
