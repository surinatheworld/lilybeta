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


def _lobes(region, neck_ratio=NECK_RATIO):
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
            threshold = neck_ratio * min(float(dist[old]), float(dist[point]))
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
        _, lab = cv2.connectedComponents((dist >= neck_ratio * float(dist[pole])).astype(np.uint8), connectivity=4)
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
        short = min(rect[1])
        straight = cv2.approxPolyDP(contour,max(.7,.006*short),True).reshape(-1,2).astype(float)
        vectors = straight-np.roll(straight,1,axis=0)
        lengths = np.linalg.norm(vectors,axis=1)
        corners = cv2.boxPoints(rect)
        rectangular = True
        for axis in (corners[1]-corners[0],corners[2]-corners[1]):
            axis = axis/max(1,np.linalg.norm(axis))
            aligned = (lengths >= short*.4) & (abs(vectors@axis) >= .99*lengths)
            offsets = ((straight+np.roll(straight,1,axis=0))*.5)[aligned] @ np.array([-axis[1],axis[0]])
            if len(offsets)<2 or np.ptp(offsets)<short*.6:
                rectangular = False
                break
        if rectangular:
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
            bx, by, w, h = cv2.boundingRect(contour)
            rows = defects.reshape(-1,4)
            deep = rows[rows[:,3]/256 > max(3,min(w,h)*.018)]
            points = contour[deep[:,2]].reshape(-1,2) if len(deep) else np.empty((0,2))
            angles = np.arctan2(points[:,1]-(by+h/2),points[:,0]-(bx+w/2))
            spread = len(np.unique(np.floor((angles+np.pi)/(np.pi/2))))
            if spread>=3 and (3<=len(deep)<=16 or (len(deep)>16 and np.median(deep[:,3]/256)>.04*min(w,h))):
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
    _, _, stats, _ = cv2.connectedComponentsWithStats(ink)
    sizes = [max(w,h) for x,y,w,h,area in stats[1:] if area >= 8]
    gap = max(3,min(13,round(float(np.percentile(sizes,75))*.35))) | 1 if sizes else 3
    _, words = cv2.connectedComponents(cv2.dilate(ink,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(gap,gap))))
    groups = _assign_lettering(ink, owners, relevant, words)
    if groups is not None:
        return groups
    # Dilation can join two complete glyphs across a narrow balloon neck.
    # Retry with undilated components; a connected stroke still stays intact.
    _, strokes = cv2.connectedComponents(ink)
    return _assign_lettering(ink, owners, relevant, strokes)


def _assign_lettering(ink, owners, relevant, words):
    stride = int(owners.max())+1
    count = int(words.max())+1
    votes = np.bincount((words[ink>0]*stride+owners[ink>0]),minlength=count*stride).reshape(count,stride)
    assignment = np.zeros(count,np.int32)
    for g in range(1,count):
        row = votes[g]
        total = int(row.sum())
        part = int(row.argmax())
        if sum(row[p] >= max(2,.04*total) for p in relevant) > 1 and row[part] < .75*total:
            return None  # a connected word truly spans both lobes; keep it intact
        if part in relevant and row[part] >= .75*total:
            assignment[g] = part
    mapped = assignment[words]
    inside = [(p,(mapped == p) & (ink > 0)) for p in relevant]
    outside = _ink_groups(((ink > 0) & (mapped == 0)).astype(np.uint8))
    return inside + [(0,group) for group in outside]


def _broad_neck(region):
    contour = max(cv2.findContours(region,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0],key=cv2.contourArea)
    hull = cv2.convexHull(contour,returnPoints=False)
    if len(contour) <= len(hull) or len(hull) <= 3:
        return False
    try:
        defects = cv2.convexityDefects(contour,hull)
    except cv2.error:
        return False
    if defects is None:
        return False
    depths = np.sort(defects.reshape(-1,4)[:,3]/256)[::-1]
    short = min(region.shape)
    return depths[0] > .12*short and (depths[0] > .2*short or (len(depths)>1 and depths[1]>.08*short))


def _framed_caption(body):
    """Find a rectangular writing area even if folds round off its outer ends."""
    height, width = body.shape
    if height < 1.12 * width or float(body.mean()) < .84:
        return False
    start, stop = round(height * .14), round(height * .86)
    middle = body[start:stop]
    if len(middle) < 18 or not middle.any(axis=1).all():
        return False
    left = middle.argmax(axis=1)
    right = width - middle[:, ::-1].argmax(axis=1)
    spans = right - left
    narrow, wide = np.quantile(spans, (.05, .95))
    if narrow < .88 * width or wide - narrow > .07 * width:
        return False
    yy = np.arange(len(middle))
    return all(np.quantile(abs(edge - np.polyval(np.polyfit(yy, edge, 1), yy)), .9) < .035 * width
               for edge in (left, right))


