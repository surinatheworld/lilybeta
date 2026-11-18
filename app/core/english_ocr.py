"""Verify English script rows against line OCR on the original page."""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
import re

import cv2
import numpy as np

from .clean import screentone_only
from .docs import TAG
from .page import Region
from .screentone import restore_screentone
from .tone_restore import restore_gradient
from .typeset import layout
from .typeset_profiles import balloon_shape


def _letters(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def script_rows(lines):
    tags = [TAG.match(line) for line in lines]
    if not any(tag and tag[1] == "en" for tag in tags):
        return None
    rows = [(tag[2], line[tag.end():].strip()) for line, tag in zip(lines, tags)
            if tag and tag[1] == "en" and _letters(tag[2] or "") and line[tag.end():].strip()]
    if len(rows) != len(lines):
        raise ValueError("Mỗi câu trên trang tiếng Anh cần có dạng ⟦en|câu gốc⟧Câu dịch.")
    return rows


@dataclass(frozen=True)
class Line:
    text: str
    confidence: float
    quad: np.ndarray
    box: tuple[int, int, int, int]

    @property
    def height(self):
        return self.box[3] - self.box[1]

    @property
    def cy(self):
        return (self.box[1] + self.box[3]) / 2


@lru_cache(maxsize=1)
def _ocr():
    from rapidocr import RapidOCR

    return RapidOCR(params={"Global.use_cls": False, "Global.max_side_len": 2400,
                            "Det.limit_side_len": 1800, "Det.limit_type": "max",
                            "EngineConfig.onnxruntime.intra_op_num_threads": 4,
                            "EngineConfig.onnxruntime.inter_op_num_threads": 1})


def read_lines(image):
    output = _ocr()(image, use_cls=False, text_score=.58, box_thresh=.30)
    if output.boxes is None:
        return []
    height, width = image.shape[:2]
    lines = []
    for quad, text, score in zip(output.boxes, output.txts, output.scores):
        if not _letters(text):
            continue
        quad = np.rint(quad).astype(np.int32)
        x1, y1 = np.maximum(quad.min(axis=0), 0)
        x2, y2 = np.minimum(quad.max(axis=0) + 1, (width, height))
        if x2 > x1 and y2 > y1:
            lines.append(Line(text, score, quad, (int(x1), int(y1), int(x2), int(y2))))
    return sorted(lines, key=lambda line: line.cy)


def _follows(first, second):
    distance = second.cy - first.cy
    height = min(first.height, second.height)
    a, _, b, _ = first.box
    c, _, d, _ = second.box
    overlap = min(b, d) - max(a, c)
    return (.52 * height <= distance <= 1.65 * height
            and overlap >= .53 * min(b - a, d - c)
            and abs((a + b) - (c + d)) <= max(b - a, d - c))


def match_rows(rows, lines):
    keys = [_letters(source) for source, _ in rows]
    length_limit = max(map(len, keys), default=0)
    pieces = [_letters(line.text) for line in lines]
    links = []
    for i, line in enumerate(lines):
        following = (j for j in range(i + 1, len(lines))
                     if _follows(line, lines[j]))
        links.append(list(following)[:4])
    matches = [[] for _ in rows]

    def extend(indices, text):
        if len(text) > length_limit * 1.25 + 1:
            return
        confidence = min(lines[i].confidence for i in indices)
        for index, key in enumerate(keys):
            if text == key:
                matches[index].append((1., indices))
            elif len(key) >= 4 and abs(len(key) - len(text)) <= max(1, len(key) * .14):
                score = SequenceMatcher(None, key, text, autojunk=False).ratio()
                if score >= .90 or (len(key) <= 5 and confidence >= .75 and
                                     len(text) == len(key) + 1 and text.startswith(key)):
                    matches[index].append((score, indices))
        if len(indices) < 10:
            for j in links[indices[-1]]:
                extend((*indices, j), text + pieces[j])

    for i, piece in enumerate(pieces):
        extend((i,), piece)

    # Leaving an ambiguous target unplaced is safer than assigning it by reading order.
    proposals = []
    for index, options in enumerate(matches):
        options.sort(reverse=True)
        if not options:
            continue
        score, best = options[0]
        best_set = set(best)
        if all(score - rival_score >= .035 or
               (score == 1. and rival_score < 1. and set(rival) < best_set)
               for rival_score, rival in options[1:]):
            proposals.append((score, best, index))
    locked = set()
    placed = {}
    for score, indices, index in sorted(proposals, reverse=True):
        if locked.intersection(indices):
            continue
        if any(other != index and abs(score - rival_score) < .035 and set(indices).intersection(rival)
               for rival_score, rival, other in proposals):
            continue
        placed[index] = [lines[i] for i in indices]
        locked.update(indices)
    return placed


def _seed_mask(image, detected, lines, box):
    """Limit detector strokes to recognized lines and recover their missing foreground cores."""
    x1, y1, x2, y2 = box
    gray = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    cells = np.zeros_like(gray)
    core = np.zeros_like(gray)
    for line in lines:
        quad = line.quad - (x1, y1)
        cell = np.zeros_like(gray)
        cv2.fillConvexPoly(cell, quad.astype(np.int32), 1)
        cells |= cell
        values = gray[cell > 0]
        if not len(values):
            continue
        # Only add contrast-derived strokes on simple backgrounds. Halftone and line art rely on CTD.
        if np.mean(values > 232) > .63:
            core[(cell > 0) & (gray < 150)] = 1
        elif np.mean(values < 23) > .63:
            core[(cell > 0) & (gray > 130)] = 1
    detector = detected[y1:y2, x1:x2]
    return np.maximum(detector * cells, core * .7).astype(np.float32)


def _flat_glyphs(image, box, evidence, *, decorative=False):
    """Lift isolated glyphs on nearly flat ink/paper without cutting through an outline."""
    x1, y1, x2, y2 = box
    left, top = max(0, x1 - 12), max(0, y1 - 12)
    area = np.s_[top:y2 + 12, left:x2 + 12]
    crop = image[area]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    window = np.s_[y1 - top:y2 - top, x1 - left:x2 - left]
    support = np.zeros_like(gray, dtype=bool)
    support[window] = evidence > .38
    bright_text = np.median(gray[window]) < 110
    ink = gray > 124 if bright_text else gray < 166
    num, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    known_area = np.bincount(labels[support], minlength=num)
    height, width = gray.shape
    keep = np.zeros(num, dtype=bool)
    plausible = np.zeros(num, dtype=bool)
    for i, (x, y, w, h, count) in enumerate(stats[1:], 1):
        if count < 4 or known_area[i] < .70 * count:
            continue
        if decorative and count < .18 * w * h:
            continue  # the decorative oval/arcs are much sparser than individual letters
        plausible[i] = True
        keep[i] = (x > 0 and y > 0 and x + w < width and y + h < height
                   and max(w, h) <= .88 * max(x2 - x1, y2 - y1))
    foreground = keep[labels].astype(np.uint8)
    if not foreground.any():
        return False
    # The foreground threshold omits near-white or near-black anti-aliasing.
    pad = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.dilate(foreground, pad) > 0
    other_ink = ink & ~foreground.astype(bool)
    protected = cv2.dilate(other_ink.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    if np.any(protected & foreground.astype(bool)):
        return False
    mask &= ~protected
    near = (cv2.dilate(mask.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0) & ~mask
    rim = crop[near & ~protected]
    if len(rim) < max(24, .74 * np.count_nonzero(near)):
        return False
    background = np.median(rim, axis=0)
    flat = np.abs(rim - background).max(axis=1) <= 30
    if flat.mean() < .89 or not (background.min() >= 230 or background.max() <= 25):
        return False
    # Compare against the source polarity so misclassified model pixels cannot
    # compensate for a surviving letter or decorative outline.
    possible = plausible[labels]
    if not possible.any() or (mask & possible).sum() < .94 * possible.sum():
        return False
    crop[mask] = background.astype(np.uint8)
    return True


def _lettering_area(mask, shape, top, left):
    support = np.zeros(shape, np.uint8)
    h, w = mask.shape
    support[top:top + h, left:left + w] = (mask > .3).astype(np.uint8)
    return cv2.dilate(support, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))) > 0


def _smooth_lettering(crop, target):
    """Restore a locally smooth background only when it predicts the rim."""
    if not target.any():
        return False
    distance = cv2.distanceTransform((~target).astype(np.uint8), cv2.DIST_L2, 3)
    train = (distance >= 6) & (distance <= 15)
    check = (distance >= 1) & (distance <= 5)
    sy, sx = np.nonzero(train)
    cy, cx = np.nonzero(check)
    if len(sy) < 96 or len(cy) < 64:
        return False
    if len(sy) > 1500:
        sample = np.random.default_rng(0).choice(len(sy), 1500, replace=False)
        sy, sx = sy[sample], sx[sample]

    def terms(y, x):
        x = x.astype(np.float32) / crop.shape[1]
        y = y.astype(np.float32) / crop.shape[0]
        return np.column_stack((np.ones_like(x), x, y, x * x, x * y, y * y))

    samples = crop[sy, sx].astype(np.float32)
    ok, coefficients = cv2.solve(terms(sy, sx), samples, flags=cv2.DECOMP_SVD)
    if not ok:
        return False
    residual = np.abs(terms(cy, cx) @ coefficients - crop[cy, cx]).max(axis=1)
    if np.mean(residual) > 8 or np.mean(residual > 20) > .04:
        return False
    hy, hx = np.nonzero(target)
    crop[hy, hx] = np.clip(np.rint(terms(hy, hx) @ coefficients), 0, 255).astype(np.uint8)
    return True


def _visible_residue(crop, target):
    """Look for a strong glyph-shaped patch after redraw against the nearby background."""
    if not target.any():
        return True
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    known = (~target).astype(np.float32)
    weight = cv2.GaussianBlur(known, (0, 0), 6)
    local = cv2.GaussianBlur(gray * known, (0, 0), 6) / np.maximum(weight, .001)
    deviation = np.abs(gray - local)
    tested = target & (weight >= .23)
    return tested.any() and np.mean(deviation[tested] > 43) > .18


def _outline_residue(before, after, seed):
    """Detect intact halos and extreme remnants beside partly erased letters."""
    if not seed.any():
        return False
    old = cv2.cvtColor(before, cv2.COLOR_BGR2GRAY)
    gray = cv2.cvtColor(after, cv2.COLOR_BGR2GRAY).astype(np.float32)
    scope = cv2.dilate(seed.astype(np.uint8), np.ones((17, 17), np.uint8)) > 0
    outside = np.float32(~scope)
    weight = cv2.GaussianBlur(outside, (0, 0), 9)
    shade = cv2.GaussianBlur(gray * outside, (0, 0), 9) / np.maximum(weight, .001)
    near = scope & (weight >= .12)
    residue = (gray >= 237) & (gray - shade >= 24) & near
    # Sample the unchanged shell as well as new patches with the same color.
    touch = (old <= 115) & (gray > old + 32.) & seed
    if np.count_nonzero(touch) < 16:
        return False
    # A tonal split next to a balloon border gives unreliable local estimates.
    # On white paper, pure white highlights are expected.
    low, middle, high = np.percentile(shade[touch], (25, 50, 75))
    if middle >= 234 or high - low > 55:
        return False
    border = cv2.dilate(touch.astype(np.uint8), np.ones((13, 13), np.uint8)) > 0
    return np.count_nonzero((residue & border)) >= max(35, .11 * np.count_nonzero(touch))


def _color_art(crop, target):
    rim = (cv2.dilate(target.astype(np.uint8), np.ones((19, 19), np.uint8)) > 0) & ~target
    pixels = crop[rim]
    if not len(pixels) or np.mean(np.ptp(pixels, axis=1) > 25) <= .28:
        return False
    median = np.median(pixels, axis=0)
    return bool(np.mean(np.abs(pixels - median).max(axis=1) > 28) > .16)


def _overlap(a, b):
    w = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return w * h / max(1, min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1])))


