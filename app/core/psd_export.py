"""Xuất trang đã type ra file PSD có lớp.

- Có Photoshop: điều khiển Photoshop (ExtendScript qua COM) để tạo lớp chữ THẬT, sửa được:
  Background = ảnh raw, lớp "Redraw" = phần đã xoá/vẽ lại nền (hoặc bản redraw đã upload), mỗi bóng một lớp chữ.
- Không có Photoshop: ghi PSD bằng psd-tools, chữ là lớp ảnh (không sửa được nội dung chữ).
"""
from __future__ import annotations

import json
import os
import tempfile

import cv2
import numpy as np

from functools import lru_cache

from .fonts import files_for, postscript_name
from .typeset import SOFT_BREAK, text_block

# Font ký hiệu có sẵn trên Windows: ký tự font chính không có (♡ ♪ — …) mượn font này, Photoshop không tự mượn.
SYMBOL_FONTS = ("Segoe UI Symbol", "MS Gothic")

JUSTIFY = {("left", ""): "LEFT", ("center", ""): "CENTER", ("right", ""): "RIGHT",
           "left": "LEFTJUSTIFIED", "center": "CENTERJUSTIFIED", "right": "RIGHTJUSTIFIED", "all": "FULLYJUSTIFIED"}
ANTIALIAS = {"none": "NONE", "sharp": "SHARP", "crisp": "CRISP", "strong": "STRONG", "smooth": "SMOOTH"}


