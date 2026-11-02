"""Trạng thái một trang: ảnh gốc, ảnh đã xoá chữ và các vùng chữ."""
from __future__ import annotations

import itertools
import math
import os
from dataclasses import dataclass, field, replace

import cv2
import numpy as np
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage, QPainter

from .clean import MARGIN, clean_local, fill_bubble, is_small_sfx, lobe_shape, text_area
from .detect import TextDetector, sort_reading_order
from .typeset import TextStyle, best_rect, block_box, block_quality, draw_text, layout, place_in_shape
from .typeset_profiles import apply_profile, profile_style

Box = tuple[int, int, int, int]
AUTO_CAP_FRAC = 0.031
_region_ids = itertools.count(1)


def new_uid() -> int:
    return next(_region_ids)


def reserve_uids(upto: int) -> None:
    """Mở lại dự án: uid mới phải lớn hơn mọi uid đã lưu."""
    global _region_ids
    _region_ids = itertools.count(max(next(_region_ids), upto + 1))


MIN_READABLE = .5  # chữ ngoài bóng / SFX không nhỏ hơn nửa cỡ chuẩn của trang
REJOIN = .55  # hai câu chung bóng mà một phần chữ < 55% cỡ chuẩn: thử gộp lại thành một khối
OVERLAY_READABLE = .8  # chữ dịch đè lên chữ gốc (không xoá được): nới khung tới khi ≥ 80% cỡ chuẩn
OVERLAY_STROKE = .12  # …và viền trắng ~3px (12% cỡ chuẩn) để đọc được trên nét chữ gốc / tranh
DARK_BACKGROUND = 90  # độ sáng trung vị dưới mức này: nền tối, chữ trắng
LOBE_SIZE = 0.85  # bóng đôi: dàn theo thuỳ khi chữ ≥ 85% cỡ đặt được trong khung chữ nhật
EVEN_KINDS = {"normal", "thought"}  # thoại thường / suy nghĩ: cùng cỡ trên một trang (ô dẫn truyện, SFX, hét thì không)
EVEN_SLACK = 1.1  # …bóng nào cho chữ to hơn 1,1 lần cỡ chung của trang thì hạ xuống
EVEN_GROW = 1.15  # …câu ngắn ở bóng đã hạ cỡ: to thêm tối đa 15% (≈1,25 lần cỡ chung), không phải 1,5 lần
EVEN_MIN_REGIONS = 3  # …khi trang có từ 3 bóng thoại
BOX_KINDS = {"square"}  # ô dẫn truyện: cùng cỡ với nhau trên một trang…
BOX_OUTLIER = 0.7  # …theo ô chật nhất, bỏ qua ô chật bất thường (cỡ < 70% trung vị)
TIGHT_MIN_WORDS = 3  # chế độ chỗ chật chỉ cho câu dài hơn 3 từ
PART_GAIN = 1.15  # khung đã căn giữa thuỳ: chỉ đổi sang dáng phần bóng khi chữ to hơn rõ (> 15%)
LOBE_OFF_CENTER = 0.5  # khối chữ lệch khỏi tâm thuỳ quá nửa bán kính thuỳ: không còn là "căn giữa" (lệch 0,44 vẫn đẹp, 0,61 thì sát mép)
STAGGER = 0.3  # hai câu chung bóng là "so le" khi tâm chữ gốc lệch nhau ≥ 30% bề cao cột chữ
SHARED_GAP = 0.4  # hai câu chung một bóng: khối chữ cách nhau ≥ 0,4 cỡ chuẩn (bản type tay: 5–20px ở trang 840px)
SHARED_SIZE_TOL = 0.9  # …chọn cặp có dáng dòng đẹp nhất trong các cặp có cỡ chữ ≥ 90% cỡ to nhất…
SHARED_LANKY = 0.5  # …cộng phạt khối dài / bẹt (0 = vuông)
TRIM_OPTIONS = 6  # …chạm nhau thì thử cắt bớt 6 khung cho chữ to nhất của mỗi câu


