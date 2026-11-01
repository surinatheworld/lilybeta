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
MERGE_SHORT = 1.0  # thêm cho mỗi câu ngắn (≤ 2 từ) gộp chung bóng: thường là SFX / câu chen vẽ tay riêng
CLUSTER_GAP = 1.2  # nét chữ cách nhau hơn ~1,2 ký tự: hai cụm chữ riêng…
CLUSTER_MIN = 0.15  # …mỗi cụm ≥ 15% lượng nét của vùng (bỏ furigana, dấu "…")
DROP_COST = 3.0  # để riêng một câu (layer "CHƯA ĐẶT") thay vì đẩy lệch các câu sau sang bóng khác
TINY_INK = .08  # bóng chỉ có dấu ("…", "!", "?"): nét < 8% một bóng thoại điển hình của trang
TINY_WORDS_PENALTY = 3.0  # câu nhiều từ rơi vào bóng chỉ có dấu
BIG_SFX = .5  # SFX nhiều nét cỡ một bóng thoại (biển, chữ in trên tranh): thường được dịch, khó bỏ qua


def _footer_strip(page, r) -> bool:
    x1, y1, x2, y2 = r.box
    return r.kind == "text" and y1 >= .93 * page.size[1] and x2 - x1 >= 3 * (y2 - y1)


def region_ink(page, r) -> float:
    """Lượng chữ gốc trong vùng (số điểm nét chữ)."""
    if r.polygon:
        return float(page.area_mask(r).sum() / 255) * 0.3
    x1, y1, x2, y2 = r.box
    return float((page.text_mask[y1:y2, x1:x2] > 0.3).sum())


def text_clusters(page, r, glyph: float = 0.) -> int:
    """Số cụm chữ tách rời trong vùng (ô bậc thang "よくない / 俺よくないぞ", hai khối so le): mỗi cụm thường là một
    câu dịch riêng, nên ghép nhiều câu vào vùng như vậy là tự nhiên."""
    if r.polygon or getattr(page, "text_mask", None) is None:
        return 1
    x1, y1, x2, y2 = r.box
    mask = (page.text_mask[y1:y2, x1:x2] > 0.3).astype(np.uint8)
    if not mask.any():
        return 1
    k = max(3, int(glyph * CLUSTER_GAP) if glyph else 9)
    n, _, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(mask, np.ones((k, k), np.uint8)), connectivity=8)
    areas = stats[1:, cv2.CC_STAT_AREA]
    return max(1, int((areas >= CLUSTER_MIN * areas.sum()).sum()))


