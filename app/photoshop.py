"""Bộ xử lý cho script Lilyzono.jsx trong Photoshop.

Script ghi request.json rồi trả quyền cho Photoshop ngay; bộ xử lý chạy riêng (cửa sổ tiến độ + nút Huỷ), ghi
result.json và – ở chế độ "callback" – gọi lại Photoshop (LilyzonoPS.finish) để xem trước và tạo layer. Không có
"callback" (kiểm thử tự động): chạy ngầm, chỉ ghi result.json.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
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


def process_job(path: Path, report=None) -> dict:
    report = report or (lambda step: None)
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
    report("Nạp font và ảnh…")
    register_fonts(FONTS.values())
    page = Page(request["image"])
    expected = request.get("size")
    if expected is not None and list(page.size) != expected:
        raise ValueError("Kích thước ảnh chụp không khớp tài liệu Photoshop.")
    page.typeset_profile = profile
    report("Nhận diện chữ…")
    page.detect(TextDetector(), right_to_left=request.get("right_to_left", True))
    plan = assign_lines(page, page.regions, request["lines"])
    if not plan:
        raise ValueError("Không nhận diện được khung chữ. Hãy dùng ảnh rõ nét hơn.")
    apply_plan(page, plan, TextStyle())
    if engine == "lama":
        report("Nạp AI LaMa (lần đầu ~20–30 giây)…")
        from .core.lama import get_session, inpaint
        get_session()
        page.inpainter = inpaint
    elif engine == "simple":
        page.inpainter = skip_hard
    # Only translated regions are cleaned: unassigned SFX may be drawing details.
    translated = [r for r in page.regions if r.text.strip()]
    report(f"Xoá chữ {len(translated)} khung…")
    page.clean_many(translated)
    report("Dựng bản xem trước…")
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
            "doc_id": request.get("doc_id"), "doc_name": request.get("doc_name", ""),
            "redraw": str(folder / "redraw.png"), "preview": str(folder / "preview-ui.png"),
            "layers": specs, "detected": len(page.regions),
            "needs_redraw": sum(r.needs_redraw for r in translated)}


def _failure(job: Path, exc: Exception) -> dict:
    (job.parent / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
    return {"protocol": PROTOCOL, "ok": False, "error": str(exc)}


def notify_photoshop(script: str, folder: Path) -> None:
    """Gọi lại Photoshop đang mở để xem trước và tạo layer. Không mở Photoshop mới nếu nó đã bị tắt."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        ps = win32com.client.GetActiveObject("Photoshop.Application")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Photoshop đã đóng.") from exc
    code = ("var LILYZONO_PS_NO_DIALOG = true; $.evalFile(new File(%s));"
            "try { LilyzonoPS.finish(%s); } catch (e) { alert('Lilyzono: ' + e.message); }"
            % (json.dumps(script), json.dumps(str(folder))))
    ps.DoJavaScript(code)


def run_with_progress(job: Path, request: dict) -> int:
    """Chế độ dùng thật: cửa sổ tiến độ (Huỷ được bất cứ lúc nào), xử lý ở luồng riêng, xong thì gọi lại Photoshop."""
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget

    app = QApplication.instance() or QApplication([])
    try:
        from .ui.theme import apply_dark

        apply_dark(app)
    except Exception:  # noqa: BLE001 - giao diện tối chỉ để đẹp
        pass
    win = QWidget(None, Qt.WindowStaysOnTopHint)
    win.setWindowTitle("Lilyzono")
    lay = QVBoxLayout(win)
    title = QLabel(f"Đang xử lý <b>{request.get('doc_name', '')}</b> – Photoshop vẫn dùng bình thường.")
    step = QLabel("Khởi động…")
    bar = QProgressBar()
    bar.setRange(0, 0)
    cancel = QPushButton("Huỷ")
    for w in (title, step, bar, cancel):
        lay.addWidget(w)
    win.resize(420, 140)
    state = {"step": "Khởi động…", "result": None}

    def work():
        try:
            state["result"] = process_job(job, lambda text: state.__setitem__("step", text))
        except Exception as exc:  # noqa: BLE001 - báo lỗi cho người dùng
            state["result"] = _failure(job, exc)
        write_json(job.parent / "result.json", state["result"])

    def abort():
        os._exit(2)  # dừng ngay cả khi AI đang chạy; Photoshop không bị ảnh hưởng

    cancel.clicked.connect(abort)
    win.closeEvent = lambda e: abort()
    thread = threading.Thread(target=work, daemon=True)

    def poll():
        step.setText(state["step"])
        if thread.is_alive():
            return
        timer.stop()
        result = state["result"]
        if not result["ok"]:
            QMessageBox.critical(win, "Lilyzono", result["error"])
            app.quit()
            return
        step.setText("Đang mở bản xem trước trong Photoshop…")
        app.processEvents()
        win.hide()
        try:
            notify_photoshop(request.get("script", ""), job.parent)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(None, "Lilyzono", f"Không gửi được kết quả sang Photoshop: {exc}\n"
                                f"Kết quả nằm ở: {job.parent}")
        app.quit()

    timer = QTimer()
    timer.timeout.connect(poll)
    win.show()
    thread.start()
    timer.start(200)
    app.exec()
    return 0 if state["result"] and state["result"].get("ok") else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bộ xử lý cục bộ cho Lilyzono Photoshop")
    parser.add_argument("--job", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.job.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 - request hỏng: chạy ngầm để ghi lỗi vào result.json
        request = {}
    if request.get("callback"):
        return run_with_progress(args.job, request)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - cần cho font, chữ
    try:
        result = process_job(args.job)
    except Exception as exc:
        result = _failure(args.job, exc)
    write_json(args.job.parent / "result.json", result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
