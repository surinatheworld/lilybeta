"""Local color-cover cleanup scoped to lettering selected in Photoshop."""
from __future__ import annotations

import cv2
import numpy as np

from .clean import opencv_inpaint
from .tone_restore import _fit_tone, _terms, restore_gradient


def _palette(image):
    blue, green, red = image.astype(np.int16).transpose(2, 0, 1)
    low, high = image.min(axis=2), image.max(axis=2)
    white = (low >= 245) & (high - low <= 12)
    pale = (low >= 220) & (high - low <= 32)
    coral = (np.abs(green - blue) < 19) & (red - green >= 61) & (red >= 220) & (green >= 102) & (green <= 173)
    rose = (np.abs(green - blue) < 33) & (red - green >= 43) & (red >= 174) & (green >= 102) & (green <= 185)
    return white | coral, pale | rose


def _inside_components(labels, areas, box):
    x1, y1, x2, y2 = box
    block = labels[y1:y2, x1:x2]
    inside = np.bincount(block.ravel(), minlength=len(areas))
    # Keep almost all of a component in the selection. A bright rail or
    # cloud that extends out of it generally belongs to the illustration.
    contained = (inside >= .84 * areas) & (areas >= 3)
    contained[0] = False
    return contained[block]


def _grow(mask, radius):
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel) > 0


def _uniform_holes(image, mask):
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = cv2.drawContours(np.zeros(mask.shape, np.uint8), contours, -1, 1, cv2.FILLED) > 0
    count, labels, stats, _ = cv2.connectedComponentsWithStats((filled & ~mask).astype(np.uint8))
    for i, (x, y, w, h, size) in enumerate(stats[1:], 1):
        if size < 4:
            continue
        region = np.s_[y:y + h, x:x + w]
        cells = labels[region] == i
        pixels = image[region][cells]
        median = np.median(pixels, axis=0)
        if np.mean(np.abs(pixels - median).max(axis=1) <= 14) >= .93:
            mask[region] |= cells
    return mask


def _smooth_fill(image, mask, protected):
    distance = cv2.distanceTransform((~mask).astype(np.uint8), cv2.DIST_L2, 5)
    train = (distance >= 12) & (distance <= 34) & ~protected
    check = (distance >= 2) & (distance <= 10) & ~protected
    model = _fit_tone(image, train)
    y, x = np.nonzero(check)
    if model is None or len(x) < 96:
        return False
    guess = np.clip(_terms(y, x, mask.shape) @ model, 0, 255)
    error = np.abs(guess - image[y, x]).max(axis=1)
    if np.quantile(error, .87) > 9:
        return False
    y, x = np.nonzero(mask)
    color = _terms(y, x, mask.shape) @ model
    image[y, x] = np.clip(np.rint(color), 0, 255).astype(np.uint8)
    return True


def _line_directions(image, mask):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 45, 100)
    edges[_grow(mask, 4)] = 0
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, 36, minLineLength=42, maxLineGap=8)
    if lines is None:
        return []
    segments = lines.reshape(-1, 4).astype(np.float32)
    length = np.hypot(segments[:, 2] - segments[:, 0], segments[:, 3] - segments[:, 1])
    return segments[np.argsort(-length)[:10]]


def _sample_side(image, unknown, x, y, dx, dy, reach):
    height, width = unknown.shape
    distance = np.zeros(len(x), np.float32)
    value = np.zeros((len(x), 3), np.float32)
    for step in range(1, reach + 1):
        todo = np.flatnonzero(distance == 0)
        if not len(todo):
            break
        px, py = x[todo] + round(step * dx), y[todo] + round(step * dy)
        valid = (px >= 0) & (px < width) & (py >= 0) & (py < height)
        todo, px, py = todo[valid], px[valid], py[valid]
        hit = ~unknown[py, px]
        distance[todo[hit]] = step
        value[todo[hit]] = image[py[hit], px[hit]]
    return distance, value


def _follow_edges(image, mask, protected):
    """Select nearby background samples on both sides of each removed pixel."""
    y, x = np.nonzero(mask)
    reach = min(92, max(28, round(max(mask.shape) * .26)))
    unknown = mask | protected
    out = image.copy()
    opencv_inpaint(out, mask.astype(np.uint8) * 255)
    estimate = out[y, x].astype(np.float32)
    best = np.full(len(x), np.inf)
    candidates = [(float(angle), None) for angle in np.linspace(0, np.pi, 24, endpoint=False)]
    for a, b, c, d in _line_directions(image, unknown):
        angle = np.arctan2(d - b, c - a)
        dx, dy = np.cos(angle), np.sin(angle)
        across = np.abs((x - a) * dy - (y - b) * dx)
        along = (x - a) * dx + (y - b) * dy
        adjacent = (across <= 13) & (along >= -reach) & (along <= np.hypot(c - a, d - b) + reach)
        if adjacent.any():
            candidates.append((float(angle), adjacent))
    for angle, aligned in candidates:
        dx, dy = np.cos(angle), np.sin(angle)
        d0, c0 = _sample_side(image, unknown, x, y, -dx, -dy, reach)
        d1, c1 = _sample_side(image, unknown, x, y, dx, dy, reach)
        span = d0 + d1
        cost = np.abs(c0 - c1).max(axis=1) + .26 * span
        if aligned is not None:
            cost -= np.where(aligned, 7, 0)
        take = (d0 > 0) & (d1 > 0) & (cost < best)
        best[take] = cost[take]
        estimate[take] = (c0[take] * d1[take, None] + c1[take] * d0[take, None]) / span[take, None]
    confidence = np.clip(1 - (best - 48) / 76, 0, 1)[:, None]
    out[y, x] = np.clip(np.rint(estimate * confidence + out[y, x] * (1 - confidence)), 0, 255).astype(np.uint8)
    image[mask] = cv2.medianBlur(out, 3)[mask]


def redraw_cover(page, boxes):
    """Return a count of smooth fills, directional fills, and empty selections."""
    height, width = page.original.shape[:2]
    palette = []
    for ink in _palette(page.original):
        _, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
        palette.append((labels, stats[:, cv2.CC_STAT_AREA]))
    seeds = []
    all_letters = np.zeros((height, width), dtype=bool)
    for box in boxes:
        x1, y1, x2, y2 = box
        seed = np.logical_or.reduce([_inside_components(labels, areas, box) for labels, areas in palette])
        all_letters[y1:y2, x1:x2] |= seed
        seeds.append(seed)
    smooth = directional = empty = 0
    for (x1, y1, x2, y2), seed in zip(boxes, seeds):
        if not seed.any():
            empty += 1
            continue
        left, top = max(0, x1 - 46), max(0, y1 - 46)
        area = np.s_[top:y2 + 46, left:x2 + 46]
        crop = page.original[area].copy()
        window = np.s_[y1 - top:y2 - top, x1 - left:x2 - left]
        selected = np.zeros(crop.shape[:2], dtype=bool)
        selected[window] = seed
        protected = _grow(all_letters[area] & ~selected, 4)
        broad = _uniform_holes(crop, _grow(selected, 13)) & ~protected
        if _smooth_fill(crop, broad, protected):
            smooth += 1
        else:
            narrow = _uniform_holes(crop, _grow(selected, 5)) & ~protected
            if restore_gradient(crop, narrow, excluded=protected, halo=5):
                smooth += 1
            else:
                _follow_edges(crop, narrow, protected)
                directional += 1
        changed = np.any(crop != page.original[area], axis=2)
        page.working[area][changed] = crop[changed]
    return {"smooth": smooth, "directional": directional, "empty": empty}