def clean_layer(page, against: np.ndarray | None = None) -> np.ndarray:
    """Ảnh BGRA của layer Redraw: những điểm đã xoá/vẽ lại (khác ảnh gốc) hoặc người dùng tự vẽ, còn lại trong suốt.
    `against`: so với ảnh này thay cho ảnh gốc (vd bản redraw có sẵn trong tài liệu Photoshop: chỉ lấy phần tool vẽ thêm)."""
    diff = np.abs(page.working.astype(np.int16) - (page.original if against is None else against)).max(axis=2) > 2
    diff = (cv2.dilate(diff.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) | (page.paint_mask > 0)
    out = cv2.cvtColor(page.working, cv2.COLOR_BGR2BGRA)
    out[..., 3] = np.where(diff, 255, 0).astype(np.uint8)
    return out


def layer_specs(page) -> list[dict]:
    """Thông số từng lớp chữ (toạ độ px của ảnh)."""
    return [text_spec(r) for r in page.regions if r.cleaned and r.text.strip()]


KERNING = {"optical": "OPTICAL", "metrics": "METRICSKERN", "none": "MANUAL"}  # AutoKernType của Photoshop
BLEND_PS = {"normal": "NORMAL", "multiply": "MULTIPLY", "screen": "SCREEN", "darken": "DARKEN", "lighten": "LIGHTEN"}


def doc_layers(doc, tmp: str) -> list[dict]:
    """Các layer trên Background theo đúng thứ tự (dưới → trên): ảnh (ghi ra PNG trong `tmp`) và chữ."""
    out = []
    for i, L in enumerate(doc.layers[1:]):
        base = {"name": L.name, "visible": L.visible, "opacity": round(L.opacity * 100), "blend": BLEND_PS[L.blend]}
        if L.kind == "text":
            r = doc.region_of(L)
            if r is None or not r.text.strip():
                continue
            out.append({**text_spec(r), **base, "type": "text", "custom_name": L.custom_name})
            continue
        bgra = clean_layer(doc.page) if L.kind == "redraw" else L.rgba
        if not bgra[..., 3].any():  # layer trống (vd Redraw chưa vẽ gì): tạo layer rỗng để vẽ tiếp trong Photoshop
            out.append({**base, "type": "empty"})
            continue
        png = os.path.join(tmp, f"layer{i}.png")
        cv2.imencode(".png", bgra)[1].tofile(png)
        out.append({**base, "type": "pixel", "png": png.replace("\\", "/")})
    return out


def fallback_runs(style, contents: str) -> tuple[list[list[int]], str]:
    """([[từ, tới), …] các ký tự font chính không có mà font ký hiệu có, tên PostScript font ký hiệu) – vd ♡ trong
    câu dịch tiếng Anh: không font nào của bộ Ren AI Flag Eng có ♡ / ♪."""
    have = _charset(style.family)
    if have is None:
        return [], ""
    missing = {ch for ch in contents if ch.isprintable() and not ch.isspace() and ord(ch) not in have}
    if not missing:
        return [], ""
    for family in SYMBOL_FONTS:
        symbols = _charset(family)
        usable = {ch for ch in missing if symbols and ord(ch) in symbols}
        if not usable:
            continue
        runs = []
        for i, ch in enumerate(contents):
            if ch in usable:
                if runs and runs[-1][1] == i:
                    runs[-1][1] = i + 1
                else:
                    runs.append([i, i + 1])
        return runs, postscript_name(family)
    return [], ""


@lru_cache(maxsize=64)
def _charset(family: str) -> frozenset | None:
    """Các ký tự font có (bảng cmap trong file font; Qt báo nhầm là có ♡ với MTO Dom). None nếu không đọc được."""
    from fontTools.ttLib import TTFont

    for path in files_for(family):
        try:
            return frozenset(TTFont(path, fontNumber=0, lazy=True).getBestCmap() or ())
        except Exception:  # noqa: BLE001 - file font lạ: bỏ qua, không mượn font
            continue
    return None


def text_spec(r) -> dict:
    """Thông số lớp chữ của một vùng (toạ độ px của ảnh)."""
    st = r.effective_style
    x1, y1, x2, y2 = r.text_rect
    b = text_block(r.text, st, (x1, y1, x2, y2))
    just = JUSTIFY[st.justify] if st.justify else JUSTIFY[(st.align, "")]
    runs, symbol_font = fallback_runs(st, b["contents"])
    return {
        "fallback": runs, "fallback_font": symbol_font,
        "contents": b["contents"], "font": postscript_name(st.family, st.style_name), "size": b["size"],
        "leading": b["leading"], "auto_leading": st.leading <= 0, "left": b["left"], "width": b["width"],
        "first_baseline": b["first_baseline"], "ascent": b["ascent"], "block_height": b["block_height"],
        "justification": just, "color": st.color.lstrip("#"), "tracking": st.tracking,
        "hscale": st.hscale, "vscale": st.vscale, "baseline_shift": st.baseline_shift,
        "faux_bold": st.bold, "faux_italic": st.italic,
        "caps": "ALLCAPS" if st.uppercase else ("SMALLCAPS" if st.small_caps else "NORMAL"),
        "underline": st.underline, "strike": st.strike,
        "baseline": "SUPERSCRIPT" if st.superscript else ("SUBSCRIPT" if st.subscript else "NORMAL"),
        "kerning": KERNING.get(st.kerning, "OPTICAL"),
        "antialias": ANTIALIAS.get(st.antialias, "SMOOTH"), "hyphenate": st.hyphenate,
        "indent_left": st.indent_left, "indent_right": st.indent_right, "indent_first": st.indent_first,
        "space_before": st.space_before, "space_after": st.space_after,
        "stroke": st.stroke_width, "stroke_color": st.stroke_color.lstrip("#"),
        "rotation": st.rotation, "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2, "redraw": r.needs_redraw, "orphan": r.orphan,
        "shadow": st.shadow, "shadow_color": st.shadow_color.lstrip("#"), "shadow_opacity": st.shadow_opacity,
        "shadow_angle": st.shadow_angle, "shadow_distance": st.shadow_distance, "shadow_size": st.shadow_size,
    }


# ---------------------------------------------------------------- Photoshop ----
JSX = r"""
(function () {
var D = %(data)s;
function cTID(s) { return app.charIDToTypeID(s); }
function sTID(s) { return app.stringIDToTypeID(s); }
function hex(c) { var s = new SolidColor(); s.rgb.hexValue = c; return s; }
// Gán thuộc tính; thuộc tính/kiểu nào phiên bản Photoshop này không có thì bỏ qua.
function put(o, k, f) { try { o[k] = f(); } catch (e) {} }
var oldRuler = app.preferences.rulerUnits, oldType = app.preferences.typeUnits;
app.preferences.rulerUnits = Units.PIXELS;
app.preferences.typeUnits = TypeUnits.PIXELS;
app.displayDialogs = DialogModes.NO;
var doc = null;
try {
  // Tài liệu cùng file đang mở (vd lần chạy trước bị dừng) thì đóng, không lưu, để khỏi cộng dồn lớp.
  for (var k = app.documents.length - 1; k >= 0; k--) {
    try { if (app.documents[k].fullName.fsName == new File(D.raw).fsName) app.documents[k].close(SaveOptions.DONOTSAVECHANGES); } catch (e) {}
  }
  doc = app.open(new File(D.raw));
  if (doc.mode != DocumentMode.RGB) doc.changeMode(ChangeMode.RGB);
  // 72 ppi: 1 pt = 1 px, để cỡ chữ / khung chữ đúng bằng số px tool đã tính.
  doc.resizeImage(undefined, undefined, 72, ResampleMethod.NONE);
  if (doc.layers.length == 1 && !doc.activeLayer.isBackgroundLayer) doc.activeLayer.name = "Background";
  if (!D.bg_visible) put(doc.layers[0], "visible", function () { return false; });
  for (var i = 0; i < D.layers.length; i++) {
    var L = D.layers[i];
    var lay;
    if (L.type == "empty") {
      lay = doc.artLayers.add();
      lay.name = L.name;
      lay.opacity = L.opacity;
      put(lay, "blendMode", function () { return BlendMode[L.blend]; });
      if (!L.visible) lay.visible = false;
      continue;
    }
    if (L.type == "pixel") {
      var pd = app.open(new File(L.png));
      pd.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
      pd.close(SaveOptions.DONOTSAVECHANGES);
      app.activeDocument = doc;
      lay = doc.layers[0];
      doc.activeLayer = lay;
      lay.name = L.name;
      lay.opacity = L.opacity;
      put(lay, "blendMode", function () { return BlendMode[L.blend]; });
      if (!L.visible) lay.visible = false;
      continue;
    }
    lay = doc.artLayers.add();
    lay.kind = LayerKind.TEXT;
    var t = lay.textItem;
    t.kind = TextType.PARAGRAPHTEXT;
    t.contents = L.contents;
    // Nền khó tool để nguyên chữ gốc: đánh dấu tên lớp để biết chỗ cần tự redraw.
    if (L.custom_name) lay.name = L.name;
    else if (L.redraw || L.orphan) put(lay, "name", function () {
      return (L.orphan ? "CHƯA ĐẶT - " : "") + (L.redraw ? "REDRAW - " : "") + L.contents.replace(/[\r\u0003]/g, " ").substr(0, 40);
    });
    t.font = L.font;
    t.size = new UnitValue(L.size, "px");
    t.useAutoLeading = L.auto_leading;
    if (L.auto_leading) t.autoLeadingAmount = 120; else t.leading = new UnitValue(L.leading, "px");
    t.color = hex(L.color);
    put(t, "tracking", function () { return L.tracking; });
    put(t, "horizontalScale", function () { return L.hscale; });
    put(t, "verticalScale", function () { return L.vscale; });
    if (L.baseline_shift) put(t, "baselineShift", function () { return new UnitValue(L.baseline_shift, "px"); });
    put(t, "fauxBold", function () { return L.faux_bold; });
    put(t, "fauxItalic", function () { return L.faux_italic; });
    put(t, "capitalization", function () { return TextCase[L.caps]; });
    if (L.underline) put(t, "underline", function () { return UnderlineType.UNDERLINELEFT; });
    if (L.strike) put(t, "strikeThru", function () { return StrikeThruType.STRIKEBOX; });
    if (L.baseline != "NORMAL") put(t, "baseline", function () { return eval("Baseline")[L.baseline]; });
    put(t, "autoKerning", function () { return AutoKernType[L.kerning]; });
    put(t, "antiAliasMethod", function () { return AntiAlias[L.antialias]; });
    put(t, "hyphenation", function () { return L.hyphenate; });
    put(t, "justification", function () { return Justification[L.justification]; });
    if (L.indent_left) t.leftIndent = new UnitValue(L.indent_left, "px");
    if (L.indent_right) t.rightIndent = new UnitValue(L.indent_right, "px");
    if (L.indent_first) t.firstLineIndent = new UnitValue(L.indent_first, "px");
    if (L.space_before) t.spaceBefore = new UnitValue(L.space_before, "px");
    if (L.space_after) t.spaceAfter = new UnitValue(L.space_after, "px");
    // Khung đoạn rộng hơn chút để Photoshop không tự ngắt dòng khác tool (dòng đã ngắt sẵn).
    var slack = L.width * 0.12 + 4;
    t.width = new UnitValue(L.width + slack, "px");
    t.height = new UnitValue(L.block_height + L.size * 2, "px");
    t.position = [new UnitValue(L.left - slack / 2, "px"), new UnitValue(L.first_baseline - L.ascent, "px")];
    if (L.stroke > 0 || L.shadow) {
      var d = new ActionDescriptor(), r = new ActionReference();
      r.putProperty(cTID("Prpr"), cTID("Lefx"));
      r.putEnumerated(cTID("Lyr "), cTID("Ordn"), cTID("Trgt"));
      d.putReference(cTID("null"), r);
      var fx = new ActionDescriptor();
      fx.putUnitDouble(cTID("Scl "), cTID("#Prc"), 100);
      if (L.stroke > 0) {
        var st = new ActionDescriptor(), c = new ActionDescriptor();
        st.putBoolean(cTID("enab"), true);
        st.putEnumerated(cTID("Styl"), cTID("FStl"), cTID("OutF"));
        st.putEnumerated(cTID("PntT"), cTID("FrFl"), cTID("SClr"));
        st.putEnumerated(cTID("Md  "), cTID("BlnM"), cTID("Nrml"));
        st.putUnitDouble(cTID("Opct"), cTID("#Prc"), 100);
        st.putUnitDouble(cTID("Sz  "), cTID("#Pxl"), L.stroke);
        var sc = hex(L.stroke_color);
        c.putDouble(cTID("Rd  "), sc.rgb.red); c.putDouble(cTID("Grn "), sc.rgb.green); c.putDouble(cTID("Bl  "), sc.rgb.blue);
        st.putObject(cTID("Clr "), cTID("RGBC"), c);
        fx.putObject(cTID("FrFX"), cTID("FrFX"), st);
      }
      if (L.shadow) {
        var ds = new ActionDescriptor(), dc = new ActionDescriptor();
        ds.putBoolean(cTID("enab"), true);
        ds.putEnumerated(cTID("Md  "), cTID("BlnM"), cTID("Mltp"));
        var shc = hex(L.shadow_color);
        dc.putDouble(cTID("Rd  "), shc.rgb.red); dc.putDouble(cTID("Grn "), shc.rgb.green); dc.putDouble(cTID("Bl  "), shc.rgb.blue);
        ds.putObject(cTID("Clr "), cTID("RGBC"), dc);
        ds.putUnitDouble(cTID("Opct"), cTID("#Prc"), L.shadow_opacity);
        ds.putBoolean(cTID("uglg"), false);
        ds.putUnitDouble(cTID("lagl"), cTID("#Ang"), L.shadow_angle);
        ds.putUnitDouble(cTID("Dstn"), cTID("#Pxl"), L.shadow_distance);
        ds.putUnitDouble(cTID("Ckmt"), cTID("#Pxl"), 0);
        ds.putUnitDouble(cTID("blur"), cTID("#Pxl"), L.shadow_size);
        fx.putObject(cTID("DrSh"), cTID("DrSh"), ds);
      }
      d.putObject(cTID("T   "), cTID("Lefx"), fx);
      executeAction(cTID("setd"), d, DialogModes.NO);
    }
    if (L.rotation) lay.rotate(L.rotation, AnchorPosition.MIDDLECENTER);
    if (L.opacity < 100) lay.opacity = L.opacity;
    if (!L.visible) lay.visible = false;
  }
  var opts = new PhotoshopSaveOptions();
  opts.layers = true;
  doc.saveAs(new File(D.out), opts, true, Extension.LOWERCASE);
} finally {
  if (doc) try { doc.close(SaveOptions.DONOTSAVECHANGES); } catch (e) {}
  app.preferences.rulerUnits = oldRuler;
  app.preferences.typeUnits = oldType;
}
return "ok";
})();
"""


def photoshop_available() -> bool:
    try:
        import winreg

        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Adobe\Photoshop")
        return True
    except OSError:
        return False


class PhotoshopExporter:
    """Giữ một kết nối Photoshop cho cả chương (mở Photoshop mất thời gian, chỉ làm một lần)."""

    def __init__(self):
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        self.app = win32com.client.Dispatch("Photoshop.Application")
        self.tmp = tempfile.mkdtemp(prefix="lilyzono_")

    def export(self, page, out_path: str) -> None:
        from .document import doc_for

        doc = doc_for(page)
        doc.sync()
        data = {"raw": os.path.abspath(page.path).replace("\\", "/"), "bg_visible": doc.layers[0].visible,
                "out": os.path.abspath(out_path).replace("\\", "/"), "layers": doc_layers(doc, self.tmp)}
        res = self.app.DoJavaScript(JSX % {"data": json.dumps(data, ensure_ascii=False)})
        if res != "ok" or not os.path.exists(out_path):
            raise RuntimeError(f"Photoshop không lưu được {out_path}: {res}")


def _ascii_name(name: str) -> str:
    """Tên lớp kiểu cũ trong PSD chỉ nhận ký tự Mac Roman: bỏ dấu tiếng Việt (vd "Xoá chữ" → "Xoa chu")."""
    import unicodedata

    name = name.replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))[:60]


