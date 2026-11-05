"""In-process NLLB translation evidence. No network or service client is used."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from .paths import model_path

class Translator:
    def __init__(self, folder=None):
        import ctranslate2
        import sentencepiece

        root = Path(folder or model_path("translation"))
        from .translation_weights import FILES
        for filename, expected in FILES.items():
            path = root / filename
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Thiếu/sai model dịch cục bộ; chạy tools/install_translation_models.py: " + filename)
        self.sp = sentencepiece.SentencePieceProcessor(model_file=str(root / "sentencepiece.bpe.model"))
        self.model = ctranslate2.Translator(str(root), device="cpu", compute_type="int8", intra_threads=4)
        self.cache, self.translations = {}, {}

    def tokens(self, text, lang, target=False):
        pieces = self.sp.encode(text, out_type=str)
        return [lang] + pieces + ["</s>"] if target else pieces + ["</s>", lang]

    def translate(self, texts, source="jpn_Jpan", target="vie_Latn"):
        missing = list(dict.fromkeys(t for t in texts if (source, target, t) not in self.translations))
        if missing:
            results = self.model.translate_batch([self.tokens(t, source) for t in missing],
                        target_prefix=[[target] for _ in missing], beam_size=2,
                        max_decoding_length=120, no_repeat_ngram_size=3, max_batch_size=8)
            for text, result in zip(missing, results):
                self.translations[source, target, text] = self.sp.decode(result.hypotheses[0][1:])
        return [self.translations[source, target, t] for t in texts]

    def evidence(self, japanese, vietnamese, selected=None):
        """Actual supplied translations scored in both directions, not generated answers."""
        pairs = [(ja, vi) for ja in japanese for vi in vietnamese]
        wanted = pairs if selected is None else [pairs[i*len(vietnamese)+j] for j,i in zip(*np.nonzero(selected))]
        missing = list(dict.fromkeys(p for p in wanted if p not in self.cache))
        for start in range(0, len(missing), 96):
            batch = missing[start:start+96]
            forward = self.model.score_batch([self.tokens(a, "jpn_Jpan") for a,b in batch],
                          [self.tokens(b, "vie_Latn", True) for a,b in batch], max_batch_size=12, offset=1)
            reverse = self.model.score_batch([self.tokens(b, "vie_Latn") for a,b in batch],
                          [self.tokens(a, "jpn_Jpan", True) for a,b in batch], max_batch_size=12, offset=1)
            for pair, f, r in zip(batch, forward, reverse):
                self.cache[pair] = (float(np.mean(f.log_probs)), float(np.mean(r.log_probs)))
        values = np.asarray([self.cache.get(p,(-30.,-30.)) for p in pairs]).reshape(len(japanese), len(vietnamese), 2)
        if selected is not None:
            values=np.where(selected.T[...,None],values,-30.)
        return values.transpose(1, 0, 2)


_TRANSLATOR = None


def translator():
    global _TRANSLATOR
    if _TRANSLATOR is None:
        _TRANSLATOR = Translator()
    return _TRANSLATOR
