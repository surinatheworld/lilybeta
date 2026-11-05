# Bước 7: đọc Nhật, dịch Việt tham chiếu và ghép thoại

Trước thay đổi này, Bước 5 đã đọc tiếng Nhật bằng manga-ocr ONNX. Bước 7 có AI so nghĩa tiếng Nhật với bản dịch,
nhưng chạy riêng Bước 7 không tự đọc các ô Nhật còn trống và không lưu bản Việt tham chiếu theo ID.

Bước 7 hiện làm như sau:

1. Đọc tiếng Nhật còn thiếu của từng ID bằng model OCR trên máy. Ưu tiên `raw.png`; bản đang mở là Raw thì có thể
   dùng `work.png`. Bản Redraw thiếu Raw được cảnh báo và không đưa ảnh đã xóa chữ vào OCR trên máy.
2. Khi bật AI, một yêu cầu duy nhất vừa trả chữ Nhật/bản Việt tham chiếu theo từng ID, vừa ghép các câu đầu vào theo
   nghĩa. AI có thể đọc phần Nhật còn thiếu từ ảnh nếu bật gửi ảnh cho Bước 7. Thứ tự câu chỉ là bằng chứng phụ.
3. Lưu bản Việt tham chiếu trong `vi_ref`, tách khỏi `lines` là câu người dùng muốn typeset. Cột **Việt tham chiếu (AI)**
   chỉ đọc để đối chiếu; layer vẫn dùng nguyên câu đầu vào, gồm cả nhãn kiểu chữ trong script.
4. Khi thay chữ Nhật hoặc tọa độ vùng, bản tham chiếu cũ không được dùng lại. Khi nhận diện lại/tách bóng tạo ID mới,
   mỗi vùng phải có dữ liệu tương ứng với chính nó.
5. Nếu không dùng AI, tool không tạo bản dịch Nhật → Việt mới. Nó có thể ghép theo bản Việt tham chiếu đã lưu nếu
   nội dung khớp duy nhất; tiếp theo là bộ ghép câu gốc tiếng Nhật/độ dài/thứ tự đọc đã có.

Câu ngắn lặp lại không được coi là mốc nội dung duy nhất khi dùng tham chiếu offline. Câu chưa có ID trả về nằm
trong danh sách chưa đặt; câu có độ tin cậy thấp được đánh dấu cần kiểm tra. Nếu AI đọc khác chữ Nhật đã sửa tay,
giữ chữ sửa tay và hạ độ tin cậy của tham chiếu/kết quả ghép. Không cam kết mọi câu sẽ tự ghép đúng khi OCR sai,
thiếu bóng hoặc bản dịch khác nghĩa bản gốc.

## Cách dùng

- Nhận diện/tách bóng ở Bước 2. Với Redraw, nạp Raw ở Bước 4.
- Nạp file thoại Việt hoặc nhập câu dịch của trang như trước.
- Trong **Cấu hình AI…**, chọn nhà cung cấp và model Bước 7 có khả năng dịch Nhật → Việt; cấu hình key của bạn
  (hoặc máy chủ tương thích đang chạy). Model OCR tiếng Nhật trên máy không phải model dịch ngôn ngữ.
- Chạy **7 · Dịch tham chiếu & ghép thoại**. Không cần chạy riêng Bước 5 nếu chỉ muốn bổ sung các ô Nhật đang trống.
- So cột Nhật, Việt tham chiếu và bản dịch đã gán; sửa/gán tay các dòng chưa chắc rồi Typeset ở Bước 8.

Các câu/ô Nhật đã sửa tay giữ nguyên. Kết quả OCR được lưu trước khi gọi AI, nên lỗi API không xóa thoại đã gán
hoặc làm mất chữ Nhật vừa đọc. Bản dịch tham chiếu được tái sử dụng trong prompt nếu vẫn khớp nguồn Nhật/tọa độ.

## Kiểm tra

```powershell
.venv\Scripts\python -m unittest tests.test_step7_content -v
.venv\Scripts\python tests/panel_step7_smoke.py
```

Test bao gồm đảo thứ tự thoại Việt, đọc đúng Raw thay vì ảnh đã xóa, tái sử dụng OCR, khóa sửa tay, Redraw thiếu Raw,
lỗi API, cache sai nguồn/tọa độ, ghép offline theo tham chiếu, câu ngắn lặp lại, ID không tồn tại và số liệu JSON sai.
Test widget chạy riêng để kiểm tra cột tham chiếu chỉ đọc và các cột sửa Nhật/bản dịch không bị lệch sau khi thêm cột.

Kiểm tra cục bộ trang 013: manga-ocr thật đọc được nội dung ở 10 vùng; dùng bản dịch người dùng làm dữ liệu đối chiếu
và đảo toàn bộ thứ tự đầu vào, bộ ghép theo tham chiếu gán lại đúng 10/10 vùng theo dữ liệu đó. Đây là kiểm tra OCR
và cơ chế ghép, không phải phép đo chất lượng dịch AI. Máy kiểm tra chưa cấu hình key AI nên phần dịch qua nhà cung
cấp được kiểm thử bằng phản hồi mô phỏng; chưa xác minh chất lượng dịch/ghép của một model từ dịch vụ thực tế.
