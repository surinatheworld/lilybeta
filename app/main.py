import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .core.paths import app_dir


def selftest() -> int:
    """`Lilyzono.exe --selftest [ảnh]`: chạy thử các phần chính, ghi kết quả ra selftest.log cạnh exe."""
    import time
    import traceback

    import cv2
    import numpy as np

    log_path = os.path.join(app_dir(), "selftest.log")
    lines, ok = [], True

    def step(name, fn):
        nonlocal ok
        t = time.time()
        try:
            res = fn()
            lines.append(f"OK   {name} ({time.time() - t:.1f}s) {res if res is not None else ''}")
        except Exception:  # noqa: BLE001 - ghi lại để người dùng gửi khi báo lỗi
            ok = False
            lines.append(f"FAIL {name}\n{traceback.format_exc()}")

    args = [a for a in sys.argv[1:] if a not in ("--selftest", "--psd")]
    img = cv2.imdecode(np.fromfile(args[0], np.uint8), cv2.IMREAD_COLOR) if args else None
    if img is None:
        img = np.full((600, 400, 3), 255, np.uint8)
        cv2.putText(img, "TEST", (80, 300), cv2.FONT_HERSHEY_SIMPLEX, 3, (0, 0, 0), 8)

    app = QApplication(sys.argv)  # noqa: F841 - cần cho đo font
    from .core import lama, secrets
    from .core.detect import TextDetector
    from .core.typeset import TextStyle, missing_chars

    found = {}
    step("Nhận diện chữ", lambda: f"{len(found.setdefault('b', TextDetector().detect(img)[0]))} vùng")
    step("Font hệ thống", lambda: f"Arial thiếu: '{missing_chars(TextStyle(family='Arial'), 'Ngươi đã chết rồi')}'")

    def run_lama():
        lama.get_session()
        m = np.zeros(img.shape[:2], np.uint8)
        m[10:60, 10:60] = 255
        lama.inpaint(img.copy(), m)

    step("AI LaMa", run_lama)
    from .core.fonts import postscript_name
    from .core.psd_export import photoshop_available

    step("Tên font cho Photoshop", lambda: postscript_name("Arial"))
    step("Photoshop", lambda: "có" if photoshop_available() else "không có (PSD sẽ có lớp chữ dạng ảnh)")
    if "--psd" in sys.argv and photoshop_available():
        def psd():
            from .core.page import Page
            from .core.psd_export import PhotoshopExporter
            from .core.typeset import TextStyle

            page = Page(args[0])
            page.detect(TextDetector())
            for r in page.regions[:2]:
                r.text, r.style = "Chạy thử Lilyzono", TextStyle(family="Arial")
            page.clean_many(page.regions[:2])
            out = os.path.join(app_dir(), "selftest.psd")
            PhotoshopExporter().export(page, out)
            return f"{os.path.getsize(out) // 1024} KB"

        step("Xuất PSD qua Photoshop", psd)
    step("Két lưu API key", lambda: type(__import__("keyring").get_keyring()).__name__ + f", gemini key: {bool(secrets.get_key('gemini'))}")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())

    from .ui.main_window import MainWindow
    from .ui.theme import apply_dark

    app = QApplication(sys.argv)
    apply_dark(app)
    icon = os.path.join(app_dir(), "assets", "icon.ico")
    if os.path.exists(icon):
        app.setWindowIcon(QIcon(icon))
    win = MainWindow()
    win.show()
    for arg in sys.argv[1:]:
        if arg.lower().endswith((".docx", ".txt")):
            win.load_docs(arg)
        else:
            win.load_image(arg)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
