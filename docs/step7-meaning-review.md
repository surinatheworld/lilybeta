# Đối chiếu nghĩa bắt buộc trước typeset (không dùng API)

**Xem chữ trong bóng trước khi duyệt:** sau Bước 7, panel tự dựng và mở **Bản gán thử (chữ Việt trong bóng)**.
Câu Việt được đặt trực tiếp vào vị trí đã gán, kể cả vùng đang cần xem lại. Bạn xem ảnh rồi mới sửa/gán lại hoặc
xác nhận nghĩa. Ảnh thử lưu riêng ở `assignment-preview.png`; không ghi đè bản typeset đã xuất và không tự
đánh dấu đã duyệt. Vùng nền đơn giản được xoá chữ trên bản ảnh thử; phần cần redraw vẫn có thể còn chữ Nhật.

## Vì sao bản cũ vẫn gắn sai

OCR tiếng Nhật chỉ nhận mặt chữ, không dịch nghĩa. Với cấu hình không API, Bước 7 cũ dùng cặp Nhật trong
script hoặc bản Việt tham chiếu đã lưu; khi thiếu chúng, bộ ghép vẫn dựa vào độ dài và thứ tự đọc. Một SFX
bị bỏ sót hoặc hai bóng bị gộp có thể làm những câu phía sau lệch hàng loạt. Nhãn “đã gán” và điểm ghép cao
không chứng minh câu Việt có cùng nghĩa với chữ Nhật. Bước 8 cũ vẫn xuất layer từ những đề xuất này.

## Quy trình hiện tại

1. Bước 7 bổ sung OCR từ Raw và tạo đề xuất ghép. Cấu hình mặc định của panel là **Không dùng API**.
2. Model `multilingual-e5-small` và `paraphrase-multilingual-MiniLM-L12-v2` chạy ONNX trực tiếp trên CPU.
   Mỗi câu Việt thực tế được so với chữ Nhật của vùng được gán và các vùng khác trên cùng trang.
3. Để thông qua tự động, cả hai model phải chọn đúng vùng đang gán, cùng vượt ngưỡng điểm và khoảng cách
   với vùng đứng kế tiếp. Đây là điểm tương đồng, không phải xác suất dịch đúng. Câu rất ngắn/SFX, OCR trống
   hoặc lặp, model lỗi/thiếu, hoặc kết quả mơ hồ đều cần xem lại. Nhãn Nhật trong script cũng không thay thế
   phép so nghĩa. Hai model có thể cùng sai, nhất là với phủ định, tên riêng hoặc ngữ cảnh truyện.
4. Cột **Đối chiếu nghĩa** hiển thị trạng thái. Di chuột xem lý do và các ID/chữ Nhật được gợi ý. Sửa ô Nhật
   nếu OCR sai, rồi sửa/gán lại câu Việt. Với vùng cần đánh giá ngữ cảnh, chọn vùng, xem ảnh Raw và bấm
   **Xác nhận nghĩa vùng…**; hộp xác nhận hiển thị đúng cặp Nhật–Việt, mặc định chọn Không.
5. Bước 8 kiểm tra lại trước khi xuất thông số layer hoặc gọi Photoshop. Còn vùng chưa xác minh, câu chưa đặt,
   thiếu/trùng câu đầu vào thì dừng xuất layer. Bản gán thử ở Bước 7 vẫn xem được trước khi xác nhận nghĩa.

Kết quả đối chiếu gắn với nội dung ảnh, chữ Nhật, tọa độ, các vùng trên trang và danh sách câu đầu vào.
Thay dữ liệu liên quan thì phải kiểm tra lại, kể cả khi mở dự án cũ đã có preview/typeset.json. File xuất
cũ được giữ nguyên để không mất dữ liệu; chúng không được coi là kết quả vừa xác minh.

Chế độ chạy nhanh cũng kiểm tra trước khi vẽ/xuất. Nếu bị chặn, đường dẫn dự án `meaning-review` xuất hiện
trong lỗi để mở trong panel, xem lại loại bóng, đối chiếu, xoá chữ và chạy Bước 8. Bộ font tuỳ chọn được lưu
theo dự án. Với đầu vào không có tên tài liệu Photoshop, cần đồng bộ lại tài liệu trong panel.

## Model cục bộ

Cài `requirements.txt`, sau đó cài trọng số một lần:

```powershell
.venv\Scripts\python tools/install_semantic_models.py
```

Lệnh cài chỉ tải file trọng số/tokenizer tĩnh và kiểm SHA-256 đã ghim trong mã. Nó không gửi nội dung
truyện. Hoặc chép từ bộ model đã tải sẵn, hoàn toàn offline:

```powershell
.venv\Scripts\python tools/install_semantic_models.py --from-folder D:\model-bundle
```

Bộ model chứa `e5/model.onnx`, `e5/tokenizer.json`, `minilm/model.onnx`, `minilm/tokenizer.json`.
Khi chạy tool, không tải model ngầm, không dùng inference API, kể cả localhost. Thiếu model thì dừng để
đối chiếu; không quay về tự chấp nhận theo độ dài. Hai model và tokenizer chiếm khoảng 263 MB.

Trong bản tích hợp hiện tại, `photoshop/Lilyzono.jsx` ưu tiên khởi động Python từ `.venv` của dự án và
`LilyzonoPhotoshop.py`. Sau khi cập nhật mã nguồn/dependency/model, đóng bảng Lilyzono rồi mở lại từ script
trong Photoshop để nạp bản sửa.

Model gốc: [E5, MIT](https://huggingface.co/intfloat/multilingual-e5-small) và
[MiniLM, Apache 2.0](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2).
Văn bản giấy phép nằm trong `docs/model-licenses/`. Model giữ nguyên, không fine-tune.

## Kiểm chứng và giới hạn

Test kiểm tra câu gán đảo vùng, hai model bất đồng, thiếu/lỗi model, SFX, OCR lặp, cache cũ, thay Raw/chữ/tọa độ,
xác nhận thủ công, thiếu câu và chặn trước khi dựng/xuất. Test widget chạy riêng:

```powershell
.venv\Scripts\python -m unittest discover -s tests -q
.venv\Scripts\python tests/panel_step7_smoke.py
```

Kiểm tra lớp chặn trên đúng 28 ảnh và file Word người dùng đã cung cấp: 222 vùng có câu được kiểm tra; 1 vùng đạt đồng
thuận rõ, 221 vùng cần xem lại. Cả 28 trang bị chặn trước typeset vì còn vùng chưa xác minh, gồm lỗi gán
đã biết ở trang 9 và 23. Không gọi API, không chạy hàm dựng chữ, không ghi đè preview/typeset.json cũ.
Đây là kiểm tra khả năng chặn kết quả chưa xác minh, không phải số đo 100% ghép đúng. Model đối chiếu
không tạo bản dịch Việt mới và không tự sửa hết lỗi OCR/tách bóng; các trường hợp chưa đủ bằng chứng
vẫn cần người biết nội dung truyện đối chiếu.

Sau khi bổ sung bản gán thử, cả 28 trang đã được dựng thành ảnh có chữ Việt trong các bóng theo đúng kết quả
ghép đang có. Không thay câu/gán sẵn đáp án để làm đẹp kết quả; lỗi ghép vẫn hiện trong ảnh để đối chiếu.
Test kiểm tra ảnh thử thực sự có chữ, không tự duyệt nghĩa, không xuất layer/ghi đè bản đã duyệt, tự mở đúng
ảnh sau Bước 7 và không vẽ nhãn ID đè lên bản chữ.
