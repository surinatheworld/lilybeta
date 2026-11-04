"""Text-seeded analysis of closed, interrupted and conjoined balloons for step 2.

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
        straight = cv2.approxPolyDP(contour,.006*perimeter,True).reshape(-1,2)
        lengths = np.linalg.norm(straight-np.roll(straight,1,axis=0),axis=1)
        if np.count_nonzero(lengths >= min(rect[1])*.45) >= 3:
            return "square", contour  # two frame edges cannot turn a clipped oval into a caption
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


def _ink_groups(ink):
    """Group nearby exterior glyphs without absorbing the balloon's lettering."""
    size = max(9,min(35,round(min(ink.shape)*.09))) | 1
    _, groups = cv2.connectedComponents(cv2.dilate(ink,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(size,size))))
    return [((groups == g) & (ink > 0)) for g in np.unique(groups[ink > 0]) if g]


def _partition_lettering(ink, owners, relevant):
    """Assign whole connected strokes, including exterior text; never clip a glyph."""
    n, glyphs = cv2.connectedComponents(ink)
    stride = int(owners.max())+1
    votes = np.bincount((glyphs*stride+owners).ravel(),minlength=n*stride).reshape(n,stride)
    assignment = np.zeros(n,np.int32)
    for g in range(1,n):
        row = votes[g]
        total = int(row.sum())
        if sum(row[p] >= max(2,.04*total) for p in relevant) > 1:
            return None  # a real stroke crosses the lobe boundary
        part = int(row.argmax())
        if part in relevant and row[part] >= .95*total:
            assignment[g] = part
    mapped = assignment[glyphs]
    inside = [(p,(mapped == p) & (ink > 0)) for p in relevant]
    outside = _ink_groups(((ink > 0) & (mapped == 0)).astype(np.uint8))
    return inside + [(0,group) for group in outside]


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


