"""Bộ xử lý cho script Lilyzono.jsx trong Photoshop.

Script ghi request.json rồi trả quyền cho Photoshop ngay; bộ xử lý chạy riêng (cửa sổ tiến độ + nút Huỷ), ghi
result.json và – ở chế độ "callback" – gọi lại Photoshop (LilyzonoPS.finish) để xem trước và tạo layer. Không có
"callback" (kiểm thử tự động): chạy ngầm, chỉ ghi result.json.

Ba chế độ (request["mode"]):
- "page" (mặc định): một trang – nhận diện, ghép thoại, xoá chữ, dàn chữ.
- "batch": cả chương – mọi ảnh đang mở trong Photoshop, thoại lấy theo số trang trong file bản dịch.
- "sfx": ảnh người dùng đã redraw SFX → layer chỉ chứa phần khác ảnh gốc (chỗ SFX đã xoá).

Luật xoá chữ: chỉ chỗ dễ (bóng thoại / hộp sáng khép kín, nền trơn một màu). Không vẽ lại tranh, lưới chấm, nền
chuyển màu; SFX không xoá – chữ dịch đặt đè lên bản gốc (layer tên "REDRAW · …").
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
import traceback

import math
import tempfile

import cv2
import numpy as np

from .core import project
from .core.assign import apply_plan, assign_lines
from .core.clean import easy_only
from .core.docs import load_pages, page_number
from .core.detect import TextDetector
from .core.document import Document
from .core.fonts import files_for
from .core.page import Page
from .core.paths import model_path
from .core.psd_export import clean_layer, layer_specs
from .core.typeset import TextStyle
from .core.typeset_profiles import GAI_BANG, PROFILES, fonts_for, region_kind

PROTOCOL = 1


def write_json(path: Path, data: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _check_lines(lines) -> None:
    if not isinstance(lines, list) or not lines or not all(isinstance(s, str) and s.strip() for s in lines):
        raise ValueError("Nhập bản dịch, mỗi câu một dòng.")
    if len(lines) > 500 or sum(map(len, lines)) > 100_000:
        raise ValueError("Bản dịch quá dài cho một trang.")


def _check_image(path) -> None:
    if not path or not Path(path).is_file():
        raise ValueError("Không tìm thấy ảnh chụp tài liệu Photoshop.")


def load_request(path: Path) -> dict:
    request = json.loads(path.read_text(encoding="utf-8-sig"))
    if request.get("protocol") != PROTOCOL:
        raise ValueError("Phiên bản công cụ và bộ xử lý không khớp.")
    if request.get("engine", "simple") not in ("lama", "opencv", "simple"):
        raise ValueError("Chế độ xoá chữ không hợp lệ.")
    mode = request.get("mode", "page")
    if mode == "sfx":
        _check_image(request.get("image"))
        if not request.get("redrawn") or not Path(request["redrawn"]).is_file():
            raise ValueError("Không tìm thấy ảnh redraw SFX.")
        return request
    has_file = bool(request.get("translation_file") or request.get("translation_text"))
    if mode == "batch":
        pages = request.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ValueError("Không có trang nào để làm.")
        for item in pages:
            _check_image(item.get("image"))
        if not has_file:
            raise ValueError("Làm cả chương cần bản dịch có số trang (1, 2, …) trước thoại mỗi trang.")
        return request
    if mode != "page":
        raise ValueError("Chế độ không hợp lệ.")
    if not has_file or "lines" in request:
        _check_lines(request.get("lines"))
    _check_image(request.get("image"))
    return request


def register_fonts(families) -> None:
    """The offscreen Qt plugin may not enumerate Windows user fonts."""
    from PySide6.QtGui import QFontDatabase

    for family in set(families):
        for path in files_for(family):
            QFontDatabase.addApplicationFont(path)


def _check_setup(profile: str) -> None:
    if not Path(model_path("comictextdetector.pt.onnx")).is_file():
        raise ValueError("Thiếu model comictextdetector.pt.onnx. Chạy build_photoshop.bat hoặc chép model vào thư mục models.")
    if profile not in PROFILES:
        raise ValueError("Chưa hỗ trợ bộ font này.")
    missing = sorted({font for font in fonts_for(profile).values() if not files_for(font)}) if sys.platform == "win32" else []
    if missing:
        raise ValueError("Cần cài font trước khi chạy: " + ", ".join(missing))


def _translations(request: dict) -> dict[int, list[str]]:
    """Bản dịch chia theo trang ({số trang: [câu]}; không có dòng số trang thì mọi câu thuộc trang 0)."""
    if request.get("translation_file"):
        return load_pages(request["translation_file"])
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(request.get("translation_text", ""))
    try:
        return load_pages(f.name)
    finally:
        os.remove(f.name)


def _lines_for(pages: dict[int, list[str]], doc_name: str, single: bool) -> list[str]:
    n = page_number(doc_name)
    if n is not None and n in pages:
        return pages[n]
    if single and list(pages) == [0]:
        return pages[0]
    return []


def typeset_page(image: str, lines: list[str], folder: Path, prefix: str = "", size=None, right_to_left: bool = True,
                 detector=None, report=None, profile: str = GAI_BANG) -> dict:
    """Một trang: nhận diện, ghép thoại, xoá chữ theo luật "chỉ chỗ dễ", dàn chữ; ghi ảnh Redraw/xem trước."""
    report = report or (lambda step: None)
    page = Page(image)
    if size is not None and list(page.size) != list(size):
        raise ValueError("Kích thước ảnh chụp không khớp tài liệu Photoshop.")
    page.typeset_profile = profile
    report("Nhận diện chữ…")
    page.detect(detector or TextDetector(), right_to_left=right_to_left)
    plan = assign_lines(page, page.regions, lines)
    if not plan:
        raise ValueError("Không nhận diện được khung chữ. Hãy dùng ảnh rõ nét hơn.")
    apply_plan(page, plan, TextStyle())
    translated = [r for r in page.regions if r.text.strip()]
    report(f"Xoá chữ ở chỗ dễ ({len(translated)} khung)…")
    page.inpainter = easy_only
    for r in translated:
        if r.kind == "sfx":  # SFX không xoá: chữ dịch đặt đè lên bản gốc
            r.cleaned, r.needs_redraw = True, True
            page.clean(r)
    page.clean_many([r for r in translated if r.kind != "sfx"])
    report("Dựng bản xem trước…")
    page.doc = Document(page)
    page.doc.export(str(folder / f"{prefix}preview.png"))
    preview = page.doc.composite()
    scale = min(420 / page.size[0], 560 / page.size[1], 1)
    thumb = cv2.resize(preview, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    cv2.imencode(".png", thumb)[1].tofile(folder / f"{prefix}preview-ui.png")
    cv2.imencode(".png", clean_layer(page))[1].tofile(folder / f"{prefix}redraw.png")
    project.save(str(folder / f"{prefix}editable.lzp"), [page])
    specs = layer_specs(page)
    for r, spec in zip(translated, specs):
        spec["source_box"] = list(r.box)
        spec["family"] = r.style.family
        spec["kind"] = r.kind
        spec["typeset_kind"] = region_kind(page, r)
    return {"size": list(page.size), "profile_name": PROFILES[profile], "redraw": str(folder / f"{prefix}redraw.png"),
            "preview": str(folder / f"{prefix}preview-ui.png"), "layers": specs, "detected": len(page.regions),
            "needs_redraw": sum(r.needs_redraw for r in translated)}


def redraw_sfx(base_path: str, redrawn_path: str, out_path: Path) -> dict:
    """Ảnh đã redraw SFX → layer BGRA chỉ giữ phần khác ảnh gốc (chỗ đã xoá), căn khớp nếu ảnh bị lệch / co giãn."""
    from .core.cloud import align_to

    base = cv2.imdecode(np.fromfile(base_path, np.uint8), cv2.IMREAD_COLOR)
    red = cv2.imdecode(np.fromfile(redrawn_path, np.uint8), cv2.IMREAD_COLOR)
    if base is None or red is None:
        raise ValueError("Không đọc được ảnh redraw SFX (dùng PNG / JPG / WEBP).")
    h, w = base.shape[:2]
    rh, rw = red.shape[:2]
    if abs(math.log((rw / rh) / (w / h))) > 0.03:
        raise ValueError(f"Ảnh redraw ({rw}×{rh}) khác tỉ lệ trang ({w}×{h}): phải là cả trang, cùng khung với ảnh gốc.")
    if (rw, rh) != (w, h):
        red = cv2.resize(red, (w, h), interpolation=cv2.INTER_AREA if rw > w else cv2.INTER_CUBIC)

    def changed(img):
        return (np.abs(img.astype(np.int16) - base).max(axis=2) > 40).astype(np.uint8)

    # Căn theo cả trang: phần SFX đã xoá chỉ chiếm ít nên không làm lệch phép căn (AI hay dịch / co giãn vài px).
    red = align_to(red, base, np.zeros(base.shape[:2], np.uint8))
    mask = cv2.morphologyEx(changed(red), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))  # bỏ lệch mép nét mảnh
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= 60  # bỏ đốm nhiễu (nén JPG, AI vẽ khác vài điểm)
    mask = keep[lab].astype(np.uint8)
    if not mask.any():
        raise ValueError("Ảnh redraw không khác ảnh gốc ở đâu cả – chọn đúng ảnh đã xoá SFX của trang này.")
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=2)
    alpha = cv2.GaussianBlur(mask.astype(np.float32) * 255, (0, 0), 1.0)
    cv2.imencode(".png", np.dstack([red, np.clip(alpha, 0, 255).astype(np.uint8)]))[1].tofile(out_path)
    return {"size": [w, h], "layer": str(out_path), "changed_percent": round(float(mask.mean()) * 100, 2)}


def process_job(path: Path, report=None) -> dict:
    report = report or (lambda step: None)
    request = load_request(path)
    folder = path.parent
    mode = request.get("mode", "page")
    base = {"protocol": PROTOCOL, "ok": True, "mode": mode,
            "doc_id": request.get("doc_id"), "doc_name": request.get("doc_name", "")}
    if mode == "sfx":
        report("So ảnh redraw với ảnh gốc…")
        return {**base, **redraw_sfx(request["image"], request["redrawn"], folder / "sfx_redraw.png")}
    profile = request.get("profile", GAI_BANG)
    _check_setup(profile)
    report("Nạp font và model…")
    register_fonts(fonts_for(profile).values())
    detector = TextDetector()
    rtl = request.get("right_to_left", True)
    if mode == "batch":
        pages = _translations(request)
        out = []
        items = request["pages"]
        for i, item in enumerate(items):
            name = item.get("doc_name", "")
            step = f"Trang {i + 1}/{len(items)} ({name}): "
            lines = _lines_for(pages, name, len(items) == 1)
            if not lines:
                out.append({"ok": False, "doc_id": item.get("doc_id"), "doc_name": name,
                            "error": f"Bản dịch không có trang {page_number(name)}."})
                continue
            try:
                res = typeset_page(item["image"], lines, folder, f"p{i}_", item.get("size"), rtl, detector,
                                   lambda text, step=step: report(step + text), profile)
                out.append({"ok": True, "doc_id": item.get("doc_id"), "doc_name": name, **res})
            except Exception as exc:  # noqa: BLE001 - trang lỗi không làm hỏng cả chương
                out.append({"ok": False, "doc_id": item.get("doc_id"), "doc_name": name, "error": str(exc)})
        return {**base, "pages": out}
    lines = request.get("lines")
    if not lines:
        lines = _lines_for(_translations(request), request.get("doc_name", ""), True)
        if not lines:
            raise ValueError(f"Bản dịch không có trang {page_number(request.get('doc_name', ''))}.")
    return {**base, **typeset_page(request["image"], lines, folder, "", request.get("size"), rtl, detector, report, profile)}


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
    # Photoshop làm xong nhưng không vẽ lại màn hình / bảng Layers (và không trả lời lệnh này) cho tới khi nhận thêm
    # một lệnh scripting khác – người dùng tưởng chưa tạo gì. Trong lúc chờ, cứ 2 giây gửi một lệnh vẽ lại nhẹ.
    done = threading.Event()

    def nudge():
        pythoncom.CoInitialize()
        try:
            other = win32com.client.GetActiveObject("Photoshop.Application")
        except Exception:  # noqa: BLE001
            return
        while not done.wait(2.0):
            try:
                other.DoJavaScript("app.refresh(); 1")
            except Exception:  # noqa: BLE001 - Photoshop đang bận / đã đóng: thử lại sau
                pass

    threading.Thread(target=nudge, daemon=True).start()
    try:
        ps.DoJavaScript(code)
    finally:
        done.set()


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
    what = {"batch": f"cả chương ({len(request.get('pages') or [])} trang)",
            "sfx": f"ảnh redraw SFX của {request.get('doc_name', '')}"}.get(request.get("mode"), request.get("doc_name", ""))
    title = QLabel(f"Đang xử lý <b>{what}</b> – Photoshop vẫn dùng bình thường.")
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
