"""Opt-in live Photoshop test: only creates/changes/closes its own temporary documents.

python tools/test_photoshop_integration.py --image page.jpg --translation page.txt --output output/test
Uses Adobe's scripting API, without mouse, keyboard or foreground-window automation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import uuid

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


DRIVER = r"""
(function () {
  var D = %s;
  var oldActive = app.documents.length ? app.activeDocument : null;
  var oldDialogs = app.displayDialogs, oldRuler = app.preferences.rulerUnits, oldType = app.preferences.typeUnits;
  var owned = [], report = {version:app.version, cases:[]};
  function check(ok, message) { if (!ok) throw new Error(message); }
  function originals() {
    var docs = [], active = app.documents.length ? app.activeDocument : null;
    try {
      for (var i=0; i<app.documents.length; i++) {
        var doc = app.documents[i]; app.activeDocument = doc;
        docs.push({id:doc.id, saved:doc.saved, layers:doc.layers.length, history:doc.historyStates.length,
                   width:doc.width.as("px"), height:doc.height.as("px"), resolution:doc.resolution});
      }
    } finally { if (active) app.activeDocument = active; }
    return LilyzonoPS.encode(docs);
  }
  var before = originals();
  try {
    app.displayDialogs = DialogModes.NO;
    var data = D.data || null;
    if (data) report.job = new File(data.redraw).parent.fsName;
    for (var n=0; n<D.resolutions.length; n++) {
      var dpi = D.resolutions[n];
      var doc = app.open(new File(D.input)); owned.push(doc);
      doc.resizeImage(undefined, undefined, dpi, ResampleMethod.NONE);
      var optionsWindow = LilyzonoPS.options(doc, false);
      check(optionsWindow.children.length > 0, "Options window failed to build");
      optionsWindow.close();
      var baseLayers = doc.layers.length, baseHistory = doc.activeHistoryState, baseCount = doc.historyStates.length;
      if (!data) {
        data = LilyzonoPS.prepare(doc, D.request, new Folder(D.scriptFolder), false);
        check(data && data.ok, "No worker result");
        report.job = new File(data.redraw).parent.fsName;
        var previewWindow = LilyzonoPS.preview(data, false);
        check(previewWindow.children.length > 0, "Preview window failed to build");
        previewWindow.close();
      }
      var bad = LilyzonoPS.decode(LilyzonoPS.encode(data)), caught = false;
      // Photoshop silently clamps some invalid numeric values. A null contents
      // value fails during layer construction, exercising actual partial rollback.
      bad.layers[1].contents = null;
      try { LilyzonoPS.applyToDocument(doc, bad); } catch (expected) { caught = true; }
      check(caught && doc.layerSets.length === 0 && doc.layers.length === baseLayers, "Failure did not roll back partial layers");
      LilyzonoPS.applyToDocument(doc, data);
      check(doc.layerSets.length === 1, "Expected one new group");
      check(doc.historyStates.length - baseCount === 1, "Layer creation must be one history step");
      check(doc.layerSets[0].artLayers.length === data.layers.length + 1, "Incorrect layer count");
      var group = doc.layerSets[0], items = [], textCount = 0;
      for (var i=0; i<group.artLayers.length; i++) {
        var layer = group.artLayers[i];
        if (layer.kind === LayerKind.TEXT) {
          textCount++;
          var t = layer.textItem, bounds = layer.bounds;
          var ruler = app.preferences.rulerUnits;
          app.preferences.rulerUnits = Units.PIXELS;
          bounds = layer.bounds;
          var box = [bounds[0].value, bounds[1].value, bounds[2].value, bounds[3].value];
          app.preferences.rulerUnits = ruler;
          var spec = data.layers[data.layers.length - 1 - i];
          check(Math.abs(t.size.as("pt") * dpi / 72 - spec.size) < .05, "Wrong pixel font size");
          check(Math.abs((box[0]+box[2])/2 - spec.cx) <= Math.max(5, spec.size*.35), "Text shifted horizontally at " + dpi + " ppi: " + layer.name);
          items.push({name:layer.name, contents:t.contents, font:t.font, italic:t.fauxItalic,
                      pixels:t.size.as("pt") * dpi / 72,
                      bounds:box});
        } else check(layer.name === "Redraw", "Unexpected non-text layer");
      }
      check(textCount === data.layers.length, "Text layers were rasterized or missing");
      check(doc.resolution === dpi, "Document resolution changed");
      var grouped = doc.activeHistoryState, editable = group.artLayers[0].textItem;
      var content = editable.contents;
      editable.contents = content + " TEST";
      check(editable.contents.indexOf(" TEST") >= 0, "Text contents are not editable");
      doc.activeHistoryState = grouped;
      var psd = new PhotoshopSaveOptions(); psd.layers = true;
      doc.saveAs(new File(D.output + "/013-photoshop-" + dpi + ".psd"), psd, true, Extension.LOWERCASE);
      doc.saveAs(new File(D.output + "/013-photoshop-" + dpi + ".png"), new PNGSaveOptions(), true, Extension.LOWERCASE);
      doc.activeHistoryState = baseHistory;
      check(doc.layers.length === baseLayers && doc.layerSets.length === 0, "Single undo did not restore base");
      report.cases.push({dpi:dpi, text_layers:textCount, redraw:true, single_undo:true,
                         rollback:true, editable:true, layers:items});
      doc.close(SaveOptions.DONOTSAVECHANGES); owned.pop();
    }
    report.ok = true;
  } catch (error) { report.ok = false; report.error = error.message; report.line = error.line; }
  finally {
    for (var k=owned.length-1; k>=0; k--) { try { owned[k].close(SaveOptions.DONOTSAVECHANGES); } catch (ignore) {} }
    app.displayDialogs = oldDialogs; app.preferences.rulerUnits = oldRuler; app.preferences.typeUnits = oldType;
    if (oldActive) app.activeDocument = oldActive;
    report.original_documents_preserved = before === originals();
    report.active_document_restored = !oldActive || app.activeDocument.id === oldActive.id;
    report.preferences_restored = app.displayDialogs === oldDialogs && app.preferences.rulerUnits === oldRuler && app.preferences.typeUnits === oldType;
  }
  return LilyzonoPS.encode(report);
}());
"""


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--translation", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--script-folder", type=Path, default=ROOT / "photoshop")
    parser.add_argument("--reuse-job", type=Path, help="Reuse a completed worker job when testing JSX-only changes")
    args = parser.parse_args()
    import win32com.client

    app = win32com.client.GetActiveObject("Photoshop.Application")
    args.output.mkdir(parents=True, exist_ok=True)
    image = cv2.imdecode(np.fromfile(args.image, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Cannot load test image")
    input_path = args.output / ("test-input-" + uuid.uuid4().hex + ".png")
    cv2.imencode(".png", image)[1].tofile(input_path)
    from app.core.docs import load_pages, page_number
    pages = load_pages(str(args.translation))
    number = page_number(str(args.image))
    lines = pages.get(number, pages.get(0))
    if not lines:
        raise ValueError("No translation found for the test image's page number")
    config = {"input":str(input_path.resolve()), "output":str(args.output.resolve()),
              "scriptFolder":str(args.script_folder.resolve()), "resolutions":[72,300],
              "request":{"protocol":1, "profile":"gai_bang", "engine":"lama", "right_to_left":True, "lines":lines}}
    if args.reuse_job:
        config["data"] = json.loads((args.reuse_job / "result.json").read_text(encoding="utf-8"))
    source = (ROOT / "photoshop/Lilyzono.jsx").read_text(encoding="utf-8")
    source = "\n".join(line for line in source.splitlines() if not line.startswith("#target"))
    script = "var LILYZONO_PS_NO_DIALOG = true;\n" + source + "\n" + DRIVER % json.dumps(config, ensure_ascii=False)
    result = json.loads(app.DoJavaScript(script))
    (args.output / "photoshop-integration.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k not in ("cases", "job")}, ensure_ascii=False))
    for case in result.get("cases", []):
        print(json.dumps({k:v for k,v in case.items() if k != "layers"}))
    return 0 if result.get("ok") and result.get("original_documents_preserved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
