# Lilyzono trong Photoshop 2023 (Windows)

Tạo một nhóm **Lilyzono · Typeset Gái Băng** ngay trong tài liệu đang mở:

- **Redraw**: layer ảnh trong suốt, chỉ chứa những điểm đã xoá/vẽ lại.
- Mỗi câu thoại/SFX là **layer chữ Photoshop thật**, sửa được nội dung, font và vị trí.
- Giữ các layer, kích thước và độ phân giải của tài liệu; Undo một lần để bỏ nhóm vừa tạo.

## Chạy

1. Mở ảnh trong Photoshop.
2. **File → Scripts → Browse…**, chọn `Lilyzono.jsx` trong thư mục này.
3. Dán bản dịch, **mỗi câu một dòng theo thứ tự đọc**, không thêm số thứ tự. Có thể mở file `.txt` UTF-8.
4. Chọn LaMa / OpenCV / chỉ nền đơn giản, bấm **Nhận diện và xem trước**.
5. Kiểm tra ảnh và danh sách thoại, bấm **Tạo layer trong Photoshop**. Sửa/căn lại chữ trực tiếp trong Photoshop nếu cần.
6. **Ctrl+S** để lưu PSD theo cách thông thường của Photoshop.

Có thể dùng Actions để gán phím tắt cho lần chạy script bằng Browse. Giữ file JSX cạnh bộ xử lý,
hoặc trong thư mục `photoshop` của repo khi chạy từ mã nguồn.

## Bộ xử lý

- Từ mã nguồn: giữ cấu trúc repo, cài môi trường bằng `run.bat`. JSX dùng `.venv/Scripts/pythonw.exe` của dự án.
- Bản độc lập: chạy `build_photoshop.bat`, gửi cả thư mục `dist/LilyzonoPhotoshop`. Không cần Python trên máy dùng.
- Cần Windows Script Host để khởi động bộ xử lý ẩn. Tool không thay đổi thiết lập bảo mật hay cần mở máy chủ HTTP.
- Model ONNX nằm trong thư mục `models` cạnh bộ xử lý. Xử lý trên máy, không gửi ảnh/bản dịch qua mạng.

Cần cài các font: `000 WildWords2 TB`, `000 AnimeAce3 [TeddyBear]`, `00 CE Scriptorama Markdown JF`, `MTO augie`.
Font suy nghĩ dùng kiểu nghiêng. Tool không phân phối các font này.

Nhận dạng và ghép thoại là tự động, có thể sai với bố cục khó. Luôn xem trước, đặc biệt SFX và chữ ngoài khung.
LaMa có thể làm mờ nét/lưới chấm quanh SFX; sửa trên layer Redraw. Chế độ chỉ nền đơn giản đánh dấu `REDRAW` ở các layer cần làm tay.
Chỉ xoá những vùng có bản dịch; các SFX chưa ghép và chi tiết bị nhận nhầm được giữ nguyên.

## Kiểm thử tích hợp ngầm

`tools/test_photoshop_integration.py` dùng API scripting của Photoshop đang chạy, không dùng chuột/bàn phím.
Chỉ mở, sửa và đóng bản sao tạm của ảnh kiểm thử; khôi phục tài liệu đang chọn và thiết lập đơn vị/hộp thoại.
Kiểm tra layer chữ thật, Undo một bước, rollback khi lỗi, font/kiểu nghiêng và độ phân giải 72/300 ppi.

```powershell
.venv\Scripts\python tools/test_photoshop_integration.py --image 013.jpg --translation translation.txt --output output/ps-test
```

Thêm `--script-folder dist/LilyzonoPhotoshop` để kiểm tra bộ xử lý đã đóng gói thay vì Python của repo.
Ảnh xem trước PNG, PSD có layer và báo cáo JSON được ghi vào thư mục output đã chọn.
