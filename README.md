# Lilybeta là bản thử nghiệm trên Photoshop của Lilyzono

Lilybeta giúp dàn bản dịch truyện vào từng bóng thoại trong Photoshop 2023 trên Windows. Bạn đưa vào ảnh gốc (raw), hoặc cả ảnh gốc và bản đã xóa chữ (redraw), cùng bản dịch có sẵn. Kết quả là tài liệu Photoshop có lớp redraw và các lớp chữ chỉnh sửa được. Sau khi cài đặt một lần, mở **File → Scripts → Browse…** và chọn `photoshop/lilybeta.jsx`. Script tự gọi bộ xử lý Python cho các bước nhận diện, xóa chữ và dàn chữ.

## Chuẩn bị bộ xử lý Photoshop từ mã nguồn

Cài Python 3.12 (64-bit) và Photoshop 2023 trên Windows. Tải hoặc clone repo, mở PowerShell tại thư mục chứa `requirements.txt`, rồi chạy một lần để cài thư viện và tải model nhận diện vùng chữ.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "from app.core.detect import ensure_model; print(ensure_model())"
```

Lệnh cuối lưu model ở `models/comictextdetector.pt.onnx`. Giữ nguyên vị trí thư mục `photoshop` và `.venv` để script tìm được bộ xử lý. Chọn font đã cài trên máy hoặc cài các font của bộ mẫu bạn muốn dùng. [Danh sách font và hướng dẫn Photoshop](photoshop/README.md)

### Nếu ghép thoại Nhật–Việt trên máy

1. Tạo thư mục `models/manga-ocr/` và chép vào đó ba tệp `encoder_model.onnx`, `decoder_model.onnx` và `vocab.txt`. [Tải tệp Manga OCR](https://huggingface.co/mayocream/manga-ocr-onnx/tree/main)
2. Chạy hai lệnh sau để cài E5, MiniLM, NLLB và bộ OCR bổ sung. Các model này dùng để đọc câu gốc và ghép với bản dịch Việt bạn cung cấp.

```powershell
.\.venv\Scripts\python.exe tools/install_semantic_models.py
.\.venv\Scripts\python.exe tools/install_translation_models.py
```

Chọn **Không dùng API (xử lý trên máy)** trong **Cấu hình AI…** để xử lý cục bộ. Nếu bật dịch vụ AI bên ngoài, Lilybeta gửi ảnh hoặc nội dung thoại tới nhà cung cấp đã chọn tùy bước xử lý. Trọng số NLLB dùng giấy phép CC BY-NC 4.0, giới hạn ở mục đích phi thương mại. [Nguồn và giấy phép model](docs/model-licenses/local-translation-notice.md)

## Dàn chữ trang đầu tiên

https://github.com/user-attachments/assets/83fb0bda-9f30-4af8-b04e-975901446470

1. Mở ảnh trong Photoshop. Vào **File → Scripts → Browse…**, chọn `photoshop/lilybeta.jsx` để mở bảng điều khiển.
2. Bấm **Lấy ảnh đang mở trong Photoshop**, chọn **Bản Raw** hoặc **Bản Redraw**. Nếu dùng bản redraw, chọn thêm thư mục raw có ảnh gốc tương ứng theo số trang.
3. Bấm **Mở bản dịch** và chọn tệp TXT, DOCX hoặc XLSX. Với TXT hoặc DOCX, đặt số trang trên một dòng riêng, mỗi câu thoại ở một dòng bên dưới. Ví dụ sau dùng cho ảnh `013.png`.

   ```text
   13
   Cậu đến rồi à?
   Ừ, mình đây.
   ```

4. Chạy từng bước để nhận diện, xóa chữ và ghép thoại. Sau **Bước 7**, xem bản gán thử. Với vùng còn chờ duyệt, đối chiếu ảnh gốc, chỉnh câu và bấm **Xác nhận nghĩa vùng…**.
5. Chạy **Bước 8** để tạo các lớp chữ. Bạn có thể sửa trực tiếp trong Photoshop rồi lưu tài liệu dưới dạng PSD.

Giữ **Shift** khi mở script để dùng hộp thoại nhanh. Để xử lý cả chương hoặc chỉnh cách ghép những câu chưa có vùng, xem [hướng dẫn Photoshop](photoshop/README.md) và [hướng dẫn ghép thoại Nhật–Việt](docs/step7-meaning-review.md).

## Kiểm thử

Chạy bộ kiểm thử Python và kiểm thử script Photoshop từ thư mục gốc. Lệnh thứ hai cần Node.js.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
node --test tests/photoshop_script.test.cjs
```