@dataclass
class Region:
    box: Box
    text: str = ""
    style: TextStyle = field(default_factory=TextStyle)
    cleaned: bool = False
    text_rects: list[Box] = field(default_factory=list)  # các khung có thể đặt chữ (lòng bóng thoại)
    kind: str = "text"  # "text" | "sfx" | "manual" (vẽ tay) | "lasso"
    polygon: list[tuple[int, int]] | None = None  # đường khoanh lasso (toạ độ ảnh)
    auto_cap: float = 0.0  # cỡ chữ chuẩn của trang (do Page đặt)
    manual_rect: Box | None = None  # khung chữ người dùng tự kéo/căn chỉnh (ưu tiên hơn khung tự động)
    needs_redraw: bool = False  # nền khó, tool để nguyên chữ gốc cho người dùng tự redraw
    uid: int = field(default_factory=new_uid)  # định danh bền (layer chữ, lịch sử hoàn tác trỏ tới vùng qua uid)
    typeset_kind: str = "auto"  # automatic balloon shape, or a user-selected font category
    orphan: bool = False  # câu không ghép được vào bóng nào: khung tạm, người dùng kéo vào đúng chỗ
    tight: bool = False  # chung bóng với câu khác mà không tách được thuỳ: ưu tiên chữ to, dòng ngắn
    even: bool = False  # cỡ đã hạ về cỡ chung của trang (even_sizes)
    group: int = 0  # các câu tách từ cùng một bóng mang chung số này: cùng cỡ chữ
    marked: str = ""  # loại khung bản dịch đánh dấu (“ ” / ( ) / [ ] – bộ Ren AI Flag Eng)
    # Dáng lòng bóng (cx, cy, ((y, trái, phải), …)) để dàn chữ theo đúng hình bóng; None = chỉ dùng text_rects.
    text_shape: tuple | None = None

    @property
    def text_rect(self) -> Box:
        """Khung đặt chữ: khung người dùng tự chỉnh; không thì dàn theo dáng lòng bóng (chính giữa bóng, dòng giữa
        dài, trên/dưới ngắn); không được nữa thì khung chữ nhật cho cỡ chữ lớn nhất."""
        if self.manual_rect:
            return self.manual_rect
        if self.text_shape and self.text.strip():
            rect = place_in_shape(self.text, self.effective_style, self.text_shape)
            if rect:
                return rect
        return best_rect(self.text, self.effective_style, self.text_rects) if self.text_rects else self.box

    @property
    def effective_style(self) -> TextStyle:
        """Kiểu chữ dùng để dàn chữ: cỡ tự động thì giới hạn theo cỡ chuẩn của trang (nếu chưa đặt tay)."""
        style = self.style
        if self.tight and len(self.text.split()) > TIGHT_MIN_WORDS:
            # chỗ chật vì nhường chỗ cho câu kia: không thu cỡ thêm, chữ to dòng ngắn (câu 2–3 từ thì không cần)
            style = replace(style, fit_scale=1.0, tight=True)
        if self.even:  # đã hạ về cỡ chung của trang: câu ngắn chỉ được to hơn chút (bản type tay: "Nagi" 1,25 lần)
            style = replace(style, short_grow=EVEN_GROW)
        if self.needs_redraw and style.stroke_width == 0 and self.auto_cap:
            style = replace(style, stroke_width=round(OVERLAY_STROKE * self.auto_cap, 1), stroke_color="#ffffff")
        if style.size == 0 and style.max_size == 0 and self.auto_cap:
            return replace(style, max_size=self.auto_cap)
        return style

    @property
    def done(self) -> bool:
        return self.cleaned and bool(self.text.strip())


