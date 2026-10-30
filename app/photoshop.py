"""Local file bridge for the Photoshop JSX tool. Never controls Photoshop itself."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import traceback

import cv2

from .core import project
from .core.assign import apply_plan, assign_lines
from .core.clean import skip_hard
from .core.detect import TextDetector
from .core.document import Document
from .core.fonts import files_for
from .core.page import Page
from .core.paths import model_path
from .core.psd_export import clean_layer, layer_specs
from .core.typeset import TextStyle
from .core.typeset_profiles import FONTS, GAI_BANG

PROTOCOL = 1


def write_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def load_request(path: Path) -> dict:
    request = json.loads(path.read_text(encoding="utf-8-sig"))
    if request.get("protocol") != PROTOCOL:
        raise ValueError("Phiên bản công cụ và bộ xử lý không khớp.")
    if request.get("engine", "lama") not in ("lama", "opencv", "simple"):
        raise ValueError("Chế độ xoá chữ không hợp lệ.")
    lines = request.get("lines")
    if not isinstance(lines, list) or not lines or not all(isinstance(s, str) and s.strip() for s in lines):
        raise ValueError("Nhập bản dịch, mỗi câu một dòng.")
    if len(lines) > 500 or sum(map(len, lines)) > 100_000:
        raise ValueError("Bản dịch quá dài cho một trang.")
    image = Path(request["image"])
    if not image.is_file():
        raise ValueError("Không tìm thấy ảnh chụp tài liệu Photoshop.")
    return request


def register_fonts(families) -> None:
    """The offscreen Qt plugin may not enumerate Windows user fonts."""
    from PySide6.QtGui import QFontDatabase

    for family in set(families):
        for path in files_for(family):
            QFontDatabase.addApplicationFont(path)


def process_job(path: Path) -> dict:
    request = load_request(path)
    folder = path.parent
    engine = request.get("engine", "lama")
    for name in ("comictextdetector.pt.onnx",) + (("lama_fp32.onnx",) if engine == "lama" else ()):
        if not Path(model_path(name)).is_file():
            raise ValueError("Thiếu model " + name + ". Chạy build_photoshop.bat hoặc chép model vào thư mục models.")
    profile = request.get("profile", GAI_BANG)
    if profile != GAI_BANG:
        raise ValueError("Chưa hỗ trợ bộ font này.")
    missing = [font for font in FONTS.values() if not files_for(font)] if sys.platform == "win32" else []
    if missing:
        raise ValueError("Cần cài font trước khi chạy: " + ", ".join(missing))
    register_fonts(FONTS.values())
    page = Page(request["image"])
    expected = request.get("size")
    if expected is not None and list(page.size) != expected:
        raise ValueError("Kích thước ảnh chụp không khớp tài liệu Photoshop.")
    page.typeset_profile = profile
    page.detect(TextDetector(), right_to_left=request.get("right_to_left", True))
    plan = assign_lines(page, page.regions, request["lines"])
    if not plan:
        raise ValueError("Không nhận diện được khung chữ. Hãy dùng ảnh rõ nét hơn.")
    apply_plan(page, plan, TextStyle())
    if engine == "lama":
        from .core.lama import inpaint
        page.inpainter = inpaint
    elif engine == "simple":
        page.inpainter = skip_hard
    # Only translated regions are cleaned: unassigned SFX may be drawing details.
    translated = [r for r in page.regions if r.text.strip()]
    page.clean_many(translated)
    page.doc = Document(page)
    page.doc.export(str(folder / "preview.png"))
    preview = page.doc.composite()
    scale = min(420 / page.size[0], 560 / page.size[1], 1)
    thumb = cv2.resize(preview, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cv2.imencode(".png", thumb)[1].tofile(folder / "preview-ui.png")
    cv2.imencode(".png", clean_layer(page))[1].tofile(folder / "redraw.png")
    project.save(str(folder / "editable.lzp"), [page])
    specs = layer_specs(page)
    for r, spec in zip(translated, specs):
        spec["source_box"] = list(r.box)
        spec["family"] = r.style.family
        spec["kind"] = r.kind
    return {"protocol": PROTOCOL, "ok": True, "size": list(page.size),
            "redraw": str(folder / "redraw.png"), "preview": str(folder / "preview-ui.png"),
            "layers": specs, "detected": len(page.regions),
            "needs_redraw": sum(r.needs_redraw for r in translated)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bộ xử lý cục bộ cho Lilyzono Photoshop")
    parser.add_argument("--job", required=True, type=Path)
    args = parser.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication([])
    try:
        result = process_job(args.job)
    except Exception as exc:
        result = {"protocol": PROTOCOL, "ok": False, "error": str(exc)}
        (args.job.parent / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
    write_json(args.job.parent / "result.json", result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
