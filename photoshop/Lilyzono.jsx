#target photoshop

// Lilyzono: add editable typesetting layers to the current Photoshop document.
var LilyzonoPS = (function () {
    var context = null;
    // Đường dẫn file script này: bộ xử lý dùng để gọi lại Photoshop khi xong.
    var SCRIPT_FILE = (typeof $ !== "undefined" && $.fileName) ? $.fileName : "";

    function encode(value) {
        if (value === null) return "null";
        if (typeof value === "string") return '"' + value.replace(/[\\"\x00-\x1f]/g, function (c) {
            var n = c.charCodeAt(0).toString(16);
            return "\\u" + ("0000" + n).slice(-4);
        }) + '"';
        if (typeof value === "number" || typeof value === "boolean") return String(value);
        var parts = [], i, key;
        if (value instanceof Array) {
            for (i = 0; i < value.length; i++) parts.push(encode(value[i]));
            return "[" + parts.join(",") + "]";
        }
        for (key in value) if (value.hasOwnProperty(key)) parts.push(encode(key) + ":" + encode(value[key]));
        return "{" + parts.join(",") + "}";
    }

    // ExtendScript does not consistently provide JSON. Parse data without eval.
    function decode(source) {
        var at = 0;
        function space() { while (/\s/.test(source.charAt(at)) && at < source.length) at++; }
        function fail() { throw new Error("Dữ liệu trả về không hợp lệ (JSON, vị trí " + at + ")."); }
        function string() {
            var out = "", ch, escaped, hex;
            at++;
            while (at < source.length) {
                ch = source.charAt(at++);
                if (ch === '"') return out;
                if (ch === "\\") {
                    escaped = source.charAt(at++);
                    if (escaped === "u") {
                        hex = source.substr(at, 4);
                        if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail();
                        out += String.fromCharCode(parseInt(hex, 16)); at += 4;
                    } else {
                        var codes = {'"':'"', "\\":"\\", "/":"/", b:"\b", f:"\f", n:"\n", r:"\r", t:"\t"};
                        if (!codes.hasOwnProperty(escaped)) fail();
                        out += codes[escaped];
                    }
                } else { if (ch.charCodeAt(0) < 32) fail(); out += ch; }
            }
            fail();
        }
        function value() {
            space();
            var ch = source.charAt(at), out, key, match;
            if (ch === '"') return string();
            if (ch === "[") {
                at++; out = []; space();
                if (source.charAt(at) === "]") { at++; return out; }
                while (true) {
                    out.push(value()); space(); ch = source.charAt(at++);
                    if (ch === "]") return out;
                    if (ch !== ",") fail();
                }
            }
            if (ch === "{") {
                at++; out = {}; space();
                if (source.charAt(at) === "}") { at++; return out; }
                while (true) {
                    space(); if (source.charAt(at) !== '"') fail(); key = string(); space();
                    if (source.charAt(at++) !== ":" || key === "__proto__") fail();
                    out[key] = value(); space(); ch = source.charAt(at++);
                    if (ch === "}") return out;
                    if (ch !== ",") fail();
                }
            }
            match = /^(true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)/.exec(source.substr(at));
            if (!match) fail();
            at += match[0].length;
            if (match[0] === "true") return true;
            if (match[0] === "false") return false;
            if (match[0] === "null") return null;
            return Number(match[0]);
        }
        var result = value(); space(); if (at !== source.length) fail(); return result;
    }

    function write(file, text, encoding) {
        file.encoding = encoding || "UTF-8";
        if (!file.open("w")) throw new Error("Không ghi được " + file.fsName);
        try { file.write(text); } finally { file.close(); }
    }
    function read(file) {
        file.encoding = "UTF-8";
        if (!file.open("r")) throw new Error("Không đọc được " + file.fsName);
        try { return file.read().replace(/^\uFEFF/, ""); } finally { file.close(); }
    }
    function quote(path) { return '"' + path + '"'; }
    function vbsString(text) {
        var parts = [], buffer = "", i, ch;
        function flush() { if (buffer) { parts.push('"' + buffer.replace(/"/g, '""') + '"'); buffer = ""; } }
        for (i = 0; i < text.length; i++) {
            ch = text.charCodeAt(i);
            if (ch > 127) { flush(); parts.push("ChrW(" + ch + ")"); }
            else buffer += text.charAt(i);
        }
        flush(); return parts.length ? parts.join(" & ") : '""';
    }

    function workerCommand(folder, job) {
        // Máy có sẵn dự án (photoshop/ hoặc dist/LilyzonoPhotoshop/ trong repo): ưu tiên Python của dự án – luôn chạy
        // code mới nhất và không bị Smart App Control chặn như exe tự build. Không có dự án: dùng exe đóng gói kèm.
        var root = folder.parent, python, entry;
        for (var up = 0; up < 2 && root; up++, root = root.parent) {
            python = new File(root.fsName + "/.venv/Scripts/pythonw.exe");
            entry = new File(root.fsName + "/LilyzonoPhotoshop.py");
            if (python.exists && entry.exists) return quote(python.fsName) + " " + quote(entry.fsName) + " --job " + quote(job.fsName);
        }
        var exe = new File(folder.fsName + "/LilyzonoPhotoshop.exe");
        if (exe.exists) return quote(exe.fsName) + " --job " + quote(job.fsName);
        throw new Error("Không tìm thấy bộ xử lý. Giữ Lilyzono.jsx cạnh LilyzonoPhotoshop.exe, hoặc chạy build_photoshop.bat.");
    }

    function snapshot(doc, file) {
        var copy = doc.duplicate("Lilyzono snapshot", true);
        try {
            if (copy.mode !== DocumentMode.RGB) copy.changeMode(ChangeMode.RGB);
            copy.bitsPerChannel = BitsPerChannelType.EIGHT;
            copy.saveAs(file, new PNGSaveOptions(), true, Extension.LOWERCASE);
        } finally { copy.close(SaveOptions.DONOTSAVECHANGES); app.activeDocument = doc; }
    }

    // Chỉ layer dưới cùng (ảnh raw), bỏ chữ / Redraw: để so với ảnh redraw SFX. Chép layer đó sang một tài liệu trống
    // cùng cỡ (xoá / ẩn nhóm layer trong bản sao của tài liệu không phải lúc nào Photoshop cũng cho).
    function snapshotBackground(doc, file) {
        var oldDialogs = app.displayDialogs, oldRuler = app.preferences.rulerUnits, size = pixelSize(doc), tmp = null;
        app.displayDialogs = DialogModes.NO;
        app.preferences.rulerUnits = Units.PIXELS;
        try {
            tmp = app.documents.add(size[0], size[1], doc.resolution, "Lilyzono nền gốc", NewDocumentMode.RGB,
                                    DocumentFill.WHITE);
            app.activeDocument = doc;
            doc.layers[doc.layers.length - 1].duplicate(tmp, ElementPlacement.PLACEATBEGINNING);
            app.activeDocument = tmp;
            tmp.layers[0].visible = true;
            tmp.saveAs(file, new PNGSaveOptions(), true, Extension.LOWERCASE);
        } finally {
            if (tmp) tmp.close(SaveOptions.DONOTSAVECHANGES);
            app.activeDocument = doc;
            app.displayDialogs = oldDialogs; app.preferences.rulerUnits = oldRuler;
        }
    }

    function pixelSize(doc) {
        // UnitValue.as("px") uses 72 ppi when the ruler is in inches/cm.
        // Ask Photoshop for pixel-valued dimensions before converting them.
        var old = app.preferences.rulerUnits;
        try {
            app.preferences.rulerUnits = Units.PIXELS;
            return [Math.round(doc.width.value), Math.round(doc.height.value)];
        } finally { app.preferences.rulerUnits = old; }
    }

    // Không dùng regex /i với chữ tiếng Việt: engine ExtendScript của Photoshop 2023 lặp vô hạn với câu bắt đầu
    // bằng "Tr" + chữ có dấu (vd "Trở nên…") khiến Photoshop treo cứng. Tách dòng / bỏ khoảng trắng / nhận dòng số
    // trang ("13", "Trang 13", "tr.13", "page 2", "p.5", "#4", "13:") đều làm bằng tay, từng ký tự.
    function isSpace(code) {
        return code <= 32 || code === 0xA0 || code === 0xFEFF || code === 0x3000 || (code >= 0x2000 && code <= 0x200B);
    }
    function trim(text) {
        var a = 0, b = text.length;
        while (a < b && isSpace(text.charCodeAt(a))) a++;
        while (b > a && isSpace(text.charCodeAt(b - 1))) b--;
        return text.substring(a, b);
    }
    function splitLines(text) {
        var out = [], start = 0, i, code;
        for (i = 0; i < text.length; i++) {
            code = text.charCodeAt(i);
            if (code === 13 || code === 10) {
                out.push(text.substring(start, i));
                if (code === 13 && text.charCodeAt(i + 1) === 10) i++;
                start = i + 1;
            }
        }
        out.push(text.substring(start));
        return out;
    }
    function startsWithAscii(text, prefix) {
        if (text.length < prefix.length) return false;
        for (var i = 0; i < prefix.length; i++) {
            var c = text.charCodeAt(i);
            if (c >= 65 && c <= 90) c += 32;  // chỉ đổi hoa → thường với chữ ASCII
            if (c !== prefix.charCodeAt(i)) return false;
        }
        return true;
    }
    function pageHeader(line) {
        var prefixes = ["trang", "page", "tr.", "tr", "p.", "p", ""], k, rest, j, tail;
        for (k = 0; k < prefixes.length; k++) {
            if (!startsWithAscii(line, prefixes[k])) continue;
            rest = trim(line.substring(prefixes[k].length));
            if (rest.charAt(0) === "#") rest = trim(rest.substring(1));
            j = 0;
            while (j < rest.length && rest.charCodeAt(j) >= 48 && rest.charCodeAt(j) <= 57) j++;
            if (j < 1 || j > 4) continue;
            tail = trim(rest.substring(j));
            if (tail === "" || (tail.length === 1 && ":.-\u2013".indexOf(tail) >= 0)) return String(Number(rest.substring(0, j)));
        }
        return null;
    }

    function translationLines(text, name) {
        var raw = splitLines(text.charCodeAt(0) === 0xFEFF ? text.substring(1) : text), pages = {}, page = "0";
        var header = false, i, line, number;
        for (i = 0; i < raw.length; i++) {
            line = trim(raw[i]);
            if (!line || line.indexOf("//") === 0) continue;
            number = pageHeader(line);
            if (number !== null) { page = number; header = true; }
            else { if (!pages[page]) pages[page] = []; pages[page].push(line); }
        }
        if (!header) return pages["0"] || [];
        var numbers = name.replace(/\.[^.]*$/, "").match(/\d+/g);
        var key = numbers ? String(Number(numbers[numbers.length - 1])) : "0";
        if (pages[key]) return pages[key];
        var keys = [];
        for (key in pages) if (pages.hasOwnProperty(key)) keys.push(key);
        if (keys.length === 1) return pages[keys[0]];
        throw new Error("Không tìm thấy bản dịch theo số trang của " + name + ". Hãy dán riêng thoại của trang này.");
    }

    var PROFILES = [
        {key:"gai_bang", name:"Gái Băng", fonts:"WildWords2 · AnimeAce3 nghiêng · CE Scriptorama · MTO augie"},
        {key:"isshiki", name:"Isshiki", fonts:"Chanighter Handwriting (suy nghĩ: nghiêng) · ObelixPro (in đậm) · " +
                                             "SS Rosehip (không khung) · CCComicraft (lúng túng)"}
    ];
    var SETTINGS = "lilyzono-settings";

    function savedProfile() {
        try { return app.getCustomOptions(SETTINGS).getString(stringIDToTypeID("profile")); }
        catch (error) { return PROFILES[0].key; }
    }

    function saveProfile(key) {
        try {
            var desc = new ActionDescriptor();
            desc.putString(stringIDToTypeID("profile"), key);
            app.putCustomOptions(SETTINGS, desc, true);
        } catch (error) {}
    }

    function groupName(data) { return "Lilyzono · " + (data.profile_name || "Typeset"); }

    function options(doc, display) {
        var win = new Window("dialog", "Lilyzono · Typeset");
        win.orientation = "column"; win.alignChildren = "fill";
        var size = pixelSize(doc), translationFile = null;
        win.add("statictext", undefined, doc.name + " · " + size[0] + " × " + size[1] + " px · " +
                app.documents.length + " ảnh đang mở");
        win.add("statictext", undefined, "Bản dịch: mỗi câu một dòng, theo thứ tự đọc. Làm cả chương: thêm số trang " +
                "(1, 2, …) trên thoại mỗi trang.", {multiline:true});
        var input = win.add("edittext", undefined, "", {multiline:true, scrolling:true});
        input.preferredSize = [560, 230];
        var row = win.add("group");
        var load = row.add("button", undefined, "Mở bản dịch (.txt / .docx)…");
        var fileLabel = row.add("statictext", undefined, "", {truncate:"middle"}); fileLabel.preferredSize = [330, 20];
        load.onClick = function () {
            var file = File.openDialog("Chọn bản dịch", "Bản dịch:*.txt;*.docx");
            if (!file) return;
            // Không dùng regex /i (lỗi treo của ExtendScript với tên tiếng Việt). Word: bộ xử lý đọc.
            if (file.name.substr(file.name.length - 5).toLowerCase() === ".docx") {
                translationFile = file.fsName; input.text = ""; fileLabel.text = file.name + " (đọc khi xử lý)";
            } else {
                try { input.text = read(file); translationFile = null; fileLabel.text = file.name; }
                catch (error) { alert(error.message); }
            }
        };
        input.onChanging = function () { if (translationFile && input.text) { translationFile = null; fileLabel.text = ""; } };
        var rtl = win.add("checkbox", undefined, "Đọc manga: phải → trái"); rtl.value = true;
        win.add("statictext", undefined, "Xoá chữ: chỉ chỗ dễ (bóng thoại, nền trơn). Chữ trên tranh và SFX đặt đè lên " +
                "bản gốc – layer tên REDRAW; redraw tay hoặc Upload bản redraw SFX.", {multiline:true});
        var pick = win.add("group");
        pick.add("statictext", undefined, "Bộ font:");
        var names = [], chosen = savedProfile(), i;
        for (i = 0; i < PROFILES.length; i++) names.push("Typeset " + PROFILES[i].name);
        var profileBox = pick.add("dropdownlist", undefined, names);
        var fontLabel = win.add("statictext", undefined, "", {multiline:true}); fontLabel.preferredSize = [560, 34];
        profileBox.onChange = function () {
            if (profileBox.selection) fontLabel.text = "Font: " + PROFILES[profileBox.selection.index].fonts;
        };
        profileBox.selection = 0;
        for (i = 0; i < PROFILES.length; i++) if (PROFILES[i].key === chosen) profileBox.selection = i;
        function profile() {
            var key = PROFILES[profileBox.selection ? profileBox.selection.index : 0].key;
            saveProfile(key);
            return key;
        }
        var buttons = win.add("group"); buttons.alignment = "fill";
        var sfx = buttons.add("button", undefined, "Upload bản redraw SFX…");
        var gap = buttons.add("group"); gap.alignment = ["fill", "center"];
        buttons.add("button", undefined, "Huỷ", {name:"cancel"});
        var batch = buttons.add("button", undefined, "Làm cả chương (" + app.documents.length + " ảnh)");
        var go = buttons.add("button", undefined, "Nhận diện và xem trước", {name:"ok"});
        function translation(request) {
            if (translationFile) request.translation_file = translationFile;
            else request.translation_text = input.text;
            return request;
        }
        sfx.onClick = function () {
            var file = File.openDialog("Ảnh đã redraw SFX của " + doc.name + " (cả trang, cùng khung)",
                                       "Ảnh:*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tif;*.tiff");
            if (!file) return;
            win.result = {protocol:1, mode:"sfx", redrawn:file.fsName};
            win.close(1);
        };
        batch.onClick = function () {
            if (!translationFile && !trim(input.text)) { alert("Mở / dán bản dịch cả chương (có số trang) trước."); return; }
            if (!translationFile) {
                var missing = [], i;
                for (i = 0; i < app.documents.length; i++) {
                    try { if (!translationLines(input.text, app.documents[i].name).length) missing.push(app.documents[i].name); }
                    catch (error) { missing.push(app.documents[i].name); }
                }
                if (missing.length === app.documents.length) { alert("Bản dịch không có số trang khớp tên ảnh nào (vd 013.jpg ↔ 13)."); return; }
                if (missing.length && !confirm("Không có thoại cho: " + missing.join(", ") + ". Bỏ qua các trang này?")) return;
            }
            win.result = translation({protocol:1, profile:profile(), mode:"batch", right_to_left:rtl.value, engine:"simple"});
            win.close(1);
        };
        go.onClick = function () {
            var request = {protocol:1, profile:profile(), mode:"page", right_to_left:rtl.value, engine:"simple"};
            if (translationFile) { win.result = translation(request); win.close(1); return; }
            var lines;
            try { lines = translationLines(input.text, doc.name); } catch (error) { alert(error.message); return; }
            if (!lines.length) { alert("Nhập bản dịch trước khi tiếp tục."); return; }
            request.lines = lines;
            win.result = request;
            win.close(1);
        };
        if (display === false) return win;
        if (win.show() !== 1) return null;
        return win.result;
    }

    // Chạy bộ xử lý ngầm và trả quyền cho Photoshop NGAY (không chờ trong Photoshop: chờ ở đây làm Photoshop
    // đứng hình cả phút, không bấm Huỷ hay tắt được). Bộ xử lý có cửa sổ tiến độ + nút Huỷ riêng, xong thì tự gọi
    // lại Photoshop (LilyzonoPS.finish) để xem trước và tạo layer.
    function launchAsync(scriptFolder, folder, request) {
        var file = new File(folder.fsName + "/request.json");
        write(file, encode(request));
        var launcher = new File(folder.fsName + "/run.vbs");
        var code = 'On Error Resume Next\r\n' +
            'CreateObject("WScript.Shell").Run ' + vbsString(workerCommand(scriptFolder, file)) + ', 1, False\r\n' +
            'If Err.Number <> 0 Then MsgBox ' + vbsString("Không khởi động được bộ xử lý Lilyzono: ") +
            ' & Err.Description, 16, "Lilyzono"\r\n';
        write(launcher, code, "ASCII");
        if (!launcher.execute()) throw new Error("Không khởi động được bộ xử lý cục bộ (Windows Script Host).");
        return null;
    }

    function runWorker(scriptFolder, folder, request, interactive) {
        if (interactive !== false) return launchAsync(scriptFolder, folder, request);
        var file = new File(folder.fsName + "/request.json"), result = new File(folder.fsName + "/result.json");
        write(file, encode(request));
        var launcher = new File(folder.fsName + "/run.vbs");
        var exitFile = new File(folder.fsName + "/exit-code.txt");
        // WSH launches the local worker hidden; no shell command interpolation.
        var launchCode = 'Dim code, output\r\nOn Error Resume Next\r\n' +
            'code = CreateObject("WScript.Shell").Run(' + vbsString(workerCommand(scriptFolder, file)) + ', 0, True)\r\n' +
            'If Err.Number <> 0 Then code = Err.Number\r\nOn Error GoTo 0\r\n' +
            'Set output = CreateObject("Scripting.FileSystemObject").CreateTextFile(' + vbsString(exitFile.fsName) + ', True, False)\r\n' +
            'output.Write CStr(code)\r\noutput.Close\r\n';
        write(launcher, launchCode, "ASCII");
        function checkExit() {
            if (exitFile.exists && !result.exists) throw new Error("Bộ xử lý dừng trước khi trả kết quả (mã " + read(exitFile) + "). Thư mục: " + folder.fsName);
        }
        if (!launcher.execute()) throw new Error("Không khởi động được bộ xử lý cục bộ (Windows Script Host).");
        var started = new Date().getTime();
        while (!result.exists) {
            checkExit();
            if (new Date().getTime() - started > 600000) throw new Error("Xử lý quá 10 phút. Xem thư mục: " + folder.fsName);
            $.sleep(150);
        }
        var backgroundData = decode(read(result));
        if (backgroundData.protocol !== 1 || !backgroundData.ok) throw new Error(backgroundData.error || "Bộ xử lý trả về lỗi.");
        return backgroundData;
    }

    function preview(data, display) {
        var win = new Window("dialog", "Lilyzono · Kiểm tra trước khi tạo layer");
        var body = win.add("group"); body.alignChildren = "top";
        body.add("image", undefined, new File(data.preview));
        var right = body.add("group"); right.orientation = "column"; right.alignChildren = "fill";
        right.add("statictext", undefined, data.detected + " vùng nhận diện · " + data.layers.length + " layer chữ");
        var labels = [], i;
        for (i = 0; i < data.layers.length; i++) labels.push((i + 1) + ". " + data.layers[i].contents.replace(/[\r\u0003]/g, " "));
        var list = right.add("listbox", undefined, labels); list.preferredSize = [360, 380];
        var info = right.add("statictext", undefined, "", {multiline:true}); info.preferredSize = [360, 65];
        list.onChange = function () { if (list.selection) { var layer = data.layers[list.selection.index]; info.text = layer.family + (layer.faux_italic ? " · nghiêng" : "") + "\n" + layer.size + " px"; } };
        if (labels.length) list.selection = 0;
        if (data.needs_redraw) right.add("statictext", undefined, data.needs_redraw + " vùng nền khó cần redraw thêm.");
        var buttons = win.add("group"); buttons.alignment = "right";
        buttons.add("button", undefined, "Huỷ", {name:"cancel"});
        buttons.add("button", undefined, "Tạo layer trong Photoshop", {name:"ok"});
        if (display === false) return win;
        return win.show() === 1;
    }

    function apply() {
        var doc = context.doc, data = context.data;
        var oldRuler = app.preferences.rulerUnits, oldType = app.preferences.typeUnits, oldDialogs = app.displayDialogs;
        var opened = null;
        try {
            app.preferences.rulerUnits = Units.PIXELS; app.preferences.typeUnits = TypeUnits.PIXELS;
            app.displayDialogs = DialogModes.NO;
            var group = doc.layerSets.add(); group.name = groupName(data);
            opened = app.open(new File(data.redraw));
            var redraw = opened.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
            opened.close(SaveOptions.DONOTSAVECHANGES); opened = null;
            app.activeDocument = doc;
            redraw.move(group, ElementPlacement.INSIDE); redraw.name = "Redraw";
            for (var i = 0; i < data.layers.length; i++) {
                var layer = data.layers[i], art = group.artLayers.add();
                art.kind = LayerKind.TEXT;
                var text = art.textItem, slack = layer.width * .12 + 4;
                text.kind = TextType.PARAGRAPHTEXT;
                text.contents = layer.contents;
                text.font = layer.font;
                // Preserve the document's resolution: typography is measured in pixels.
                text.size = new UnitValue(layer.size * 72 / doc.resolution, "pt");
                text.useAutoLeading = false;
                text.leading = new UnitValue(layer.leading * 72 / doc.resolution, "pt");
                text.fauxBold = layer.faux_bold; text.fauxItalic = layer.faux_italic;
                text.justification = Justification[layer.justification];
                text.antiAliasMethod = AntiAlias.SMOOTH;
                var color = new SolidColor(); color.rgb.hexValue = layer.color; text.color = color;
                // Paragraph dimensions use type units and otherwise interpret
                // UnitValue("px") at 72 ppi, widening the box in 300 ppi files.
                text.width = new UnitValue((layer.width + slack) * 72 / doc.resolution, "pt");
                text.height = new UnitValue((layer.block_height + layer.size * 2) * 72 / doc.resolution, "pt");
                text.position = [new UnitValue(layer.left - slack / 2, "px"), new UnitValue(layer.first_baseline - layer.ascent, "px")];
                text.hyphenation = false;
                art.name = (layer.redraw ? "REDRAW · " : "") + (i + 1) + ". " + layer.contents.replace(/[\r\u0003]/g, " ");
            }
            doc.activeLayer = group;
        } finally {
            if (opened) opened.close(SaveOptions.DONOTSAVECHANGES);
            app.activeDocument = doc;
            app.preferences.rulerUnits = oldRuler; app.preferences.typeUnits = oldType; app.displayDialogs = oldDialogs;
        }
    }

    function docId(doc) { try { return doc.id; } catch (error) { return null; } }

    function prepare(doc, request, scriptFolder, interactive) {
        var folder = new Folder(Folder.temp.fsName + "/LilyzonoPS-" + new Date().getTime() + "-" + Math.floor(Math.random() * 100000));
        if (!folder.create()) throw new Error("Không tạo được thư mục xử lý tạm.");
        workerCommand(scriptFolder, new File(folder.fsName + "/request.json"));
        if (request.mode === "batch") {
            // Cả chương: chụp mọi ảnh đang mở (Photoshop bận ~1 giây mỗi trang), rồi giao hết cho bộ xử lý.
            var current = app.activeDocument, pages = [], i, d, f;
            for (i = 0; i < app.documents.length; i++) {
                d = app.documents[i];
                f = new File(folder.fsName + "/page" + i + ".png");
                app.activeDocument = d;
                snapshot(d, f);
                pages.push({image:f.fsName, size:pixelSize(d), doc_name:d.name, doc_id:docId(d)});
            }
            app.activeDocument = current;
            request.pages = pages;
        } else {
            var input = new File(folder.fsName + "/input.png");
            if (request.mode === "sfx") snapshotBackground(doc, input); else snapshot(doc, input);
            request.image = input.fsName; request.size = pixelSize(doc);
        }
        request.doc_name = doc.name;
        request.doc_id = docId(doc);
        request.callback = interactive !== false;
        request.script = SCRIPT_FILE ? new File(SCRIPT_FILE).fsName : scriptFolder.fsName + "/Lilyzono.jsx";
        return runWorker(scriptFolder, folder, request, interactive);
    }

    function applyToDocument(doc, data) {
        var size = pixelSize(doc);
        if (data.size[0] !== size[0] || data.size[1] !== size[1]) throw new Error("Kích thước trang đã thay đổi.");
        for (var i = 0; i < data.layers.length; i++) {
            try { app.fonts.getByName(data.layers[i].font); }
            catch (error) { throw new Error("Photoshop chưa nhận font " + data.layers[i].family + ". Cài font rồi mở lại Photoshop."); }
        }
        context = {doc:doc, data:data};
        app.activeDocument = doc;
        var before = doc.activeHistoryState;
        try { doc.suspendHistory(groupName(data), "LilyzonoPS.apply()"); }
        catch (error) { doc.activeHistoryState = before; throw error; }
        finally { context = null; }
    }

    // Layer "Redraw SFX": nằm ngay trên layer Redraw của nhóm Lilyzono (dưới mọi layer chữ).
    function applySfx() {
        var doc = context.doc, data = context.data, opened = null, oldDialogs = app.displayDialogs;
        try {
            app.displayDialogs = DialogModes.NO;
            opened = app.open(new File(data.layer));
            var layer = opened.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
            opened.close(SaveOptions.DONOTSAVECHANGES); opened = null;
            app.activeDocument = doc;
            layer.name = "Redraw SFX";
            var group = null, redraw = null, i;
            for (i = 0; i < doc.layerSets.length; i++) if (doc.layerSets[i].name.indexOf("Lilyzono") === 0) { group = doc.layerSets[i]; break; }
            if (group) { try { redraw = group.artLayers.getByName("Redraw"); } catch (error) { redraw = null; } }
            if (redraw) layer.move(redraw, ElementPlacement.PLACEBEFORE);
            else if (group) layer.move(group, ElementPlacement.PLACEATEND);
            else layer.move(doc.layers[doc.layers.length - 1], ElementPlacement.PLACEBEFORE);
            doc.activeLayer = layer;
        } finally {
            if (opened) opened.close(SaveOptions.DONOTSAVECHANGES);
            app.activeDocument = doc; app.displayDialogs = oldDialogs;
        }
    }

    function finishSfx(data) {
        var doc = findDocument(data.doc_id, data.doc_name), size = pixelSize(doc);
        if (data.size[0] !== size[0] || data.size[1] !== size[1]) throw new Error("Kích thước trang đã thay đổi.");
        app.activeDocument = doc;
        context = {doc:doc, data:data};
        try { doc.suspendHistory("Lilyzono · Redraw SFX", "LilyzonoPS.applySfx()"); }
        finally { context = null; }
        try { app.refresh(); } catch (error) {}
        alert("Đã thêm layer Redraw SFX vào " + doc.name + " (" + data.changed_percent + "% trang khác ảnh gốc).");
        return "ok";
    }

    function finishBatch(data, display) {
        var ok = [], labels = [], i, p, n;
        for (i = 0; i < data.pages.length; i++) {
            p = data.pages[i];
            if (p.ok) {
                ok.push(p);
                labels.push("✓ " + p.doc_name + ": " + p.layers.length + " layer chữ" +
                            (p.needs_redraw ? " · " + p.needs_redraw + " chỗ chữ đè bản gốc" : ""));
            } else labels.push("✗ " + p.doc_name + ": " + p.error);
        }
        if (!ok.length) throw new Error("Không làm được trang nào:\n" + labels.join("\n"));
        var win = new Window("dialog", "Lilyzono · Cả chương");
        win.orientation = "column"; win.alignChildren = "fill";
        win.add("statictext", undefined, ok.length + "/" + data.pages.length + " trang sẵn sàng. Mỗi trang thành một nhóm layer " +
                "(Ctrl+Z một lần trong trang đó để bỏ).", {multiline:true});
        var list = win.add("listbox", undefined, labels); list.preferredSize = [560, 300];
        var buttons = win.add("group"); buttons.alignment = "right";
        buttons.add("button", undefined, "Huỷ", {name:"cancel"});
        buttons.add("button", undefined, "Tạo layer cho " + ok.length + " trang", {name:"ok"});
        if (display === false) return win;
        if (win.show() !== 1) return "cancel";
        var progress = new Window("palette", "Lilyzono"), status = progress.add("statictext", undefined, "");
        status.preferredSize = [380, 20];
        progress.show();
        var errors = [];
        for (n = 0; n < ok.length; n++) {
            p = ok[n];
            status.text = "Đang tạo layer trang " + (n + 1) + "/" + ok.length + ": " + p.doc_name;
            progress.update();
            try { applyToDocument(findDocument(p.doc_id, p.doc_name), p); }
            catch (error) { errors.push(p.doc_name + ": " + error.message); }
        }
        progress.close();
        try { app.refresh(); } catch (error) {}
        alert("Đã tạo layer cho " + (ok.length - errors.length) + "/" + data.pages.length + " trang." +
              (errors.length ? "\nLỗi:\n" + errors.join("\n") : ""));
        return "ok";
    }

    function findDocument(id, name) {
        var i, doc;
        for (i = 0; i < app.documents.length; i++) {
            doc = app.documents[i];
            try { if (id !== null && id !== undefined && doc.id === id) return doc; } catch (error) {}
        }
        for (i = 0; i < app.documents.length; i++) if (app.documents[i].name === name) return app.documents[i];
        throw new Error("Tài liệu " + name + " đã bị đóng trong lúc xử lý.");
    }

    // Bộ xử lý gọi hàm này (qua scripting của Photoshop) khi đã có kết quả.
    function finish(folderPath) {
        var data = decode(read(new File(folderPath + "/result.json")));
        if (data.protocol !== 1 || !data.ok) throw new Error(data.error || "Bộ xử lý trả về lỗi.");
        try { app.bringToFront(); } catch (error) {}
        if (data.mode === "batch") return finishBatch(data);
        if (data.mode === "sfx") return finishSfx(data);
        var doc = findDocument(data.doc_id, data.doc_name);
        app.activeDocument = doc;
        if (!preview(data)) return "cancel";
        applyToDocument(doc, data);
        // Được gọi từ bộ xử lý (ngoài Photoshop): Photoshop không tự vẽ lại màn hình/bảng Layers cho tới khi người
        // dùng đụng chuột, trông như chưa tạo gì. Buộc vẽ lại ngay.
        try { app.refresh(); } catch (error) {}
        return "ok";
    }

    function main(scriptFolder) {
        if (!/windows/i.test($.os)) throw new Error("Bản này hỗ trợ Photoshop 2023 trên Windows.");
        if (!app.documents.length) throw new Error("Mở ảnh cần type trong Photoshop trước.");
        var doc = app.activeDocument, request = options(doc);
        if (!request) return;
        // Không chờ: bộ xử lý chạy ngầm rồi tự gọi finish() – trong lúc đó Photoshop vẫn dùng bình thường.
        prepare(doc, request, scriptFolder, true);
    }
    return {main:main, finish:finish, finishBatch:finishBatch, applySfx:applySfx, snapshotBackground:snapshotBackground, apply:apply, prepare:prepare, applyToDocument:applyToDocument, pageHeader:pageHeader,
            options:options, preview:preview, pixelSize:pixelSize, translationLines:translationLines,
            encode:encode, decode:decode, vbsString:vbsString};
}());

if (typeof LILYZONO_PS_NO_DIALOG === "undefined" || !LILYZONO_PS_NO_DIALOG) {
    try { LilyzonoPS.main(new File($.fileName).parent); }
    catch (error) { alert("Lilyzono: " + error.message); }
}
