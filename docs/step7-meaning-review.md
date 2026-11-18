# Ghép thoại Nhật–Việt trong Photoshop

Bước 7 dùng chữ Nhật đọc từ ảnh gốc để đặt các câu tiếng Việt trong bản dịch vào từng vùng thoại. Trang đã redraw cần có ảnh raw tương ứng.

## Cài model

Cài Manga OCR, hai bộ so khớp E5 và MiniLM, bộ dịch NLLB và bộ OCR bổ sung theo [hướng dẫn cài đặt](../README.md#nếu-ghép-thoại-nhậtviệt-trên-máy). NLLB dùng giấy phép CC BY-NC 4.0. [Nguồn và giấy phép model](model-licenses/local-translation-notice.md)

## Kiểm tra trước khi tạo layer

1. Chạy Bước 7 và mở **Bản gán thử (chữ Việt trong bóng)**. So sánh câu trên ảnh thử với thoại trên ảnh gốc, nhất là tên riêng, tiếng động và bóng đôi.
2. Với vùng còn chờ duyệt, sửa chữ Nhật hoặc câu Việt nếu cần, rồi bấm **Xác nhận nghĩa vùng…**. Kiểm tra cả danh sách câu chưa có vùng.
3. Chạy Bước 8 sau khi đặt đủ câu và duyệt các vùng. Nếu đổi ảnh gốc, khung hoặc bản dịch sau đó, kiểm tra lại các vùng bị ảnh hưởng.
