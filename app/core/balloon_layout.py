"""Keep rendered text inside the specific lobe selected by semantic assignment."""
from collections import Counter
from dataclasses import replace

import cv2
import numpy as np

from .typeset import block_box, place_in_shape
from .typeset_profiles import profile_style


def panel_limits(page, anchor):
    """Straight panel borders stop a leaked white balloon mask at the gutter."""
    image=getattr(page,'original',None)
    if image is None:return (0,0,*page.size)
    lines=getattr(page,'_lilyzono_panel_lines',None)
    if lines is None:
        h,w=image.shape[:2];black=(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)<80).astype(np.uint8)
        lines=[]
        for vertical,kernel in ((True,(1,max(40,round(h*.1)))),(False,(max(40,round(w*.1)),1))):
            mask=cv2.morphologyEx(black,cv2.MORPH_OPEN,np.ones(kernel[::-1],np.uint8))
            count,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
            for x,y,bw,bh,area in stats[1:]:
                if (bw if vertical else bh)>max(12,round(min(w,h)*.02)):continue
                lines.append((vertical,int(x),int(y),int(x+bw),int(y+bh)))
        page._lilyzono_panel_lines=lines
    cx,cy=anchor;left=top=0;right,bottom=page.size
    for vertical,x,y,X,Y in lines:
        if vertical and y<=cy<=Y:
            if X<cx-6:left=max(left,X+2)
            elif x>cx+6:right=min(right,x-2)
        elif not vertical and x<=cx<=X:
            if Y<cy-6:top=max(top,Y+2)
            elif y>cy+6:bottom=min(bottom,y-2)
    return left,top,right,bottom


def constrain_lobes(page, owners):
    """Apply detected per-lobe geometry before generic balloon fitting can widen it.

    These rectangles live only on rendering Regions; they do not change manual
    document fields or the original Japanese/source bounding boxes.
    """
    def owner(region):
        return owners.get(region.uid) or owners.get(region.group)

    counts=Counter(row['id'] for r in page.regions if (row:=owner(r)))
    fitted = {}
    for region in page.regions:
        row=owner(region)
        if not row or counts[row['id']]!=1 or row.get('src')=='manual' or region.manual_rect:continue
        geometry=row.get('balloon') or {}
        is_caption = geometry.get('shape') == 'square' and row['bubble'] == 'square'
        if (region.kind=='sfx' or not (is_caption or geometry.get('split') or geometry.get('parts', 1) > 1)
                or geometry.get('uncertain') or row['bubble'] not in ('round','square','cloud','ray')):continue
        region.style=profile_style(page,region,region.style)
        region.auto_cap=page.auto_cap
        joined = geometry.get("parts", 1) > 1
        if joined and not region.style.size:
            region.style = replace(region.style, short_grow=1.0)
        result = fit_lobe(page, region, geometry.get('outline'), lone_lobe=joined and len(geometry.get('peers', [])) < 2)
        if result is None:
            continue
        shape, rect = result
        region.manual_rect=tuple(map(int,rect))
        region.text_shape=shape
        if joined and not region.style.size and geometry.get("parent") is not None:
            fitted.setdefault(geometry["parent"], []).append((region, geometry))
    for peers in fitted.values():
        if len(peers) < 2:
            continue
        sizes = [block_box(r.text, r.effective_style, r.text_rect)[4] for r, _ in peers]
        cap = max(min(sizes), round(.85 * page.auto_cap))
        for (region, geometry), size in zip(peers, sizes):
            if size > cap * 1.08:
                region.style = replace(region.style, max_size=cap, short_grow=1.0)
                result = fit_lobe(page, region, geometry["outline"])
                if result is not None:
                    region.text_shape, region.manual_rect = result


