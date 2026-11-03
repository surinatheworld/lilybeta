# Lilyzono – Tool type truyện

Thay chữ gốc (tiếng Nhật, Hàn, Trung…) trên ảnh truyện bằng bản dịch, dùng font có sẵn trên máy, tự xoá chữ cũ và vẽ lại nền.

## Dùng trực tiếp trong Photoshop 2023 (Windows)

Chạy **File → Scripts → Browse… → `photoshop/Lilyzono.jsx`**: mở **bảng điều khiển** (cửa sổ riêng, Photoshop vẫn dùng
bình thường; chạy lại script chỉ đưa cửa sổ lên trước). Giữ **Shift** khi chạy script để mở hộp thoại nhanh kiểu cũ.

### Bảng điều khiển: 8 bước theo ID vùng thoại

| Bước | Làm gì | Kết quả trong Photoshop |
|---|---|---|
| 1 Mở ảnh | “Lấy ảnh đang mở” / “Mở ảnh…”, chọn **Bản Raw** hoặc **Bản Redraw** | – |
| 2 Nhận diện & phân loại (AI) | model comic-text-detector tìm vùng chữ, gán ID (R01, R02…); model AI xem ảnh có đánh số ID → loại bóng (tròn kín, vuông kín, có tia, mây suy nghĩ, SFX, ngoài khung, không dịch), thứ tự đọc, chữ bị sót | – |
| 3 Thiết lập bảng thoại | sửa loại bóng, kiểu chữ của preset, font riêng (danh sách font của Photoshop), cỡ chữ cho từng ID; “Loại bóng → font…” cho cả chương | – |
| 4 Nạp Raw | bản redraw: “Chọn folder raw…” (tên ảnh có số trang; trang đôi `03-04` lấy raw 4 trái + 3 phải) | layer ẩn **Raw gốc (Lilyzono)** |
| 5 OCR Raw | đọc chữ Nhật từng ID (model manga-ocr trên máy) | – |
| 6 Xoá chữ | chỉ xoá chữ trong bóng kín (loại chọn trong “Loại bóng → font…”), chỉ chỗ nền một màu; bản redraw: bỏ qua | layer **Xoá chữ (Lilyzono)** |
| 7 Ghép bản dịch (AI) | model AI đọc chữ Nhật + loại bóng + vị trí từng ID và câu dịch của trang → câu nào vào ID nào (một ID nhận được nhiều câu); câu chưa chắc tô vàng, câu không có chỗ vào “Câu chưa có vùng” | – |
| 8 Typeset | dàn chữ theo bảng thoại | nhóm **Lilyzono · …**, mỗi câu một layer chữ tên `R03 · câu…`; nhóm lần trước đổi thành “(bản trước)” và ẩn |

- Mỗi bước có nút **Chạy** riêng; **Chạy toàn bộ** chạy hết (bản redraw: bước 4 chạy trước bước 2 vì chữ Nhật trên redraw đã bị
  xoá). Phạm vi: trang đang chọn hoặc tất cả trang. Trạng thái từng trang: ✓ xong · ! cần kiểm tra · ✗ lỗi · ↻ cần chạy lại.
- **Chỉnh tay được giữ**: ô sửa tay (loại bóng, chữ Nhật, câu dịch, kiểu chữ, font, cỡ) in đậm và không bị bước 2 / 5 / 7 ghi đè
  khi chạy lại; nhận diện lại giữ ID cho vùng trùng chỗ cũ. “Bỏ khoá chỉnh tay” trả vùng về cho AI. Kéo trên ảnh để thêm vùng.
- Bản redraw có raw: vùng mà bản redraw không xoá chữ (logo, chữ ký) tự thành “Không dịch”; chữ đã xoá mà model nhận diện bỏ sót
  (băng tên chương…) được thêm thành vùng mới.
- Model AI lỗi (sai key, hết hạn mức, mất mạng, trả lời sai dạng): bước đó báo **Lỗi** kèm lý do ở nhật ký và danh sách trang;
  vùng đã nhận diện vẫn được giữ để sửa tay hoặc chạy lại.
- Dữ liệu dự án: `%LOCALAPPDATA%\Lilyzono\panel\<tên folder ảnh>` (nút “Mở thư mục dự án”).

**Cấu hình model** (nút “Cấu hình AI…”):

| Nhà cung cấp | Model mặc định | API key |
|---|---|---|
| Google Gemini | `gemini-3.5-flash` | aistudio.google.com/apikey · biến `GEMINI_API_KEY` |
| OpenAI | `gpt-5.5` | platform.openai.com/api-keys · biến `OPENAI_API_KEY` |
| Anthropic Claude | `claude-sonnet-5-5` | console.anthropic.com · biến `ANTHROPIC_API_KEY` |
| Máy chủ tương thích OpenAI (Ollama, LM Studio…) | tự nhập | địa chỉ dạng `http://localhost:11434/v1`, key không bắt buộc |