def _retry_in_regions(page, rows, lines, placed):
    missing = [i for i in range(len(rows)) if i not in placed]
    if not missing:
        return
    recognized = [line for group in placed.values() for line in group]
    leftovers = [line for line in lines if all(_overlap(line.box, used.box) < .58 for used in recognized)]
    keys = [_letters(rows[i][0]) for i in missing]
    candidates = []
    for region in page.regions:
        nearby = [line for line in leftovers if _overlap(line.box, region.box) > .45]
        tokens = {_letters(word) for line in nearby for word in line.text.split()}
        score = sum(len(token) for token in tokens if len(token) >= 3 and any(token in key for key in keys))
        if score:
            candidates.append((score, region.box))
    proposals = {}
    width, height = page.size
    for _, (x, y, u, v) in sorted(candidates, reverse=True)[:5]:
        pad = max(5, min(14, (u - x) // 20))
        left, top = max(0, x - pad), max(0, y - pad)
        tile = page.original[top:min(height, v + pad), left:min(width, u + pad)]
        tile = cv2.copyMakeBorder(tile, 9, 9, 9, 9, cv2.BORDER_REPLICATE)
        focused = read_lines(tile)
        for local, group in match_rows([rows[i] for i in missing], focused).items():
            offset = (left - 9, top - 9)
            shifted = [Line(line.text, line.confidence, line.quad + offset,
                            (max(0, line.box[0] + offset[0]), max(0, line.box[1] + offset[1]),
                             min(width, line.box[2] + offset[0]), min(height, line.box[3] + offset[1])))
                       for line in group]
            if any(_overlap(line.box, used.box) >= .58 for line in shifted for used in recognized):
                continue
            proposals.setdefault(missing[local], []).append(shifted)

    for index, groups in proposals.items():
        first = groups[0]
        if any(not any(_overlap(a.box, b.box) >= .58 for b in other) for a in first for other in groups[1:]):
            continue
        if any(_overlap(line.box, used.box) >= .58 for line in first for used in recognized):
            continue
        placed[index] = first
        recognized.extend(first)


def prepare(page, rows):
    lines = read_lines(page.original)
    placed = match_rows(rows, lines)
    _retry_in_regions(page, rows, lines, placed)
    if len(placed) < len(rows):
        inverse = match_rows(rows, read_lines(cv2.bitwise_not(page.original)))
        occupied = [line for group in placed.values() for line in group]
        for index, group in inverse.items():
            if index not in placed and all(_overlap(line.box, used.box) < .58 for line in group for used in occupied):
                placed[index] = group
                occupied.extend(group)
    detector_mask = page.text_mask
    page.text_mask = np.zeros_like(detector_mask)
    page.regions = []
    masks = []
    unplaced = []
    plan = []
    for index, (source, text) in enumerate(rows):
        if index not in placed:
            unplaced.append({"source": source, "translation": text})
            continue
        group = placed[index]
        box = (min(line.box[0] for line in group), min(line.box[1] for line in group),
               max(line.box[2] for line in group), max(line.box[3] for line in group))
        mask = _seed_mask(page.original, detector_mask, group, box)
        x1, y1, x2, y2 = box
        dest = page.text_mask[y1:y2, x1:x2]
        np.maximum(dest, mask, out=dest)
        shape = balloon_shape(page.original, box, outline=True)
        quad = group[0].quad
        line_height = min(np.linalg.norm(quad[1] - quad[2]), np.linalg.norm(quad[0] - quad[3]))
        effect = len(group) == 1 and len(_letters(source)) <= 5 and line_height > 1.42 * page.auto_cap
        kind = "sfx" if effect else "text"
        region = Region(box, kind=kind)
        gray = cv2.cvtColor(page.original[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
        if kind != "sfx" and (shape == "free" or np.median(gray) < 110):
            region.typeset_kind = "free"
        page.regions.append(region)
        masks.append((region, mask))
        plan.append((region, [text]))
    return plan, masks, unplaced


def clean_verified(page, masks):
    """Apply a redraw only if the scoped glyph mask is supported and the pixels changed."""
    combined = page.text_mask
    isolated = np.zeros_like(combined)
    try:
        for region, mask in masks:
            x1, y1, x2, y2 = region.box
            context = 44
            area = np.s_[max(0, y1 - context):y2 + context, max(0, x1 - context):x2 + context]
            core = mask > .50
            if page.redrawn_covers(region):
                region.cleaned = True
                continue
            old = page.working[area].copy()
            crop = page.working[area]
            top, left = min(y1, context), min(x1, context)
            target = _lettering_area(mask, crop.shape[:2], top, left)
            if _color_art(crop, target):
                region.cleaned = region.needs_redraw = True
                continue
            page.text_mask = isolated
            isolated[y1:y2, x1:x2] = mask
            seed = isolated[area] > .35
            other_lettering = (combined[area] > .35) & ~seed
            other_lettering = cv2.dilate(other_lettering.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            other_lettering &= ~seed
            separated = _flat_glyphs(page.working, region.box, mask, decorative=region.kind == "sfx")
            if separated and (_visible_residue(crop, target) or _outline_residue(old, crop, seed)):
                crop[:] = old
                separated = False
            if separated or (region.kind != "sfx" and core.any() and _smooth_lettering(crop, target)):
                need = None
            elif region.kind == "sfx":
                # Filling a guessed bubble here could remove the decorative frame.
                # Accept a scoped tone reconstruction, but never a solid rectangle.
                tone_target = cv2.dilate((isolated > .28).astype(np.uint8), np.ones((7, 7), np.uint8))
                rendered = core.any() and restore_screentone(page.working, tone_target, combined > .28)
                need = None if rendered else True
            else:
                need = page._clean_local(region) if core.any() else True
            raw = page.original[y1:y2, x1:x2]
            drawn = page.working[y1:y2, x1:x2]
            changed = np.abs(drawn.astype(np.int16) - raw).max(axis=2) > 35
            accepted = (need is None and not region.needs_redraw and core.any() and changed[core].mean() >= .31
                        and not _visible_residue(crop, target) and not _outline_residue(old, crop, seed))
            if not accepted:
                crop[:] = old
                if page.inpainter is screentone_only and core.any():
                    accepted = any(restore_gradient(crop, seed, excluded=other_lettering, halo=halo)
                                   for halo in (6, 10, 4))
                    if accepted:
                        changed = np.abs(crop[seed].astype(np.int16) - old[seed]).max(axis=1) > 24
                        accepted = changed.mean() >= .22
                        if not accepted:
                            crop[:] = old
                region.needs_redraw = not accepted
            region.cleaned = True
            isolated[y1:y2, x1:x2] = 0
    finally:
        page.text_mask = combined
    page.clean_many([region for region, _ in masks])
    for region, _ in masks:
        if region.needs_redraw:
            continue
        # A short annotation beside a bubble can be mistaken for a line in
        # the bubble. Keep a source-width anchor when the inferred contour
        # would pull the lettering more than half a source line sideways.
        x1, y1, x2, y2 = region.box
        if region.kind == "text" and y2 - y1 < min(x2 - x1, 1.5 * page.auto_cap):
            left, _, right, _ = region.text_rect
            if abs((left + right) - (x1 + x2)) > x2 - x1:
                region.text_shape = None
                region.text_rects = [region.box]
        if region.kind == "text":
            _balance_shared_lines(region)


def _balance_shared_lines(region):
    """Avoid a stack of single words when a small size change helps."""
    def fitted():
        x1, y1, x2, y2 = region.text_rect
        return layout(region.text, region.effective_style, x2 - x1, y2 - y1)

    metrics, lines = fitted()
    if len(lines) < 4 or any(len(line.split()) > 1 for line, _, _ in lines):
        return
    original_cap = region.style.max_size
    cap = original_cap or region.auto_cap
    for ratio in (.95, .90, .85, .80):
        region.style.max_size = int(cap * ratio)
        replacement, new_lines = fitted()
        if len(new_lines) < len(lines) and replacement.size >= .8 * metrics.size:
            return
    region.style.max_size = original_cap
