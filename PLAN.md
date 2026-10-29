# Kế hoạch dự án: Tool Type Truyện

## 1. Mục tiêu

Ứng dụng desktop giúp typesetter:
1. Mở ảnh raw (một chương = một thư mục ảnh).
2. Nạp bản dịch (file text).
3. Xác định vùng chữ gốc → **xoá chữ gốc và redraw nền** (clean).
4. **Chèn bản dịch** vào đúng vùng đó, dùng **font cài trên máy người dùng**.
5. Chỉnh tay khi cần, rồi xuất ảnh đã type.

## 2. Công nghệ đề xuất

| Phần | Lựa chọn | Lý do |
|---|---|---|
| Ngôn ngữ | Python 3.12 (đã có trên máy) | Hệ sinh thái xử lý ảnh/AI mạnh |
| Giao diện | PySide6 (Qt) | Canvas kéo-thả tốt, đọc font hệ thống sẵn qua `QFontDatabase`, render chữ tiếng Việt chuẩn |
| Xử lý ảnh | OpenCV + NumPy + Pillow | Mask, inpaint, xuất ảnh |
| Phát hiện chữ | comic-text-detector (ONNX) | Ra cả khung bóng thoại lẫn mask từng nét chữ |
| Redraw nền | Bóng trắng: tô màu nền; nền phức tạp: LaMa (ONNX), dự phòng `cv2.inpaint` | LaMa vẽ lại nền texture/tranh rất tốt, chạy được bằng CPU |
| Lưu dự án | JSON | Lưu vị trí, font, cỡ chữ từng bóng để mở lại sửa |

## 3. Luồng làm việc

```
Mở thư mục raw ──► Phát hiện vùng chữ (auto) ──► Người dùng sửa/thêm/xoá vùng
       │
       ▼
Nạp bản dịch ──► Ghép câu dịch ↔ vùng chữ theo thứ tự đọc
       │
       ▼
Clean (xoá chữ gốc + redraw nền) ──► Type chữ (font, tự co cỡ, xuống dòng, căn giữa)
       │
       ▼
Chỉnh tay (kéo, đổi font, viền chữ, xoay) ──► Xuất PNG/JPG
```

### Định dạng file bản dịch (đề xuất)

```
[001.jpg]
Câu thoại bóng 1
Câu thoại bóng 2
// dòng ghi chú, bỏ qua
[002.jpg]
...
```
Dùng `\n` hoặc `|` trong câu để ép xuống dòng. Câu bắt đầu bằng tiền tố (vd `#sfx`, `#nghĩ`) sẽ áp style tương ứng.

## 4. Kiến trúc thư mục

```
project
├─ app/
│  ├─ core/            # không phụ thuộc UI, test được độc lập
│  │  ├─ fonts.py      # quét font hệ thống (C:\Windows\Fonts + font cài cho user), kiểm tra hỗ trợ dấu tiếng Việt
│  │  ├─ detect.py     # phát hiện bóng thoại + mask chữ
│  │  ├─ order.py      # sắp xếp thứ tự đọc (manga phải→trái / manhwa trái→phải)
│  │  ├─ script.py     # đọc file bản dịch, ghép với vùng chữ
│  │  ├─ clean.py      # xoá chữ + redraw nền (fill / inpaint / LaMa)
│  │  ├─ typeset.py    # word-wrap, tự co cỡ chữ vừa bóng, viền chữ, xoay
│  │  └─ project.py    # lưu/mở dự án JSON
│  ├─ ui/              # PySide6: cửa sổ chính, canvas, panel font, danh sách trang
│  └─ main.py
├─ models/             # file ONNX (tải lần đầu chạy)
├─ tests/
└─ requirements.txt
```

## 5. Các giai đoạn

### Giai đoạn 1 – MVP (làm tay, chạy được ngay)
- Mở thư mục ảnh, xem từng trang, zoom/pan.
- Danh sách font hệ thống, có ô tìm kiếm + xem trước, đánh dấu font không đủ dấu tiếng Việt.
- Kéo chuột vẽ vùng chữ → nút **Clean** (tô màu nền tự lấy mẫu) → gõ/dán bản dịch vào vùng.
- Tự xuống dòng, tự co cỡ chữ cho vừa, căn giữa; chỉnh font/cỡ/màu/viền.
- Xuất ảnh.

### Giai đoạn 2 – Tự động
- Phát hiện vùng chữ tự động + mask nét chữ.
- Nạp file bản dịch, tự ghép theo thứ tự đọc, có màn hình kiểm tra ghép sai để kéo thả đổi thứ tự.
- Chạy hàng loạt cả chương.

### Giai đoạn 3 – Chất lượng cao
- Redraw bằng LaMa cho chữ đè lên tranh / SFX.
- Preset style (thoại thường, suy nghĩ, hét, narration, SFX) gắn với font riêng.
- Undo/redo, lưu/mở dự án, xuất thêm layer chữ tách riêng (PNG trong suốt) để chỉnh trong Photoshop.

## 6. Rủi ro & cách xử lý
- **Font thiếu dấu tiếng Việt** → kiểm tra glyph trước khi dùng, cảnh báo + font dự phòng.
- **Phát hiện sai vùng chữ** → luôn cho sửa tay; auto chỉ là bước gợi ý.
- **Ghép sai câu dịch ↔ bóng** → đánh số bóng trên ảnh, cho kéo thả đổi thứ tự.
- **Model AI nặng** → chạy ONNX trên CPU, GPU là tuỳ chọn; tải model lần đầu.
