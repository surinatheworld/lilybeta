"""Restore repeating dot screens by sampling the same lattice phase outside a mask."""
from __future__ import annotations

from math import gcd

import cv2
import numpy as np


MAX_PERIOD = 20  # pixels, including diagonal translations of rotated screens
CONTEXT = 2 * MAX_PERIOD
SHIFTS = sorted(
    ((dx * dx + dy * dy, dy, dx)
     for dy in range(MAX_PERIOD + 1)
     for dx in range(-MAX_PERIOD, MAX_PERIOD + 1)
     if 9 <= dx * dx + dy * dy <= MAX_PERIOD ** 2 and (dy or dx > 0))
)


def restore_screentone(img: np.ndarray, mask: np.ndarray, excluded: np.ndarray | None = None) -> bool:
    """Replace masked pixels in place when two repeat vectors explain the surrounding texture."""
    x, y, w, h = cv2.boundingRect(mask)
    if not w or not h:
        return False
    roi = np.s_[max(0, y - CONTEXT):y + h + CONTEXT, max(0, x - CONTEXT):x + w + CONTEXT]
    crop = img[roi]
    hole = mask[roi] > 0
    distance = cv2.distanceTransform((~hole).astype(np.uint8), cv2.DIST_L2, 3)
    known = ~hole if excluded is None else ~(hole | excluded[roi])
    sample = known & (distance >= 4) & (distance <= CONTEXT)
    # Fit from farther pixels and verify against the untouched boundary before
    # changing anything. This rejects, for example, a mask crossing panel ink.
    train = known & (distance >= 8) & (distance <= CONTEXT)
    check = known & (distance < 8)
    ty, tx = np.nonzero(train)
    vy, vx = np.nonzero(check)
    hy, hx = np.nonzero(hole)
    if len(vx) < 60:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    sy, sx = np.nonzero(sample)
    if len(sx) < 240 or gray[sy, sx].std() < 8:
        return False
    # A small, deterministic sample avoids both aliasing and per-offset work
    # proportional to the mask area.
    if len(sx) > 1024:
        take = np.random.default_rng(0).choice(len(sx), 1024, replace=False)
        sy, sx = sy[take], sx[take]
    values = gray[sy, sx]
    limit = min(10, max(3, .20 * values.std()))
    training_colors, boundary_colors = crop[train], crop[check]
    repeats = []
    checked = set()
    for bx, by in _repeat_vectors(gray, sample, sy, sx, values, limit):
        for ax, ay in repeats[:12]:
            det = abs(ax * by - ay * bx)
            if not 4 <= det <= 240:
                continue
            # Canonical form of the integer lattice. Skip equivalent bases
            # (common when the image has several exact multiples of a pitch).
            pitch_y = gcd(ay, by)
            pitch_x = det // pitch_y
            # Find the horizontal shift of a lattice step of height pitch_y.
            a = pow(ay // pitch_y, -1, by // pitch_y) if by else 1
            b = (pitch_y - a * ay) // by if by else 0
            tilt = (a * ax + b * bx) % pitch_x
            lattice = (pitch_x, pitch_y, tilt)
            if lattice in checked:
                continue
            checked.add(lattice)

            def phase(px, py):
                return (py % pitch_y) * pitch_x + (px - (py // pitch_y) * tilt) % pitch_x

            keys = phase(tx, ty)
            count = np.bincount(keys, minlength=det)
            target, boundary = phase(hx, hy), phase(vx, vy)
            if count[target].min() < 3 or count[boundary].min() < 3:
                continue
            tone = np.column_stack([
                np.bincount(keys, weights=channel, minlength=len(count))
                for channel in training_colors.T
            ]) / np.maximum(1, count[:, None])
            residual = np.abs(tone[boundary] - boundary_colors).max(axis=1)
            if np.sqrt(np.mean(residual ** 2)) <= limit and (residual > 24).mean() <= .02:
                crop[hole] = np.round(tone[target]).astype(np.uint8)
                return True
        repeats.append((bx, by))
    return False


def _repeat_vectors(gray, sample, sy, sx, values, limit):
    h, w = gray.shape
    for _, dy, dx in SHIFTS:
        yy, xx = sy + dy, sx + dx
        inside = (yy < h) & (xx >= 0) & (xx < w)
        idx = np.flatnonzero(inside)
        idx = idx[sample[yy[idx], xx[idx]]]
        if len(idx) < .45 * len(sx):
            continue
        first, second = values[idx], gray[yy[idx], xx[idx]]
        diff = first - second
        if np.mean(diff * diff) <= limit ** 2 and min(first.std(), second.std()) >= 7:
            yield dx, dy
