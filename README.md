# Lilyzono – Tool type truyện

Thay chữ gốc (tiếng Nhật, Hàn, Trung…) trên ảnh truyện bằng bản dịch, dùng font có sẵn trên máy, tự xoá chữ cũ và vẽ lại nền.

## Dùng trực tiếp trong Photoshop 2023 (Windows)

1. Mở ảnh trong Photoshop, chọn **File → Scripts → Browse… → `photoshop/Lilyzono.jsx`**.
2. Dán bản dịch, chọn chế độ xoá chữ rồi **Nhận diện và xem trước**.
3. Bấm **Tạo layer trong Photoshop**: thêm một nhóm gồm layer **Redraw** trong suốt và mỗi câu một **layer chữ thật sửa được**.

Tool giữ các layer có sẵn, kích thước và độ phân giải của tài liệu. Undo một lần để bỏ nhóm vừa thêm.
Typeset Gái Băng tự chọn bốn font theo hình khung; chỉ xoá các vùng đã ghép bản dịch, giữ các SFX chưa dịch.
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

## Cài đặt & chạy từ mã nguồn (Windows)

Cần Python 3.10 trở lên ([python.org](https://www.python.org/downloads/), nhớ tick *Add Python to PATH*).

Bấm đúp **`run.bat`**. Lần đầu tool tự cài thư viện và tự tải 2 model AI về thư mục `models/`: nhận diện chữ (~95MB) và vẽ lại nền LaMa (~200MB).

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
  vẽ lại phần trong vùng chọn bằng AI LaMa; *Tạo khung chữ* biến vùng chọn thành khung chữ.
- **Redraw tay**: Brush, Eraser (trên Redraw: trả về raw), Clone Stamp, Spot Healing (tô lên chữ, nhả chuột là AI
  vẽ lại); mọi nét đều bị giới hạn trong vùng chọn nếu có.
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

### Giao diện và hiệu năng

- Thanh công cụ dùng icon nét đơn sắc, tab tài liệu có tên ảnh và dấu `*` khi chưa lưu; màu panel, ô nhập,
  tab và thanh trạng thái thống nhất theo không gian làm việc tối của Photoshop 2023.
- Bố cục panel và kích thước cửa sổ được nhớ khi đóng. Chọn **Cửa sổ → Đặt lại không gian làm việc** để khôi phục.
- Gõ/sửa/di chuyển/xoay chữ chỉ vẽ lại vùng chữ cũ và mới, gồm viền và bóng đổ. Gõ trực tiếp trên ảnh không ghép
  lại trang sau từng phím. Layers giữ nguyên các hàng và ảnh nhỏ khi chỉ đổi chữ; History tái sử dụng icon.
- Nét cọ cập nhật trực tiếp vào bộ đệm ảnh, tránh sao chép cả trang sau mỗi điểm. Viền vùng chọn chỉ chạy khi có
  vùng chọn. LaMa dùng tối đa 4 luồng CPU và tắt vòng chờ bận để nhường tài nguyên cho giao diện
  ([cấu hình luồng ONNX Runtime](https://onnxruntime.ai/docs/performance/tune-performance/threading.html)).

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
  ghép theo thứ tự). Bản redraw phải cùng khung với raw (khác cỡ thì tự co). Chữ vẫn được nhận diện trên raw, bản dịch
  type lên bản redraw; chỗ bản redraw còn sót chữ Nhật thì tool tự xoá như thường. PSD: Background = raw,
  lớp "Redraw" = bản redraw, rồi các lớp chữ.

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
     - *Tự động* (mặc định): SFX nhỏ và vùng tự khoanh vẽ lại bằng LaMa; chữ trên tranh, SFX lớn để bạn tự redraw.
     - *Chỉ xoá chỗ đơn giản*: không vẽ lại nền khó nào, để bạn tự redraw (xem mục trên).
     - *Thường (OpenCV)*: nhanh, nền tranh hơi mờ.
     - *AI LaMa* (mặc định): chạy trên máy, miễn phí, không cần mạng (vài giây mỗi vùng, model nạp ngầm khi mở ảnh).
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
| Shift+F5 | Tô vùng chọn: Content-Aware (AI LaMa) / màu trước / màu nền / trắng / đen |
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
- `app/core/lama.py` – vẽ lại nền bằng [LaMa](https://github.com/advimman/lama) (ONNX, onnxruntime)
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