def _repair_open_outlines(walls, ink, bright, boxes, scale, protected):
    """Complete a short missing arc only when the remaining outline proves two lobes.

    Directional closing is a proposal, never the output mask: most of the proposed
    contour must already exist on the page. Only that accepted contour is added,
    so unrelated panel edges and SFX joined by the same kernel remain untouched.
    """
    repaired = walls.copy()
    height, width = walls.shape
    for box in boxes:
        coords = (box.x1, box.y1, box.x2, box.y2)
        if box.kind == "sfx" or any(_overlap(coords, p) > .5 for p in protected):
            continue
        x1, y1, x2, y2 = [round(v*scale) for v in coords]
        short = min(x2-x1, y2-y1)
        if short < 24:
            continue
        pad = max(24, round(short*.8))
        left, top = max(0,x1-pad), max(0,y1-pad)
        right, bottom = min(width,x2+pad), min(height,y2+pad)
        local = repaired[top:bottom,left:right]
        lettering = np.zeros(local.shape, np.uint8)
        lettering[y1-top:y2-top,x1-left:x2-left] = ink[y1:y2,x1:x2]
        total = int(lettering.sum())
        if total < 2*MIN_INK:
            continue
        n, labels, stats, _ = cv2.connectedComponentsWithStats(1-local, connectivity=4)
        hits = np.bincount(labels[lettering > 0], minlength=n)
        hits[0] = 0
        parent = int(hits.argmax())
        px, py, pw, ph, _ = stats[parent]
        if px > 0 and py > 0 and px+pw < local.shape[1] and py+ph < local.shape[0]:
            continue  # an already enclosed interior belongs to the ordinary path
        support = cv2.dilate(local, np.ones((7,7),np.uint8))
        nearest = None
        accepted = None
        for fraction in (.4, .6, .8):
            length = min(181, max(9, round(short*fraction))) | 1
            thickness = max(3, round(length*.065)) | 1
            diagonal = np.zeros((length,length),np.uint8)
            cv2.line(diagonal,(0,0),(length-1,length-1),1,thickness)
            kernels = (np.ones((length,thickness),np.uint8),
                       np.ones((thickness,length),np.uint8), diagonal, np.fliplr(diagonal).copy())
            for kernel in kernels:
                closed = cv2.morphologyEx(local,cv2.MORPH_CLOSE,kernel)
                nc, lab, st, _ = cv2.connectedComponentsWithStats(1-closed,connectivity=4)
                counts = np.bincount(lab[lettering > 0],minlength=nc)
                counts[0] = 0
                p = int(counts.argmax())
                x, y, w, h, area = map(int,st[p])
                if (counts[p] < .9*total or area < 3*total or area > .3*width*height
                        or x == 0 or y == 0 or x+w >= local.shape[1] or y+h >= local.shape[0]):
                    continue
                body = _filled(lab[y:y+h,x:x+w] == p)
                # Closing contracts curved arcs. Snap its contour back to the
                # actual strokes before testing support; only the absent arc
                # remains a straight inferred connection between real endpoints.
                contour = max(cv2.findContours(body,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)[0],key=cv2.contourArea)
                points = contour.reshape(-1,2) + (x,y)
                if nearest is None:
                    _, nearest = cv2.distanceTransformWithLabels(1-local,cv2.DIST_L2,5,
                                                                labelType=cv2.DIST_LABEL_PIXEL)
                    ys,xs = np.nonzero(local)
                    owners = np.concatenate([[[0,0]],np.column_stack([xs,ys])])
                snapped = owners[nearest[points[:,1],points[:,0]]].astype(np.int32).reshape(-1,1,2)
                proposal = np.zeros(local.shape,np.uint8)
                cv2.drawContours(proposal,[snapped],-1,1,-1)
                x,y,w,h = cv2.boundingRect(snapped)
                if (x == 0 or y == 0 or x+w >= local.shape[1] or y+h >= local.shape[0]
                        or np.count_nonzero(proposal & lettering) < .9*total):
                    continue
                body = proposal[y:y+h,x:x+w]
                if not _plausible(body,bright[top+y:top+y+h,left+x:left+x+w]):
                    continue
                contour = max(cv2.findContours(body,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)[0],key=cv2.contourArea)
                points = contour.reshape(-1,2)
                supported = support[y+points[:,1],x+points[:,0]] > 0
                if supported.mean() < .87 or np.count_nonzero(~supported) > short*.85:
                    continue
                parts, conjoined = _lobes(body)
                amounts = np.bincount(parts[lettering[y:y+h,x:x+w] > 0],minlength=int(parts.max())+1)
                if not conjoined or np.count_nonzero(amounts[1:] >= max(MIN_INK,.15*total)) < 2:
                    continue
                accepted = contour + np.array([left+x,top+y],np.int32)
                break
            if accepted is not None:
                break
        if accepted is not None:
            cv2.drawContours(repaired,[accepted],-1,1,1)
    return repaired


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

    Evidence is supplementary: unsupported open, dark or unbounded balloons keep
    the existing classifier. Multiple text columns in one oval stay one text box.
    Protected boxes represent manual work and are never automatically divided.
    """
    if not boxes:
        return list(boxes), {}
    h, w = image.shape[:2]
    scale = min(1., MAX_SIDE / max(h, w))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image.copy()
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray.copy()
    pred = cv2.resize(text_mask, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    from .text_candidates import filter_decorations
    source_gray = gray.copy()
    # The detector supports both dark and reversed lettering. Thresholding the
    # image to dark ink here silently loses white glyphs on cards/dark artwork.
    ink = (pred > .3).astype(np.uint8)
    paper = max(220, min(245, float(np.percentile(gray,95))-10))
    bright = ((gray >= paper) | (ink > 0)).astype(np.uint8)
    # Remove actual predicted lettering, not the whole encompassing rectangle:
    # a spanning box can contain the border separating two overlapping balloons.
    gray[pred > .3] = 255
    walls = cv2.morphologyEx((gray < 190).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    walls = _repair_open_outlines(walls,ink,bright,boxes,scale,protected)
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
    outside_parent = -1
    for parent, indices in parents.items():
        x, y, bw, bh, area = map(int, stats[parent])
        text_area = sum((scaled[i][2]-scaled[i][0])*(scaled[i][3]-scaled[i][1]) for i in indices)
        if area > 60 * text_area:
            continue
        body = _filled(labels[y:y+bh, x:x+bw] == parent)
        if not _plausible(body,bright[y:y+bh,x:x+bw]):
            continue
        parts, conjoined = _lobes(body)
        occupied = np.bincount(parts[ink[y:y+bh,x:x+bw] > 0], minlength=int(parts.max())+1)
        has_text_lobes = np.count_nonzero(occupied[1:] >= MIN_INK) >= 2
        if not conjoined or (not has_text_lobes and _shape(body)[0] == "cloud"):
            # Single balloons need the same outline analysis as joined ones.
            # Font weight is not evidence of rays, and one deep scallop does
            # not cancel all the other dents of a cloud.
            kind, contour = _shape(body)
            if area > 8*text_area:
                continue  # a whole white panel is not a balloon
            contour = cv2.approxPolyDP(contour,max(.8,.003*cv2.arcLength(contour,True)),True).reshape(-1,2)
            outline = [[round((px+x)/scale),round((py+y)/scale)] for px,py in contour]
            for i in indices:
                b = boxes[i]
                evidence[(b.x1,b.y1,b.x2,b.y2)] = {
                    "parent":parent,"part":1,"parts":1,"shape":kind,"outline":outline,
                    "split":False,"uncertain":False,
                }
            continue
        if not has_text_lobes:
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
            split = len(relevant) > 1 and not locked and not cuts_ink
            targets = []
            if split:
                # Work over the complete detector rectangle, not just the
                # enclosing balloon's bounding box. Exterior captions can lie
                # outside that box as well as inside its concave corners.
                crop = ink[y1:y2,x1:x2]
                owners = np.zeros(crop.shape,np.int32)
                owners[y+top-y1:y+bottom-y1,x+left-x1:x+right-x1] = parts[top:bottom,left:right]
                partition = _partition_lettering(crop,owners,relevant)
                for part_id, glyph_mask in partition or []:
                    ys, xs = np.nonzero(glyph_mask)
                    if not len(xs):
                        targets = []
                        break
                    coords = (max(0, int((x1+xs.min())/scale)-2), max(0, int((y1+ys.min())/scale)-2),
                              min(w, int(np.ceil((x1+xs.max()+1)/scale))+2), min(h, int(np.ceil((y1+ys.max()+1)/scale))+2))
                    if coords[2]-coords[0] < 6 or coords[3]-coords[1] < 6:
                        targets = []
                        break
                    targets.append((TextBox(*coords, box.score, box.kind), part_id))
            if not targets:
                targets = [(box, max(relevant, key=lambda j: amounts[j]))]
            replacements[i] = [b for b, _ in targets]
            for target, part_id in targets:
                if part_id == 0:
                    small = min(target.x2-target.x1,target.y2-target.y1)*scale < 12
                    evidence[(target.x1,target.y1,target.x2,target.y2)] = {
                        "parent":outside_parent,"part":1,"parts":1,"shape":"free","outline":[],
                        "split":True,"uncertain":small,"note":"Chữ ngoài bóng: tách khỏi khung gộp"
                        + (" · ít nét, cần kiểm tra" if small else ""),
                    }
                    outside_parent -= 1
                    continue
                kind, outline = outlines[part_id]
                evidence[(target.x1, target.y1, target.x2, target.y2)] = {
                    "parent": parent, "part": part_id, "parts": len(part_ids), "shape": kind,
                    "outline": outline, "split": len(targets) > 1,
                    "uncertain": len(relevant) > 1 and len(targets) == 1,
                }
    out = [b for i, box in enumerate(boxes) for b in replacements.get(i, [box])]
    out = filter_decorations(source_gray,pred,out,scale,protected)
    keys = {(b.x1,b.y1,b.x2,b.y2) for b in out}
    return out, {k:v for k,v in evidence.items() if k in keys}
