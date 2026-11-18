"""Reconstruct smooth manga shading inside a detected lettering mask."""
from __future__ import annotations

import cv2
import numpy as np


def _terms(y, x, shape):
    x = x.astype(np.float32) / max(shape[1] - 1, 1) - .5
    y = y.astype(np.float32) / max(shape[0] - 1, 1) - .5
    return np.column_stack((np.ones_like(x), x, y, x * x, x * y, y * y))


def _fit_tone(crop, train):
    y, x = np.nonzero(train)
    if len(x) < 160:
        return None
    if len(x) > 2600:
        selected = np.random.default_rng(0).choice(len(x), 2600, replace=False)
        y, x = y[selected], x[selected]
    design = _terms(y, x, train.shape)
    colors = crop[y, x].astype(np.float32)
    ok, model = cv2.solve(design, colors, flags=cv2.DECOMP_SVD)
    if not ok:
        return None
    errors = np.abs(design @ model - colors).max(axis=1)
    valid = errors <= min(46, max(10, 2.8 * np.median(errors)))
    if valid.mean() < .62:
        return None
    ok, model = cv2.solve(design[valid], colors[valid], flags=cv2.DECOMP_SVD)
    if not ok:
        return None
    return model


def restore_gradient(crop, seed, *, excluded=None, halo=10):
    """Remove lettering and its halo when the surrounding shade predicts the cutout.

    An outer band fits the shade, a separate band checks the actual cutout,
    and only unexplained pixels connected to the lettering enlarge it.
    Reject a fill if a strong contour connects the lettering to the surrounding art.
    """
    if not seed.any() or seed.mean() > .48:
        return False
    distance = cv2.distanceTransform((~seed).astype(np.uint8), cv2.DIST_L2, 5)
    allowed = np.ones_like(seed) if excluded is None else ~excluded
    train = allowed & (distance >= halo + 4) & (distance <= halo + 26)
    model = _fit_tone(cv2.medianBlur(crop, 3), train)
    if model is None:
        return False
    ty, tx = np.nonzero(train)
    if len(tx) > 2600:
        take = np.random.default_rng(0).choice(len(tx), 2600, replace=False)
        ty, tx = ty[take], tx[take]
    noise = crop[ty, tx] - np.clip(_terms(ty, tx, seed.shape) @ model, 0, 255)
    grain = float(np.median(np.abs(noise).max(axis=1)))
    if grain > 15:
        return False
    local = allowed & (distance <= halo + 6)
    ly, lx = np.nonzero(local)
    predictions = np.clip(_terms(ly, lx, seed.shape) @ model, 0, 255)
    errors = np.zeros(seed.shape, np.float32)
    errors[ly, lx] = np.abs(predictions - crop[ly, lx]).max(axis=1)
    outliers = (cv2.medianBlur(errors, 3) > max(14, grain * 3.2)) & local
    count, labels = cv2.connectedComponents(outliers.astype(np.uint8), connectivity=8)
    near_seed = cv2.dilate(seed.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    touch = np.bincount(labels[near_seed], minlength=count) > 0
    touch[0] = False
    connected = touch[labels]
    if np.any(connected & (distance > halo + 1)):
        return False
    fill = (cv2.dilate(connected.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0) | seed
    # Black ink inside a white outline can be close to the panel's own
    # shade. Fill enclosed holes too, to avoid leaving recognizable letters.
    contours, _ = cv2.findContours(fill.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    enclosed = cv2.drawContours(np.zeros(seed.shape, np.uint8), contours, -1, 1, cv2.FILLED) > 0
    fill |= enclosed & (distance <= halo)
    fill &= local
    edge = cv2.distanceTransform((~fill).astype(np.uint8), cv2.DIST_L2, 3)
    rim = errors[local & (edge >= 1) & (edge <= 4)]
    if len(rim) < 96 or np.median(rim) > max(6, grain * 1.65) or np.mean(rim > max(18, grain * 3.5)) > .065:
        return False
    fy, fx = np.nonzero(fill)
    estimate = _terms(fy, fx, seed.shape) @ model
    if grain > 1.25:
        # Sample the measured scan grain without transplanting nearby ink.
        rng = np.random.default_rng(1)
        noise = noise[np.abs(noise).max(axis=1) <= min(48, max(12, 3.8 * grain))]
        if len(noise):
            estimate += noise[rng.integers(len(noise), size=len(fx))]
    crop[fy, fx] = np.clip(np.rint(estimate), 0, 255).astype(np.uint8)
    return True
