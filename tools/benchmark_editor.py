"""Offline editor benchmark: python tools/benchmark_editor.py [--output result.json]."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFontDatabase, QImage
from PySide6.QtWidgets import QApplication

from app.core.document import doc_for
from app.core.page import Page, Region
from app.ui.main_window import MainWindow
from app.ui.theme import apply_dark


def sample_page(folder: str, width=1600, height=2400) -> Page:
    # A deterministic textured page; no model download or external document required.
    rng = np.random.default_rng(42)
    image = rng.integers(200, 256, (height, width, 3), dtype=np.uint8)
    path = str(Path(folder) / "benchmark.png")
    cv2.imencode(".png", image)[1].tofile(path)
    page = Page(path)
    page.detected = True
    for i in range(24):
        x, y = 80 + (i % 4) * 380, 80 + (i // 4) * 380
        box = (x, y, x + 240, y + 220)
        page.regions.append(Region(box, text=f"Bản dịch vùng {i + 1}", cleaned=True, manual_rect=box))
    return page


def median_ms(fn, count=9):
    fn()
    values = []
    for _ in range(count):
        start = time.perf_counter()
        fn()
        values.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(values), 3)


def load_offscreen_fonts():
    # Qt's Windows offscreen plugin may not enumerate installed fonts.
    # Register actual fonts explicitly so measurements include real text layout.
    if not QFontDatabase.families() and os.name == "nt":
        folder = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "segoeuib.ttf", "segoeuii.ttf", "arial.ttf", "arialbd.ttf", "ariali.ttf", "seguisym.ttf", "times.ttf"):
            if (folder / name).exists():
                QFontDatabase.addApplicationFont(str(folder / name))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--output")
    parser.add_argument("--preview", help="Save an offscreen screenshot of the editor")
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    load_offscreen_fonts()
    apply_dark(app)
    scratch = Path(__file__).resolve().parents[1] / "build"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=scratch) as folder:
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, folder)
        window = MainWindow()
        page = sample_page(folder)
        window._show_page(page)
        window.select_region(0)
        doc = doc_for(page)
        counter = 0

        def type_text():
            nonlocal counter
            counter += 1
            window.txt.setPlainText(f"Bản dịch tiếng Việt lần {counter}")

        patch = QImage(64, 64, QImage.Format_RGB32)
        patch.fill(0xffaaaaaa)
        result = {
            "page": "1600x2400, 24 text layers",
            "full_render_ms": median_ms(doc.render),
            "type_text_ms": median_ms(type_text),
            "canvas_patch_64_ms": median_ms(lambda: window.view.update_patch(20, 20, patch)),
            "layers_panel_ms": median_ms(lambda: window.layers_panel.set_doc(doc)),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.output:
            Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.preview:
            window.refresh()
            window.show()
            app.processEvents()
            window.view.fit_page()
            window.grab().save(args.preview)
        window.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    main()