def glyph_size(page, r) -> float:
    """Cỡ một ký tự gốc (px): bề ngang cột chữ dọc / bề cao dòng chữ ngang; 0 nếu không đo được."""
    from .typeset_profiles import lettering_features

    if r.polygon or getattr(page, "text_mask", None) is None:
        return 0.
    return lettering_features(page.original, page.text_mask, r.box)["char"]


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
    if _footer_strip(page, r):
        # A short horizontal strip at the foot of the page is commonly a next-
        # chapter notice. Keep it eligible, but don't sacrifice a translated SFX
        # or thought balloon just to fill this extra detector result.
        cost *= .12
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
    raw_ink = [max(1.0, region_ink(page, r)) for r in regions]
    skip = [skip_cost(page, r) for r in regions]
    # Độ dài câu gốc ~ số ký tự = lượng nét / diện tích một ký tự: chữ hét to nhiều nét mà ít chữ,
    # khung chữ nhỏ li ti (chú thích, tiếng Anh) ít nét mà nhiều chữ.
    sizes = [glyph_size(page, r) if r.kind != "sfx" else 0. for r in regions]  # SFX đã có trần độ dài riêng
    known = [g for g in sizes if g > 0]
    g0 = float(np.median(known)) if known else 1.
    ink = [v * (g0 / min(max(g, .5 * g0), 2.5 * g0)) ** 2 if g > 0 else v for v, g in zip(raw_ink, sizes)]
    chars = [max(1, len(s.strip())) for s in lines]
    # Hệ số đổi lượng chữ Nhật → số ký tự tiếng Việt, ước lượng trên cả trang.
    # Decorative SFX and a large untranslated footer can otherwise shrink the
    # estimate enough to pair a two-word SFX translation with a thought balloon.
    dialogue_ink = [ink[i] for i, r in enumerate(regions) if r.kind != "sfx" and not _footer_strip(page, r)]
    alpha = sum(chars) / sum(dialogue_ink or ink)
    # Lượng nét của một bóng thoại điển hình trên trang: SFX nhỏ hơn hẳn thường là dấu "!!", vạch hiệu ứng.
    text_ink = float(np.median([ink[i] for i, r in enumerate(regions) if r.kind != "sfx"] or ink))
    raw_text_ink = float(np.median([raw_ink[i] for i, r in enumerate(regions) if r.kind != "sfx"] or raw_ink))
    big = [r.kind == "sfx" and raw_ink[i] >= BIG_SFX * raw_text_ink for i, r in enumerate(regions)]
    clusters = [text_clusters(page, r, sizes[i]) if r.kind != "sfx" else 1 for i, r in enumerate(regions)]
    for i, r in enumerate(regions):
        if big[i]:  # biển / chữ in cỡ một bóng thoại: chấm như chữ thường
            skip[i] = max(skip[i], skip_cost(page, r) * 2.4)  # bỏ hệ số giảm của SFX (×0.25 → ×0.6)

    def match(i, a, b):
        got = sum(chars[a:b])
        exp = alpha * ink[i]
        fit = math.log((got + 6) / (exp + 6)) ** 2
        extra = max(0, b - a - clusters[i])  # câu gộp quá số cụm chữ gốc của vùng
        cost = fit + extra * MERGE_PENALTY + MERGE_SHORT * sum(len(s.split()) <= 2 for s in lines[a:b]) * (extra > 0)
        if _footer_strip(page, regions[i]):
            cost += .75
        if raw_ink[i] < TINY_INK * raw_text_ink and max(len(s.split()) for s in lines[a:b]) >= 2:
            cost += TINY_WORDS_PENALTY
        if regions[i].kind == "sfx":
            # Bản dịch SFX ngắn ("Vụt", "Ôm chặt") dù chữ gốc vẽ to: độ dài kỳ vọng có trần; câu thoại hiếm khi
            # rơi vào SFX. SFX nhỏ (dấu "!!", vạch hiệu ứng) ít khi được dịch: ưu tiên SFX lớn.
            sfit = math.log((got + 6) / (min(exp, SFX_MAX_CHARS) + 6)) ** 2
            if big[i]:  # SFX lớn hoặc biển / chữ in trên tranh: câu ngắn kiểu SFX hay cụm từ đều hợp
                return min(sfit * 4, fit) + 0.4
            space = _space_penalty(lines[a:b])
            cost = (sfit * 4 + (b - a - 1) * MERGE_PENALTY + 0.4 + space
                    + (0.8 * max(0.0, 1 - ink[i] / text_ink) if space else 0.0))
        return cost

    INF = float("inf")
    dp = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    for j in range(m + 1):
        dp[0][j], back[0][j] = j * (DROP_COST + 1e-3), (j - 1, -1)  # ngang nhau thì để riêng câu sau
    for i in range(1, n + 1):
        for j in range(0, m + 1):
            best, arg = dp[i - 1][j] + skip[i - 1], (j, 0)
            most = 1 if regions[i - 1].kind == "sfx" else MAX_LINES_PER_REGION  # SFX / biển: một câu
            for k in range(1, min(most, j) + 1):
                c = dp[i - 1][j - k] + match(i - 1, j - k, j)
                if c < best:
                    best, arg = c, (j - k, k)
            if j and dp[i][j - 1] + DROP_COST < best:  # câu j-1 không hợp bóng nào quanh đó: để riêng
                best, arg = dp[i][j - 1] + DROP_COST, (j - 1, -1)
            dp[i][j], back[i][j] = best, arg
    if dp[n][m] == INF:  # nhiều câu hơn sức chứa: dồn phần dư vào vùng cuối
        plan = assign_lines(page, regions, lines[: n * MAX_LINES_PER_REGION])
        if plan:
            plan[-1] = (plan[-1][0], plan[-1][1] + lines[n * MAX_LINES_PER_REGION:])
        return plan
    out, dropped = [], []
    i, j = n, m
    while i > 0 or j > 0:
        pj, k = back[i][j]
        if k == -1:
            dropped.append((i - 1, lines[pj]))  # -1: trước mọi vùng
            j = pj
            continue
        if k:
            out.append((regions[i - 1], lines[pj:j]))
        i, j = i - 1, pj
    out.reverse()
    out = _swap_pass(out, regions, ink, alpha)
    stack: dict[int, int] = {}
    for anchor, line in reversed(dropped):  # theo thứ tự đọc: đặt ngay sau bóng đứng trước câu đó
        k = stack[max(0, anchor)] = stack.get(max(0, anchor), -1) + 1  # xếp theo bóng mà khung bám vào
        out.insert(_after(out, regions, anchor), (_orphan(page, regions[max(0, anchor)], k), [line]))
    return out


def _after(plan, regions, anchor: int) -> int:
    """Vị trí trong plan ngay sau các vùng có thứ tự đọc ≤ anchor."""
    order = {id(r): i for i, r in enumerate(regions)}
    k = 0
    for idx, (r, _) in enumerate(plan):
        if order.get(id(r), -1) <= anchor:
            k = idx + 1
    return k