class Page:
    def __init__(self, path: str):
        self.path = path
        data = np.fromfile(path, np.uint8)  # đọc được cả đường dẫn có dấu tiếng Việt
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError(f"Không đọc được ảnh: {path}")
        self.original = img
        self.redrawn: np.ndarray | None = None  # bản đã redraw (xoá chữ) nạp từ ngoài, cùng cỡ ảnh gốc
        self.working = img.copy()
        # Điểm người dùng tự vẽ trên layer Redraw (cọ, clone…): thuộc layer Redraw dù trùng màu ảnh gốc.
        self.paint_mask = np.zeros(img.shape[:2], np.uint8)
        self.text_mask = np.zeros(img.shape[:2], np.float32)
        self.regions: list[Region] = []
        self.detected = False
        self.typeset_profile = ""
        self.custom_fonts: dict = {}  # bộ "tự chọn font": {loại khung: {"family", "style", "italic"}}
        self.inpainter = None  # hàm inpaint(img, mask) bằng AI cho nền có hoạ tiết; None = OpenCV

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.original.shape[:2]
        return w, h

    @property
    def base(self) -> np.ndarray:
        """Nền để xoá chữ / hoàn tác: bản redraw nạp từ ngoài nếu có, không thì ảnh raw."""
        return self.original if self.redrawn is None else self.redrawn

    def set_redrawn(self, path: str | None) -> None:
        """Dùng ảnh đã redraw sẵn (xoá chữ bằng Photoshop, AI…) làm nền; None = bỏ, quay về ảnh raw.

        Vị trí chữ vẫn nhận diện trên ảnh raw; nền là nguyên bản redraw, tool không xoá chữ thêm (redrawn_covers).
        Các vùng đã xử lý được dàn lại trên nền mới (giữ câu dịch).
        """
        if path is None:
            self.redrawn = None
        else:
            img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError(f"Không đọc được ảnh: {path}")
            w, h = self.size
            rh, rw = img.shape[:2]
            if abs(np.log((rw / rh) / (w / h))) > 0.02:
                raise ValueError(f"{os.path.basename(path)} ({rw}x{rh}) khác tỉ lệ ảnh raw ({w}x{h}): "
                                 "bản redraw phải cùng khung với ảnh gốc.")
            if (rw, rh) != (w, h):
                img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA if rw > w else cv2.INTER_CUBIC)
            self.redrawn = img
        self.working = self.base.copy()
        self.paint_mask[:] = 0
        for r in self.regions:
            r.cleaned = r.needs_redraw = False
            r.text_rects = []
            r.text_shape = None

    def redrawn_covers(self, region: Region) -> bool:
        """Có bản redraw thì dùng nguyên bản redraw làm nền: tool không xoá chữ, không đặt chữ đè lên chữ gốc (chỗ nào
        nhóm redraw để lại chữ là ý của họ). Ảnh raw chỉ để biết chỗ đặt chữ và ghép câu dịch."""
        return self.redrawn is not None

    def detect(self, detector: TextDetector, right_to_left: bool = True) -> None:
        boxes, mask = detector.detect(self.original)
        self.text_mask = np.maximum(mask, color_text_mask(self.original))
        boxes = sort_reading_order(boxes, right_to_left, self.original)
        self.regions = [r for r in self.regions if r.cleaned]
        self.regions += [Region((b.x1, b.y1, b.x2, b.y2), kind=b.kind) for b in boxes]
        apply_profile(self)
        self.detected = True

    def add_region(self, box: Box) -> Region:
        x1, y1, x2, y2 = box
        w, h = self.size
        box = (max(0, min(x1, x2)), max(0, min(y1, y2)), min(w, max(x1, x2)), min(h, max(y1, y2)))
        r = Region(box, kind="manual")
        r.style = profile_style(self, r, r.style)
        self.regions.append(r)
        return r

    def add_lasso(self, points: list[tuple[int, int]]) -> Region:
        w, h = self.size
        pts = [(int(np.clip(x, 0, w - 1)), int(np.clip(y, 0, h - 1))) for x, y in points]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        r = Region((min(xs), min(ys), max(xs) + 1, max(ys) + 1), kind="lasso", polygon=pts)
        self.regions.append(r)
        return r

    def area_mask(self, region: Region) -> np.ndarray | None:
        if not region.polygon:
            return None
        w, h = self.size
        m = np.zeros((h, w), np.uint8)
        cv2.fillPoly(m, [np.array(region.polygon, np.int32)], 255)
        return m

    def _clean_local(self, region: Region):
        """Xoá chữ phần làm được trên máy; trả về mask còn cần AI vẽ lại (None nếu xong / để lại)."""
        if self.redrawn_covers(region):
            return None
        skip = getattr(self.inpainter, "skip", False)
        if skip and not region.polygon and not self.in_balloon(region):
            # Chỉ xoá chữ trong bóng thoại / ô khép kín: SFX, chữ đè lên tranh (dù nền trắng) để nguyên, đặt chữ đè lên.
            region.needs_redraw = True
            return None
        need = clean_local(self.working, self.text_mask, region.box, self.area_mask(region), self.inpainter,
                           whole_box=region.kind == "text")
        if need is not None and (skip(self, region) if callable(skip) else skip):
            region.needs_redraw = True
            return None
        return need

    def in_balloon(self, region: Region) -> bool:
        """Không phải SFX; chữ có nằm trong lòng bóng một màu hay không thì clean_local kiểm tra sát quanh nét chữ."""
        return region.kind != "sfx"

    def auto_clean(self, small_sfx: bool = True, fit_bubble: bool = True) -> int:
        """Xoá ngay những chỗ chắc ăn, chưa cần câu dịch: chữ trong bóng thoại / hộp dẫn truyện sáng khép kín,
        chỗ bản redraw đã xoá, và (nếu `small_sfx`) SFX nhỏ bằng `self.inpainter`. Chữ đè lên tranh, SFX lớn,
        chữ ngoài bóng (tên tác giả…) để nguyên. Trả về số vùng đã xoá."""
        n = 0
        for r in self.regions:
            if r.cleaned or r.polygon:
                continue
            if r.kind == "sfx" and not small_sfx:
                continue  # detector false positives can be hearts, hair or other drawing details
            if self.redrawn_covers(r) or (self.in_balloon(r) and fill_bubble(self.working, self.text_mask, r.box)):
                pass
            elif small_sfx and is_small_sfx(r, self.size):
                need = clean_local(self.working, self.text_mask, r.box, None, self.inpainter)
                if need is not None:
                    self.inpainter(self.working, need)
            else:
                continue
            r.cleaned = True
            self.clean(r, fit_bubble)  # đã xoá: chỉ tính khung đặt chữ
            n += 1
        return n

    def _clean_pixels(self, region: Region) -> None:
        need = self._clean_local(region)
        if need is not None:
            self.inpainter(self.working, need)

    def remove_region(self, region: Region) -> None:
        self.reset(region)
        self.regions.remove(region)

    @property
    def auto_cap(self) -> float:
        """Cỡ chữ chuẩn khi tự co: ~3,1% bề ngang trang (đo từ chương typeset tay: 836px → 26px)."""
        return round(self.size[0] * AUTO_CAP_FRAC)

    def clean(self, region: Region, fit_bubble: bool = True) -> None:
        region.style = profile_style(self, region, region.style)
        region.auto_cap = self.auto_cap
        if region.orphan:  # khung tạm trên tranh: không xoá gì, đặt chữ ngay trong khung
            region.cleaned, region.text_rects, region.text_shape = True, [region.box], None
            return
        if not region.cleaned:
            self._clean_pixels(region)
            region.cleaned = True
        region.text_shape, region.tight = None, False
        if region.kind == "sfx":
            # Dịch SFX đặt ngay tại chỗ SFX gốc (không loang ra nền trắng của panel).
            x1, y1, x2, y2 = region.box
            ex, ey = (x2 - x1) // 5, (y2 - y1) // 5
            w, h = self.size
            region.text_rects = [(max(0, x1 - ex), max(0, y1 - ey), min(w, x2 + ex), min(h, y2 + ey))]
        elif fit_bubble:
            cands, shape = text_area(self.working, region.box, self.area_mask(region))
            # Khung đặt chữ không được đè lên chữ gốc của vùng khác (vd hai câu chung một bóng).
            others = [o.box for o in self.regions if o is not region]

            def hits(c):
                return any(c[0] < o[2] - 2 and o[0] < c[2] - 2 and c[1] < o[3] - 2 and o[1] < c[3] - 2 for o in others)

            free = [c for c in cands if not hits(c)]
            region.text_rects = free or [region.box]
            if shape:
                span = (min(r[1] for r in shape[2]), shape[2][0][0], max(r[2] for r in shape[2]), shape[2][-1][0] + 1)
                if not hits(span):
                    region.text_shape = shape
                else:  # nhiều câu chung một bóng / khung: tách theo chỗ thắt của bóng đôi, hoặc chia dải trên-dưới
                    region.text_shape = self._shared_shape(region, shape, span)
        else:
            region.text_rects = [region.box]
        self._readable(region)
        self._separate(region)
        self._contrast(region)

    def _contrast(self, region: Region) -> None:
        """Chữ đặt trên nền tối (biển / băng rôn đen, ô đen) mà vẫn màu đen mặc định thì đổi sang trắng."""
        if region.style.color.lower() != "#000000" or not region.text.strip():
            return
        x1, y1, x2, y2 = map(int, region.text_rect)
        patch = self.working[max(0, y1):y2, max(0, x1):x2]
        if patch.size and float(np.median(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY))) < DARK_BACKGROUND:
            region.style = replace(region.style, color="#ffffff")

    def _separate(self, region: Region) -> None:
        """Hai câu chung một bóng: khối chữ không được chồng / sát nhau. Chạm thì chọn lại khung cho cả hai cùng lúc,
        cỡ chữ hai bên càng đều càng tốt (bản type tay: khối trước ở thuỳ trên phải, khối sau thuỳ dưới trái)."""
        if region.kind == "sfx" or region.orphan or region.manual_rect or not region.text.strip():
            return
        gap = SHARED_GAP * region.auto_cap

        def apart(a, b):
            """Không dòng nào của khối này sát dòng nào của khối kia (khung bao chồng góc vẫn được: dòng đầu/cuối
            ngắn, như bản type tay)."""
            return not any(p[0] < q[2] + gap and q[0] < p[2] + gap and p[1] < q[3] + gap and q[1] < p[3] + gap
                           for p in a[5] for q in b[5])

        def options(r):
            """(khung, có giữ dáng bóng không, khung khối chữ) – khung đang dùng đứng đầu."""
            current = r.text_rect
            if r.manual_rect:
                return [(current, True, block_box(r.text, r.effective_style, current))]
            rects = [current] + [c for c in r.text_rects if c != current]
            return [(c, bool(r.text_shape) and i == 0, block_box(r.text, r.effective_style, c))
                    for i, c in enumerate(rects)]

        for other in self.regions:
            if (other is region or other.kind == "sfx" or other.orphan or not other.cleaned
                    or not other.text.strip() or not (other.text_rects or other.text_shape or other.manual_rect)):
                continue
            mine, theirs = options(region), options(other)
            if apart(mine[0][2], theirs[0][2]):
                continue
            pairs = [(a, b) for a in mine for b in theirs if apart(a[2], b[2])]
            # Khung to bị khối kia chiếm một góc: cắt bỏ phần đó (khối trước nhích lên, khối sau nhích xuống).
            big = lambda opts: sorted(opts, key=lambda o: -o[2][4])[:TRIM_OPTIONS]  # noqa: E731
            for a in big(mine):
                for b in big(theirs):
                    if apart(a[2], b[2]):
                        continue
                    for r, keep, fixed, flip in ((region, a, b, False), (other, b, a, True)):
                        if keep[1] and (r.manual_rect or r.text_shape):
                            continue
                        for cut in _cuts(keep[0], fixed[2], gap):
                            o = (cut, False, block_box(r.text, r.effective_style, cut))
                            if apart(o[2], fixed[2]):
                                pairs.append((fixed, o) if flip else (o, fixed))
            if not pairs:
                continue
            # Cỡ chữ hai bên đều và to nhất; gần bằng nhau (≤ 10%) thì chọn cặp có dáng dòng đẹp (không thụt thò, không
            # vụn mỗi dòng một chữ) và khối gọn, tránh cột dài.
            top = max(min(a[2][4], b[2][4]) for a, b in pairs)
            pairs = [p for p in pairs if min(p[0][2][4], p[1][2][4]) >= SHARED_SIZE_TOL * top]
            looks = {}

            def look(r, o):
                key = (r.uid, o[0])
                if key not in looks:
                    looks[key] = block_quality(r.text, r.effective_style, o[0]) + SHARED_LANKY * _lanky(o[2])
                return looks[key]

            a, b = min(pairs, key=lambda p: (look(region, p[0]) + look(other, p[1]), -p[0][2][4] - p[1][2][4]))
            for r, (rect, keep, _) in ((region, a), (other, b)):
                if not keep:
                    r.text_shape, r.text_rects = None, [rect]

    def _shared_shape(self, region: Region, shape, span):
        """Dáng đặt chữ khi bóng chứa cả chữ của câu khác: bóng đôi thì lấy thuỳ của câu này (tách tại chỗ thắt),
        không thì dải ngang theo thứ tự đọc; None nếu khung chữ nhật cho chữ to hơn."""
        region.tight = True
        style = region.effective_style

        def size(rect):
            return layout(region.text, style, rect[2] - rect[0], rect[3] - rect[1])[0].size if rect else 0

        boxed = size(best_rect(region.text, style, region.text_rects))
        # Chữ gốc của các câu thoại khác (SFX vắt qua viền bóng, khung tạm trên tranh không tính).
        others = [o.box for o in self.regions if o is not region and o.kind != "sfx" and not o.orphan]
        lobe = lobe_shape(self.working, region.box, others, self.area_mask(region))
        if lobe:
            # Thuỳ không được chứa chữ gốc của câu khác (hai câu cùng thuỳ).
            spans = {y: (left, right) for y, left, right in lobe[2]}
            mine = not any(left <= (o[0] + o[2]) / 2 <= right for o in others
                           for y in [int((o[1] + o[3]) / 2)] if y in spans for left, right in [spans[y]])
            if mine and size(place_in_shape(region.text, style, lobe)) >= LOBE_SIZE * boxed:
                region.tight = False  # thuỳ riêng: như một bóng thường
                return lobe  # chữ dàn theo dáng thuỳ, giữa thuỳ: đẹp hơn khung chữ nhật dù nhỏ hơn chút
            return None  # bóng đôi: dải ngang sẽ cắt qua thuỳ của câu khác, dùng khung chữ nhật
        # Bóng liền khối hai câu dài so le: mỗi câu căn giữa phần bóng gần chữ gốc của nó, theo dáng phần đó (như
        # người type: khối chữ là bản thu nhỏ của phần bóng, lề đều). Có câu chen ngắn ("Uầy~") thì thôi.
        mates = [o for o in self.regions if o is not region and o.kind != "sfx" and not o.orphan and o.text.strip()
                 and _inside(shape, o.box)]
        if len(region.text.split()) > TIGHT_MIN_WORDS and mates and all(
                len(o.text.split()) > TIGHT_MIN_WORDS for o in mates):
            part = _shared_part(shape, region.box, [o.box for o in mates], SHARED_GAP * region.auto_cap)
            if part:
                # Khung chữ nhật cho chữ to hơn và khối chữ vẫn nằm giữa thuỳ: giữ khung (chữ to). Khung làm khối lệch
                # khỏi giữa thuỳ (sát mép bóng): dàn theo dáng phần bóng cho khối căn giữa, chịu nhỏ hơn chút.
                lx, ly, radius = _lobe_center(part[2])
                b = block_box(region.text, style, best_rect(region.text, style, region.text_rects))
                off = math.hypot((b[0] + b[2]) / 2 - lx, (b[1] + b[3]) / 2 - ly) / max(radius, 1)
                placed = size(place_in_shape(region.text, style, part))
                if placed > PART_GAIN * boxed or (off > LOBE_OFF_CENTER and placed >= LOBE_SIZE * boxed):
                    return part
        band = self._shared_band(region, shape, span)
        # Hai bóng dính nhau nằm ngang thì từng khung riêng vẫn tốt hơn: chỉ dùng dải khi chữ to hơn rõ.
        return band if band and size(place_in_shape(region.text, style, band)) > 1.1 * boxed else None

    def _shared_band(self, region: Region, shape, span):
        """Phần của `region` khi bóng chứa chữ của vùng khác: mọi vùng trong bóng đều có câu dịch (chữ gốc sẽ
        bị xoá) thì chia bóng thành các dải ngang theo thứ tự đọc, cao theo độ dài câu; không thì None."""
        x1, y1, x2, y2 = span
        group = [o for o in self.regions if o is region or
                 (x1 <= (o.box[0] + o.box[2]) / 2 <= x2 and y1 <= (o.box[1] + o.box[3]) / 2 <= y2)]
        if len(group) < 2 or any(o.kind == "sfx" or not o.text.strip() or o.needs_redraw for o in group):
            return None
        cx, _, rows = shape
        weights = [len(o.text.strip()) + 6 for o in group]
        k = group.index(region)
        start, end = sum(weights[:k]) / sum(weights), sum(weights[:k + 1]) / sum(weights)
        gap = .03 * len(rows)
        band = rows[int(start * len(rows) + (gap if k else 0)):int(end * len(rows) - (gap if k < len(group) - 1 else 0))]
        if len(band) < 3:
            return None
        return cx, (band[0][0] + band[-1][0]) / 2, tuple(band)

    def _readable(self, region: Region) -> None:
        """Chữ ngoài bóng / SFX nhỏ hơn MIN_READABLE cỡ chuẩn của trang: nới khung đặt chữ ra xung quanh
        (không lấn sang khung của câu khác) cho tới khi đọc được."""
        cap = region.auto_cap
        if not cap or region.manual_rect or not region.text.strip():
            return
        style = region.effective_style

        def size(rect):
            return layout(region.text, style, rect[2] - rect[0], rect[3] - rect[1])[0].size

        floor = (OVERLAY_READABLE if region.needs_redraw else MIN_READABLE) * cap
        x1, y1, x2, y2 = region.text_rect
        if size((x1, y1, x2, y2)) >= floor:
            return
        if region.text_shape:
            # Lòng bóng lấy được quá nhỏ (bóng trong suốt bị nét tóc / nét vẽ cắt ngang): bỏ dáng bóng, nới khung
            # quanh chữ gốc như chữ ngoài bóng – thà chữ đè nét vẽ mờ còn hơn chữ bé không đọc được.
            region.text_shape = None
            region.text_rects = [region.box]
            x1, y1, x2, y2 = region.box
        w, h = self.size
        cx, cy, bw, bh = (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1
        others = [o.box for o in self.regions if o is not region and o.text.strip() and not o.orphan]
        best = None
        for f in (1.3, 1.6, 2.0, 2.5, 3.0, 3.6):
            for fw, fh in ((f, 1), (1, f), (f, f)):
                nw, nh = max(bw * fw, bh * fh * .6), bh * fh
                c = (int(max(0, cx - nw / 2)), int(max(0, cy - nh / 2)), int(min(w, cx + nw / 2)), int(min(h, cy + nh / 2)))
                if any(c[0] < o[2] and o[0] < c[2] and c[1] < o[3] and o[1] < c[3] for o in others):
                    continue
                got = size(c)
                if best is None or got > best[0]:
                    best = (got, c)
                if got >= floor:
                    region.text_rects = [c]
                    return
        if best:
            region.text_rects = [best[1]]

    def clean_many(self, regions: list[Region], fit_bubble: bool = True) -> None:
        """Xoá chữ nhiều vùng; phần cần AI trên mạng của các vùng gần nhau được gom vào một lần gọi."""
        pending: list[tuple[list[Region], np.ndarray]] = []
        for r in regions:
            if r.cleaned:
                continue
            need = self._clean_local(r)
            if need is None:
                r.cleaned = True
            else:
                pending.append(([r], need))
        ctx = getattr(self.inpainter, "context_box", None)
        if ctx is not None:  # gộp các vùng có vùng gửi đi chồng lên nhau
            merged = True
            while merged:
                merged = False
                for i in range(len(pending)):
                    for j in range(i + 1, len(pending)):
                        a, b = ctx(pending[i][1]), ctx(pending[j][1])
                        if a and b and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                            pending[i] = (pending[i][0] + pending[j][0], np.maximum(pending[i][1], pending[j][1]))
                            del pending[j]
                            merged = True
                            break
                    if merged:
                        break
        for group, mask in pending:
            self.inpainter(self.working, mask)
            for r in group:
                r.cleaned = True
        for r in regions:
            if r.cleaned:
                self.clean(r, fit_bubble)  # đã xoá rồi: chỉ tính lại khung đặt chữ
        self._rejoin_cramped(fit_bubble)
        self.even_sizes()

    def _rejoin_cramped(self, fit_bubble: bool = True) -> None:
        """Hai câu chung một bóng đã tách thành hai phần mà có phần chật bất thường (chữ < REJOIN cỡ chuẩn, Ren AI chap 2
        trang 7: 13px khi cỡ chuẩn 45px): gộp lại thành một khối chữ trong cả bóng nếu nhờ đó chữ to hơn."""
        from .typeset import SOFT_BREAK

        groups: dict[int, list[Region]] = {}
        for r in self.regions:
            if r.group and r.cleaned and r.text.strip() and not r.manual_rect:
                groups.setdefault(r.group, []).append(r)
        for parts in groups.values():
            if len(parts) < 2:
                continue
            cap = self.auto_cap
            small = min(self._fit_size(r) for r in parts)
            if small >= REJOIN * cap:
                continue
            x1, y1 = min(r.box[0] for r in parts), min(r.box[1] for r in parts)
            x2, y2 = max(r.box[2] for r in parts), max(r.box[3] for r in parts)
            whole = Region((x1, y1, x2, y2), text=SOFT_BREAK.join(r.text for r in parts), style=parts[0].style,
                           kind=parts[0].kind, cleaned=True, marked=parts[0].marked)
            at = self.regions.index(parts[0])
            others = [r for r in self.regions if r not in parts]
            self.regions[:] = others[:at] + [whole] + others[at:]
            self.clean(whole, fit_bubble)
            if self._fit_size(whole) <= small:  # gộp cũng không hơn: giữ hai phần
                self.regions[:] = others[:at] + parts + others[at:]

    def even_sizes(self) -> None:
        """Như người type (bản type tay, nhóm NaJung): thoại thường trên một trang cùng một cỡ chữ – bóng to thì để
        lề rộng chứ không phóng chữ to theo bóng. Cỡ chung = trung vị cỡ vừa khít của các bóng thoại; bóng nào cho
        chữ to hơn EVEN_SLACK lần cỡ đó thì hạ xuống, bóng nhỏ giữ cỡ của nó (câu rất ngắn vẫn được to hơn chút).
        Ô dẫn truyện trên trang cũng cùng một cỡ (nhóm riêng): theo ô chật nhất, trừ ô chật bất thường."""
        from .typeset_profiles import region_kind
        groups: dict[str, list[Region]] = {}
        for r in self.regions:
            if (r.cleaned and r.text.strip() and not r.orphan and not r.manual_rect and r.style.size == 0
                    and r.style.max_size == 0):
                kind = region_kind(self, r)
                if kind in EVEN_KINDS:
                    groups.setdefault("talk", []).append(r)
                elif kind in BOX_KINDS:
                    groups.setdefault("box", []).append(r)
        for group, regions in groups.items():
            for r in regions:
                r.auto_cap, r.even = self.auto_cap, False
            fits = [self._fit_size(r) for r in regions]
            if group == "talk":
                if len(fits) < EVEN_MIN_REGIONS:
                    continue
                cap = float(np.median(fits)) * EVEN_SLACK
            else:
                if len(fits) < 2:
                    continue
                usual = [f for f in fits if f >= BOX_OUTLIER * float(np.median(fits))]
                cap = min(usual) * EVEN_SLACK
            for r, fit in zip(regions, fits):
                if fit > cap:
                    r.auto_cap, r.even = cap, True
        # Hai câu chung một bóng: cùng cỡ (chap 23 trang 23: 17px cạnh 21px trông như hai bóng khác nhau).
        shared: dict[int, list[Region]] = {}
        for r in (r for rs in groups.values() for r in rs):
            if r.group:
                shared.setdefault(r.group, []).append(r)
        for parts in shared.values():
            if len(parts) < 2:
                continue
            fits = [self._fit_size(r) for r in parts]
            cap = min(fits) * EVEN_SLACK
            for r, fit in zip(parts, fits):
                if fit > cap:
                    r.auto_cap, r.even = min(r.auto_cap, cap), True

    def _fit_size(self, r: Region) -> int:
        x1, y1, x2, y2 = r.text_rect
        return layout(r.text, r.effective_style, x2 - x1, y2 - y1)[0].size

    def reset(self, region: Region) -> None:
        """Hoàn tác vùng: khôi phục ảnh gốc và bỏ chữ."""
        if region.cleaned:
            x1, y1, x2, y2 = region.box
            w, h = self.size
            x1, y1, x2, y2 = max(0, x1 - MARGIN), max(0, y1 - MARGIN), min(w, x2 + MARGIN), min(h, y2 + MARGIN)
            self.working[y1:y2, x1:x2] = self.base[y1:y2, x1:x2]
            # Vẽ lại các vùng khác đã xoá mà chồng lên phần vừa khôi phục.
            for other in self.regions:
                if other is not region and other.cleaned and _overlap(other.box, (x1, y1, x2, y2)):
                    self._clean_pixels(other)
        region.cleaned = region.needs_redraw = False
        region.text = ""
        region.text_rects = []
        region.text_shape = None
        region.manual_rect = None

    def render(self) -> QImage:
        rgb = cv2.cvtColor(self.working, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        p = QPainter(img)
        for r in self.regions:
            if r.cleaned and r.text.strip():
                x1, y1, x2, y2 = r.text_rect
                draw_text(p, r.text, r.effective_style, QRectF(x1, y1, x2 - x1, y2 - y1))
        p.end()
        return img

    def export(self, path: str) -> None:
        if not self.render().save(path, None, 95):  # JPEG chất lượng 95
            raise IOError(f"Không lưu được: {path}")


def _inside(shape, box) -> bool:
    """Tâm `box` nằm trong lòng bóng `shape`."""
    spans = {y: (left, right) for y, left, right in shape[2]}
    y = int((box[1] + box[3]) / 2)
    return y in spans and spans[y][0] <= (box[0] + box[2]) / 2 <= spans[y][1]


def _box_distance(x, y, box):
    """Khoảng cách từ các điểm (x mảng, y) tới hình chữ nhật `box` (0 nếu nằm trong)."""
    x1, y1, x2, y2 = box
    dx = np.maximum(np.maximum(x1 - x, 0), x - x2)
    dy = max(y1 - y, 0, y - y2)
    return np.hypot(dx, dy)


def _shared_part(shape, box, others, gap):
    """Bóng liền khối (không có chỗ thắt) chứa chữ của nhiều câu, xếp so le như bóng mây hai câu: phần bóng gần chữ
    gốc của câu này hơn chữ gốc của các câu khác (cách đường chia `gap`), dạng (cx, cy, hàng) như lòng bóng.
    None nếu bóng không có câu nào khác hoặc phần còn lại quá nhỏ."""
    rows = shape[2]
    rivals = [o for o in others if _inside(shape, o)]
    # Chỉ bóng hai câu so le (một cao một thấp); hai cột chữ ngang hàng thì xếp chồng trên-dưới (dải ngang).
    tall = box[3] - box[1]
    if not rivals or any(abs((o[1] + o[3]) - (box[1] + box[3])) / 2 < STAGGER * max(tall, o[3] - o[1]) for o in rivals):
        return None
    part = []
    for y, left, right in rows:
        xs = np.arange(left, right)
        mine = _box_distance(xs, y, box) + gap <= np.min([_box_distance(xs, y, o) for o in rivals], axis=0)
        if not mine.any():
            continue
        # đoạn liền dài nhất của hàng thuộc phần này
        edges = np.flatnonzero(np.diff(np.concatenate(([0], mine.astype(np.int8), [0]))))
        starts, ends = edges[::2], edges[1::2]
        k = int(np.argmax(ends - starts))
        part.append((y, int(left + starts[k]), int(left + ends[k])))
    if len(part) < 10:
        return None
    ys = [y for y, _, _ in part]
    if max(ys) - min(ys) + 1 != len(part):  # phần bị đứt quãng theo chiều dọc: lấy khúc liền chứa nhiều hàng nhất
        runs, cur = [], [part[0]]
        for row in part[1:]:
            if row[0] == cur[-1][0] + 1:
                cur.append(row)
            else:
                runs.append(cur)
                cur = [row]
        part = max(runs + [cur], key=len)
    cx = float(np.median([(a + b) / 2 for _, a, b in part]))
    cy = (part[0][0] + part[-1][0]) / 2
    return cx, cy, tuple(part)


def _lobe_center(rows):
    """Tâm thuỳ của một phần bóng: điểm nằm sâu nhất (xa viền nhất) và bán kính hình tròn lớn nhất đặt vừa ở đó –
    giữa thuỳ tròn, không bị dải thừa (đuôi, chỗ nối sang thuỳ kia) kéo lệch như khi lấy giữa các hàng."""
    y0 = rows[0][0]
    x0 = min(a for _, a, _ in rows)
    mask = np.zeros((rows[-1][0] - y0 + 3, max(b for _, _, b in rows) - x0 + 2), np.uint8)
    for y, a, b in rows:
        mask[y - y0 + 1, a - x0 + 1:b - x0 + 1] = 255
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    py, px = np.unravel_index(int(np.argmax(dist)), dist.shape)
    return float(px + x0 - 1), float(py + y0 - 1), float(dist[py, px])


def _lanky(block) -> float:
    """Khối chữ dài / bẹt cỡ nào: 0 = vuông."""
    return abs(math.log(max(block[3] - block[1], 1) / max(block[2] - block[0], 1)))


def _cuts(rect, block, gap):
    """Phần của `rect` nằm hẳn một phía (trái / phải / trên / dưới) của khối chữ `block`, cách `gap`."""
    x1, y1, x2, y2 = rect
    bx1, by1, bx2, by2 = (int(v) for v in block[:4])
    out = [(x1, y1, min(x2, bx1 - int(gap)), y2), (max(x1, bx2 + int(gap)), y1, x2, y2),
           (x1, y1, x2, min(y2, by1 - int(gap))), (x1, max(y1, by2 + int(gap)), x2, y2)]
    return [c for c in out if c[2] - c[0] > 20 and c[3] - c[1] > 20 and c != rect]


def color_text_mask(img: np.ndarray) -> np.ndarray:
    """Truyện đen trắng: điểm có màu (chữ tên truyện hồng/xanh, hoạ tiết trang trí) coi như chữ.

    Truyện màu thì bỏ qua, không thì cả tranh trong khung chữ cũng bị xoá.
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    colored = (hsv[..., 1] > 50) & (hsv[..., 2] > 60)
    if colored.mean() > 0.05:
        return np.zeros(img.shape[:2], np.float32)
    return colored.astype(np.float32)


def _overlap(a: Box, b: Box) -> bool:
    m = 2 * MARGIN
    return a[0] - m < b[2] and b[0] < a[2] + m and a[1] - m < b[3] and b[1] < a[3] + m
