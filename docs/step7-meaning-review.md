# Ghép nghĩa Nhật–Việt trên máy trước typeset

Bước 7 của bảng Photoshop dùng nghĩa để **chọn vị trí ngay từ đầu**. Nhánh không API không còn gọi bộ ghép
theo độ dài/thứ tự, cũng không dùng câu Việt tham chiếu đã lưu làm đáp án. Thiếu model dịch thì báo lỗi và giữ
bản gán cũ; không tự quay về ghép theo vị trí.

## Cách ghép

1. Đọc chữ Nhật từ Raw. PP-OCRv5 bổ sung một nguồn OCR độc lập, đọc lại crop chặt/crop nới và chữ ngắn
   xoay hướng khi cần. Các lần đọc được lưu cùng ảnh, tọa độ; đổi Raw phải đọc lại. Chữ Nhật và câu đã sửa tay
   được giữ nguyên. Chỉ thêm vùng bị bỏ sót khi có chữ Nhật đủ rõ, không suy tọa độ từ câu Việt.
   Chữ trên màn hình có đuôi câu ở dòng kế được nối theo hướng dòng và căn thẳng trước khi đọc lại; mở rộng
   crop sẽ bỏ OCR và cờ xoá chữ của khung cũ. Tên Latin có thể khớp furigana/kana thực sự đọc được trên Raw.
2. E5 và MiniLM tìm các nguồn có liên quan. NLLB-200 int8 chạy trực tiếp bằng CTranslate2, chấm **chính câu
   Việt đầu vào theo cả Nhật→Việt và Việt→Nhật**. Không chỉ so câu với bản dịch máy rồi chấp nhận một vùng.
   Tên, cách nói tắt, tiếng động và dấu câu có kiểm tra riêng; biến thể dùng để chấm không sửa văn bản người dùng.
3. Tối ưu các đích trên toàn trang với lựa chọn “Chưa gán”. Một nguồn thiếu/OCR rác không kéo lệch những câu sau.
   Hai phần của bóng đôi được xét cùng ngữ cảnh; SFX xen giữa trong danh sách không làm đứt cặp bóng. Một vùng
   Nhật có lời đáp ngắn và câu dài có thể nhận hai đơn vị Việt, nếu có bằng chứng nội dung cho cả hai.
4. Nếu một câu Việt bao trùm hai bóng nối nhau, chọn điểm cắt giữa các từ bằng nghĩa của từng phần. Lưu vị trí
   ký tự trong câu gốc để kiểm tra không mất, lặp hoặc tự viết lại bản dịch. Cặp có đảo cấu trúc ngữ pháp Việt
   được xét theo nghĩa chung, thay vì buộc mỗi mảnh khớp từng chữ Nhật riêng lẻ.
   Với câu Word gộp các mệnh đề nằm ở những bóng không nối nhau, chỉ tách khi từng mảnh có đích rõ theo nghĩa;
   giữ nguyên mọi ký tự. Khung dàn chữ dùng hình từng thùy và giới hạn panel để không tràn sang bóng khác/lề trang.

Khi một ngữ cảnh chung được chọn làm ứng viên, các bóng con cũng được chấm riêng. Bộ nhớ đệm không làm một
ứng viên chưa xét tự có điểm; kết quả ở trạng thái cache mới và cache có sẵn phải nhất quán. Khoảng cách với
phương án khác được tính trên phép ghép cả trang, kể cả khi phải đổi chỗ những câu còn lại.

Trang rất dày (>18 đơn vị đầu vào) dùng tìm kiếm giới hạn bộ nhớ. Điểm chấm là bằng chứng tương đối của model,
không phải xác suất dịch đúng. Model vẫn có thể nhầm tên, thành ngữ, lời rút gọn hoặc chữ cách điệu. Không có
cam kết nhận đúng mọi truyện; câu thiếu nguồn đáng tin phải để “Chưa gán”.

## Xem kết quả và xuất layer