def _deep_notches(body, relative_depth=.12):
    contour = max(cv2.findContours(body, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
    hull = cv2.convexHull(contour, returnPoints=False)
    if not 3 < len(hull) < len(contour):
        return []
    try:
        defects = cv2.convexityDefects(contour, hull)
    except cv2.error:
        return []
    if defects is None:
        return []
    short = min(body.shape)
    dents = []
    for start, end, at, depth in defects.reshape(-1, 4):
        if depth < 256 * max(4, relative_depth * short):
            continue
        left, right, tip = contour[[start, end, at], 0].astype(float)
        edge = right - left
        inward = tip - (left + edge * np.clip((tip - left) @ edge / max(1, edge @ edge), 0, 1))
        dents.append((depth, tip, inward / max(1, np.linalg.norm(inward))))
    dents.sort(key=lambda item: -item[0])
    return dents


def _notch_split(body, ink, relative_depth=.12):
    """Split a wide overlap where two opposed outline dents bracket a gap in the ink."""
    dents = _deep_notches(body, relative_depth)
    short = min(body.shape)
    text_y, text_x = np.nonzero((ink > 0) & (body > 0))
    if len(text_x) < 2 * MIN_INK:
        return None
    yr, xr = np.ogrid[:body.shape[0], :body.shape[1]]
    for i, (_, first, inward) in enumerate(dents[:5]):
        for _, second, other_inward in dents[i + 1:5]:
            axis = second - first
            length = np.linalg.norm(axis)
            if length < .42 * short or inward @ other_inward > -.35:
                continue
            side = ((text_x - first[0]) * axis[1] - (text_y - first[1]) * axis[0]) / length
            gap = max(2.0, .012 * short)
            if min((side >= gap).sum(), (side <= -gap).sum()) < max(MIN_INK, .09 * len(side)):
                continue
            if (abs(side) < gap).any():
                side = _ink_gap_seam(body, ink, first, second, gap)
                if side is None:
                    continue
            else:
                side = (xr - first[0]) * axis[1] - (yr - first[1]) * axis[0]
            parts = np.where(body > 0, 1 + (side > 0), 0).astype(np.int32)
            sizes = np.bincount(parts[body > 0], minlength=3)
            text_sizes = np.bincount(parts[(ink > 0) & (body > 0)], minlength=3)
            if min(sizes[1:]) >= .16 * sizes.sum() and min(text_sizes[1:]) >= max(MIN_INK, .09 * text_sizes.sum()):
                return parts
    return None


def _ink_gap_seam(body, ink, first, second, gap):
    """Follow a clear route near the chord when the straight chord clips a glyph."""
    transpose = abs(second[0] - first[0]) > abs(second[1] - first[1])
    if transpose:
        body, ink = body.T, ink.T
        first, second = first[::-1], second[::-1]
    (x0, y0), (x1, y1) = sorted((first.astype(int), second.astype(int)), key=lambda p: p[1])
    ys = np.arange(body.shape[0])
    guide = x0 + (ys - y0) * (x1 - x0) / (y1 - y0)
    xs = np.arange(body.shape[1])
    band = max(8, round(.17 * min(body.shape)))
    wall = cv2.dilate(ink.astype(np.uint8), np.ones((2 * int(gap) + 1,) * 2, np.uint8))
    allowed = (cv2.dilate(body, np.ones((3, 3), np.uint8)) > 0) & (wall == 0)
    trace = np.zeros((y1 - y0 + 1, len(xs)), np.int8)
    cost = np.full(len(xs), np.inf)
    cost[x0] = 0
    steps = np.array([-2, -1, 0, 1, 2], np.int8)
    # Penalize deviations from the observed neck and changes of direction;
    # never route through predicted lettering to gain width.
    for y in range(y0 + 1, y1 + 1):
        previous = np.pad(cost, (2, 2), constant_values=np.inf)
        moves = np.stack([previous[k:k + len(xs)] + .22 * abs(d) for k, d in enumerate(steps)])
        pick = moves.argmin(axis=0)
        trace[y - y0] = steps[pick]
        deviation = abs(xs - guide[y])
        cost = moves[pick, xs] + (deviation / band) ** 2
        cost[(deviation > band) | ~allowed[y]] = np.inf
    if not np.isfinite(cost[x1]):
        return None
    x = x1
    for y in range(y1, y0, -1):
        guide[y] = x
        x += int(trace[y - y0, x])
    side = xs[None, :] - guide[:, None]
    return side.T if transpose else side


def _frame_notch_split(body, ink, gray, x, y):
    """Separate text-bearing lobes cut off by a panel rule or the page edge."""
    height, width = body.shape
    edges = []
    for border, edge in ((y, 0), (y + height, height - 1)):
        rule = gray[max(0, border - 2):border + 5, x:x + width]
        cropped = border == 0 or border == gray.shape[0]
        ruled = rule.size and (rule < 80).mean(axis=1).max() >= .89
        if (cropped or ruled) and body[edge].mean() >= .62:
            edges.append(edge)
    if not edges:
        return None
    dents = _deep_notches(body, .08)
    column = ((body > 0) & (ink > 0)).sum(axis=0)
    transitions = np.flatnonzero(np.diff(np.r_[False, column <= 1, False]))
    gaps = [(left, right, (left + right) // 2) for left, right in zip(transitions[::2], transitions[1::2])
            if right - left >= .025 * width
            and min(column[:left].sum(), column[right:].sum()) >= max(MIN_INK, .13 * column.sum())]
    # For a balloon touching a row divider, the missing end of the
    # separation must meet that divider in the same clear column gap.
    for edge in edges:
        cuts = {}
        for _, point, inward in dents:
            delta = edge - point[1]
            if abs(delta) < .48 * height or inward[1] * np.sign(delta) < .45:
                continue
            eligible = [(abs(middle - point[0]), middle) for _, _, middle in gaps
                        if abs(middle - point[0]) < .085 * width]
            for _, middle in sorted(eligible):
                if body[edge, middle] and middle not in cuts:
                    seam = _ink_gap_seam(body, ink, point, np.array([middle, edge]), 2)
                    if seam is not None:
                        cuts[middle] = seam
                        break
        # A flat convex-hull edge can span several equal lobes and report only
        # one defect. Inspect the actual outline opposite each remaining gap.
        distance = height - 1 - (body[::-1] if edge == 0 else body).argmax(axis=0)
        distance[~body.any(axis=0)] = 0
        reach = max(12, round(.2 * height))
        for left, right, middle in gaps:
            if middle in cuts or not body[edge, middle]:
                continue
            at = left + int(np.argmin(distance[left:right]))
            span = distance[at]
            shoulders = (distance[max(0, at - reach):at], distance[at + 1:at + reach + 1])
            if span < .48 * height or any(not len(s) or s.max() - span < .08 * height for s in shoulders):
                continue
            point = np.array([at, span if edge == 0 else edge - span])
            seam = _ink_gap_seam(body, ink, point, np.array([middle, edge]), 2)
            if seam is not None:
                cuts[middle] = seam
        parts = body.astype(np.int32)
        for _, seam in sorted(cuts.items()):
            proposed = parts + ((body > 0) & (seam > 0))
            sizes = np.bincount(proposed[body > 0])[1:]
            lettering = np.bincount(proposed[(body > 0) & (ink > 0)], minlength=len(sizes) + 1)[1:]
            if min(sizes) >= .13 * body.sum() and min(lettering) >= max(MIN_INK, .10 * column.sum()):
                parts = proposed
        if parts.max() > 1:
            return parts
    return None


def _split_weak_peak(body, ink):
    """A little outline bump should not make a third lobe between two clear text blocks."""
    dents = _deep_notches(body)
    if len(dents) != 2 or dents[0][2] @ dents[1][2] > -.25:
        return None
    tips = np.array([item[1] for item in dents])
    text = (body > 0) & (ink > 0)
    choices = []
    for axis in (0, 1):
        counts = text.sum(axis=axis)
        changes = np.flatnonzero(np.diff(np.r_[False, counts == 0, False]))
        for first, last in zip(changes[::2], changes[1::2]):
            middle = (first + last) // 2
            if (last - first >= .12 * min(body.shape)
                    and tips[:, axis].min() <= middle <= tips[:, axis].max()
                    and min(counts[:first].sum(), counts[last:].sum()) >= .13 * counts.sum()):
                choices.append((last - first, axis, middle))
    if not choices:
        return None
    _, axis, middle = max(choices)
    y, x = np.ogrid[:body.shape[0], :body.shape[1]]
    side = (x if axis == 0 else y) >= middle
    parts = np.where(body > 0, 1 + side, 0).astype(np.int32)
    sizes = np.bincount(parts[body > 0], minlength=3)
    return parts if min(sizes[1:]) >= .16 * sizes.sum() else None


def _plausible(body, bright=None):
    """White panel background winds around artwork; a balloon is more compact."""
    if bright is not None and np.count_nonzero(body & bright) < .85*np.count_nonzero(body):
        return False  # screentone/art background, not a predominantly plain light interior
    if bright is not None:
        _,_,islands,_ = cv2.connectedComponentsWithStats((body & (1-bright)).astype(np.uint8))
        if any(a > max(36,.02*body.sum()) and min(w,h)>=8 for x,y,w,h,a in islands[1:]):
            return False  # a mostly white panel can still enclose a character's black hair
    contours, _ = cv2.findContours(body, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return False
    contour = max(contours,key=cv2.contourArea)
    hull = cv2.convexHull(contour)
    solidity = cv2.contourArea(contour)/max(1,cv2.contourArea(hull))
    complexity = cv2.arcLength(contour,True)/max(1,cv2.arcLength(hull,True))
    return solidity >= .7 and complexity <= 1.85


def _ellipse_match(body,threshold=.9):
    contour = max(cv2.findContours(body,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)[0],key=cv2.contourArea)
    if len(contour)<5:
        return False
    fitted = np.zeros(body.shape,np.uint8)
    cv2.ellipse(fitted,cv2.fitEllipse(contour),1,-1)
    return np.count_nonzero(fitted & body) >= threshold*max(1,np.count_nonzero(fitted | body))


def _thought_tail(body,x,y,labels,stats,gray,text_boxes=()):
    """Empty round satellites on plain paper, with a chain or an attached bulb."""
    h,w = body.shape;short=min(w,h);pad=max(12,round(short*.35))
    candidates=np.flatnonzero((stats[:,0]+stats[:,2]>x-pad)&(stats[:,0]<x+w+pad)
        &(stats[:,1]+stats[:,3]>y-pad)&(stats[:,1]<y+h+pad)
        &(stats[:,2]>=4)&(stats[:,3]>=4)&(stats[:,2]<=short*.22)&(stats[:,3]<=short*.22)
        &(stats[:,4]>=8)&(stats[:,4]<body.sum()*.05))
    contour=max(cv2.findContours(body,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)[0],key=cv2.contourArea)
    boundary=contour.reshape(-1,2).astype(float)
    satellites=[]
    for label in candidates:
        if not label:continue
        sx,sy,sw,sh,area=map(int,stats[label])
        component=(labels[sy:sy+sh,sx:sx+sw]==label).astype(np.uint8)
        if any(a<=sx+sw/2<c and b<=sy+sh/2<d for a,b,c,d in text_boxes):continue
        if np.count_nonzero((component>0)&(gray[sy:sy+sh,sx:sx+sw]<180))>.1*area:continue
        ring=max(cv2.findContours(component,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0],key=cv2.contourArea)
        perimeter=cv2.arcLength(ring,True)
        if (min(sw,sh)<.6*max(sw,sh) or 4*np.pi*cv2.contourArea(ring)<.65*perimeter**2
                or not _ellipse_match(component,.8)):continue
        radius=max(sw,sh)
        left,top=max(0,sx-radius),max(0,sy-radius)
        context=gray[top:sy+sh+radius,left:sx+sw+radius]
        if np.mean(context>=220)<.72:continue
        center=np.array([sx+sw/2-x,sy+sh/2-y])
        ix,iy=np.rint(center).astype(int)
        if 0<=ix<w and 0<=iy<h and body[iy,ix]:
            continue  # scene bubbles inside a panel are not an exterior thought tail
        distance=np.linalg.norm(boundary-center,axis=1)
        nearest=boundary[int(distance.argmin())]
        if distance.min()>short*.35:continue
        satellites.append((center,nearest,float(distance.min())))
    for center,nearest,distance in satellites:
        for other,_,farther in satellites:
            a=center-np.array([w/2,h/2]);b=other-np.array([w/2,h/2])
            if (distance<short*.2 and farther>distance and 3<np.linalg.norm(center-other)<short*.3
                    and a@b>.85*np.linalg.norm(a)*np.linalg.norm(b)):
                return True
        if len(contour)>=5 and distance<max(10,short*.2):
            (cx,cy),(ew,eh),angle=cv2.fitEllipse(contour)
            delta=nearest-np.array([cx,cy]);theta=np.deg2rad(angle)
            u=delta[0]*np.cos(theta)+delta[1]*np.sin(theta)
            v=-delta[0]*np.sin(theta)+delta[1]*np.cos(theta)
            if (u/max(1,ew/2))**2+(v/max(1,eh/2))**2>1.12:
                return True
    return False


def _radial_outline(walls,box,gray):
    x1,y1,x2,y2=box;short=min(x2-x1,y2-y1);extent=max(x2-x1,y2-y1)
    if short<16:return False
    near=max(12,round(short*.5))
    left,top=max(0,x1-near),max(0,y1-near)
    context=gray[top:y2+near,left:x2+near]
    ring=np.ones(context.shape,bool)
    ring[y1-top:y2-top,x1-left:x2-left]=False
    if np.mean(context[ring]>=220)<.9:return False
    pad=round(extent*.9)
    left,top=max(0,x1-pad),max(0,y1-pad)
    crop=walls[top:y2+pad,left:x2+pad]
    cx,cy=(x1+x2)/2-left,(y1+y2)/2-top
    lines=cv2.HoughLinesP(crop*255,1,np.pi/180,max(8,round(short*.12)),
                          minLineLength=max(8,short*.13),maxLineGap=2)
    bins=set();count=0
    for a,b,c,d in [] if lines is None else lines.reshape(-1,4):
        dx,dy=float(c-a),float(d-b);rx,ry=(a+c)/2-cx,(b+d)/2-cy
        length=np.hypot(dx,dy);radius=np.hypot(rx,ry)
        if not (.45*short<radius<1.5*extent) or abs(dx*rx+dy*ry)<.92*length*radius:continue
        ex=(x2-x1)/2+.25*short;ey=(y2-y1)/2+.25*short
        if not 1<np.hypot(rx/ex,ry/ey)<1.9:continue
        xx=np.rint(np.linspace(a,c,max(2,round(length)))).astype(int)
        yy=np.rint(np.linspace(b,d,len(xx))).astype(int)
        if np.mean(crop[yy,xx]>0)<.85:continue
        bins.add(int((np.arctan2(ry,rx)+np.pi)*16/(2*np.pi))%16);count+=1
    return count>=10 and len(bins)>=10


def _complete_ellipse(walls,lettering,bright):
    """Fit an observed C-shaped arc, then verify almost all of its circumference."""
    if not lettering.any():return None
    ys,xs=np.nonzero(lettering);tx1,tx2=xs.min(),xs.max()+1;ty1,ty2=ys.min(),ys.max()+1
    contours=cv2.findContours(walls,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)[0]
    contours=sorted(contours,key=lambda c:cv2.arcLength(c,True),reverse=True)[:8]
    support=cv2.dilate(walls,np.ones((9,9),np.uint8))
    trials=[]
    for observed in contours:
        if len(observed)<12 or cv2.arcLength(observed,True)<2*((tx2-tx1)+(ty2-ty1)):continue
        trials.append(observed)
        ox,oy,ow,oh=cv2.boundingRect(observed)
        if ox<=tx1 and oy<=ty1 and ox+ow>=tx2 and oy+oh>=ty2:
            for fraction in (.2,.35,.5):
                length=max(12,round(len(observed)*fraction))
                extended=np.concatenate([observed,observed])
                trials.extend(extended[start:start+length] for start in range(0,len(observed),max(1,len(observed)//8)))
    best=None;score=.85
    for observed in trials:
        try:ellipse=cv2.fitEllipse(observed[::max(1,len(observed)//4096)])
        except cv2.error:continue
        (cx,cy),(ew,eh),angle=ellipse
        if min(ew,eh)<12 or max(ew,eh)>2*max(walls.shape):continue
        points=cv2.ellipse2Poly((round(cx),round(cy)),(round(ew/2),round(eh/2)),round(angle),0,360,5)
        if (points[:,0].min()<=0 or points[:,1].min()<=0 or points[:,0].max()>=walls.shape[1]-1
                or points[:,1].max()>=walls.shape[0]-1):continue
        coverage=float(np.mean(support[points[:,1],points[:,0]]>0))
        if coverage<score:continue
        body=np.zeros(walls.shape,np.uint8);cv2.ellipse(body,ellipse,1,-1)
        contour=max(cv2.findContours(body,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)[0],key=cv2.contourArea)
        x,y,w,h=cv2.boundingRect(contour);area=int(body.sum())
        if (x==0 or y==0 or x+w>=walls.shape[1] or y+h>=walls.shape[0]
                or not 1.15*(tx2-tx1)*(ty2-ty1)<=area<=8*(tx2-tx1)*(ty2-ty1)
                or np.count_nonzero(body & lettering)<.98*lettering.sum()):continue
        points=contour.reshape(-1,2)
        coverage=float(np.mean(support[points[:,1],points[:,0]]>0))
        if coverage<score or not _plausible(body,bright):continue
        best=contour;score=coverage
        if score>.99:break
    return best


def _dark_container(gray,pred,box,cache):
    if not cache:
        dark=((gray<100)|(pred>.3)).astype(np.uint8)
        cache.extend(cv2.connectedComponentsWithStats(dark,connectivity=4)[1:3])
    labels,stats=cache;x1,y1,x2,y2=box
    hits=np.bincount(labels[y1:y2,x1:x2].ravel(),minlength=len(stats));hits[0]=0
    p=int(hits.argmax());x,y,w,h,area=map(int,stats[p])
    if not p or area>20*(x2-x1)*(y2-y1) or hits[p]<.5*(x2-x1)*(y2-y1):return "free",[]
    body=(labels[y:y+h,x:x+w]==p).astype(np.uint8)
    kind,contour=_shape(body)
    if (kind=="square" and area>=.85*w*h) or (kind=="round" and _ellipse_match(body)):
        return kind,(contour.reshape(-1,2)+(x,y)).tolist()
    return "free",[]


def _repair_open_outlines(walls, ink, bright, boxes, scale, protected):
    """Complete a short missing arc supported by the remaining observed contour.

    Directional closing is a proposal, never the output mask: most of the proposed
    contour must already exist on the page. Only that accepted contour is added,
    so unrelated panel edges and SFX joined by the same kernel remain untouched.
    """
    repaired = walls.copy()
    height, width = walls.shape
    _, enclosed, enclosed_stats, _ = cv2.connectedComponentsWithStats(1-walls, connectivity=4)
    closed_parents = {}
    for box in boxes:
        coords = (box.x1, box.y1, box.x2, box.y2)
        if box.kind == "sfx" or any(_overlap(coords, p) > .5 for p in protected):
            continue
        x1, y1, x2, y2 = [round(v*scale) for v in coords]
        # Inspect the full-page component before cropping. A closed, tall
        # balloon may extend beyond this local ROI; cropping must not turn its
        # edge into a fictitious gap and close each lobe independently.
        seed = ink[y1:y2,x1:x2] > 0
        if seed.any():
            hits = np.bincount(enclosed[y1:y2,x1:x2][seed], minlength=len(enclosed_stats))
            hits[0] = 0
            parent = int(hits.argmax())
            px, py, pw, ph, _ = enclosed_stats[parent]
            if (hits[parent] >= .9*seed.sum() and px>0 and py>0
                    and px+pw<width and py+ph<height):
                # A closed panel also encloses an open balloon. Its artwork and
                # winding background must not suppress recovery of the real arc.
                if parent not in closed_parents:
                    body = _filled(enclosed[py:py+ph,px:px+pw] == parent)
                    closed_parents[parent] = _plausible(body, bright[py:py+ph,px:px+pw])
                if closed_parents[parent]:
                    continue
        short = min(x2-x1, y2-y1)
        if short < 24:
            continue
        pad = max(24, round(short*1.2))
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
                    if (conjoined or area>8*(x2-x1)*(y2-y1) or supported.mean()<.93
                            or not _ellipse_match(body)):
                        continue
                accepted = contour + np.array([left+x,top+y],np.int32)
                break
            if accepted is not None:
                break
        if accepted is not None:
            cv2.drawContours(repaired,[accepted],-1,1,1)
        else:
            arc=_complete_ellipse(local,lettering,bright[top:bottom,left:right])
            if arc is not None:
                cv2.drawContours(repaired,[arc+np.array([left,top],np.int32)],-1,1,1)
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
        for parent in np.flatnonzero(hits >= MIN_INK):
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


def _merge_repeated_lobe_boxes(boxes, evidence, text_mask, protected):
    """Consolidate a nested re-detection of the same ink in one established lobe."""
    merged = []
    for box in boxes:
        rect = (box.x1, box.y1, box.x2, box.y2)
        info = evidence.get(rect, {})
        if info.get("parts", 1) > 1 and not info.get("uncertain") and not any(_overlap(rect, p) > .5 for p in protected):
            for i, prior in enumerate(merged):
                old = (prior.x1, prior.y1, prior.x2, prior.y2)
                other = evidence.get(old, {})
                if (other.get("uncertain") or (info["parent"], info["part"]) != (other.get("parent"), other.get("part"))
                        or any(_overlap(old, p) > .5 for p in protected)):
                    continue
                x, y, right, bottom = max(old[0], rect[0]), max(old[1], rect[1]), min(old[2], rect[2]), min(old[3], rect[3])
                if right <= x or bottom <= y:
                    continue
                shared = np.count_nonzero(text_mask[y:bottom, x:right] > .3)
                counts = [np.count_nonzero(text_mask[b:d, a:c] > .3) for a, b, c, d in (old, rect)]
                if shared < .84 * min(counts) or shared < MIN_INK:
                    continue
                full = (min(old[0], rect[0]), min(old[1], rect[1]), max(old[2], rect[2]), max(old[3], rect[3]))
                merged[i] = TextBox(*full, max(prior.score, box.score), box.kind)
                evidence[full] = {**info, "split": info.get("split", False) or other.get("split", False)}
                break
            else:
                merged.append(box)
        else:
            merged.append(box)
    return merged


def refine(image, text_mask, boxes, protected=()):
    """Return (text boxes, evidence keyed by box). Ambiguous ink is never cut.

    Unsupported boundaries are marked for review as unboxed text. Multiple text
    columns in one oval stay one text box.
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
    _,raw_white,raw_stats,_=cv2.connectedComponentsWithStats((source_gray>=200).astype(np.uint8),connectivity=4)
    # The detector supports both dark and reversed lettering. Thresholding the
    # image to dark ink here silently loses white glyphs on cards/dark artwork.
    # Pool a binary mask before scaling: interpolating probabilities first can
    # erase a small stroke just above the threshold, then silently drop it when
    # a spanning rectangle is replaced by child rectangles.
    ink = cv2.resize((text_mask > .3).astype(np.float32), (gray.shape[1], gray.shape[0]),
                     interpolation=cv2.INTER_AREA) > 0
    ink = ink.astype(np.uint8)
    paper = max(220, min(245, float(np.percentile(gray,95))-10))
    bright = ((gray >= paper) | (ink > 0)).astype(np.uint8)
    # Remove actual predicted lettering, not the whole encompassing rectangle:
    # a spanning box can contain the border separating two overlapping balloons.
    gray[ink > 0] = 255
    walls = cv2.morphologyEx((gray < 190).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    walls = _repair_open_outlines(walls,ink,bright,boxes,scale,protected)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(1 - walls, connectivity=4)
    sh, sw = gray.shape
    boxes, evidence = _outlined_children(boxes,labels,stats,ink,bright,scale,protected,(w,h))
    scaled = []
    parents = {}
    validated=set();dark_cache=[];negative_parent=-1
    def separate_evidence(shape,outline=(),uncertain=False,note=""):
        nonlocal negative_parent
        item={"parent":negative_parent,"part":1,"parts":1,"shape":shape,"outline":list(outline),
              "split":False,"uncertain":uncertain,"note":note}
        negative_parent-=1
        return item
    for i, box in enumerate(boxes):
        coords = (box.x1, box.y1, box.x2, box.y2)
        x1, y1 = max(0, round(coords[0]*scale)), max(0, round(coords[1]*scale))
        x2, y2 = min(sw, round(coords[2]*scale)), min(sh, round(coords[3]*scale))
        scaled.append((x1, y1, x2, y2))
        if x2 <= x1 or y2 <= y1 or box.kind == "sfx":
            continue
        pad=max(3,min(20,round(min(x2-x1,y2-y1)*.3)))
        px1,py1=max(0,x1-pad),max(0,y1-pad)
        background=source_gray[py1:y2+pad,px1:x2+pad][pred[py1:y2+pad,px1:x2+pad]<=.3]
        if len(background)>12 and np.median(background)<100:
            kind,outline=_dark_container(source_gray,pred,(x1,y1,x2,y2),dark_cache)
            outline=[[round(px/scale),round(py/scale)] for px,py in outline]
            evidence[coords]=separate_evidence(kind,outline)
            continue
        hits = np.bincount(labels[y1:y2, x1:x2].ravel(), minlength=n)
        hits[0] = 0
        parent = int(hits.argmax())
        if hits[parent] < .4 * (x2-x1)*(y2-y1):
            continue
        x, y, bw, bh, area = map(int, stats[parent])
        if x == 0 or y == 0 or x+bw >= sw or y+bh >= sh or area > .3*sw*sh:
            if area>8*(x2-x1)*(y2-y1):continue
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
        body_kind, body_contour = _shape(body)
        if area > 5*text_area and body_kind=="cloud" and not _ellipse_match(body):
            continue
        thought = _thought_tail(body,x,y,raw_white,raw_stats,source_gray,
                               [s for s,b in zip(scaled,boxes) if b.kind!="sfx"])
        local_ink = ink[y:y+bh, x:x+bw]
        frame = _framed_caption(body)
        parts, conjoined = (body.astype(np.int32), False) if frame else _lobes(body)
        if not frame and not conjoined and body_kind in ("round", "cloud") and not thought:
            partition = _frame_notch_split(body, local_ink, source_gray, x, y)
            if partition is None and body_kind == "cloud" and len(_deep_notches(body, .075)) == 2:
                partition = _notch_split(body, local_ink, .075)
            if partition is not None:
                parts, conjoined = partition, True
        if not frame and not conjoined and _broad_neck(body):
            partition = _notch_split(body, local_ink) if body_kind == "round" and not thought else None
            if partition is not None:
                parts, conjoined = partition, True
            else:
                parts, conjoined = _lobes(body,.94)
                if not conjoined:
                    parts, conjoined = _lobes(body,.99)
        occupied = np.bincount(parts[local_ink > 0], minlength=int(parts.max())+1)
        active = occupied[1:][occupied[1:] >= MIN_INK]
        if body_kind == "round" and len(indices) == 1 and len(active) == 3 and min(active) < .05 * sum(active):
            partition = _split_weak_peak(body, local_ink)
            if partition is not None:
                parts = partition
                occupied = np.bincount(parts[local_ink > 0], minlength=3)
        has_text_lobes = np.count_nonzero(occupied[1:] >= MIN_INK) >= 2
        if not conjoined or (not has_text_lobes and body_kind == "cloud"):
            # Single balloons need the same outline analysis as joined ones.
            # Font weight is not evidence of rays, and one deep scallop does
            # not cancel all the other dents of a cloud.
            kind, contour = body_kind, body_contour
            if frame:
                kind = "square"
            elif thought:
                kind = "cloud"
            if area > (8 if kind=="square" else 5)*text_area and not _ellipse_match(body):
                continue  # a whole white panel is not a balloon
            contour = cv2.approxPolyDP(contour,max(.8,.003*cv2.arcLength(contour,True)),True).reshape(-1,2)
            outline = [[round((px+x)/scale),round((py+y)/scale)] for px,py in contour]
            for i in indices:
                validated.add((boxes[i].x1,boxes[i].y1,boxes[i].x2,boxes[i].y2))
                b = boxes[i]
                if evidence.get((b.x1,b.y1,b.x2,b.y2),{}).get("parts",1)>1:
                    continue  # preserve an overlap split already proved by the interior border
                evidence[(b.x1,b.y1,b.x2,b.y2)] = {
                    "parent":parent,"part":1,"parts":1,"shape":kind,"outline":outline,
                    "split":False,"uncertain":False,
                }
            continue
        if not has_text_lobes:
            validated.update((boxes[i].x1,boxes[i].y1,boxes[i].x2,boxes[i].y2) for i in indices)
            continue  # a wide tail/empty decorative lobe is not a second text-bearing balloon
        part_ids = [int(part) for part in np.flatnonzero(occupied[1:] >= MIN_INK) + 1]
        if not thought:
            thought = any(_thought_tail((parts==p).astype(np.uint8),x,y,raw_white,raw_stats,source_gray,
                          [s for s,b in zip(scaled,boxes) if b.kind!="sfx"]) for p in part_ids)
        outlines = {}
        for part_id in part_ids:
            kind, contour = _shape(parts == part_id)
            if thought:kind="cloud"
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
            amounts = np.bincount(parts[lettering > 0], minlength=len(occupied))
            relevant = [j for j in part_ids if amounts[j] >= MIN_INK]
            if not relevant:
                continue
            locked = any(_overlap(original, p) > .5 for p in protected)
            split = len(relevant) > 1 and not locked
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
                    if part_id and (coords[2]-coords[0] < 6 or coords[3]-coords[1] < 6):
                        targets = []
                        break
                    targets.append((TextBox(*coords, box.score, box.kind), part_id))
            if not targets:
                targets = [(box, max(relevant, key=lambda j: amounts[j]))]
            replacements[i] = [b for b, _ in targets]
            for target, part_id in targets:
                if part_id == 0:
                    small = min(target.x2-target.x1,target.y2-target.y1)*scale < 12
                    meta=separate_evidence("free",uncertain=small,note="Chữ ngoài bóng: tách khỏi khung gộp"
                        + (" · ít nét, cần kiểm tra" if small else ""))
                    meta["split"]=True
                    evidence[(target.x1,target.y1,target.x2,target.y2)]=meta
                    continue
                kind, outline = outlines[part_id]
                evidence[(target.x1, target.y1, target.x2, target.y2)] = {
                    "parent": parent, "part": part_id, "parts": len(part_ids), "shape": kind,
                    "outline": outline, "split": len(targets) > 1,
                    "uncertain": len(relevant) > 1 and len(targets) == 1,
                }
                if evidence[(target.x1, target.y1, target.x2, target.y2)]["uncertain"]:
                    evidence[(target.x1, target.y1, target.x2, target.y2)]["alternatives"] = [
                        {"part": p, "shape": outlines[p][0], "outline": outlines[p][1]} for p in relevant
                    ]
    out = [b for i, box in enumerate(boxes) for b in replacements.get(i, [box])]
    for box in out:
        coords=(box.x1,box.y1,box.x2,box.y2)
        if (box.kind=="sfx" or coords in evidence or coords in validated
                or any(_overlap(coords,p)>.5 for p in protected)):continue
        scaled_box=tuple(round(v*scale) for v in coords)
        if _radial_outline(walls,scaled_box,source_gray):
            evidence[coords]=separate_evidence("cloud")
        else:
            evidence[coords]=separate_evidence("free",uncertain=True,note="Không xác nhận được viền bóng: kiểm tra chữ ngoài khung")
    out = filter_decorations(source_gray,pred,out,scale,protected)
    out = _merge_repeated_lobe_boxes(out, evidence, text_mask, protected)
    keys = {(b.x1,b.y1,b.x2,b.y2) for b in out}
    return out, {k:v for k,v in evidence.items() if k in keys}
