"""Tự ghép các câu dịch của một trang vào các vùng chữ (cùng theo thứ tự đọc).

Số câu và số vùng thường không khớp: có vùng không cần dịch (logo, tên tác giả, SFX nhỏ…), có bóng
mà người dịch tách thành 2–3 câu. Ta căn hàng hai dãy bằng quy hoạch động: mỗi vùng nhận 0 câu
(bỏ qua) hoặc 1–3 câu liền nhau; chọn cách ghép có độ dài câu dịch hợp với lượng chữ Nhật trong
vùng nhất, và ưu tiên bỏ những vùng ít khả năng được dịch.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

MAX_LINES_PER_REGION = 3
MERGE_PENALTY = 1.2  # mỗi câu thêm vào cùng một vùng
SKIP_COST = 2.5  # bỏ qua một vùng chữ bình thường
SFX_MAX_CHARS = 10  # bản dịch SFX ngắn dù chữ gốc vẽ to


def region_ink(page, r) -> float:
    """Lượng chữ gốc trong vùng (số điểm nét chữ)."""
    if r.polygon:
        return float(page.area_mask(r).sum() / 255) * 0.3
    x1, y1, x2, y2 = r.box
    return float((page.text_mask[y1:y2, x1:x2] > 0.3).sum())


def skip_cost(page, r) -> float:
    """Chi phí bỏ qua vùng: thấp với vùng hay để nguyên (SFX nhỏ, chữ màu như logo, sát mép trang)."""
    if r.kind in ("manual", "lasso"):
        return SKIP_COST * 4  # người dùng tự vẽ: chắc chắn cần dịch
    cost = SKIP_COST
    x1, y1, x2, y2 = r.box
    w, h = page.size
    if r.kind == "sfx":
        cost *= 0.25
    crop = page.original[y1:y2, x1:x2]
    if crop.size:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        colored = ((hsv[..., 1] > 50) & (hsv[..., 2] > 60)).mean()
        if colored > 0.03:
            cost *= 0.2  # chữ màu trên truyện đen trắng: tên truyện/logo
    if min(x1, y1, w - x2, h - y2) < 0.02 * w:
        cost *= 0.6  # sát mép trang: tên chương, tác giả, chú thích
    if (x2 - x1) * (y2 - y1) > 0.04 * w * h:
        cost *= 0.6  # khối chữ rất lớn: thường là logo/tiêu đề
    return cost


def _space_penalty(lines: list[str]) -> float:
    """Câu có nhiều từ ít khi là SFX; SFX dịch hai từ ("Ôm chặt", "Khẽ bước") vẫn thường gặp."""
    words = max(len(s.split()) for s in lines)
    return 0.0 if words <= 1 else (0.5 if words == 2 else 1.0)


def assign_lines(page, regions, lines: list[str]) -> list[tuple[object, list[str]]]:
    """Trả về [(vùng, [các câu])] theo thứ tự đọc; vùng bị bỏ qua không có trong danh sách."""
    n, m = len(regions), len(lines)
    if n == 0 or m == 0:
        return []
    ink = [max(1.0, region_ink(page, r)) for r in regions]
    skip = [skip_cost(page, r) for r in regions]
    chars = [max(1, len(s.strip())) for s in lines]
    # Hệ số đổi lượng chữ Nhật → số ký tự tiếng Việt, ước lượng trên cả trang.
    alpha = sum(chars) / sum(ink)
    # Lượng nét của một bóng thoại điển hình trên trang: SFX nhỏ hơn hẳn thường là dấu "!!", vạch hiệu ứng.
    text_ink = float(np.median([ink[i] for i, r in enumerate(regions) if r.kind != "sfx"] or ink))

    def match(i, a, b):
        got = sum(chars[a:b])
        exp = alpha * ink[i]
        fit = math.log((got + 6) / (exp + 6)) ** 2
        cost = fit + (b - a - 1) * MERGE_PENALTY
        if regions[i].kind == "sfx":
            # Bản dịch SFX ngắn ("Vụt", "Ôm chặt") dù chữ gốc vẽ to: độ dài kỳ vọng có trần; câu thoại hiếm khi
            # rơi vào SFX. SFX nhỏ (dấu "!!", vạch hiệu ứng) ít khi được dịch: ưu tiên SFX lớn.
            sfit = math.log((got + 6) / (min(exp, SFX_MAX_CHARS) + 6)) ** 2
            space = _space_penalty(lines[a:b])
            cost = (sfit * 4 + (b - a - 1) * MERGE_PENALTY + 0.4 + space
                    + (0.8 * max(0.0, 1 - ink[i] / text_ink) if space else 0.0))
        return cost

    INF = float("inf")
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0
    for i in range(1, n + 1):
        for j in range(0, m + 1):
            best, arg = dp[i - 1][j] + skip[i - 1], (j, 0)
            for k in range(1, min(MAX_LINES_PER_REGION, j) + 1):
                c = dp[i - 1][j - k] + match(i - 1, j - k, j)
                if c < best:
                    best, arg = c, (j - k, k)
            dp[i][j], back[i][j] = best, arg
    if dp[n][m] == INF:  # nhiều câu hơn sức chứa: dồn phần dư vào vùng cuối
        plan = assign_lines(page, regions, lines[: n * MAX_LINES_PER_REGION])
        if plan:
            plan[-1] = (plan[-1][0], plan[-1][1] + lines[n * MAX_LINES_PER_REGION:])
        return plan
    out = []
    i, j = n, m
    while i > 0:
        pj, k = back[i][j]
        if k:
            out.append((regions[i - 1], lines[pj:j]))
        i, j = i - 1, pj
    out.reverse()
    return _swap_pass(out, regions, ink, alpha)


SWAP_GAIN = 1.5  # chỉ đổi khi độ khớp độ dài tốt lên rõ rệt


def _swap_pass(plan, regions, ink, alpha):
    """Hai bóng liền nhau mà đổi chỗ câu thì độ dài câu khớp lượng chữ gốc hơn hẳn → đổi.

    Sửa trường hợp thứ tự đọc phụ thuộc nội dung (vd bóng "はい" nằm cao hơn câu hỏi trước nó).
    """
    idx = {id(r): i for i, r in enumerate(regions)}

    def fit(r, ls):
        got = sum(len(s.strip()) for s in ls)
        return math.log((got + 6) / (alpha * ink[idx[id(r)]] + 6)) ** 2

    for k in range(len(plan) - 1):
        (ra, la), (rb, lb) = plan[k], plan[k + 1]
        if len(la) == 1 and len(lb) == 1 and ra.kind != "sfx" and rb.kind != "sfx":
            if fit(ra, la) + fit(rb, lb) - (fit(ra, lb) + fit(rb, la)) > SWAP_GAIN:
                plan[k], plan[k + 1] = (ra, lb), (rb, la)
    return plan


def split_region(page, region, lines: list[str]):
    """Bóng mà người dịch tách thành nhiều câu: chia vùng chữ gốc thành từng cụm cột (chữ dọc) hoặc
    hàng (chữ ngang), mỗi câu một cụm, theo tỉ lệ độ dài câu. Trả về các vùng mới, hoặc None."""
    from dataclasses import replace

    k = len(lines)
    x1, y1, x2, y2 = region.box
    strokes = page.text_mask[y1:y2, x1:x2] > 0.3
    if k < 2 or not strokes.any():
        return None
    vertical = (y2 - y1) >= (x2 - x1)
    prof = strokes.sum(axis=0 if vertical else 1)
    bands, start = [], None
    for i, v in enumerate(np.append(prof, 0)):
        if v > 0 and start is None:
            start = i
        elif v == 0 and start is not None:
            bands.append([start, i])
            start = None
    # Gộp cột furigana (rất hẹp) vào cột chữ bên cạnh.
    if bands:
        med = float(np.median([b - a for a, b in bands]))
        merged = []
        for a, b in bands:
            if merged and (b - a < 0.5 * med or a - merged[-1][1] < 2):
                merged[-1][1] = b
            else:
                merged.append([a, b])
        bands = merged
    if vertical:
        bands = bands[::-1]  # chữ dọc đọc từ phải sang trái
    if len(bands) < k:
        return None
    ink = [float(prof[a:b].sum()) for a, b in bands]
    chars = [max(1, len(s.strip())) for s in lines]
    best, best_err = None, 1e9
    import itertools

    for cut in itertools.combinations(range(1, len(bands)), k - 1):
        edges = (0,) + cut + (len(bands),)
        parts = [sum(ink[a:b]) for a, b in zip(edges, edges[1:])]
        err = sum(abs(pi / sum(ink) - c / sum(chars)) for pi, c in zip(parts, chars))
        if min(parts) / sum(ink) >= 0.12 and err < best_err:
            best, best_err = edges, err
    if best is None or best_err > 0.5:
        return None
    out = []
    for (a, b), line in zip(zip(best, best[1:]), lines):
        group = bands[a:b]
        lo, hi = min(g[0] for g in group), max(g[1] for g in group)
        sub = strokes[:, lo:hi] if vertical else strokes[lo:hi, :]
        ys, xs = np.nonzero(sub)
        if vertical:
            box = (x1 + lo, y1 + int(ys.min()), x1 + hi, y1 + int(ys.max()) + 1)
        else:
            box = (x1 + int(xs.min()), y1 + lo, x1 + int(xs.max()) + 1, y1 + hi)
        out.append(replace(region, box=box, text=line, text_rects=[], manual_rect=None))
    return out


def apply_plan(page, plan, style=None) -> int:
    """Ghi câu dịch vào các vùng theo `plan`; vùng nhận nhiều câu thì tách thành từng khối nếu được.
    Trả về số vùng có chữ."""
    from .page import new_uid
    from .typeset_profiles import profile_style

    n = 0
    for r, ls in plan:
        parts = split_region(page, r, ls) if len(ls) > 1 else None
        if parts:
            i = page.regions.index(r)
            page.regions[i:i + 1] = parts
            for part in parts:
                part.uid = new_uid()  # mỗi phần là một vùng (một layer chữ) riêng
                if part.cleaned:  # bóng đã xoá chữ sẵn: tính lại khung đặt chữ cho từng phần
                    page.clean(part)
            targets = list(zip(parts, [[s] for s in ls]))
        else:
            targets = [(r, ls)]
        for reg, lines in targets:
            reg.text = "\n".join(lines)
            if style is not None:
                reg.style = profile_style(page, reg, style)
            n += 1
    return n
