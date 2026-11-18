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
OFFSETS = np.array([(dy, dx) for _, dy, dx in SHIFTS], dtype=np.int32)


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
    return _restore_resampled(crop, gray, hole, train, check)


def _repeat_vectors(gray, sample, sy, sx, values, limit):
    # Score several candidate offsets in one array operation. Work in small
    # batches so exact screens can stop at the first valid lattice.
    pixels = np.pad(gray, MAX_PERIOD)
    valid = np.pad(sample, MAX_PERIOD)
    sy, sx = sy + MAX_PERIOD, sx + MAX_PERIOD
    for start in range(0, len(OFFSETS), 40):
        offsets = OFFSETS[start:start + 40]
        yy, xx = sy + offsets[:, :1], sx + offsets[:, 1:]
        use = valid[yy, xx]
        other = pixels[yy, xx]
        delta = other - values
        count = use.sum(axis=1)
        error = np.sum(delta * delta, axis=1, where=use)
        for i in np.flatnonzero((count >= .45 * len(sx)) & (error <= (limit ** 2) * count)):
            if min(values[use[i]].std(), other[i, use[i]].std()) >= 7:
                dy, dx = offsets[i]
                yield int(dx), int(dy)


def _screen_frequencies(gray, known):
    """Find periodic components including fractional pixel pitches."""
    h, w = gray.shape
    weight = known.astype(np.float32)
    slow = cv2.GaussianBlur(gray * weight, (0, 0), 5)
    slow /= np.maximum(cv2.GaussianBlur(weight, (0, 0), 5), .001)
    window = np.hanning(h)[:, None] * np.hanning(w)
    spectrum = np.abs(np.fft.rfft2((gray - slow) * weight * window))
    # Move negative y frequencies to the first half. Exclude DC and retain
    # one of each conjugate pair along the vertical and Nyquist axes.
    spectrum = np.fft.fftshift(spectrum, axes=0)
    fy, fx = np.fft.fftshift(np.fft.fftfreq(h)), np.fft.rfftfreq(w)
    allowed = np.hypot(fx[None, :], fy[:, None]) >= .07
    allowed[:, 0] &= fy > 0
    if w % 2 == 0:
        allowed[:, -1] &= fy >= 0
    maxima = spectrum >= cv2.dilate(spectrum, np.ones((3, 3), np.uint8))
    yy, xx = np.nonzero(allowed & maxima)
    if not len(xx):
        return []
    power = spectrum[yy, xx]
    noise = np.median(power)
    order = np.argsort(power)[::-1][:14]

    def offset(a, b, c):
        # Interpolate the log peak instead of snapping to the nearest FFT bin.
        a, b, c = np.log(np.maximum([a, b, c], .001))
        return 0.0 if a - 2 * b + c >= 0 else float(np.clip(.5 * (a - c) / (a - 2 * b + c), -.5, .5))

    peaks = []
    for i in order:
        y, x, energy = yy[i], xx[i], power[i]
        if energy < max(7 * noise, .09 * power[order[0]]):
            break
        dx = offset(*spectrum[y, x - 1:x + 2]) if 0 < x < len(fx) - 1 else 0.0
        dy = offset(*spectrum[y - 1:y + 2, x]) if 0 < y < h - 1 else 0.0
        peaks.append((fx[x] + dx / w, fy[y] + dy / h, energy))
    if len(peaks) < 2:
        return []
    a, b, high = peaks[0]
    if not any(energy >= .17 * high and abs(a * y - b * x) >= .4 * np.hypot(a, b) * np.hypot(x, y)
               for x, y, energy in peaks[1:]):
        return []
    return np.array([(x, y) for x, y, _ in peaks], dtype=np.float32)


def _restore_resampled(crop, gray, hole, train, check):
    """Reconstruct lightly resized/compressed dots, with a held-out border check."""
    if min(gray.shape) < 24:
        return False
    frequencies = _screen_frequencies(gray, train | check)
    if not len(frequencies):
        return False
    ty, tx = np.nonzero(train)
    vy, vx = np.nonzero(check)
    if len(tx) < 240 or len(vx) < 120:
        return False
    if len(tx) > 1800:
        keep = np.random.default_rng(1).choice(len(tx), 1800, replace=False)
        ty, tx = ty[keep], tx[keep]
    h, w = gray.shape

    def features(x, y, modulated):
        coordinates = np.column_stack((x, y)).astype(np.float32)
        angle = (2 * np.pi) * (coordinates @ frequencies.T)
        trend = coordinates / np.array([w, h], np.float32) - .5
        waves = np.column_stack((np.cos(angle), np.sin(angle)))
        columns = [np.ones(len(x), np.float32), trend, waves]
        if modulated:
            columns.extend((waves * trend[:, :1], waves * trend[:, 1:]))
        return np.column_stack(columns)

    rim = crop[vy, vx]
    deviation = gray[vy, vx].std()
    if deviation < 8:
        return False
    samples = crop[ty, tx].astype(np.float32)
    for modulated in (False, True):
        if modulated and len(tx) < 12 * (3 + 6 * len(frequencies)):
            break
        ok, model = cv2.solve(features(tx, ty, modulated), samples, flags=cv2.DECOMP_QR)
        if not ok:
            continue
        predicted = np.clip(features(vx, vy, modulated) @ model, 0, 255)
        residual = np.abs(predicted - rim).max(axis=1)
        # A contrast gradient can change the dots' amplitude, while a contour
        # crossing the hole leaves connected errors on the held-out rim.
        if (residual @ residual) / len(residual) > min(24, .48 * deviation) ** 2:
            continue
        outlier = residual > 48
        if outlier.mean() > .035:
            continue
        if outlier.any():
            defects = np.zeros_like(hole, dtype=np.uint8)
            defects[vy[outlier], vx[outlier]] = 1
            _, _, stats, _ = cv2.connectedComponentsWithStats(defects)
            if stats[1:, cv2.CC_STAT_AREA].max() >= 8:
                continue
        hy, hx = np.nonzero(hole)
        for start in range(0, len(hx), 16384):
            sl = slice(start, start + 16384)
            values = np.clip(features(hx[sl], hy[sl], modulated) @ model, 0, 255)
            crop[hy[sl], hx[sl]] = np.round(values).astype(np.uint8)
        return True
    return False
