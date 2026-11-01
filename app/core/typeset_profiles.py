"""Series-specific font rules and lightweight balloon classification on the raw image."""
from __future__ import annotations

import math
import re

import cv2
import numpy as np

from .typeset import TextStyle

GAI_BANG = "gai_bang"
ISSHIKI = "isshiki"
CUSTOM = "custom"
PROFILES = {GAI_BANG: "Typeset Gái Băng", ISSHIKI: "Typeset Isshiki", CUSTOM: "Tự chọn font theo loại khung"}
KINDS = {"auto": "Tự nhận dạng", "normal": "Thoại thường · tròn kín",
         "thought": "Suy nghĩ · mây / tia", "square": "Khung ô vuông", "sfx": "SFX"}
FONTS = {"normal": "000 WildWords2 TB", "thought": "000 AnimeAce3 [TeddyBear]",
         "square": "00 CE Scriptorama Markdown JF", "sfx": "MTO augie"}
ISSHIKI_KINDS = {"auto": "Tự nhận dạng", "normal": "Thoại thường · tròn kín", "thought": "Suy nghĩ · tròn có tia",
                 "square": "Suy nghĩ · khung vuông", "bold": "Thoại in đậm", "free": "Suy nghĩ · không khung",
                 "flustered": "Thoại lúng túng", "sfx": "SFX"}
_HAND = "SJ Chanighter Handwriting"
ISSHIKI_FONTS = {"normal": _HAND, "thought": _HAND, "square": _HAND, "bold": "000 ObelixPro TB",
                 "free": "SJ SS Rosehip", "flustered": "SJ CCComicraft", "sfx": "MTO augie"}
# Isshiki: trong bóng thoại chữ nhỏ hơn chút so với cỡ vừa khít, lề quanh chữ rộng; ô vuông / dẫn truyện thì chữ lấp
# gần kín ô (như nhóm NaJung). Giữ nguyên cách xuống dòng.
FIT_SCALE = {ISSHIKI: 0.88}
FULL_KINDS = {"square", "sfx"}
# Tự chọn font: tool nhận loại khung, người dùng chọn font / nghiêng cho từng loại (page.custom_fonts).
CUSTOM_KINDS = {"auto": "Tự nhận dạng", "normal": "Thoại thường · tròn kín", "thought": "Suy nghĩ · mây / tia",
                "square": "Khung vuông · ô dẫn truyện", "bold": "Chữ in đậm · hét", "free": "Chữ ngoài khung",
                "flustered": "Thoại lúng túng · nói lắp", "sfx": "SFX"}
CUSTOM_DEFAULTS = {**FONTS, "bold": FONTS["normal"], "free": FONTS["thought"], "flustered": FONTS["normal"]}
_RULES = {GAI_BANG: (KINDS, FONTS, {"thought"}), ISSHIKI: (ISSHIKI_KINDS, ISSHIKI_FONTS, {"thought", "square"}),
          CUSTOM: (CUSTOM_KINDS, CUSTOM_DEFAULTS, set())}
# Chữ raw in đậm (gothic đậm khi hét): nét dày ≥ 12% cỡ chữ và mực phủ ≥ 54% ô chữ (thoại thường 8–11%, 37–53%).
BOLD_STROKE, BOLD_DENSITY, SOLID_DENSITY = .12, .54, .78
# Viền bóng (độ lõm / cạnh ngắn của bóng): thắt ≥ 20% là hai bóng dính nhau; mây lõm ≥ 8%; nông hơn là viền run.
PINCH, CLOUD_DENT = .2, .08
_STUTTER = re.compile(r"(?:^|[\s\"'“(…])(?P<stem>\w{1,3})[-–—]\s*(?P=stem)", re.IGNORECASE)  # "e- em", "a- anh"


def kinds_for(profile: str) -> dict:
    return _RULES.get(profile, _RULES[GAI_BANG])[0]


def fonts_for(profile: str) -> dict:
    return _RULES.get(profile, _RULES[GAI_BANG])[1]


def is_italic(profile: str, kind: str) -> bool:
    return kind in _RULES.get(profile, _RULES[GAI_BANG])[2]


def flustered(text: str) -> bool:
    """Câu nói lắp ("e- em ổn", "a- anh xin lỗi!") – thoại lúng túng."""
    return bool(_STUTTER.search(text or ""))


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
    """Infer round/rectangular/scalloped/radiating balloons; default to ordinary dialogue."""
    shape = balloon_shape(image, box)
    return "normal" if shape == "free" else shape