def _orphan(page, near, k: int = 0):
    """Câu không ghép được vào bóng nào: một khung riêng cạnh bóng đứng trước (câu thứ k xếp chồng xuống dưới),
    để người dùng kéo vào đúng chỗ."""
    from .page import Region

    w, h = page.size
    x1, y1, x2, y2 = near.box
    bw, bh = int(max(x2 - x1, .16 * w)), int(.07 * h)
    top = y2 + 8 + k * (bh + 8)
    if top + bh > h:
        top = max(0, y1 - (k + 1) * (bh + 8))
    left = max(0, min(w - bw, x1))
    return Region((left, top, left + bw, top + bh), kind="manual", cleaned=True, orphan=True)


SWAP_GAIN = 1.5  # chỉ đổi khi độ khớp độ dài tốt lên rõ rệt


def _swap_pass(plan, regions, ink, alpha):
    """Hai bóng liền nhau mà đổi chỗ câu thì độ dài câu khớp lượng chữ gốc hơn hẳn → đổi.

    Sửa trường hợp thứ tự đọc phụ thuộc nội dung (vd bóng "はい" nằm cao hơn câu hỏi trước nó).
    """
    idx = {id(r): i for i, r in enumerate(regions)}

    def fit(r, ls):
        got = sum(len(s.strip()) for s in ls)
        return math.log((got + 6) / (alpha * ink[idx[id(r)]] + 6)) ** 2

    words = [w.strip(".,!?…~\"'“”‘’()") for _, ls in plan for s in ls for w in s.split()]
    for k in range(len(plan) - 1):
        (ra, la), (rb, lb) = plan[k], plan[k + 1]
        if len(la) == 1 and len(lb) == 1 and ra.kind != "sfx" and rb.kind != "sfx":
            if fit(ra, la) + fit(rb, lb) - (fit(ra, lb) + fit(rb, la)) > SWAP_GAIN:
                plan[k], plan[k + 1] = (ra, lb), (rb, la)
        elif len(la) == 1 and len(lb) == 1 and (ra.kind == "sfx") != (rb.kind == "sfx"):
            # SFX nhận phải câu chỉ là tên người ("Nagi" khi gọi) mà bóng kề bên nhận câu kiểu SFX ("Giật"): thứ tự đọc
            # SFX / bóng trong panel phụ thuộc nội dung → đổi.
            sfx_line, talk_line = (la[0], lb[0]) if ra.kind == "sfx" else (lb[0], la[0])
            if _just_a_name(sfx_line, words) and not _just_a_name(talk_line, words):
                plan[k], plan[k + 1] = (ra, lb), (rb, la)
    return plan


def _just_a_name(line: str, words: list[str]) -> bool:
    """Câu chỉ gồm tên riêng (viết hoa, có ở câu khác trên trang, hoặc kèm -kun / -san…): lời gọi, không phải SFX."""
    parts = [w.strip(".,!?…~\"'“”‘’()") for w in line.split()]
    parts = [w for w in parts if w]
    if not parts or len(parts) > 2:
        return False
    for w in parts:
        base = w.split("-")[0]
        if not base[:1].isupper():
            return False
        if "-" not in w and sum(x == w for x in words) < 2:
            return False
    return True


def split_region(page, region, lines: list[str]):
    """Bóng mà người dịch tách thành nhiều câu: chia vùng chữ gốc thành từng cụm cột (chữ dọc) hoặc
    hàng (chữ ngang), mỗi câu một cụm, theo tỉ lệ độ dài câu. Trả về các vùng mới, hoặc None.
    Thử hướng theo dáng khung trước; không chia được thì thử hướng kia (bóng đôi nằm ngang chứa chữ dọc)."""
    x1, y1, x2, y2 = region.box
    tall = (y2 - y1) >= (x2 - x1)
    return _split_region(page, region, lines, tall) or _split_region(page, region, lines, not tall)


def _split_region(page, region, lines: list[str], vertical: bool):
    from dataclasses import replace

    k = len(lines)
    x1, y1, x2, y2 = region.box
    strokes = page.text_mask[y1:y2, x1:x2] > 0.3
    if k < 2 or not strokes.any():
        return None
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
        out.append(replace(region, box=box, text=line, text_rects=[], text_shape=None, manual_rect=None))
    return out


def apply_plan(page, plan, style=None) -> int:
    """Ghi câu dịch vào các vùng theo `plan`; vùng nhận nhiều câu thì tách thành từng khối nếu được.
    Trả về số vùng có chữ."""
    from .page import new_uid
    from .typeset import SOFT_BREAK
    from .typeset_profiles import profile_style

    n, prev = 0, None
    for r, ls in plan:
        if not any(x is r for x in page.regions):  # khung "chưa đặt": chèn ngay sau vùng đứng trước trong plan
            at = next((i + 1 for i, x in enumerate(page.regions) if x is prev), 0)
            page.regions.insert(at, r)
        prev = r
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
            reg.text = SOFT_BREAK.join(lines)  # một bóng nhiều câu: dàn thành một khối
            if style is not None:
                reg.style = profile_style(page, reg, style)
            n += 1
    return n
