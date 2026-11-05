"""Japanese OCR preparation and source-bound Vietnamese references for step 7."""
from __future__ import annotations

from collections import Counter
import math
import re
import unicodedata


def reference(region: dict) -> dict | None:
    """A reference is reusable only for the same Japanese text and text rectangle."""
    ref = region.get("vi_ref")
    if (isinstance(ref, dict) and ref.get("source_ja") == region.get("ja", "")
            and ref.get("box") == list(region["box"]) and isinstance(ref.get("text"), str)
            and ref["text"].strip() and isinstance(ref.get("confidence"), (int, float))
            and math.isfinite(ref["confidence"]) and 0 <= ref["confidence"] <= 1):
        return ref
    return None


def read_missing(project, page, regions, image, report=None) -> list[str]:
    """Read only missing Japanese; never OCR a redraw without its original raw."""
    todo = [r for r in regions if r.get("ja_src") != "manual" and
            (not r.get("ja", "").strip() or
             (r.get("vi_ref") and r["vi_ref"].get("box") != list(r["box"])))]
    if not todo:
        return []
    cleared = False
    for r in todo:
        if r.get("ja", "").strip():
            r["ja"], r["ja_src"] = "", ""  # automatic reading belongs to the old box
            cleared = True
    if cleared:
        project.save()
    if project.data["source"] == "redraw" and not project.has(page, "raw.png"):
        return ["Bước 7: bản redraw thiếu Raw; nạp Raw ở bước 4 để đọc đúng tiếng Nhật."]
    from .ocr import reader

    read = reader()
    if read is None:
        return ["Bước 7: thiếu model OCR tiếng Nhật (models/manga-ocr); chưa tự đọc được chữ còn trống."]
    warnings = []
    for i, r in enumerate(todo, 1):
        if report:
            report(f"Bước 7 · đọc tiếng Nhật {r['id']} ({i}/{len(todo)})…")
        text = read.read(image, r["box"]).strip()
        if text:
            r["ja"], r["ja_src"] = text, "ocr"
        else:
            warnings.append(f"{r['id']}: chưa đọc được tiếng Nhật; kiểm tra Raw hoặc sửa cột chữ Nhật.")
    project.save()  # Preserve OCR even if the following AI request fails.
    return warnings


def save_references(regions, readings):
    """Save AI references separately from the user's typeset translations."""
    from .ocr import coverage, normalize

    for r in regions:
        item = readings.get(r["id"])
        if not item:
            continue
        # Vision can fill missing OCR, but cannot replace manually entered text.
        if not r.get("ja", "").strip() and r.get("ja_src") != "manual" and item["ja"]:
            r["ja"], r["ja_src"] = item["ja"], "ai"
        if not r.get("ja", "").strip() or not item["vi"]:
            continue
        confidence, note = item["confidence"], item["note"]
        a, b = normalize(r["ja"]), normalize(item["ja"])
        if a and b and a != b and (r.get("ja_src") == "manual"
                                   or min(coverage(a,b), coverage(b,a)) < .65):
            confidence = min(confidence, .5)
            note = "Bản Nhật AI đọc khác chữ Nhật của vùng; cần kiểm tra. " + note
        r["vi_ref"] = {"source_ja":r["ja"], "box":list(r["box"]), "text":item["vi"],
                       "confidence":confidence, "note":note, "read_ja":item["ja"]}


def _normalized(text):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def reference_anchors(regions, lines):
    """Reuse unique exact Vietnamese matches offline; paraphrases require an AI model.

    Repeated short utterances are deliberately left to the contextual matcher.
    Style/source tags in imported scripts are metadata, not translated content.
    """
    from .docs import TAG

    texts = [_normalized(TAG.sub("", s)) for s in lines]
    counts = Counter(texts)
    targets = {}
    for r in regions:
        ref = reference(r)
        if ref and ref.get("confidence", 0) >= .7:
            targets.setdefault(_normalized(ref["text"]), []).append((r["id"], ref["confidence"]))
    return {i:(targets[s][0][0], targets[s][0][1], "Khớp bản Việt tham chiếu theo nội dung")
            for i,s in enumerate(texts) if s and counts[s] == 1 and len(targets.get(s, [])) == 1}