Model bước 2 phải đọc được ảnh; model bước 7 để trống thì dùng như bước 2. “Tải danh sách model” lấy tên model thật của
nhà cung cấp, “Kiểm tra kết nối” gửi thử một câu hỏi nhỏ. Key lưu trong Windows Credential Manager, không ghi ra file.
Đầu ra luôn là JSON theo schema (Gemini `responseJsonSchema`, OpenAI `json_schema` strict, Claude tool bắt buộc) theo ID vùng.

### Hộp thoại nhanh (giữ Shift)

1. Mở ảnh trong Photoshop, giữ Shift và chạy **`photoshop/Lilyzono.jsx`**.
2. Dán bản dịch, chọn chế độ xoá chữ rồi **Nhận diện và xem trước**.
3. Bấm **Tạo layer trong Photoshop**: thêm một nhóm gồm layer **Redraw** trong suốt và mỗi câu một **layer chữ thật sửa được**.

Tool giữ các layer có sẵn, kích thước và độ phân giải của tài liệu. Undo một lần để bỏ nhóm vừa thêm.
Typeset Gái Băng / Isshiki tự chọn font theo loại thoại và hình khung; chỉ xoá các vùng đã ghép bản dịch, giữ các SFX chưa dịch.
Chạy `build_photoshop.bat` để đóng gói bản không cần Python, rồi giữ cả thư mục `dist/LilyzonoPhotoshop`.
Chi tiết: [hướng dẫn Photoshop](photoshop/README.md). Bộ xử lý chạy trên máy, không cần mở ứng dụng Lilyzono riêng.

## Chạy bằng Lilyzono.exe (không cần cài Python)

