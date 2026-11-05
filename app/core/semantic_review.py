"""Local Japanese/Vietnamese meaning checks and a source-bound typesetting gate.

Cosine similarities are model scores, not probabilities of a correct translation.
Short, missing or ambiguous text requires explicit review rather than a guess.
This module never downloads models or calls an inference service.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import numpy as np

from .docs import TAG, translation_units
from .ocr import normalize
from .paths import model_path

VERSION = 3
MODEL_FILES = {
    "e5": ("dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88",
           "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"),
    "minilm": ("98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f",
               "2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8"),
}


class LocalMeaningModels:
    def __init__(self, folder=None):
        from tokenizers import Tokenizer
        import onnxruntime as ort

        root = Path(folder or model_path("semantic-review"))
        self.models, self.cache = {}, {}
        for name, hashes in MODEL_FILES.items():
            files = [root/name/f for f in ("model.onnx", "tokenizer.json")]
            for path, expected in zip(files, hashes):
                if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError(f"Model đối chiếu nghĩa sai SHA-256: {path.name}")
            opt = ort.SessionOptions()
            opt.intra_op_num_threads, opt.inter_op_num_threads = 4, 1
            session = ort.InferenceSession(str(files[0]), sess_options=opt, providers=["CPUExecutionProvider"])
            tokenizer = Tokenizer.from_file(str(files[1]))
            tokenizer.enable_truncation(max_length=256)
            tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
            self.models[name] = (session, tokenizer)
            self.cache[name] = {}

    def _embed(self, name, texts):
        session, tokenizer = self.models[name]
        cache = self.cache[name]
        if len(cache) > 4096:
            cache.clear()
        missing = list(dict.fromkeys(t for t in texts if t not in cache))
        for start in range(0, len(missing), 12):
            group = missing[start:start+12]
            encoded = tokenizer.encode_batch([("query: " if name=="e5" else "")+t for t in group])
            ids = np.asarray([e.ids for e in encoded], np.int64)
            mask = np.asarray([e.attention_mask for e in encoded], np.int64)
            feed = {"input_ids":ids, "attention_mask":mask, "token_type_ids":np.zeros_like(ids)}
            hidden = session.run(None, {i.name:feed[i.name] for i in session.get_inputs()})[0]
            pooled = (hidden*mask[..., None]).sum(1)/np.maximum(1, mask.sum(1)[:, None])
            pooled /= np.maximum(1e-9, np.linalg.norm(pooled, axis=1, keepdims=True))
            cache.update(zip(group, pooled))
        return np.asarray([cache[t] for t in texts])

    def compare(self, japanese, vietnamese):
        return {name:self._embed(name, vietnamese) @ self._embed(name, japanese).T for name in self.models}


_MODELS = None


def local_models():
    global _MODELS
    if _MODELS is None:
        _MODELS = LocalMeaningModels()
    return _MODELS


def _signature(project, page, region):
    image = project.file(page, "raw.png" if project.has(page, "raw.png") else "work.png")
    stamp = hashlib.sha256(image.read_bytes()).hexdigest() if image.exists() else "missing"
    value = {"version":VERSION, "source":project.data["source"], "image":stamp, "inputs":project.page_lines(page),
             "context":[[r["id"], r["box"], r.get("ja", ""), r["bubble"],r["lines"],
                         r.get("ja_variants",[]),r.get("balloon",{}),r.get('source_ocr_confidence')] for r in page["regions"]],
             "units":page.get("assignment_units",[]),
             "id":region["id"], "ja":region.get("ja", ""), "box":region["box"], "lines":region["lines"]}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def current_review(project, page, region):
    review = region.get("meaning_review")
    if isinstance(review, dict) and review.get("signature") == _signature(project, page, region):
        return review
    return None


def confirm_region(project, page, region):
    """Called only after the user explicitly confirms the displayed Japanese/VI pair."""
    if not region["lines"] or region["bubble"] == "skip":
        raise ValueError("Chọn vùng có câu dịch để xác nhận đối chiếu.")
    region["meaning_review"] = {"state":"confirmed", "method":"human", "signature":_signature(project,page,region),
                                "reason":"Người dùng đã đối chiếu Nhật–Việt của vùng này"}
    project.save()


def _unreliable_japanese(text):
    chars = normalize(text)
    return not chars or (len(chars)>=12 and Counter(chars).most_common(1)[0][1] > .65*len(chars))


def valid_provenance(project, page):
    """Check exact original character coverage plus the actual current region contents."""
    units=page.get("assignment_units")
    if not units:return False
    inputs=project.page_lines(page); expected={}; seen=set()
    for unit in units:
        i=unit.get("index")
        if not isinstance(i,int) or i<0 or i>=len(inputs) or i in seen or unit.get("text")!=inputs[i]:return False
        seen.add(i); end=0
        for part in unit.get("parts",[]):
            start,stop=part.get("start"),part.get("end")
            if start!=end or not isinstance(stop,int) or stop<=start or stop>len(inputs[i]):return False
            if part.get("text")!=inputs[i][start:stop]:return False
            expected.setdefault(part.get("id"),[]).append(part["text"]);end=stop
        if end!=len(inputs[i]):return False
    rows={r['id']:r for r in page['regions']}
    if not expected.keys()<=rows.keys():return False
    if any(rows[rid]['bubble']=='skip' or rows[rid]['lines']!=texts for rid,texts in expected.items()):return False
    actual={r['id']:r['lines'] for r in page['regions'] if r['bubble']!='skip' and r['lines_src']!='manual' and r['lines']}
    return {rid:texts for rid,texts in expected.items() if rows[rid]['lines_src']!='manual'}==actual


def review_local_assignments(project, page):
    from .semantic_match import VERSION as MATCH_VERSION
    from .match_language import lexical
    source_present=project.has(page,'raw.png') or (project.data['source']!='redraw' and project.has(page,'work.png'))
    valid=source_present and valid_provenance(project,page)
    for region in page['regions']:
        if not region['lines'] or region['lines_src']=='manual':continue
        units=[u for u in page.get('assignment_units',[]) if any(p['id']==region['id'] for p in u['parts'])]
        checks=[]
        for unit in units:
            evidence=unit.get('evidence') or {}; score=evidence.get('score',-99); margin=evidence.get('margin',-99)
            exact=lexical(unit['text'],region.get('ja',''))>=6
            good=bool(valid and evidence.get('version')==MATCH_VERSION and
                      region.get('source_ocr_confidence',1.)>=.8 and
                      (exact or (score>=.8 and margin>=.3 and not _unreliable_japanese(evidence.get('ja','')))))
            checks.append({'text':unit['text'],'state':'passed' if good else 'review','models':evidence,
                           'reason':'Khớp chữ/SFX' if exact else 'So nghĩa hai chiều và ngữ cảnh bóng', 'candidates':[]})
        passed=bool(checks) and all(c['state']=='passed' for c in checks)
        region['meaning_review']={'signature':_signature(project,page,region),'state':'passed' if passed else 'review',
                                  'method':'local-bidirectional','checks':checks,
                                  'reason':'Nghĩa Nhật–Việt phù hợp' if passed else 'Chưa đủ bằng chứng nghĩa; xem ảnh gán thử'}
        region['uncertain']=not passed
    project.save()


def verify_page(project, page, report=None, models=None):
    """Review the actual assigned strings against Japanese, never against a copied VI answer.

    Both independent encoders must agree on a clearly preferred region for every
    translation unit. Low scores, short replies and missing OCR remain pending.
    """
    regions = [r for r in page["regions"] if r["bubble"] != "skip"]
    pending = [r for r in regions if r["lines"] and
               (current_review(project,page,r) or {}).get("state") not in ("passed","confirmed")]
    japanese = [r.get("ja", "") for r in regions]
    texts = list(dict.fromkeys(TAG.sub("", s).strip() for r in pending for s in translation_units(r["lines"])))
    scores, error = {}, ""
    if pending and texts:
        if report:
            report("Đối chiếu nghĩa Nhật–Việt trên máy trước khi typeset…")
        try:
            if not project.has(page,"raw.png") and (project.data["source"]=="redraw" or not project.has(page,"work.png")):
                raise ValueError("Thiếu ảnh gốc Raw để đối chiếu")
            scores = (models or local_models()).compare(japanese, texts)
            if set(scores) != set(MODEL_FILES) or any(
                    np.asarray(v).shape != (len(texts),len(regions)) or not np.isfinite(v).all() for v in scores.values()):
                raise ValueError("Đầu ra model đối chiếu không hợp lệ")
        except Exception as exc:  # ONNX runtime errors must also leave review pending.
            error = "Chưa chạy được model đối chiếu cục bộ; cài model hoặc xác nhận sau khi xem ảnh. " + str(exc)[:180]
    for region in pending:
        i = regions.index(region)
        checks = []
        for unit in translation_units(region["lines"]):
            text = TAG.sub("", unit).strip()
            check = {"text":text, "state":"review", "reason":"", "models":{}, "candidates":[]}
            if _unreliable_japanese(japanese[i]):
                check["reason"] = "Thiếu chữ Nhật đáng tin cậy hoặc OCR lặp nét trang trí"
            elif len(normalize(japanese[i])) < 6 or len(re.findall(r"\w+",text)) < 4:
                check["reason"] = "Câu ngắn/SFX cần đối chiếu ngữ cảnh và xác nhận"
            elif error:
                check["reason"] = error
            elif not scores:
                check["reason"] = "Chưa có kết quả đối chiếu nghĩa"
            else:
                k = texts.index(text)
                agreed = True
                candidates = {}
                for name, matrix in scores.items():
                    row = np.asarray(matrix[k])
                    order = np.argsort(-row)
                    other = max([float(v) for j,v in enumerate(row) if j!=i] or [1.])
                    value, margin = float(row[i]), float(row[i])-other
                    good = int(order[0])==i and value >= (.85 if name=="e5" else .78) and margin >= .08
                    agreed &= good
                    check["models"][name] = {"score":round(value,4),"margin":round(margin,4),"agreed":bool(good)}
                    for rank in order[:3]:
                        candidates.setdefault(regions[rank]["id"],[]).append(float(row[rank]))
                check["candidates"] = [{"id":rid,"ja":next(r["ja"] for r in regions if r["id"]==rid)} for rid in candidates]
                check.update(state="passed" if agreed else "review",
                             reason="Hai model đồng ý với vùng theo nghĩa" if agreed else
                             "Model chưa đồng ý rõ với nghĩa của vùng đã gán; kiểm tra các vùng gợi ý")
            checks.append(check)
        passed = bool(checks) and all(c["state"]=="passed" for c in checks)
        region["meaning_review"] = {"signature":_signature(project,page,region), "state":"passed" if passed else "review",
                                    "method":"local-meaning", "reason":"; ".join(dict.fromkeys(c["reason"] for c in checks)),
                                    "checks":checks}
        if not passed:
            region["uncertain"] = True
    project.save()
    return [r["id"] for r in regions if r["lines"] and (current_review(project,page,r) or {}).get("state") not in ("passed","confirmed")]


def require_verified(project, page, report=None):
    pending = verify_page(project,page,report)
    problems = []
    if pending:
        problems.append("chưa xác minh nghĩa: " + ", ".join(pending))
    if page.get("unplaced"):
        problems.append(f"{len(page['unplaced'])} câu chưa có vùng")
    expected = translation_units(project.page_lines(page))
    actual = translation_units([s for r in page["regions"] if r["bubble"]!="skip" for s in r["lines"]])
    conserved=Counter(expected)==Counter(actual)
    if not conserved and valid_provenance(project,page):
        used={u['index'] for u in page['assignment_units']}
        covered={p['id'] for u in page['assignment_units'] for p in u['parts']}
        manual=translation_units([s for r in page['regions'] if r['lines_src']=='manual' and r['bubble']!='skip' and r['id'] not in covered for s in r['lines']])
        conserved=Counter(inputs for i,inputs in enumerate(project.page_lines(page)) if i not in used)==Counter(manual)
    if expected and not conserved:
        problems.append("câu đã gán không khớp danh sách đầu vào (thiếu, trùng hoặc còn bản cũ)")
    if problems:
        raise ValueError("Dừng trước typeset — " + "; ".join(problems) +
                         ". Xem cột Đối chiếu nghĩa, sửa/gán lại câu và xác nhận từng vùng sau khi đối chiếu Nhật–Việt.")
