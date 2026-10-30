# Lilyzono trong Photoshop 2023 (Windows)

Tạo một nhóm **Lilyzono · Typeset Gái Băng** ngay trong tài liệu đang mở:

- **Redraw**: layer ảnh trong suốt, chỉ chứa những điểm đã xoá chữ.
- Mỗi câu thoại/SFX là **layer chữ Photoshop thật**, sửa được nội dung, font và vị trí.
- Giữ các layer, kích thước và độ phân giải của tài liệu; Undo một lần để bỏ nhóm vừa tạo.

**Luật xoá chữ:** chỉ xoá chỗ dễ – chữ trong bóng thoại / hộp sáng khép kín và nền trơn một màu. Không vẽ lại tranh,
lưới chấm, nền chuyển màu; **SFX không xoá**, chữ dịch đặt đè lên bản gốc. Những layer chữ này có tên bắt đầu bằng
`REDRAW · ` – redraw tay, hoặc dùng **Upload bản redraw SFX**.

**Căn chữ:** chữ dàn theo đúng hình lòng bóng và đặt chính giữa thân bóng; tự xuống dòng cho khối chữ bo tròn (dòng
giữa dài, trên/dưới ngắn – vd 1·2·3·2·1 từ), khung ô vuông thì các dòng dài đều; không cắt đôi từ ghép ("cảm ơn",
"trân trọng"…). Mỗi dòng là một lần xuống dòng thật trong layer chữ, sửa tay trong Photoshop như thường.

## Chạy

1. Mở ảnh trong Photoshop.
2. **File → Scripts → Browse…**, chọn `Lilyzono.jsx` trong thư mục này.
3. Dán bản dịch, **mỗi câu một dòng theo thứ tự đọc**, không thêm số thứ tự; hoặc **Mở bản dịch (.txt / .docx)**
   (file chia theo trang: dòng chỉ có số trang, các dòng sau là thoại của trang đó – tool tự lấy đúng trang theo tên ảnh).
4. Bấm **Nhận diện và xem trước**. Hộp thoại đóng ngay và **Photoshop dùng tiếp được bình thường**; bộ xử lý chạy
   riêng với cửa sổ tiến độ (bấm **Huỷ** để dừng bất cứ lúc nào), khoảng 10–15 giây.
5. Xong, Photoshop tự hiện bảng xem trước. Kiểm tra ảnh và danh sách thoại, bấm **Tạo layer trong Photoshop**
   (mất vài giây). Sửa/căn lại chữ trực tiếp trong Photoshop nếu cần.
6. **Ctrl+S** để lưu PSD theo cách thông thường của Photoshop.

### Làm cả chương bằng một nút
Mở mọi trang trong Photoshop (kéo cả thư mục ảnh vào), chạy script, **Mở bản dịch** cả chương (.txt / .docx có số
trang), bấm **Làm cả chương (N ảnh)**. Tool chụp từng trang, xử lý hết rồi hiện bảng tóm tắt (trang nào bao nhiêu
layer chữ, trang nào thiếu thoại); bấm **Tạo layer cho N trang** – mỗi trang thành một nhóm layer riêng (Ctrl+Z một
lần trong trang đó để bỏ). Ghép trang theo số trong tên ảnh (`013.jpg` ↔ trang 13).

### Upload bản redraw SFX (từng ảnh)
Sau khi redraw SFX ở nơi khác (Photoshop, AI…) thành **một ảnh cả trang, cùng khung** với ảnh gốc: mở trang đó, chạy
script, bấm **Upload bản redraw SFX…**, chọn ảnh. Tool căn khớp ảnh với ảnh gốc (ảnh bị lệch / co giãn vài px vẫn
được), **chỉ lấy phần khác ảnh gốc** (chỗ SFX đã xoá) và thêm layer **Redraw SFX** ngay trên layer Redraw, dưới các
layer chữ. Undo một lần để bỏ.

Có thể dùng Actions để gán phím tắt cho lần chạy script bằng Browse. Giữ file JSX cạnh bộ xử lý,
hoặc trong thư mục `photoshop` của repo khi chạy từ mã nguồn.

## Bộ xử lý

- Từ mã nguồn: giữ cấu trúc repo, cài môi trường bằng `run.bat`. JSX (trong `photoshop/` hoặc
  `dist/LilyzonoPhotoshop/`) ưu tiên `.venv/Scripts/pythonw.exe` của dự án – luôn chạy code mới nhất và không bị
  Smart App Control chặn như exe tự build.
- Dán bản dịch: nếu các câu bị dính liền thành một dòng (một số nguồn copy chỉ xuống dòng kiểu LF), copy lại từ
  Notepad / Word, hoặc dùng **Mở bản dịch (.txt / .docx)**.
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