1. Bấm đúp **`build_exe.bat`** (một lần, trên máy có Python) → tạo thư mục `dist\Lilyzono\`.
2. Mở **`dist\Lilyzono\Lilyzono.exe`**. Có thể chuột phải → *Send to → Desktop (create shortcut)* để có biểu tượng ngoài màn hình.
3. Muốn dùng trên máy khác: nén cả thư mục `dist\Lilyzono` thành .zip rồi gửi, giải nén ra là chạy (không cần Python).
   Thư mục `models` bên trong chứa sẵn model AI; nếu thiếu, tool tự tải lần đầu.

Gặp lỗi trên máy khác: chạy `Lilyzono.exe --selftest` (có thể thêm đường dẫn một ảnh) rồi xem file `selftest.log` cạnh exe.
Nếu bản exe cũ báo lỗi DLL khi khởi động, đóng gói lại bằng `build_exe.bat`: bước build ưu tiên DLL của Windows,
tránh lấy nhầm `icuuc.dll` từ phần mềm khác trong `PATH` gây lỗi import QtGui.

## Card đồ hoạ và đọc chữ Nhật

- Có card đồ hoạ (DirectX 12, vd RTX 4060) thì nhận diện chữ chạy trên card qua ONNX Runtime DirectML: ~0,1 giây/trang
  thay vì 1,5–3 giây trên CPU, RAM ít hơn. Không có card thì tự dùng CPU (OpenCV). `LILYZONO_CPU=1` để ép chạy CPU.
- Script `.xlsx` có cột Japanese: tool đọc chữ Nhật trong từng bóng (manga-ocr) rồi khớp với câu gốc trong script,
  nên câu vào đúng bóng dù thứ tự đọc khung khó. Câu không tìm thấy chỗ thành khung "CHƯA ĐẶT"; bóng không có trong
  script thì để trống. Cần model ở `models/manga-ocr/` (không có trong repo, ~450 MB): tải `encoder_model.onnx`,
  `decoder_model.onnx`, `vocab.txt` từ [mayocream/manga-ocr-onnx](https://huggingface.co/mayocream/manga-ocr-onnx)
  (Apache-2.0). Trên card ~0,4 giây/bóng, CPU ~2 giây/bóng.

## Cài đặt & chạy từ mã nguồn (Windows)

Cần Python 3.10 trở lên ([python.org](https://www.python.org/downloads/), nhớ tick *Add Python to PATH*).

Bấm đúp **`run.bat`**. Lần đầu tool tự cài thư viện và tự tải model nhận diện chữ (~95MB) về thư mục `models/`.

Hoặc chạy tay:
```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m app.main
```

## Giao diện kiểu Photoshop

Bố cục và thao tác quen thuộc của Photoshop 2023 (giao diện tối), chỉ gồm những gì cần cho dịch truyện:
- **Menu** Tệp / Sửa / Layer / Chọn / Chữ / Xem / Cửa sổ; **hộp công cụ** bên trái; **thanh tuỳ chọn** phía trên đổi
  theo công cụ (cỡ / độ cứng / opacity của cọ, chế độ vùng chọn, Feather, dung sai Magic Wand…).
- **Panel** bên phải (kéo tab để sắp xếp, bật/tắt ở menu Cửa sổ): *Lilyzono – dịch truyện* (khung chữ, bản dịch),
  *Character / Paragraph*, *Layers* (ẩn/hiện, đổi tên, opacity, blend mode, thứ tự, gộp), *History*, *Trang*.
- **Layer**: Background (raw, khoá) → Redraw (chỗ đã xoá chữ / vẽ lại / bản redraw upload) → layer ảnh bạn tạo
  thêm → mỗi bóng đã có bản dịch là một layer chữ. Lưu PSD giữ nguyên các layer này (thứ tự, ẩn/hiện, opacity, blend).
- **Vùng chọn**: Marquee, Lasso, Magic Wand, cộng/trừ/giao, Feather, mở rộng/thu hẹp, đảo; *Content-Aware Fill*
  vẽ lại phần trong vùng chọn theo nền xung quanh (OpenCV); *Tạo khung chữ* biến vùng chọn thành khung chữ.
- **Redraw tay**: Brush, Eraser (trên Redraw: trả về raw), Clone Stamp, Spot Healing (tô lên chữ, nhả chuột là
  vẽ lại theo nền xung quanh); mọi nét đều bị giới hạn trong vùng chọn nếu có.
- **Hoàn tác nhiều bước** cho mọi thao tác (gõ chữ, đổi font, nét cọ, xoá chữ, nhận diện…), bảng History như PS.
- **Nhiều tài liệu**: Ctrl+O cho phép chọn nhiều file; mở thêm ảnh/PSD/dự án tạo tab mới, giữ nguyên các tab đang sửa.
  Mỗi tab giữ Layers, History, vùng chọn, bản dịch, vị trí xem và mức zoom riêng. Kéo để đổi thứ tự tab;
  Ctrl+Tab / Ctrl+Shift+Tab chuyển tab, Ctrl+W hoặc nút × đóng tab. Mở lại cùng file sẽ chuyển về tab đã mở.
- **Lưu mặc định là PSD**: Ctrl+S chọn tên `.psd` ở lần đầu, các lần sau lưu lại vào PSD của riêng tab đó.
  Ctrl+Shift+S lưu PSD thành tên khác. Đóng tab/app sẽ hỏi lưu những tài liệu đã sửa; huỷ hoặc lưu lỗi sẽ giữ tab.
  Bản dịch trong khung chưa xử lý cần được xử lý trước khi lưu PSD; dùng `.lzp` nếu muốn giữ bản nháp và khung nhận diện.
- **Chữ**: công cụ **T** – bấm vào bóng thoại là gõ ngay trong bóng (chọn sẵn một bóng rồi bấm T cũng vậy);
  bóng còn chữ gốc thì tự xoá chữ trước. Esc / Ctrl+Enter / bấm ra ngoài để xong. Bấm đúp vào khung chữ để sửa.
  Kéo khung tím để di chuyển, kéo góc để đổi cỡ khung, kéo ngay ngoài góc để **xoay** (Shift: bước 15°).
  Hiệu ứng: **viền (Stroke)** và **bóng đổ (Drop Shadow)** – màu, opacity, góc, khoảng cách, độ nhoè; xuất PSD
  thành layer effect thật của Photoshop.
- **Move (V)** kéo layer ảnh (có vùng chọn: chỉ kéo phần trong vùng chọn). **Free Transform (Ctrl+T)**: co giãn
  (kéo góc giữ tỉ lệ, Shift: tự do), xoay (kéo ngoài khung, Shift: 15°), Enter áp dụng, Esc huỷ.
- **Dự án (.lzp)**: *Lưu dự án Lilyzono* (Ctrl+Alt+Shift+S) giữ mọi thứ – layer, khung chữ, kiểu chữ, bản redraw, bản dịch, cả chương –
  để mở lại làm tiếp (kèm ảnh raw, mở được trên máy khác). Đóng app khi chưa lưu sẽ được hỏi.
- **Mở PSD** (Ctrl+O): Background thành raw, layer ảnh giữ tên / ẩn hiện / opacity / blend, layer chữ thành khung
  chữ sửa được (nội dung, font, cỡ, màu, căn lề, xoay, Stroke, Drop Shadow) – sửa tiếp PSD đã type tay.

### Typeset Gái Băng

Chọn **Typeset → Typeset Gái Băng** trong panel Lilyzono, hoặc **Chữ → Typeset Gái Băng** (cũng có nút trên
thanh tuỳ chọn của công cụ T). Font được áp cho các khung của trang và tiếp tục dùng khi gõ, tự điền bản dịch,
xử lý nhiều khung hoặc xử lý cả chương:

| Loại khung | Font | Kiểu |
|---|---|---|
| Bóng thoại tròn kín | 000 WildWords2 TB | Thường |
| Suy nghĩ hình mây / bóng thoại có tia | 000 AnimeAce3 [TeddyBear] | Nghiêng |
| Khung ô vuông | 00 CE Scriptorama Markdown JF | Thường |
| SFX | MTO augie | Thường |

Tool xét hình khung trên **ảnh gốc**; SFX dùng nhãn nhận diện SFX. Đây là nhận dạng hình học nên khung méo,
hở hoặc dính nét tranh có thể bị nhận nhầm: chọn khung rồi đổi **Loại khung** (chọn nhiều khung để đổi cùng lúc).
Chọn **Tự nhận dạng** để bỏ lựa chọn thủ công. Font/kiểu mới hỗ trợ Ctrl+Z; chế độ và loại khung được lưu trong `.lzp`.
Cỡ chữ, màu, viền, bóng đổ và căn lề vẫn giữ theo thiết lập của bạn. Chọn Typeset **Tự do** để dùng font tuỳ ý.
Các font cần được cài trên máy; tool báo những font còn thiếu, không tự tải font.

### Typeset Isshiki

Chọn **Typeset → Typeset Isshiki** (trong Photoshop: **Bộ font → Typeset Isshiki**). Font theo loại thoại:

| Loại thoại | Font | Kiểu | Tool nhận ra bằng |
|---|---|---|---|
| Thoại thường – khung tròn kín | SJ Chanighter Handwriting | Thường | hình khung |
| Suy nghĩ – khung tròn có tia / mây | SJ Chanighter Handwriting | Nghiêng | hình khung |
| Suy nghĩ – khung vuông (cả ô đen chữ trắng) | SJ Chanighter Handwriting | Nghiêng | hình khung |
| Thoại in đậm | 000 ObelixPro TB | Thường | chữ raw in đậm (nét dày, đặc) |
| Suy nghĩ không có khung | SJ SS Rosehip | Thường | chữ nằm trên tranh, không có khung kín |
| Thoại lúng túng | SJ CCComicraft | Thường | câu nói lắp: "e- em…", "a- anh…" |
| SFX | MTO augie | Thường | nhãn SFX |

Chữ raw in đậm được ưu tiên trước, rồi đến câu nói lắp, rồi hình khung. Hai bóng dính nhau (có chỗ thắt sâu) và bóng
viền lượn sóng tính là thoại thường; chỉ viền mây lõm sâu mới là suy nghĩ. Bóng suy nghĩ có nét gạch toả mảnh quanh
viền khó tách khỏi nền tranh nên chưa tự nhận. Nhận nhầm thì chọn khung và đổi **Loại khung** như với Gái Băng.

Bản dịch: dòng ghi chú của nhóm dịch được bỏ qua ("T/N: …", dòng trong ngoặc nhắc tới sfx/xoá/typer) và nhãn chỗ
đặt chữ được bỏ phần nhãn ("chữ dọc: …", "vé tàu:", "chữ trên gáy sách: …"). Tool ghép trang theo số: nếu raw có
trang tranh không chữ mà bản dịch bỏ qua (vd raw 030), đánh số trang trong bản dịch theo đúng tên ảnh.

### Giao diện và hiệu năng

- Thanh công cụ dùng icon nét đơn sắc, tab tài liệu có tên ảnh và dấu `*` khi chưa lưu; màu panel, ô nhập,
  tab và thanh trạng thái thống nhất theo không gian làm việc tối của Photoshop 2023.
- Bố cục panel và kích thước cửa sổ được nhớ khi đóng. Chọn **Cửa sổ → Đặt lại không gian làm việc** để khôi phục.
- Gõ/sửa/di chuyển/xoay chữ chỉ vẽ lại vùng chữ cũ và mới, gồm viền và bóng đổ. Gõ trực tiếp trên ảnh không ghép
  lại trang sau từng phím. Layers giữ nguyên các hàng và ảnh nhỏ khi chỉ đổi chữ; History tái sử dụng icon.
- Nét cọ cập nhật trực tiếp vào bộ đệm ảnh, tránh sao chép cả trang sau mỗi điểm. Viền vùng chọn chỉ chạy khi có
  vùng chọn. Model nhận diện chữ được nhả khỏi RAM sau mỗi lần nhận diện (~400MB), lần sau tự nạp lại.

Benchmark cục bộ so với bản `39bde3f`, cùng trang giả lập 1600×2400, 24 lớp chữ, font Arial; trung vị 9 lần sau
khởi động làm nóng, Qt offscreen trên cùng máy Windows:

| Thao tác | Trước | Sau |
|---|---:|---:|
| Đổi nội dung một khung | 686,2 ms | 6,0 ms |
| Ghép toàn trang | 487,6 ms | 42,4 ms |
| Cập nhật mảnh canvas 64×64 | 4,20 ms | 0,008 ms |
| Cập nhật bảng Layers khi không đổi điểm ảnh | 184,5 ms | 0,52 ms |

Đây là thời gian xử lý của từng thao tác, không phải FPS hay độ trễ đầu cuối; không bao gồm nhận diện/AI.
Tốc độ thực tế phụ thuộc máy, kích thước ảnh, font và số layer/hiệu ứng. Chạy lại bằng:

```bash
python tools/benchmark_editor.py --output build/benchmark.json --preview build/editor.png
python -m unittest discover -s tests -v
```

Các kiểm tra đối chiếu ghép ảnh với đường xử lý tổng quát, sửa chữ/vị trí/hiệu ứng theo vùng, undo/redo,
trạng thái chưa lưu, lưu/mở `.lzp`, cache Layers, gõ trực tiếp và khôi phục bố cục ở cửa sổ 1280×720.

## Luồng làm việc: 3 lớp (raw → Redraw → chữ)

1. **Mở ảnh raw** tiếng Nhật hoặc tiếng Anh (Ctrl+O, kéo thả, hoặc *Mở chương* cho cả thư mục). Chọn chiều đọc ở ô *Raw* trong panel
   Lilyzono: *Tiếng Nhật (manga): phải → trái* / *Tiếng Anh / manhwa: trái → phải*.
2. Tool **tự nhận diện chữ và xoá ngay những chỗ chắc ăn**: chữ trong bóng thoại / hộp dẫn truyện sáng, khép kín.
   SFX chỉ được xoá khi chọn xử lý, tránh xoá nhầm trái tim, tóc hoặc chi tiết tranh chưa có bản dịch.
3. **Lớp chữ**: bấm chọn khung rồi gõ bản dịch vào ô nội dung – chữ hiện ngay, tự co cỡ và căn giữa lòng bóng, dùng
   font đang chọn (font yêu thích ★ / bộ kiểu đã lưu). **Chọn mọi khung đã có bản dịch** (Ctrl+Shift+A) hoặc
   Ctrl/Shift+bấm nhiều khung rồi đổi font/kiểu là áp cho tất cả. Khung chưa xoá chữ (chữ trên tranh): Xử lý
   (Ctrl+Enter) – chữ dịch nằm trên, nền gốc giữ nguyên, khung vàng nét đứt = cần redraw.
4. **Upload bản redraw** (lớp 2): ảnh đã xoá chữ/redraw sẵn (Photoshop, nhờ người khác, AI…) nằm trên raw, dưới lớp
   chữ; chữ đã gõ được giữ và đặt lại lên nền mới. Chế độ chương: chọn thư mục, ghép theo số trang trong tên file.
5. **Lưu PSD** (Ctrl+Alt+S) hoặc **Xuất JPEG** (Ctrl+Alt+Shift+W); cả chương: *Xuất cả chương* → PSD + JPEG / PSD / JPEG.
   PSD gồm `Background` (raw), `Redraw` (bản redraw + phần tool đã xoá) và mỗi bóng một **lớp chữ thật sửa được**
   (máy có Photoshop; không có thì lớp chữ là ảnh).

## Cách dùng nhanh: type cả chương từ file bản dịch

1. **Mở chương** (Ctrl+Shift+O): chọn thư mục ảnh raw (001.jpg, 002.jpg…). Danh sách trang hiện bên trái.
2. **Mở bản dịch** (Ctrl+Alt+D): file `.docx`/`.txt` chia theo trang – dòng chỉ có số là tên trang, các dòng sau là câu dịch
   của trang đó theo thứ tự đọc:
   ```
   1
   Không cần phải làm thế đâu ạ.
   Một Nagi đã nghe được tâm tư của cha mình…
   2
   Nagi…
   ```
   Mỗi trang tự nhận diện chữ và **tự điền câu vào từng khung**: đọc theo panel (phải → trái, trên → dưới), tự bỏ qua
   chỗ không dịch (logo, tên tác giả, SFX nhỏ), tự cho một bóng nhận 2–3 câu khi người dịch tách câu.
3. Chọn font/kiểu chữ, rồi **Xử lý cả chương** → xoá chữ + vẽ lại nền + đặt chữ mọi trang.
4. Kiểm tra từng trang (sửa tay nếu cần), rồi **Xuất PSD cả chương**: mỗi trang một file PSD gồm Background (raw),
   lớp "Redraw" và **lớp chữ thật sửa được trong Photoshop** (máy có Photoshop). Không có Photoshop thì lớp chữ là ảnh.

Không cần giao diện: `python tools/run_chapter.py <thư mục raw> <file docs> <thư mục PSD> --font "000 WildWords2 TB"`
(thêm `--engine simple` và/hoặc `--redrawn <thư mục>` cho hai cách bên dưới).

### Nền khó: để lại tự redraw, hoặc nạp bản đã redraw
Redraw tranh tự động chưa đẹp bằng tay, nên có hai cách chỉ để tool lo phần chắc ăn:
- **Chỉ xoá chỗ đơn giản**: ở mục **Vẽ lại nền** chọn *Chỉ xoá chỗ đơn giản*. Tool xoá chữ trong bóng thoại / nền trơn /
  nền chuyển màu, còn chữ đè lên tranh hoặc lưới chấm thì **giữ nguyên nền gốc** nhưng vẫn đặt bản dịch lên trên.
  Những vùng này có **khung vàng nét đứt**, trang có `⚠N cần redraw` trong danh sách, và trong PSD lớp chữ tên
  `REDRAW - …` để bạn tự redraw ngay dưới lớp chữ.
- **Upload bản redraw**: đã có ảnh xoá chữ/redraw sẵn (làm trong Photoshop, nhờ người khác, AI…) → bấm **Upload bản redraw**.
  Chế độ chương: chọn thư mục, ảnh tự ghép với trang theo số trong tên file (`001.png` ↔ `001.jpg`; cùng số ảnh thì
  ghép theo thứ tự). Bản redraw phải cùng khung với raw (khác cỡ thì tự co). Có bản redraw thì tool dùng nguyên bản
  redraw, không xoá chữ thêm và không đặt chữ đè lên chữ gốc; raw chỉ để nhận diện chỗ đặt chữ và ghép câu dịch.
  PSD: Background = raw, lớp "Redraw" = bản redraw, rồi các lớp chữ. Trong Photoshop, PSD có sẵn layer Redraw trên
  ảnh raw cũng được xử lý như vậy.
- **Trong Photoshop**: *Upload ảnh redraw…* (chọn được nhiều ảnh) hoặc *Upload folder redraw…*: mỗi ảnh vào trang đang
  mở cùng số trang (`012.png` ↔ `12.psd`), thành layer "Bản redraw" ngay trên raw. Từ đó type trang này là type trên
  bản redraw: tool không tự xoá chữ trên raw nữa (lớp "Redraw" tool đã xoá trước đó được ẩn đi).
- **Mở thẳng bản redraw**: mở các trang redraw trong Photoshop, bấm *Upload folder raw…*: mỗi ảnh raw (cùng số trang,
  khác cỡ thì tự co) thành layer ẩn "Raw gốc (Lilyzono)". Sau đó mở bản dịch và type như thường: tool đọc chữ Nhật trên
  raw để ghép câu, chữ đặt lên bản redraw, không xoá gì.

Đo trên chương 18.1 (10 trang, 48 câu, so với PSD typeset tay): tự điền đúng 48/48 câu, xoá hết chữ Nhật trong các
vùng đã dịch, 43/44 bóng có cỡ chữ lệch < 25% so với bản tay. Công cụ đo: `tools/evaluate.py`.

## Cách dùng chi tiết

1. **Mở ảnh raw** (Ctrl+O hoặc kéo thả ảnh vào cửa sổ). Tool tự nhận diện chữ và khoanh các vùng, đánh số theo thứ tự đọc (chọn chiều đọc ở ô *Raw* trong panel Lilyzono).
2. **Mở bản dịch** (Ctrl+Alt+D): file `.docx` hoặc `.txt`, mỗi đoạn là một câu. Dòng bắt đầu bằng `//` bị bỏ qua. Xuống dòng mềm (Shift+Enter trong Word) hoặc dấu `|` để ép xuống dòng trong bóng thoại.
3. **Chọn vùng**: bấm vào khung trên ảnh.
   - Khung **đỏ**: chữ trong bóng thoại / hộp dẫn truyện. Bóng thoại đôi được tách thành từng nửa, mỗi nửa một câu.
     Chữ màu trên truyện đen trắng (vd tên truyện trên trang bìa) cũng được xoá.
   - Khung **cam nét đứt** (nhãn `SFX`): cụm chữ lẻ tự tìm thấy (SFX, chữ nhỏ ngoài bóng). Không cần thì bấm *Xoá khung*.
   - Thiếu vùng thì tự vẽ: chọn công cụ **Khung chữ** (phím K) hoặc **Khung chữ lasso** (Shift+K) rồi kéo chuột.
     Lasso khoanh tự do quanh SFX/chữ vẽ tay: **toàn bộ phần khoanh sẽ bị xoá** rồi vẽ lại nền, bản dịch đặt trong vùng khoanh.
