# EditManga – Tool type truyện

Thay chữ gốc (tiếng Nhật, Hàn, Trung…) trên ảnh truyện bằng bản dịch, dùng font có sẵn trên máy, tự xoá chữ cũ và vẽ lại nền.

## Chạy bằng EditManga.exe (không cần cài Python)

1. Bấm đúp **`build_exe.bat`** (một lần, trên máy có Python) → tạo thư mục `dist\EditManga\`.
2. Mở **`dist\EditManga\EditManga.exe`**. Có thể chuột phải → *Send to → Desktop (create shortcut)* để có biểu tượng ngoài màn hình.
3. Muốn dùng trên máy khác: nén cả thư mục `dist\EditManga` thành .zip rồi gửi, giải nén ra là chạy (không cần Python).
   Thư mục `models` bên trong chứa sẵn model AI; nếu thiếu, tool tự tải lần đầu.

Gặp lỗi trên máy khác: chạy `EditManga.exe --selftest` (có thể thêm đường dẫn một ảnh) rồi xem file `selftest.log` cạnh exe.

## Cài đặt & chạy từ mã nguồn (Windows)

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
     Chữ màu trên truyện đen trắng (vd tên truyện trên trang bìa) cũng được xoá.
   - Khung **cam nét đứt** (nhãn `SFX`): cụm chữ lẻ tự tìm thấy (SFX, chữ nhỏ ngoài bóng). Không cần thì bấm *Xoá khung*.
   - Thiếu vùng thì tự vẽ: chọn công cụ **▭ Khung chữ nhật** (phím R) hoặc **➰ Lasso** (phím L) rồi kéo chuột.
     Lasso khoanh tự do quanh SFX/chữ vẽ tay: **toàn bộ phần khoanh sẽ bị xoá** rồi vẽ lại nền, bản dịch đặt trong vùng khoanh.
4. **Chọn câu dịch**: bấm một câu trong danh sách (bấm đúp = gán và xử lý luôn). Tool tự gợi ý câu chưa dùng tiếp theo.
5. **Chỉnh chữ** ở bảng **Chữ (Character / Paragraph)** – bố cục theo Photoshop 2023 (đơn vị px ≈ pt ở 72 ppi):
   - Thẻ **Ký tự**: họ font + kiểu mặt chữ (Regular, Light, Bold…), cỡ chữ (*Tự động* = tự co vừa bóng),
     khoảng cách dòng (Leading), Kerning, Tracking, co giãn dọc/ngang, dịch đường cơ sở, màu;
     hàng nút: giả đậm, giả nghiêng, IN HOA, chữ hoa nhỏ, chỉ số trên/dưới, gạch chân, gạch ngang; khử răng cưa.
   - Thẻ **Đoạn**: căn trái/giữa/phải, dàn đều (dòng cuối trái/giữa/phải, dàn đều tất cả), thụt lề trái/phải/dòng đầu,
     khoảng cách trước/sau đoạn, gạch nối. Enter hoặc `|` trong nội dung = sang đoạn mới.
   - Mục **Hiệu ứng**: viền chữ (Stroke) – màu và độ dày. Font thiếu dấu tiếng Việt sẽ có cảnh báo đỏ.
6. **Xử lý** (Ctrl+Enter): xoá chữ gốc, vẽ lại nền, chèn bản dịch. Tool tự chuyển sang vùng tiếp theo.
   - Bóng thoại trắng / nền trơn: tô lại màu nền (tức thì).
   - Nền có hoạ tiết (tranh, lưới chấm, SFX đè lên tranh): vẽ lại theo lựa chọn ở mục **Vẽ lại nền**:
     - *Thường (OpenCV)*: nhanh, nền tranh hơi mờ.
     - *AI LaMa* (mặc định): chạy trên máy, miễn phí, không cần mạng (vài giây mỗi vùng, model nạp ngầm khi mở ảnh).
     - *AI Google Gemini* / *AI OpenAI (ChatGPT)*: bấm **API key…** để nhập key và chọn model, có nút *Kiểm tra key*.
       Gửi một vùng rộng quanh chữ (≥ 768px, giữ tỉ lệ khung, xin ảnh 2K) để AI vẽ lại cả mảng tranh; ảnh trả về
       được tự căn khớp với ảnh gốc rồi dán lại **cả khung chữ** (xoá luôn nét mà mask bỏ sót).
       Chọn nhiều vùng gần nhau rồi Xử lý → **gom chung 1 lần gọi API**. Tính phí theo nhà cung cấp.
       Gemini cần tài khoản đã bật thanh toán (gói miễn phí không dùng được model sửa ảnh).
       Key được cất trong Windows Credential Manager, không ghi ra file.
7. **Xuất ảnh** (Ctrl+S).

Vùng đã xử lý vẫn sửa được: chọn lại rồi đổi chữ/font, ảnh cập nhật ngay. *Hoàn tác vùng* (Ctrl+Z) trả lại ảnh gốc ở vùng đó.

### Chọn nhiều vùng cùng lúc
- **Ctrl + bấm** thêm/bỏ từng vùng, **Shift + bấm** chọn liền một dải, **Ctrl+A** chọn tất cả.
- Đổi font, cỡ, màu, viền, bộ kiểu… → áp cho **mọi vùng đang chọn**.
- **Xử lý** → làm lần lượt từng vùng; vùng chưa có chữ tự nhận câu dịch kế tiếp theo thứ tự đọc.
- *Chỉ xoá chữ*, *Hoàn tác vùng*, *Xoá khung* cũng áp cho cả nhóm.

### Tự căn chỉnh chữ
Chọn **1** vùng đã xử lý → hiện khung tím quanh chữ:
- Kéo bên trong khung để **di chuyển** chữ, kéo **góc** để đổi cỡ khung (chữ tự xuống dòng/co cỡ lại).
- Phím mũi tên nhích 1px, Shift + mũi tên nhích 10px.
- Mục **Căn chỉnh chữ**: căn trái / giữa / phải, **xoay** (độ), *Tự động lại* để bỏ chỉnh tay.

### Font yêu thích & bộ kiểu
- Bấm **☆** cạnh ô Font để thêm font vào danh sách **Yêu thích**; chọn trong danh sách để dùng ngay.
- **Bộ kiểu**: bấm *Lưu* để lưu cả font + cỡ + màu + viền + căn lề thành tên (vd *Thoại*, *Hét*, *SFX*);
  chọn bộ kiểu để áp cho mọi vùng đang chọn.

| Phím | Tác dụng |
|---|---|
| Lăn chuột / Ctrl + lăn | Cuộn / phóng to-thu nhỏ |
| Chuột phải hoặc giữa + kéo | Di chuyển ảnh |
| R / L | Công cụ khung chữ nhật / lasso |
| Ctrl / Shift + bấm, Ctrl+A | Chọn nhiều vùng / chọn dải / chọn tất cả |
| Mũi tên (Shift) | Nhích khung chữ 1px (10px) |
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
