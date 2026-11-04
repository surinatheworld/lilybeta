# Balloon detection references

| Nguồn | Commit đã đọc | Giấy phép trong repo | Phần hữu ích |
|---|---|---|---|
| [meangrinch/MangaTranslator](https://github.com/meangrinch/MangaTranslator) | `e347a09e024d11702441dd3637f044fdd8990ccb` | Apache-2.0 | `core/image/detection.py`: parent/child mask, seed theo vùng chữ, chia phần chồng theo cách bố trí và chọn đường cắt không đi qua chữ |
| [Ashu11-A/Manga-Segment](https://github.com/Ashu11-A/Manga-Segment) | `2c9776722f071949c7454fa54197dc32a94b25e5` | AGPL-3.0 | `src/algorithms/yolo/segmenter.py`: giữ mask từng instance cùng nhãn, thay vì coi mọi điểm trắng là một bóng; tài liệu có lớp speech/thought/caption/text |
| [igun997/manga-translator](https://github.com/igun997/manga-translator) | `1397af38e3464cdf4931c705857b8bef015030dc` | MIT; model được ghi Apache-2.0 | `balloon_detector.py`: kết hợp hình học với phân đoạn; mask mỗi bóng được thu vào để tránh đọc lẫn chữ hàng xóm |
