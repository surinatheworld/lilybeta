"""Install local NLLB (CC-BY-NC-4.0) and supplemental OCR weights. No page data is sent."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.paths import model_path
from app.core.translation_weights import FILES, OCR_FILES


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--from-folder", type=Path, help="Offline bundle with translation/ and source-ocr/ folders")
    args = parser.parse_args()
    bundles = [("translation", FILES, "https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8/resolve/main/"),
               ("source-ocr", OCR_FILES, "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/PP-OCRv5/")]
    for name, files, base in bundles:
        folder = Path(model_path(name)); folder.mkdir(parents=True, exist_ok=True)
        for filename, digest in files.items():
            target = folder / filename
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                continue
            part = target.with_suffix(target.suffix + ".part")
            if args.from_folder:
                shutil.copyfile(args.from_folder/name/filename, part)
            else:
                prefix = ("det/" if "_det_" in filename else "rec/") if name == "source-ocr" else ""
                with urllib.request.urlopen(base + prefix + filename, timeout=120) as response, part.open("wb") as out:
                    shutil.copyfileobj(response, out)
            if hashlib.sha256(part.read_bytes()).hexdigest() != digest:
                part.unlink(missing_ok=True)
                raise ValueError("Sai SHA-256: " + filename)
            part.replace(target)
        print("Installed", name, flush=True)


if __name__ == "__main__":
    main()
