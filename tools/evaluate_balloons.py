"""Offline, read-only evaluation of step-2 balloon geometry; writes annotated review images."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.balloons import refine
from app.core.detect import TextBox, TextDetector, sort_reading_order
from app.core.workflow import heuristic_bubble


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache", type=Path, help="Cache detector output separately, for repeatable geometry-only comparisons")
    parser.add_argument("--annotations", type=Path, help="JSON text anchors: pairs must separate; single groups must stay together")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    detector = None
    records = []
    annotations = json.loads(args.annotations.read_text(encoding="utf-8"))["pages"] if args.annotations else {}
    failures = []
    for i, path in enumerate(args.images):
        image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        cache = args.cache/f"{i+1:02d}-{path.stem}.npz" if args.cache else None
        stat = path.stat()
        if cache and cache.exists():
            with np.load(cache,allow_pickle=False) as data:
                if int(data["mtime"]) != stat.st_mtime_ns or int(data["bytes"]) != stat.st_size:
                    raise ValueError("Input changed: use a fresh detector cache")
                mask = data["mask"].astype(np.float32)
                boxes = [TextBox(*map(int,row[:4]),float(row[4]),"sfx" if row[5] else "text") for row in data["boxes"]]
        else:
            detector = detector or TextDetector()
            boxes, mask = detector.detect(image)
            if cache:
                cache.parent.mkdir(parents=True,exist_ok=True)
                np.savez_compressed(cache,mask=mask,mtime=stat.st_mtime_ns,bytes=stat.st_size,
                                    boxes=np.asarray([[b.x1,b.y1,b.x2,b.y2,b.score,int(b.kind=="sfx")] for b in boxes]))
        started = time.perf_counter()
        refined, evidence = refine(image, mask, boxes)
        elapsed = time.perf_counter() - started
        before, after = image.copy(), image.copy()
        changes = []
        for b in boxes:
            cv2.rectangle(before, (b.x1,b.y1),(b.x2,b.y2),(0,100,255),2)
        for j, b in enumerate(sort_reading_order(refined, True, image), 1):
            key = (b.x1,b.y1,b.x2,b.y2)
            meta = evidence.get(key)
            color = (0,180,0) if meta else (0,100,255)
            cv2.rectangle(after, (b.x1,b.y1),(b.x2,b.y2),color,2)
            cv2.putText(after, str(j), (b.x1,max(15,b.y1-5)),cv2.FONT_HERSHEY_SIMPLEX,.55,color,2)
            if meta:
                if meta["outline"]:
                    cv2.polylines(after, [np.asarray(meta["outline"],np.int32)],True,color,2)
                changes.append({"box":list(key), "before":heuristic_bubble(image,mask,key,b.kind),
                                "shape":meta["shape"], "part":meta["part"], "parts":meta["parts"],
                                "split":meta["split"], "uncertain":meta["uncertain"]})
        panel = np.concatenate([before,after],axis=1)
        scale = min(1,1800/panel.shape[1])
        panel = cv2.resize(panel,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
        cv2.imencode(".jpg",panel,[cv2.IMWRITE_JPEG_QUALITY,92])[1].tofile(args.output/f"{i+1:02d}-{path.stem}.jpg")
        row = {"image":str(path), "before":len(boxes), "after":len(refined), "geometry_ms":round(elapsed*1000,1),
               "evidence":changes}
        # Check all predicted lettering inside regular detector boxes. A count of
        # boxes alone would miss clipped edge glyphs after a geometrical split.
        expected = np.zeros(mask.shape, bool)
        actual = np.zeros(mask.shape, bool)
        for b in boxes:
            if b.kind != "sfx":
                expected[b.y1:b.y2,b.x1:b.x2] = True
        for b in refined:
            actual[b.y1:b.y2,b.x1:b.x2] = True
        expected &= mask > .3
        row["uncovered_text_pixels"] = int(np.count_nonzero(expected & ~actual))
        if row["uncovered_text_pixels"]:
            failures.append(f"{path.name}: lost predicted text pixels")
        cut_components = 0
        for b in boxes:
            if b.kind == "sfx":
                continue
            _, _, stats, _ = cv2.connectedComponentsWithStats(
                (mask[b.y1:b.y2,b.x1:b.x2] > .3).astype(np.uint8))
            for x,y,w,h,area in stats[1:]:
                if area >= 8 and not any(c.x1<=b.x1+x and c.y1<=b.y1+y
                                        and c.x2>=b.x1+x+w and c.y2>=b.y1+y+h for c in refined):
                    cut_components += 1
        row["cut_text_components"] = cut_components
        if cut_components:
            failures.append(f"{path.name}: cut connected lettering components")
        marked = annotations.get(path.name)
        if marked:
            if marked["size"] != [image.shape[1],image.shape[0]]:
                raise ValueError(f"Annotation size does not match {path}")
            checks = []
            for category in ("pairs", "single"):
                for group in marked.get(category, []):
                    hits = [{j for j,b in enumerate(refined) if b.kind != "sfx"
                             and b.x1<=x<b.x2 and b.y1<=y<b.y2} for x,y in group["points"]]
                    present = all(hits)
                    valid = present and (not set.intersection(*hits) if category=="pairs"
                                         else bool(set.intersection(*hits)))
                    checks.append({"name":group["name"],"category":category,
                                   "hits":[sorted(h) for h in hits],"passed":bool(valid)})
                    if not valid:
                        failures.append(f"{path.name}: {group['name']}")
            row["checks"] = checks
        records.append(row)
        print(json.dumps({k:v for k,v in row.items() if k not in ("evidence","checks")} | {"lobes":len(changes)},ensure_ascii=True),flush=True)
    (args.output/"report.json").write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding="utf-8")
    if detector:
        detector.release()
    if args.annotations:
        missing = set(annotations) - {p.name for p in args.images}
        failures.extend(f"Not evaluated: {name}" for name in sorted(missing))
    summary = {"pages":len(records), "checks":sum(len(r.get("checks",[])) for r in records),
               "passed":sum(c["passed"] for r in records for c in r.get("checks",[])),
               "uncovered_text_pixels":sum(r["uncovered_text_pixels"] for r in records),
               "cut_text_components":sum(r["cut_text_components"] for r in records),
               "failures":failures}
    (args.output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(summary,ensure_ascii=True))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