def balloon_shape(image: np.ndarray, box, outline: bool = False) -> str:
    """"normal" | "square" | "thought" (cloud or rays) | "free" (lettering on the art, no closed balloon).

    With `outline`, the dents of the outline are read more closely: one deep pinch is two touching balloons
    ("normal"), many shallow dents are a trembling outline ("wobbly"), only deep scallops are a thought cloud.

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
                depths = defects.reshape(-1, 4)[:, 3] / 256
                dents = int((depths > max(2, min(cw, ch) * .018)).sum())
                if outline:
                    rel = np.sort(depths / max(1, min(cw, ch)))[::-1]
                    if rel[0] >= PINCH:
                        return "normal"  # hai bóng dính nhau: một chỗ thắt sâu, không phải mây
                    if dents >= 4:
                        return "thought" if np.median(rel[:4]) >= CLOUD_DENT else "wobbly"
                elif dents >= 4:
                    return "thought"
        return "normal"
    if _ray_balloon(walls, seed, extent * scale):
        # Isshiki: nét bao quanh chữ mà lòng bóng hở là bóng thoại bị hở viền (dính bóng khác / khung tranh).
        return "normal" if outline else "thought"
    return "normal" if bounded else "free"


def _runs(mask: np.ndarray) -> np.ndarray:
    """Lengths of the runs of 1s along each row of a 0/1 mask."""
    padded = np.pad(mask.astype(np.int8), ((0, 0), (1, 1)))
    d = np.diff(padded, axis=1)
    return np.nonzero(d == -1)[1] - np.nonzero(d == 1)[1]


def lettering_features(image: np.ndarray, text_mask, box) -> dict:
    """Stroke weight of the raw lettering: stroke width relative to the character size, and how much of each glyph
    cell is ink. Bold gothic (shouting) has strokes ~2x thicker than ordinary dialogue at the same size."""
    h, w = image.shape[:2]
    x1, y1, x2, y2 = map(int, box)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 - x1 < 4 or y2 - y1 < 4 or text_mask is None:
        return {"stroke": 0., "density": 0., "char": 0., "dark": False}
    gray = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
    dark = bool(np.median(gray) < 110)
    if dark:  # chữ trắng trên nền đen (ô vuông đen)
        gray = 255 - gray
    cells = (text_mask[y1:y2, x1:x2] > .5).astype(np.uint8)
    ink = ((gray < 110) & (cells > 0)).astype(np.uint8)
    area = int(ink.sum())
    if area < 20 or cells.sum() < 50:
        return {"stroke": 0., "density": 0., "char": 0., "dark": dark}
    contours, _ = cv2.findContours(ink, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    perimeter = sum(cv2.arcLength(c, True) for c in contours)
    stroke = 2 * area / max(1., perimeter)
    # Glyph size: thickest column (vertical text) or row (horizontal) of ink; furigana columns are thinner.
    across = [r.max() for r in (_runs(ink.any(0)[None]), _runs(ink.any(1)[None])) if len(r)]
    char = float(min(across)) if across else 0.
    return {"stroke": stroke / max(1., char), "density": area / float(cells.sum()), "char": char, "dark": dark}


def detailed_kind(page, box) -> str:
    """Loại khung chi tiết (Isshiki, tự chọn font): thường / suy nghĩ / vuông / in đậm / ngoài khung."""
    feats = lettering_features(page.original, getattr(page, "text_mask", None), box)
    if feats["dark"]:
        return "square"  # chữ trắng trong ô đen: lời dẫn / suy nghĩ khung vuông
    if feats["density"] >= SOLID_DENSITY or (feats["stroke"] >= BOLD_STROKE and feats["density"] >= BOLD_DENSITY):
        return "bold"
    shape = balloon_shape(page.original, box, outline=True)
    return "normal" if shape == "wobbly" else shape  # viền lượn sóng: cả lời chào vui lẫn hốt hoảng, không đoán


def region_kind(page, region) -> str:
    profile = getattr(page, "typeset_profile", "") or GAI_BANG
    fonts = fonts_for(profile)
    override = getattr(region, "typeset_kind", "auto")
    if override in fonts:
        return override
    if region.kind == "sfx":
        return "sfx"
    if getattr(region, "orphan", False):
        return "normal"  # khung tạm trên tranh: chưa biết loại thoại
    cache = getattr(page, "_balloon_cache", None)
    if cache is None:
        cache = page._balloon_cache = {}
    key = (profile, tuple(region.box))
    if key not in cache:
        cache[key] = (detailed_kind(page, region.box) if profile in (ISSHIKI, CUSTOM)
                      else classify_balloon(page.original, region.box))
    kind = cache[key]
    if profile in (ISSHIKI, CUSTOM) and kind != "bold" and flustered(region.text):
        return "flustered"
    return kind


def profile_style(page, region, base: TextStyle) -> TextStyle:
    style = base.copy()
    profile = getattr(page, "typeset_profile", "")
    if profile in PROFILES:
        kind = region_kind(page, region)
        style.family = fonts_for(profile)[kind]
        style.style_name = ""
        style.bold = False
        style.italic = is_italic(profile, kind)
        chosen = (getattr(page, "custom_fonts", None) or {}).get(kind) if profile == CUSTOM else None
        if chosen and chosen.get("family"):
            style.family = chosen["family"]
            style.style_name = chosen.get("style", "") if chosen.get("style", "").lower() not in ("regular", "normal", "roman") else ""
            style.italic = bool(chosen.get("italic"))
        style.fit_scale = 1.0 if kind in FULL_KINDS else FIT_SCALE.get(profile, 1.0)
        if kind == "square" and style.shape == "round":
            style.shape = "even"  # khung chữ nhật: các dòng dài đều, không bo tròn
    return style


def apply_profile(page, regions=None) -> None:
    if getattr(page, "typeset_profile", "") in PROFILES:
        for region in page.regions if regions is None else regions:
            region.style = profile_style(page, region, region.style)
