"""Series-specific font rules and lightweight balloon classification on the raw image."""
from __future__ import annotations

import math

import cv2
import numpy as np

from .typeset import TextStyle

GAI_BANG = "gai_bang"
KINDS = {"auto": "Tự nhận dạng", "normal": "Thoại thường · tròn kín",
         "thought": "Suy nghĩ · mây / tia", "square": "Khung ô vuông", "sfx": "SFX"}
FONTS = {"normal": "000 WildWords2 TB", "thought": "000 AnimeAce3 [TeddyBear]",
         "square": "00 CE Scriptorama Markdown JF", "sfx": "MTO augie"}


def _ray_balloon(walls, center, text_size):
    """Disconnected radial strokes around the text, rather than arbitrary nearby art."""
    lines = cv2.HoughLinesP(walls, 1, np.pi / 180, max(10, int(text_size * .12)),
                            minLineLength=max(8, text_size * .15), maxLineGap=3)
    if lines is None:
        return False
    bins, count = set(), 0
    cx, cy = center
    for x1, y1, x2, y2 in lines.reshape(-1, 4):
        dx, dy = float(x2 - x1), float(y2 - y1)
        rx, ry = (x1 + x2) / 2 - cx, (y1 + y2) / 2 - cy
        radius, length = math.hypot(rx, ry), math.hypot(dx, dy)
        if not (.55 * text_size < radius < 2.2 * text_size) or not length:
            continue
        if abs(dx * rx + dy * ry) / (length * radius) >= .88:
            bins.add(int((math.atan2(ry, rx) + math.pi) * 12 / (2 * math.pi)) % 12)
            count += 1
    return count >= 8 and len(bins) >= 6


def classify_balloon(image: np.ndarray, box) -> str:
    """Infer round/rectangular/scalloped/radiating balloons; default to ordinary dialogue.

    This is a geometric heuristic, not a semantic model. The user can override each
    region. Only a bounded, downsampled crop is inspected; input pixels are never changed.
    """
    h, w = image.shape[:2]
    x1, y1, x2, y2 = map(int, box)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return "normal"
    extent = max(x2 - x1, y2 - y1)
    margin = int(extent * 2)
    left, top = max(0, x1 - margin), max(0, y1 - margin)
    right, bottom = min(w, x2 + margin), min(h, y2 + margin)
    crop = image[top:bottom, left:right]
    scale = min(1., 768 / max(crop.shape[:2]))
    if scale < 1:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    bx1, by1 = int((x1 - left) * scale), int((y1 - top) * scale)
    bx2, by2 = max(bx1 + 1, int((x2 - left) * scale)), max(by1 + 1, int((y2 - top) * scale))
    gray[by1:by2, bx1:bx2] = 255  # remove the detected lettering, preserve the surrounding outline
    walls = (gray < 190).astype(np.uint8) * 255
    barriers = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    seed = ((bx1 + bx2) // 2, (by1 + by2) // 2)
    free = (barriers == 0).astype(np.uint8)
    mask = np.zeros((free.shape[0] + 2, free.shape[1] + 2), np.uint8)
    cv2.floodFill(free, mask, seed, 2, flags=4)
    interior = (free == 2).astype(np.uint8)
    contours, _ = cv2.findContours(interior, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return "normal"
    contour = max(contours, key=cv2.contourArea)
    x, y, cw, ch = cv2.boundingRect(contour)
    bounded = x > 0 and y > 0 and x + cw < gray.shape[1] and y + ch < gray.shape[0]
    area = cv2.contourArea(contour)
    if bounded and area > (bx2 - bx1) * (by2 - by1) * 1.15:
        rect = cv2.minAreaRect(contour)
        rectangularity = area / max(1, rect[1][0] * rect[1][1])
        approx = cv2.approxPolyDP(contour, .025 * cv2.arcLength(contour, True), True)
        if rectangularity >= .88 and 4 <= len(approx) <= 6:
            return "square"
        hull = cv2.convexHull(contour, returnPoints=False)
        if len(hull) > 3 and len(contour) > len(hull):
            try:
                defects = cv2.convexityDefects(contour, hull)
            except cv2.error:  # self-touching scan contours can have a non-monotonic hull
                defects = None
            if defects is not None:
                dents = sum(depth / 256 > max(2, min(cw, ch) * .018) for _, _, _, depth in defects.reshape(-1, 4))
                if dents >= 4:
                    return "thought"
        return "normal"
    return "thought" if _ray_balloon(walls, seed, extent * scale) else "normal"


def region_kind(page, region) -> str:
    override = getattr(region, "typeset_kind", "auto")
    if override in FONTS:
        return override
    if region.kind == "sfx":
        return "sfx"
    cache = getattr(page, "_balloon_cache", None)
    if cache is None:
        cache = page._balloon_cache = {}
    key = tuple(region.box)
    if key not in cache:
        cache[key] = classify_balloon(page.original, region.box)
    return cache[key]


def profile_style(page, region, base: TextStyle) -> TextStyle:
    style = base.copy()
    if getattr(page, "typeset_profile", "") == GAI_BANG:
        kind = region_kind(page, region)
        style.family = FONTS[kind]
        style.style_name = ""
        style.bold = False
        style.italic = kind == "thought"
    return style


def apply_profile(page, regions=None) -> None:
    if getattr(page, "typeset_profile", "") == GAI_BANG:
        for region in page.regions if regions is None else regions:
            region.style = profile_style(page, region, region.style)