4. **Chọn câu dịch**: bấm một câu trong danh sách (bấm đúp = gán và xử lý luôn). Tool tự gợi ý câu chưa dùng tiếp theo.
5. **Chỉnh chữ** ở bảng **Chữ (Character / Paragraph)** – bố cục theo Photoshop 2023 (đơn vị px ≈ pt ở 72 ppi):
   - Thẻ **Ký tự**: họ font + kiểu mặt chữ (Regular, Light, Bold…), cỡ chữ (*Tự động* = tự co vừa bóng),
     khoảng cách dòng (Leading), Kerning, Tracking, co giãn dọc/ngang, dịch đường cơ sở, màu;
     hàng nút: giả đậm, giả nghiêng, IN HOA, chữ hoa nhỏ, chỉ số trên/dưới, gạch chân, gạch ngang; khử răng cưa.
   - Thẻ **Đoạn**: căn trái/giữa/phải, dàn đều (dòng cuối trái/giữa/phải, dàn đều tất cả), thụt lề trái/phải/dòng đầu,
     khoảng cách trước/sau đoạn, gạch nối. Enter hoặc `|` trong nội dung = sang đoạn mới.
   - **Dáng khối chữ** (khi căn giữa, tự xuống dòng):
     - *Bo tròn* (mặc định): chữ dàn theo đúng hình lòng bóng – mỗi dòng rộng bằng bóng ở độ cao của nó, dòng giữa
       dài, trên/dưới ngắn (9 từ trong bóng tròn → 1·2·3·2·1), khối chữ đặt chính giữa thân bóng (không bị đuôi bóng
       kéo lệch), căn giữa theo nét chữ thật – khoảng hở tới mép bóng bằng nhau 4 phía. Chữ chỉ phủ khoảng
       70% bề ngang lòng bóng, chừa lề xung quanh; bóng hẹp được phủ nhiều hơn để chữ không teo dưới 85% cỡ chuẩn;
       câu ngắn ("Ừ.", "Vâng") được to tới 1,5 lần cỡ chuẩn cho khỏi lọt thỏm; dòng trên/dưới dài cân nhau.
     - *Đều dòng*: các dòng dài gần bằng nhau – tự dùng cho khung ô vuông (bộ font Gái Băng).
     - *Lấp đầy dòng*: cách cũ, mỗi dòng nhét nhiều chữ nhất.
     Không cắt đôi từ ghép thông dụng ("cảm ơn", "trân trọng", "kiểm soát"…), ưu tiên xuống dòng sau dấu phẩy/chấm;
     khi bóng hẹp, chữ được nhỏ đi tối đa 15% để khỏi ngắt xấu. Muốn tự ngắt thì dùng Enter / `|`.
   - Mục **Hiệu ứng**: viền chữ (Stroke) – màu và độ dày. Font thiếu dấu tiếng Việt sẽ có cảnh báo đỏ.