Sau Bước 7, panel tự mở **Bản gán thử (chữ Việt trong bóng)**. Đây là ảnh đặt chữ vào các vị trí đã chọn, kể cả
các cặp chưa đủ chắc để duyệt. Chỉ lưu `assignment-preview.png`; không tạo layer hoặc ghi đè bản xuất trước.
Xoá chữ trên bản thử bị giới hạn quanh khung chữ gốc để tránh mất viền bóng. SFX trên tranh vẫn có thể cần redraw.

Cột **Đối chiếu nghĩa** ghi kết quả chấm hai chiều và ngữ cảnh. Cặp rõ được thông qua tự động; cặp mơ hồ giữ
trạng thái cần xem. Chọn vùng, so với Raw, sửa chữ Nhật/câu Việt khi cần rồi **Xác nhận nghĩa vùng…** sau khi
thực sự đối chiếu. Sửa một vùng hoặc đổi ảnh, khung, nhóm bóng, danh sách đầu vào hay vị trí các câu làm kết quả
duyệt liên quan hết hiệu lực.

Bước 8 kiểm tra nguồn, các trạng thái duyệt, câu chưa đặt và bảo toàn toàn bộ đầu vào trước khi ghi thông số layer
hay gọi Photoshop. Bản gán thử luôn xem được trước bước này. Chế độ chạy nhanh cũ vẫn có lớp chặn trước xuất;
luồng ghép nghĩa mới nằm trong Bước 7 của bảng Lilyzono.

## Cài model một lần

```powershell
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python tools/install_semantic_models.py
.venv\Scripts\python tools/install_translation_models.py
```

Lệnh cài chỉ tải file trọng số/tokenizer tĩnh, kiểm SHA-256; không gửi ảnh hay bản dịch. Có thể chép hoàn toàn offline:

```powershell
.venv\Scripts\python tools/install_translation_models.py --from-folder D:\model-bundle
```

Bundle có `translation/{model.bin,config.json,shared_vocabulary.txt,sentencepiece.bpe.model}` và
`source-ocr/{ch_PP-OCRv5_det_mobile.onnx,ch_PP-OCRv5_rec_server.onnx}`. Hai encoder nghĩa dùng installer riêng.
Tổng các bộ mới cùng encoder nghĩa khoảng 1 GB. Khi xử lý truyện, **không tải ngầm, không gọi API kể cả localhost**.
NLLB chạy CPU; OCR ưu tiên DirectML và có CPU dự phòng. Bộ nhớ đệm điểm chấm nằm trong thư mục dự án,
chỉ tái dùng cho đúng cặp văn bản và phiên bản, không phải bảng đáp án theo trang/ID.

**Giấy phép trọng số:** NLLB của Meta là **CC BY-NC 4.0, chỉ dùng phi thương mại theo giấy phép đó**.
PP-OCRv5 là Apache 2.0; E5 là MIT; MiniLM là Apache 2.0. Xem
[thông tin nguồn và giấy phép](model-licenses/local-translation-notice.md).

Trong bản tích hợp, JSX ưu tiên `.venv/Scripts/pythonw.exe` và `LilyzonoPhotoshop.py` của repo. Đóng bảng Lilyzono
rồi mở lại script trong Photoshop để nạp mã mới. Không cần đóng gói EXE.

## Kiểm tra

```powershell
.venv\Scripts\python -m unittest discover -s tests -q
.venv\Scripts\python tests/panel_step7_smoke.py
```

Test riêng kiểm tra đảo danh sách, nguồn bị thiếu, dấu câu/SFX lặp, một nguồn hai câu, SFX xen giữa bóng đôi,
ngữ pháp cặp bóng, cắt câu không mất chữ, nguồn đã sửa tay, đổi Raw, model thiếu và chặn xuất khi dữ liệu cũ.
Kiểm tra ảnh thực phải dùng bản gán đã vẽ chữ, đối chiếu riêng với ảnh Nhật; số câu được gán không tự chứng minh
ghép đúng. Dữ liệu truyện và đáp án kiểm tra của người dùng không được đóng gói vào thuật toán.
