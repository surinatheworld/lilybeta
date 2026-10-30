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

    def match(i, a, b):
        got = sum(chars[a:b])
        exp = alpha * ink[i]
        fit = math.log((got + 6) / (exp + 6)) ** 2
        cost = fit + (b - a - 1) * MERGE_PENALTY
        if regions[i].kind == "sfx":
            # Bản dịch SFX ngắn và khớp cỡ chữ gốc ("Vụt", "Khẽ bước"); câu thoại hiếm khi rơi vào SFX.
            cost += 3 * fit + 0.4 + (1.0 if any(" " in s.strip() for s in lines[a:b]) else 0)
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