6. **Xử lý** (Ctrl+Enter): xoá chữ gốc, vẽ lại nền, chèn bản dịch. Tool tự chuyển sang vùng tiếp theo.
   - Bóng thoại trắng / nền trơn: tô lại màu nền (tức thì).
   - Nền có hoạ tiết (tranh, lưới chấm, SFX đè lên tranh): vẽ lại theo lựa chọn ở mục **Vẽ lại nền**:
     - *Chỉ xoá chỗ đơn giản* (mặc định): không vẽ lại nền khó nào – SFX, chữ trên tranh giữ nguyên, chữ dịch đặt
       đè lên (layer tên `REDRAW · …` nếu muốn tự redraw sau).
     - *Thường (OpenCV)*: nhanh, nền tranh hơi mờ.
     - *AI Google Gemini* / *AI OpenAI (ChatGPT)*: bấm **API key…** để nhập key và chọn model, có nút *Kiểm tra key*.
       Gửi một vùng rộng quanh chữ (≥ 768px, giữ tỉ lệ khung, xin ảnh 2K) để AI vẽ lại cả mảng tranh; ảnh trả về
       được tự căn khớp với ảnh gốc rồi dán lại **cả khung chữ** (xoá luôn nét mà mask bỏ sót).
       Chọn nhiều vùng gần nhau rồi Xử lý → **gom chung 1 lần gọi API**. Tính phí theo nhà cung cấp.
       Gemini cần tài khoản đã bật thanh toán (gói miễn phí không dùng được model sửa ảnh).
       Key được cất trong Windows Credential Manager, không ghi ra file.
