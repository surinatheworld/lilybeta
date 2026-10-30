"""Type cả chương không cần mở giao diện: raw + file docs theo trang → PSD.

    python tools/run_chapter.py <thư mục raw> <file bản dịch .docx/.txt> <thư mục PSD ra> [--font "000 WildWords2 TB"]
                                [--engine lama|opencv] [--no-photoshop]

File docs: dòng chỉ có số là tên trang, các dòng sau là câu dịch của trang đó theo thứ tự đọc.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.core import lama  # noqa: E402
from app.core.chapter import Chapter  # noqa: E402
from app.core.detect import TextDetector  # noqa: E402
from app.core.psd_export import PhotoshopExporter, export_without_photoshop, photoshop_available  # noqa: E402
from app.core.typeset import TextStyle  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_dir")
    ap.add_argument("docs")
    ap.add_argument("out_dir")
    ap.add_argument("--font", default="000 WildWords2 TB")
    ap.add_argument("--engine", default="lama", choices=["lama", "opencv"])
    ap.add_argument("--no-photoshop", action="store_true")
    a = ap.parse_args()

    app = QGuiApplication.instance() or QGuiApplication(sys.argv)  # noqa: F841 - cần cho font
    os.makedirs(a.out_dir, exist_ok=True)
    ch = Chapter(a.raw_dir)
    tr = ch.load_translations(a.docs)
    print(f"{len(ch.paths)} trang ảnh, bản dịch có {len(tr)} trang / {sum(map(len, tr.values()))} câu")
    det = TextDetector()
    style = TextStyle(family=a.font)
    exporter = None
    if not a.no_photoshop and photoshop_available():
        print("Đang mở Photoshop…")
        exporter = PhotoshopExporter()
    for path in ch.paths:
        t = time.time()
        page = ch.page(path)
        page.inpainter = lama.inpaint if a.engine == "lama" else None
        page.detect(det)
        plan = ch.auto_fill(path, style)
        page.clean_many([r for r, _ in plan])
        out = os.path.join(a.out_dir, os.path.splitext(os.path.basename(path))[0] + ".psd")
        if exporter:
            exporter.export(page, out)
        else:
            export_without_photoshop(page, out)
        print(f"{os.path.basename(path)}: {len(page.regions)} vùng, điền {len(plan)} vùng / "
              f"{len(ch.lines_for(path))} câu → {os.path.basename(out)} ({time.time() - t:.1f}s)")


if __name__ == "__main__":
    main()
