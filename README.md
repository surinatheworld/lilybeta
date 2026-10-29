# EditManga – Tool type truyện

Thay chữ gốc (tiếng Nhật, Hàn, Trung…) trên ảnh truyện bằng bản dịch, dùng font có sẵn trên máy, tự xoá chữ cũ và vẽ lại nền.

## Cài đặt & chạy (Windows)

Cần Python 3.10 trở lên ([python.org](https://www.python.org/downloads/), nhớ tick *Add Python to PATH*).

Bấm đúp **`run.bat`**. Lần đầu tool tự cài thư viện và tự tải 2 model AI về thư mục `models/`: nhận diện chữ (~95MB) và vẽ lại nền LaMa (~200MB).

Hoặc chạy tay:
```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m app.main
```

## Cách dùng

1. **Mở ảnh raw** (Ctrl+O hoặc kéo thả ảnh vào cửa sổ). Tool tự nhận diện chữ và khoanh các vùng, đánh số theo thứ tự đọc (chọn *Manga: phải → trái* hoặc *Manhwa: trái → phải* trên thanh công cụ).
2. **Mở bản dịch** (Ctrl+D): file `.docx` hoặc `.txt`, mỗi đoạn là một câu. Dòng bắt đầu bằng `//` bị bỏ qua. Xuống dòng mềm (Shift+Enter trong Word) hoặc dấu `|` để ép xuống dòng trong bóng thoại.
3. **Chọn vùng**: bấm vào khung trên ảnh.
   - Khung **đỏ**: chữ trong bóng thoại / hộp dẫn truyện. Bóng thoại đôi được tách thành từng nửa, mỗi nửa một câu.
   - Khung **cam nét đứt** (nhãn `SFX`): cụm chữ lẻ tự tìm thấy (SFX, chữ nhỏ ngoài bóng). Không cần thì bấm *Xoá khung*.
   - Thiếu vùng thì tự vẽ: chọn công cụ **▭ Khung chữ nhật** (phím R) hoặc **➰ Lasso** (phím L) rồi kéo chuột.
     Lasso khoanh tự do quanh SFX/chữ vẽ tay: **toàn bộ phần khoanh sẽ bị xoá** rồi vẽ lại nền, bản dịch đặt trong vùng khoanh.
4. **Chọn câu dịch**: bấm một câu trong danh sách (bấm đúp = gán và xử lý luôn). Tool tự gợi ý câu chưa dùng tiếp theo.
5. **Chọn font** trong danh sách font của máy, cỡ chữ (*Tự động* = tự co vừa bóng), đậm/nghiêng/in hoa, màu chữ, màu và độ dày viền. Font thiếu dấu tiếng Việt sẽ có cảnh báo đỏ.
6. **Xử lý** (Ctrl+Enter): xoá chữ gốc, vẽ lại nền, chèn bản dịch. Tool tự chuyển sang vùng tiếp theo.
   - Bóng thoại trắng / nền trơn: tô lại màu nền (tức thì).
   - Nền có hoạ tiết (tranh, lưới chấm, SFX đè lên tranh): vẽ lại theo lựa chọn ở mục **Vẽ lại nền**:
     - *Thường (OpenCV)*: nhanh, nền tranh hơi mờ.
     - *AI LaMa* (mặc định): chạy trên máy, miễn phí, không cần mạng (vài giây mỗi vùng, model nạp ngầm khi mở ảnh).
     - *AI Google Gemini* / *AI OpenAI (ChatGPT)*: bấm **API key…** để nhập key và chọn model, có nút *Kiểm tra key*.
       Chỉ gửi một ô ảnh nhỏ quanh chữ (không gửi cả trang), mỗi vùng = 1 lần gọi API, tính phí theo nhà cung cấp.
       Gemini cần tài khoản đã bật thanh toán (gói miễn phí không dùng được model sửa ảnh).
       Key được cất trong Windows Credential Manager, không ghi ra file.
7. **Xuất ảnh** (Ctrl+S).

Vùng đã xử lý vẫn sửa được: chọn lại rồi đổi chữ/font, ảnh cập nhật ngay. *Hoàn tác vùng* (Ctrl+Z) trả lại ảnh gốc ở vùng đó.

| Phím | Tác dụng |
|---|---|
| Lăn chuột / Ctrl + lăn | Cuộn / phóng to-thu nhỏ |
| Chuột phải hoặc giữa + kéo | Di chuyển ảnh |
| R / L | Công cụ khung chữ nhật / lasso |
| Tab | Sang vùng tiếp theo |
| Delete | Xoá khung đang chọn |
| B | Ẩn/hiện khung |
| F5 | Nhận diện lại |

## Cấu trúc

- `app/core/detect.py` – nhận diện vùng chữ ([comic-text-detector](https://github.com/dmMaze/comic-text-detector), chạy bằng OpenCV DNN)
- `app/core/clean.py` – xoá chữ, vẽ lại nền (tô màu nền / nền gradient / AI)
- `app/core/lama.py` – vẽ lại nền bằng [LaMa](https://github.com/advimman/lama) (ONNX, onnxruntime)
- `app/core/cloud.py` – vẽ lại nền bằng API Gemini / OpenAI; `app/core/secrets.py` – cất API key
- `app/core/typeset.py` – dàn chữ, tự co cỡ, viền chữ
- `app/core/docs.py` – đọc bản dịch `.docx` / `.txt`
- `app/core/page.py` – trạng thái trang
- `app/ui/` – giao diện PySide6