7. **Lưu PSD** (Ctrl+Alt+S) hoặc **Xuất JPEG** (Ctrl+Alt+Shift+W).

Vùng đã xử lý vẫn sửa được: chọn lại rồi đổi chữ/font, ảnh cập nhật ngay. Ctrl+Z hoàn tác từng bước; *Hoàn tác vùng* trả lại ảnh gốc
(hoặc bản redraw đã nạp) ở vùng đó.

### Redraw bằng ChatGPT / Gemini (dùng gói Plus/Pro trên web, không cần API)
Chữ đè lên tranh / lưới chấm mà bản vẽ lại tự động chưa ưng:
1. Chọn vùng (Ctrl+bấm để chọn nhiều vùng gần nhau) → **Redraw bằng ChatGPT / Gemini…** (Ctrl+Shift+R).
   Tool cắt sẵn vùng đủ rộng quanh chữ (tỉ lệ 1:1 / 2:3 / 3:2 – đúng khung ảnh ChatGPT trả về) và copy vào clipboard.
2. Dán (Ctrl+V) vào ChatGPT hoặc Gemini cùng lời nhắc (nút **Copy lời nhắc**).
3. Chuột phải ảnh kết quả → *Copy image* (hoặc tải về) → bấm **Dán kết quả** (hoặc mở / kéo thả file vào hộp thoại).
   Tool tự co về đúng cỡ, căn khớp (AI hay lệch/co giãn nhẹ) và **chỉ thay phần chữ** (viền đỏ trong ảnh xem trước);
   chữ dịch được đặt vào ngay. Không ưng: Ctrl+Z rồi thử lại.

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