def export_without_photoshop(page, out_path: str) -> None:
    """PSD có lớp nhưng chữ là lớp ảnh (khi máy không có Photoshop)."""
    from PIL import Image
    from psd_tools import PSDImage
    from psd_tools.api.layers import PixelLayer
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QRectF

    from .typeset import draw_text

    from psd_tools.constants import BlendMode

    from .document import doc_for

    doc = doc_for(page)
    doc.sync()
    h, w = page.original.shape[:2]
    psd = PSDImage.new("RGB", (w, h))
    modes = {"normal": BlendMode.NORMAL, "multiply": BlendMode.MULTIPLY, "screen": BlendMode.SCREEN,
             "darken": BlendMode.DARKEN, "lighten": BlendMode.LIGHTEN}

    def add(rgba, name, layer, top=0, left=0):
        lay = PixelLayer.frompil(Image.fromarray(rgba), psd, _ascii_name(name), top=int(top), left=int(left))
        lay.visible = layer.visible
        lay.opacity = round(layer.opacity * 255)
        lay.blend_mode = modes.get(layer.blend, BlendMode.NORMAL)
        psd.append(lay)

    add(np.dstack([cv2.cvtColor(page.original, cv2.COLOR_BGR2RGB), np.full((h, w), 255, np.uint8)]), "Background",
        doc.layers[0])
    for L in doc.layers[1:]:
        if L.kind in ("redraw", "pixel"):
            bgra = clean_layer(page) if L.kind == "redraw" else L.rgba
            add(cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA), L.name, L)
            continue
        r = doc.region_of(L)
        if r is None or not r.text.strip():
            continue
        img = QImage(w, h, QImage.Format_RGBA8888)
        img.fill(0)
        p = QPainter(img)
        x1, y1, x2, y2 = r.text_rect
        draw_text(p, r.text, r.effective_style, QRectF(x1, y1, x2 - x1, y2 - y1))
        p.end()
        arr = np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(h, img.bytesPerLine())[:, :w * 4]
        arr = arr.reshape(h, w, 4).copy()
        ys, xs = np.nonzero(arr[..., 3])
        if len(xs) == 0:
            continue
        bx1, by1, bx2, by2 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        name = L.name if L.custom_name else (("CHƯA ĐẶT - " if r.orphan else "") + ("REDRAW - " if r.needs_redraw else "")
                                             + r.text.replace("\n", " ").replace(SOFT_BREAK, " "))
        add(arr[by1:by2, bx1:bx2], name, L, top=by1, left=bx1)
    psd.save(out_path)
