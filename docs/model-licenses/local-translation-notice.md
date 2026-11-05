# Local translation and supplemental source OCR

The optional weights are downloaded separately, are unmodified, and are not included in this repository.

* **NLLB-200 distilled 600M** — Meta AI, [original model card](https://huggingface.co/facebook/nllb-200-distilled-600M),
  licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/).
  The pinned int8 CTranslate2 conversion is distributed by
  [JustFrederik](https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8).
  These weights are restricted to noncommercial use under that license. Lilyzono's code license does not replace it.
* **PP-OCRv5 detection and recognition** — PaddlePaddle/PaddleOCR contributors,
  [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR), Apache License 2.0.
  The unmodified ONNX distributions come from [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR)
  via its pinned v3.10.0 static model distribution. The source OCR adapter runs them directly with ONNX Runtime;
  the RapidOCR Python package and its download machinery are not required at runtime.

The installer validates the exact SHA-256 values in `app/core/translation_weights.py`.
Runtime inference never downloads weights or transmits page images, Japanese text, or translations.