def fit_lobe(page, region, outline, *, lone_lobe=False):
    """Find a text block fully inside one established lobe and its panel."""
    points = np.asarray(outline or [], np.int32)
    if len(points) < 3:
        return None
    x, y, right, bottom = region.box
    anchor = ((x + right) / 2, (y + bottom) / 2)
    if cv2.pointPolygonTest(points, anchor, False) < 0:
        return None
    left, top, panel_right, panel_bottom = panel_limits(page, anchor)
    minimum, maximum = points.min(0), points.max(0)
    lo = np.maximum(minimum, (left, top))
    hi = np.minimum(maximum + 1, (panel_right, panel_bottom))
    working = getattr(page, 'working', None)
    avoid_art = False
    if lone_lobe and working is not None:
        source_width = right - x
        inset = max(18, round(.7 * source_width))
        margin = max(3, round(.12 * source_width))
        if minimum[0] <= 2 and x - lo[0] >= inset:
            lo[0] = x - margin
            avoid_art = True
        if maximum[0] >= page.size[0] - 3 and hi[0] - right >= inset:
            hi[0] = right + margin
            avoid_art = True
    width, height = map(int, hi - lo)
    if min(width, height) < 16:
        return None
    mask = np.zeros((height, width), np.uint8)
    cv2.fillPoly(mask, [points - lo], 1)
    pad = max(4, round(min(width, height) * .045))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * pad + 1,) * 2)
    mask = cv2.erode(mask, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    ox, oy = map(int, lo)
    if avoid_art:
        patch = working[oy:oy + height, ox:ox + width]
        dark = (cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) < 120).astype(np.uint8)
        dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        mask[cv2.dilate(dark, np.ones((5, 5), np.uint8)) > 0] = 0
    distance = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    _, radius, _, _ = cv2.minMaxLoc(distance)
    if radius < 6:
        return None
    # The widest rows of a long caption can form a plateau. Picking its first
    # pixel makes the text sit high even when most of the writing area is empty.
    clipped_y = minimum[1] <= 2 or maximum[1] >= page.size[1] - 3
    if clipped_y:
        rows = np.arange(height)
        near_source = abs(rows - (anchor[1] - oy)) <= max(8, .08 * height)
        core = (distance >= .55 * radius) & near_source[:, None]
    else:
        core = distance >= .8 * radius
    ys, xs = np.nonzero(core if core.any() else distance >= .8 * radius)
    near_center = ((xs - np.median(xs)) ** 2 + (ys - np.median(ys)) ** 2).argmin()
    px, py = int(xs[near_center]), int(ys[near_center])
    cx, cy = ox + px, oy + py
    # An inward dent can divide a scanline into two runs. Crossing the gap
    # would put text in the other lobe or outside the balloon altogether.
    spans = {}
    for yy, line in enumerate(mask):
        ends = np.flatnonzero(np.diff(np.pad(line, 1)))
        candidates = [(int(a), int(b)) for a, b in zip(ends[::2], ends[1::2]) if b - a >= 6]
        if candidates:
            a, b = min(candidates, key=lambda span: (max(span[0] - px, px - span[1], 0), span[0] - span[1]))
            spans[yy] = (yy + oy, a + ox, b + ox)
    start = end = py
    while start - 1 in spans:
        start -= 1
    while end + 1 in spans:
        end += 1
    if end - start < 7:
        return None
    shape = (float(cx), float(cy), tuple(spans[yy] for yy in range(start, end + 1)))
    style = region.effective_style
    rect = place_in_shape(region.text, style, shape, (cx, cy))
    if rect:
        for a, b, c, d in block_box(region.text, style, rect)[5]:
            if not (ox <= a < c <= ox + width and oy <= b < d <= oy + height):
                rect = None
                break
            patch = mask[int(np.floor(b)) - oy:int(np.ceil(d)) - oy,
                         int(np.floor(a)) - ox:int(np.ceil(c)) - ox]
            if not patch.size or not patch.all():
                rect = None
                break
    if rect is None:
        half = int(distance[py, px] / np.sqrt(2))
        rect = (cx - half, cy - half, cx + half, cy + half)
    return shape, rect


def constrain_panel_rects(page, owners):
    """Giữ chữ trong panel nếu vùng chữ gốc nằm hẳn bên trong đường viền."""
    for region in page.regions:
        row = owners.get(region.uid) or owners.get(region.group)
        if not row or row.get('src') == 'manual' or region.kind == 'sfx' or region.manual_rect:
            continue
        x, y, right, bottom = region.box
        anchor = ((x + right) / 2, (y + bottom) / 2)
        left, top, panel_right, panel_bottom = panel_limits(page, anchor)
        if not (left <= x < right <= panel_right and top <= y < bottom <= panel_bottom):
            continue
        a, b, c, d = region.text_rect
        # Giữ khoảng thở sát viền khi khung tự động lấn ra ngoài panel.
        rect = (max(a, left + 3) if a < left else a, max(b, top + 3) if b < top else b,
                min(c, panel_right - 3) if c > panel_right else c, min(d, panel_bottom - 3) if d > panel_bottom else d)
        if rect != (a, b, c, d) and min(rect[2] - rect[0], rect[3] - rect[1]) >= 16:
            region.manual_rect = rect
            region.text_shape = None
