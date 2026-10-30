"""Xuất trang đã type ra file PSD có lớp.

- Có Photoshop: điều khiển Photoshop (ExtendScript qua COM) để tạo lớp chữ THẬT, sửa được:
  Background = ảnh raw, lớp "Xoá chữ" = phần đã xoá/vẽ lại nền, mỗi bóng một lớp chữ.
- Không có Photoshop: ghi PSD bằng psd-tools, chữ là lớp ảnh (không sửa được nội dung chữ).
"""
from __future__ import annotations

import json
import os
import tempfile

import cv2
import numpy as np

from .fonts import postscript_name
from .typeset import text_block

JUSTIFY = {("left", ""): "LEFT", ("center", ""): "CENTER", ("right", ""): "RIGHT",
           "left": "LEFTJUSTIFIED", "center": "CENTERJUSTIFIED", "right": "RIGHTJUSTIFIED", "all": "FULLYJUSTIFIED"}
ANTIALIAS = {"none": "NONE", "sharp": "SHARP", "crisp": "CRISP", "strong": "STRONG", "smooth": "SMOOTH"}


def clean_layer(page) -> np.ndarray:
    """Ảnh BGRA: chỉ những điểm tool đã xoá/vẽ lại (khác ảnh gốc), còn lại trong suốt."""
    diff = np.abs(page.working.astype(np.int16) - page.original).max(axis=2) > 2
    diff = cv2.dilate(diff.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    out = cv2.cvtColor(page.working, cv2.COLOR_BGR2BGRA)
    out[..., 3] = np.where(diff, 255, 0).astype(np.uint8)
    return out


def layer_specs(page) -> list[dict]:
    """Thông số từng lớp chữ (toạ độ px của ảnh)."""
    specs = []
    for r in page.regions:
        if not (r.cleaned and r.text.strip()):
            continue
        st = r.effective_style
        x1, y1, x2, y2 = r.text_rect
        b = text_block(r.text, st, (x1, y1, x2, y2))
        just = JUSTIFY[st.justify] if st.justify else JUSTIFY[(st.align, "")]
        specs.append({
            "contents": b["contents"], "font": postscript_name(st.family, st.style_name), "size": b["size"],
            "leading": b["leading"], "auto_leading": st.leading <= 0, "left": b["left"], "width": b["width"],
            "first_baseline": b["first_baseline"], "ascent": b["ascent"], "block_height": b["block_height"],
            "justification": just, "color": st.color.lstrip("#"), "tracking": st.tracking,
            "hscale": st.hscale, "vscale": st.vscale, "baseline_shift": st.baseline_shift,
            "faux_bold": st.bold, "faux_italic": st.italic,
            "caps": "ALLCAPS" if st.uppercase else ("SMALLCAPS" if st.small_caps else "NORMAL"),
            "underline": st.underline, "strike": st.strike,
            "baseline": "SUPERSCRIPT" if st.superscript else ("SUBSCRIPT" if st.subscript else "NORMAL"),
            "kerning": "METRICSKERN" if st.kerning != "none" else "MANUAL",
            "antialias": ANTIALIAS.get(st.antialias, "SMOOTH"), "hyphenate": st.hyphenate,
            "indent_left": st.indent_left, "indent_right": st.indent_right, "indent_first": st.indent_first,
            "space_before": st.space_before, "space_after": st.space_after,
            "stroke": st.stroke_width, "stroke_color": st.stroke_color.lstrip("#"),
            "rotation": st.rotation, "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2, "redraw": r.needs_redraw,
        })
    return specs


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
  var cd = app.open(new File(D.clean));
  cd.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
  cd.close(SaveOptions.DONOTSAVECHANGES);
  app.activeDocument = doc;
  doc.activeLayer.name = "Xoá chữ";
  for (var i = 0; i < D.layers.length; i++) {
    var L = D.layers[i];
    var lay = doc.artLayers.add();
    lay.kind = LayerKind.TEXT;
    var t = lay.textItem;
    t.kind = TextType.PARAGRAPHTEXT;
    t.contents = L.contents;
    // Nền khó tool để nguyên chữ gốc: đánh dấu tên lớp để biết chỗ cần tự redraw.
    if (L.redraw) put(lay, "name", function () { return "REDRAW - " + L.contents.replace(/[\r\u0003]/g, " ").substr(0, 40); });
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
    if (L.stroke > 0) {
      var d = new ActionDescriptor(), r = new ActionReference();
      r.putProperty(cTID("Prpr"), cTID("Lefx"));
      r.putEnumerated(cTID("Lyr "), cTID("Ordn"), cTID("Trgt"));
      d.putReference(cTID("null"), r);
      var fx = new ActionDescriptor(), st = new ActionDescriptor(), c = new ActionDescriptor();
      fx.putUnitDouble(cTID("Scl "), cTID("#Prc"), 100);
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
      d.putObject(cTID("T   "), cTID("Lefx"), fx);
      executeAction(cTID("setd"), d, DialogModes.NO);
    }
    if (L.rotation) lay.rotate(L.rotation, AnchorPosition.MIDDLECENTER);
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
        clean_png = os.path.join(self.tmp, "clean.png")
        cv2.imencode(".png", clean_layer(page))[1].tofile(clean_png)
        data = {"raw": os.path.abspath(page.path).replace("\\", "/"), "clean": clean_png.replace("\\", "/"),
                "out": os.path.abspath(out_path).replace("\\", "/"), "layers": layer_specs(page)}
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

    h, w = page.original.shape[:2]
    psd = PSDImage.new("RGB", (w, h))
    psd.append(PixelLayer.frompil(Image.fromarray(cv2.cvtColor(page.original, cv2.COLOR_BGR2RGB)), psd, "Background"))
    psd.append(PixelLayer.frompil(Image.fromarray(cv2.cvtColor(clean_layer(page), cv2.COLOR_BGRA2RGBA)), psd,
                                  _ascii_name("Xoá chữ")))
    for r in page.regions:
        if not (r.cleaned and r.text.strip()):
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
        name = _ascii_name(("REDRAW - " if r.needs_redraw else "") + r.text.replace("\n", " "))
        psd.append(PixelLayer.frompil(Image.fromarray(arr[by1:by2, bx1:bx2]), psd, name, top=int(by1), left=int(bx1)))
    psd.save(out_path)
