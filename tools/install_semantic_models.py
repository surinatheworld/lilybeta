"""Install pinned local meaning models. No document content is sent anywhere."""
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.paths import model_path
from app.core.semantic_review import MODEL_FILES

SOURCES = {
    "e5": ("intfloat/multilingual-e5-small", "onnx/model_qint8_avx512_vnni.onnx", "onnx/tokenizer.json"),
    "minilm": ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "onnx/model_quint8_avx2.onnx", "tokenizer.json"),
}


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--from-folder", type=Path, help="Copy an existing offline e5/minilm model bundle")
    args = parser.parse_args()
    root = Path(model_path("semantic-review"))
    for name, hashes in MODEL_FILES.items():
        folder = root/name
        folder.mkdir(parents=True,exist_ok=True)
        repo, model, tokenizer = SOURCES[name]
        for filename, remote, expected in zip(("model.onnx","tokenizer.json"),(model,tokenizer),hashes):
            target = folder/filename
            if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()==expected:
                continue
            temporary = target.with_suffix(target.suffix+".part")
            if args.from_folder:
                shutil.copyfile(args.from_folder/name/filename, temporary)
            else:
                with urllib.request.urlopen(f"https://huggingface.co/{repo}/resolve/main/{remote}",timeout=90) as response, temporary.open("wb") as out:
                    shutil.copyfileobj(response,out)
            if hashlib.sha256(temporary.read_bytes()).hexdigest()!=expected:
                temporary.unlink(missing_ok=True)
                raise ValueError(f"SHA-256 không khớp: {name}/{filename}")
            temporary.replace(target)
        print("Installed",name,flush=True)


if __name__=="__main__":
    main()