## Phím tắt (giống Photoshop)

| Phím | Tác dụng |
|---|---|
| V / M / L / W | Move / Rectangular Marquee / Lasso / Magic Wand |
| J / B / S / E | Spot Healing (AI) / Brush / Clone Stamp (Alt+bấm lấy mẫu) / Eraser |
| T | Type – bấm vào bóng thoại để gõ chữ ngay trong bóng, kéo để tạo khung đoạn văn |
| K / Shift+K | Khung chữ chữ nhật / lasso (xoá chữ gốc & đặt bản dịch) |
| H / Z, giữ Space | Hand / Zoom, kéo ảnh tạm thời |
| [ ] · D · X | Đổi cỡ cọ · màu đen/trắng · đổi chỗ màu trước/nền |
| Ctrl+Z / Ctrl+Shift+Z | Hoàn tác / làm lại nhiều bước (bảng History: bấm để nhảy tới bước bất kỳ) |
| Ctrl+A / Ctrl+D / Ctrl+Shift+I | Chọn tất cả / bỏ chọn / đảo vùng chọn; giữ Shift: cộng, Alt: trừ |
| Shift+F6 | Làm mềm vùng chọn (Feather) |
| Shift+F5 | Tô vùng chọn: Content-Aware / màu trước / màu nền / trắng / đen |
| Delete · Alt+Backspace · Ctrl+Backspace | Xoá trong vùng chọn (Redraw: trả về raw) · tô màu trước · tô màu nền |
| Ctrl+Shift+N / Ctrl+J / Ctrl+E | Layer mới / nhân đôi / gộp xuống |
| Ctrl+] / Ctrl+[ | Đưa layer lên / xuống |
| Ctrl+0 / Ctrl+1 / Ctrl + lăn | Vừa màn hình / 100% / phóng to-thu nhỏ |
| Ctrl+H | Ẩn/hiện khung chữ |
| Ctrl+O | Mở ảnh / dự án .lzp / PSD |
| Ctrl+S / Ctrl+Shift+S | Lưu PSD / lưu PSD thành… |
| Ctrl+Alt+Shift+S | Lưu dự án Lilyzono (.lzp) |
| Ctrl+W / Ctrl+Tab / Ctrl+Shift+Tab | Đóng tab / tab tiếp theo / tab trước |
| Ctrl+Alt+S / Ctrl+Alt+Shift+W | Lưu PSD / Xuất JPEG |
| Ctrl+T · Enter · Esc | Free Transform · áp dụng · huỷ |
| F5 · Ctrl+Enter · Tab | Nhận diện chữ · xử lý khung đang chọn · khung tiếp theo |
| Ctrl+Shift+A / Ctrl+Alt+A | Chọn mọi khung có bản dịch / mọi khung |
| Ctrl+Shift+R | Redraw khung đang chọn qua ChatGPT / Gemini (copy–dán) |
| Mũi tên (Shift) | Nhích khung chữ 1px (10px) |
| F1 | Bảng phím tắt |

## Cấu trúc

- `app/core/detect.py` – nhận diện vùng chữ ([comic-text-detector](https://github.com/dmMaze/comic-text-detector), chạy bằng OpenCV DNN)
- `app/core/clean.py` – xoá chữ, vẽ lại nền (tô màu nền / nền gradient / AI)
- `app/core/cloud.py` – vẽ lại nền bằng API Gemini / OpenAI; `app/core/secrets.py` – cất API key
- `app/core/typeset.py` – dàn chữ, tự co cỡ, xuống dòng bo tròn theo dáng bóng, viền chữ
- `app/core/vi_words.py` – từ ghép tiếng Việt thông dụng (không xuống dòng ở giữa)
- `app/core/docs.py` – đọc bản dịch `.docx` / `.txt`
- `app/core/page.py` – trạng thái trang (ảnh, vùng chữ)
- `app/core/document.py` – layer, ghép ảnh, vùng chọn, cọ, lịch sử hoàn tác
- `app/core/psd_export.py` – xuất PSD (lớp chữ thật qua Photoshop, hoặc lớp ảnh khi không có Photoshop)
- `app/core/psd_import.py` – mở PSD thành layer + khung chữ sửa được
- `app/core/project.py` – lưu / mở dự án `.lzp`
- `app/ui/` – giao diện PySide6
