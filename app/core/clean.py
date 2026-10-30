"""Xoá chữ gốc và vẽ lại nền trong một vùng."""
from __future__ import annotations

import cv2
import numpy as np

MARGIN = 20
FURIGANA_PAD = 10


def build_text_mask(text_mask: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """Mask nhị phân (0/255) các nét chữ trong khung, nới rộng để ăn cả viền chữ."""
    h, w = text_mask.shape[:2]
    # Nới khung ra một chút để ăn cả furigana nằm sát mép khung chữ.
    x1, y1 = max(0, box[0] - FURIGANA_PAD), max(0, box[1] - FURIGANA_PAD)
    x2, y2 = min(w, box[2] + FURIGANA_PAD), min(h, box[3] + FURIGANA_PAD)
    out = np.zeros((h, w), np.uint8)
    # Ngưỡng thấp: chữ màu, chữ nhỏ (vd dòng tiếng Anh trên bìa) chỉ được model đánh dấu mờ.
    out[y1:y2, x1:x2] = (text_mask[y1:y2, x1:x2] > 0.15).astype(np.uint8) * 255
    if out[y1:y2, x1:x2].sum() == 0:
        bx1, by1, bx2, by2 = box
        out[by1:by2, bx1:bx2] = 255  # model không ra nét chữ: xoá cả khung
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    return cv2.dilate(out, k, iterations=2)


def skip_hard(img: np.ndarray, mask: np.ndarray) -> None:
    """Chế độ "chỉ xoá chỗ đơn giản": nền có hoạ tiết (tranh, lưới chấm, SFX) để nguyên cho người dùng tự redraw."""


skip_hard.skip = True

SMALL_SFX_FRAC = 0.004  # SFX có khung ≤ 0,4% diện tích trang coi là nhỏ: AI trên máy vẽ lại được


def is_small_sfx(region, size: tuple[int, int]) -> bool:
    x1, y1, x2, y2 = region.box
    return region.kind == "sfx" and (x2 - x1) * (y2 - y1) <= SMALL_SFX_FRAC * size[0] * size[1]


def small_only(inpainter):
    """Chế độ "Tự động": `inpainter` chỉ vẽ lại SFX nhỏ và vùng người dùng tự khoanh; chữ đè lên tranh và
    SFX lớn để nguyên cho người dùng tự redraw (hoặc nạp bản redraw)."""
    def run(img: np.ndarray, mask: np.ndarray) -> None:
        inpainter(img, mask)

    run.skip = lambda page, region: region.kind == "text" or (region.kind == "sfx" and not is_small_sfx(region, page.size))
    return run


def fill_bubble(img: np.ndarray, text_mask: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    """Xoá chữ nếu nằm trong bóng thoại / hộp dẫn truyện sáng, khép kín; False (không đụng gì) nếu không phải."""
    return _fill_light_bubble(img, build_text_mask(text_mask, box), box)


def clean_region(img: np.ndarray, text_mask: np.ndarray, box: tuple[int, int, int, int],
                 area_mask: np.ndarray | None = None, inpainter=None, whole_box: bool = False) -> None:
    """Xoá chữ trong `box` trực tiếp trên `img` (BGR).

    `area_mask` (vùng khoanh lasso, 0/255 cỡ ảnh): xoá toàn bộ trong vùng đó thay vì chỉ nét chữ.
    Nền đồng màu (bóng thoại trắng…) được tô bằng màu nền; nền chuyển màu mượt được khớp lại;
    nền có hoạ tiết (tranh, lưới chấm) vẽ lại bằng `inpainter(img, mask)` (AI: LaMa / Gemini / OpenAI)
    nếu có, không thì inpaint của OpenCV.
    """
    need = clean_local(img, text_mask, box, area_mask, inpainter, whole_box)
    if need is not None:
        inpainter(img, need)


def clean_local(img: np.ndarray, text_mask: np.ndarray, box: tuple[int, int, int, int],
                area_mask: np.ndarray | None = None, inpainter=None, whole_box: bool = False) -> np.ndarray | None:
    """Phần xoá chữ làm ngay trên máy. Trả về mask (0/255, cỡ ảnh) còn cần AI vẽ lại, hoặc None nếu xong.

    Tách riêng để có thể gom nhiều vùng vào một lần gọi AI trên mạng.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    cx1, cy1 = max(0, x1 - MARGIN), max(0, y1 - MARGIN)
    cx2, cy2 = min(w, x2 + MARGIN), min(h, y2 + MARGIN)

    if area_mask is not None:
        mask = cv2.dilate(area_mask[cy1:cy2, cx1:cx2], np.ones((3, 3), np.uint8))
    else:
        mask = build_text_mask(text_mask, box)[cy1:cy2, cx1:cx2]
    if not mask.any():
        return None
    if area_mask is None and _fill_light_bubble(img, build_text_mask(text_mask, box), box):
        return None
    crop = img[cy1:cy2, cx1:cx2]

    ring = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=2)
    ring[mask > 0] = 0
    border = crop[ring > 0]
    # Nền trơn: hầu hết điểm quanh chữ cùng màu (vài nét lọt vào như vạch "──" không tính).
    med = np.median(border, axis=0) if len(border) else None
    close = (np.abs(border.astype(np.int16) - med).max(axis=1) < 25).mean() if len(border) else 0
    if len(border) and (border.std(axis=0).max() < 14 or close >= 0.9):
        crop[mask > 0] = med.astype(np.uint8)
        if area_mask is None:
            # Quét nét sót rộng hơn khung chữ (vd vạch dài phía trên chữ), nhưng chỉ trong lòng bóng.
            ex = max(x2 - x1, y2 - y1) // 2 + MARGIN
            ex1, ey1, ex2, ey2 = max(0, x1 - ex), max(0, y1 - ex), min(w, x2 + ex), min(h, y2 + ex)
            near = cv2.dilate(build_text_mask(text_mask, box)[ey1:ey2, ex1:ex2], np.ones((15, 15), np.uint8)) > 0
            _remove_leftover_strokes(img[ey1:ey2, ex1:ex2], (x1 - ex1, y1 - ey1, x2 - ex1, y2 - ey1), near=near,
                                     limit=(0, 0, ex2 - ex1, ey2 - ey1))
        return None
    # Nền chuyển màu mượt (gradient): khớp mặt cong bậc 2 theo viền rồi tô lại.
    if len(border) >= 30:
        ys, xs = np.nonzero(ring)
        coef, fitted = _fit_surface(xs, ys, border.astype(np.float32))
        if np.abs(fitted - border).mean() < 6:
            my, mx = np.nonzero(mask)
            crop[my, mx] = np.clip(_surface(mx, my) @ coef, 0, 255).astype(np.uint8)
            return None
    if inpainter is not None:
        # Chữ trên tranh (SFX) thường có viền trắng quanh nét: nới mask để xoá luôn phần viền.
        if area_mask is None:
            mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=2)
            # Xoá cả khung chữ (xoá luôn nét mờ model bỏ sót như "——", "。"): AI trên mạng luôn làm vậy;
            # LaMa làm vậy với khung chữ thoại/dẫn truyện (SFX thì chỉ theo nét để giữ tranh).
            if getattr(inpainter, "whole_box", False) or whole_box:
                bx1, by1 = max(0, x1 - 4) - cx1, max(0, y1 - 4) - cy1
                mask[by1:y2 + 4 - cy1, bx1:x2 + 4 - cx1] = 255
        full = np.zeros(img.shape[:2], np.uint8)
        full[cy1:cy2, cx1:cx2] = mask
        return full
    crop[:] = cv2.inpaint(crop, mask, 5, cv2.INPAINT_TELEA)
    return None


def _fill_light_bubble(img: np.ndarray, strokes: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    """Bóng thoại sáng (kể cả bóng tia sáng không có viền kín): tô trắng cả lòng bóng.

    Xoá nét chữ, loang màu sáng từ tâm chữ trên ảnh đã làm mờ (lưới chấm bên ngoài thành xám nên không
    loang ra được), lấp lỗ bên trong. Nếu được một vùng sáng, khép kín, không quá lớn thì mọi thứ lọt thỏm
    trong đó (chữ, furigana, dấu "……", vạch "──") là chữ → tô màu nền; tia/viền bóng chạm mép vùng được giữ.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    e = 2 * max(x2 - x1, y2 - y1)
    wx1, wy1, wx2, wy2 = max(0, x1 - e), max(0, y1 - e), min(w, x2 + e), min(h, y2 + e)
    win = img[wy1:wy2, wx1:wx2]
    strokes = strokes[wy1:wy2, wx1:wx2] > 0
    gray = cv2.cvtColor(win, cv2.COLOR_BGR2GRAY)
    # Chỉ xoá nét trong đúng khung chữ khi dò lòng bóng: chữ (nhất là chữ Anh) hay sát viền bóng, xoá cả phần
    # nới rộng thì mất viền và vùng loang tràn ra ngoài. Furigana ngoài khung thành "lỗ" trong lòng bóng, vẫn được tô.
    core = np.zeros_like(strokes)
    core[max(0, y1 - 2 - wy1):y2 + 2 - wy1, max(0, x1 - 2 - wx1):x2 + 2 - wx1] = True
    gray[strokes & core] = 255
    soft = cv2.GaussianBlur(gray, (0, 0), 2.5)
    sx, sy = (x1 + x2) // 2 - wx1, (y1 + y2) // 2 - wy1
    if soft[sy, sx] < 225:
        return False
    ff = np.zeros((soft.shape[0] + 2, soft.shape[1] + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    seed = int(soft[sy, sx])
    cv2.floodFill(soft, ff, (sx, sy), 0, seed - 222, 255, flags)  # mọi điểm ≥ 222 (đã làm mờ)
    region = ff[1:-1, 1:-1] > 0
    ys, xs = np.nonzero(region)
    if (len(xs) == 0 or xs.min() == 0 or ys.min() == 0 or xs.max() == region.shape[1] - 1
            or ys.max() == region.shape[0] - 1 or len(xs) > 0.25 * w * h):
        return False  # không khép kín trong cửa sổ: nền trắng của trang/panel, không phải bóng
    interior = _fill_holes(region.astype(np.uint8))
    # Bóng thật bao trọn khung chữ và có lề quanh chữ; viền trắng của chữ đè lên tranh (tên tác giả…) thì bám sát chữ.
    box_in = interior[y1 - wy1:y2 - wy1, x1 - wx1:x2 - wx1]
    if box_in.mean() < 0.9 or interior.sum() < 1.3 * (x2 - x1) * (y2 - y1):
        return False
    bg = np.median(win[region & ~strokes], axis=0)
    if bg.min() < 230:
        return False
    holes = _drop_edge_holes(interior & ~region, interior)
    # Nét chữ chỉ tô trong khung chữ (model hay đánh dấu nhầm cả tia của bóng tia sáng là chữ).
    in_box = np.zeros_like(strokes)
    p = FURIGANA_PAD
    in_box[max(0, y1 - p - wy1):y2 + p - wy1, max(0, x1 - p - wx1):x2 + p - wx1] = True
    win[holes | (strokes & interior & in_box)] = bg.astype(np.uint8)
    return True


def _drop_edge_holes(holes: np.ndarray, interior: np.ndarray) -> np.ndarray:
    """Bỏ các "lỗ" chạm sát mép lòng bóng: đó là tia của bóng tia sáng / răng cưa viền, không phải chữ sót."""
    edge = interior & ~cv2.erode(interior.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    n, lab = cv2.connectedComponents(holes.astype(np.uint8))
    touching = np.unique(lab[edge & holes])
    return holes & ~np.isin(lab, touching[touching > 0])


def _remove_leftover_strokes(crop: np.ndarray, box: tuple[int, int, int, int], limit=None, near=None) -> None:
    """Nền trơn: xoá nét mảnh model bỏ sót (vd "──") nằm lọt thỏm trong lòng bóng.

    Chỉ xoá những điểm bị nền bao quanh hoàn toàn, nên viền bóng và nền ngoài bóng không bị đụng tới.
    `near` (cỡ `crop`): chỉ xoá cụm nét chạm vùng này (quanh nét chữ đã nhận diện) – giữ tia của bóng
    tia sáng, hoạ tiết… nằm xa chữ.
    """
    x1, y1, x2, y2 = box
    seed = ((x1 + x2) // 2, (y1 + y2) // 2)
    ff = np.zeros((crop.shape[0] + 2, crop.shape[1] + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    cv2.floodFill(crop.copy(), ff, seed, (0, 0, 0), (30, 30, 30), (30, 30, 30), flags)
    region = ff[1:-1, 1:-1]
    contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    interior = np.zeros_like(region)
    cv2.drawContours(interior, contours, -1, 255, cv2.FILLED)
    holes = _drop_edge_holes((interior > 0) & (region == 0), interior > 0)
    if near is not None:
        n, lab = cv2.connectedComponents(holes.astype(np.uint8))
        hit = np.unique(lab[near & holes])
        holes &= np.isin(lab, hit[hit > 0])
    inside_box = np.zeros_like(holes)
    lx1, ly1, lx2, ly2 = limit or box
    inside_box[ly1:ly2, lx1:lx2] = True
    holes = cv2.dilate((holes & inside_box).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    holes &= interior > 0
    crop[holes] = crop[seed[1], seed[0]]


def _surface(xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    x, y = xs.astype(np.float32) / 100, ys.astype(np.float32) / 100
    return np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)


def _fit_surface(xs, ys, values):
    a = _surface(xs, ys)
    coef, *_ = np.linalg.lstsq(a, values, rcond=None)
    return coef, a @ coef


def text_area_candidates(img: np.ndarray, box: tuple[int, int, int, int],
                         area_mask: np.ndarray | None = None) -> list[tuple[int, int, int, int]]:
    """Các khung có thể đặt bản dịch trong lòng bóng thoại chứa `box` (sau khi đã xoá chữ).

    Loang màu từ tâm khung để lấy lòng bóng, rồi liệt kê các hình chữ nhật nằm trọn trong đó.
    Nếu vùng loang không giống bóng thoại (tràn ra mép ảnh, quá lớn) thì dùng vùng lasso
    `area_mask` (nếu có), không thì chỉ dùng khung gốc.
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in box)
    seed = ((x1 + x2) // 2, (y1 + y2) // 2)
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (255 << 8)
    cv2.floodFill(img.copy(), ff_mask, seed, (0, 0, 0), (12, 12, 12), (12, 12, 12), flags)
    region = _fill_holes(ff_mask[1:-1, 1:-1])
    n = int(region.sum())
    box_area = max(1, (x2 - x1) * (y2 - y1))
    ys, xs = np.nonzero(region) if n else (np.array([0]), np.array([0]))
    is_bubble = (n > 0 and xs.min() > 0 and ys.min() > 0 and xs.max() < w - 1 and ys.max() < h - 1
                 and n <= 0.3 * w * h and n <= 80 * box_area)
    if not is_bubble:
        if area_mask is None:
            return [box]
        region = _fill_holes(area_mask)
        ys, xs = np.nonzero(region)
        if len(xs) == 0:
            return [box]
    bx1, by1, bx2, by2 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1

    pad = 3
    cands = []
    seed_rel = (seed[0] - bx1, seed[1] - by1)
    for rx1, ry1, rx2, ry2 in _inner_rects(region[by1:by2, bx1:bx2], seed_rel):
        r = (int(bx1 + rx1 + pad), int(by1 + ry1 + pad), int(bx1 + rx2 - pad), int(by1 + ry2 - pad))
        if r[2] - r[0] > 8 and r[3] - r[1] > 8:
            cands.append(r)
    # Khung chữ gốc có thể chòi ra ngoài lòng bóng (bóng đôi, bóng méo) nên chỉ dùng khi không còn cách khác.
    return cands or [box]


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Lấp lỗ trong vùng (điểm mờ còn sót sau khi xoá chữ) để không chặn việc tìm khung đặt chữ."""
    contours, _ = cv2.findContours((mask > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    out = np.zeros(mask.shape, np.uint8)
    cv2.drawContours(out, contours, -1, 1, cv2.FILLED)
    return out > 0


def _inner_rects(region: np.ndarray, seed: tuple[int, int]) -> list[tuple[int, int, int, int]]:
    """Các hình chữ nhật nằm trọn trong `region` và chứa điểm `seed` (tâm chữ gốc).

    Bóng thoại có thể méo hoặc là bóng đôi, nên thử nhiều tâm dọc và chiều cao khác nhau rồi để
    bước dàn chữ chọn khung cho cỡ chữ lớn nhất. Mỗi hàng chỉ lấy đoạn liền chứa cột `seed`,
    nên chữ của nửa bóng nào nằm trong nửa bóng đó.
    """
    h, w = region.shape
    sx, sy = int(np.clip(seed[0], 0, w - 1)), int(np.clip(seed[1], 0, h - 1))
    cols = np.arange(w)
    valid = region[:, sx]
    left = np.where(~region[:, :sx + 1], cols[:sx + 1], -1).max(axis=1) + 1
    right = np.where(~region[:, sx:], cols[sx:], w).min(axis=1)
    left = np.where(valid, left, w)
    right = np.where(valid, right, 0)
    centers = {sy}
    centers.update(int(h * f) for f in (0.3, 0.4, 0.5, 0.6, 0.7))
    step = max(2, h // 40)
    out = []
    for cy in sorted(centers):
        cur_l, cur_r = 0, w
        for hh in range(1, h):
            top, bot = cy - hh, cy + hh - 1
            if top < 0 or bot >= h:
                break
            cur_l = max(cur_l, left[top], left[bot])
            cur_r = min(cur_r, right[top], right[bot])
            if cur_r - cur_l <= 0:
                break
            if hh % step == 0 and top <= sy <= bot:
                out.append((int(cur_l), top, int(cur_r), bot + 1))
    return out


