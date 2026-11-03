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

    function workerCommand(folder, job) { return workerBase(folder) + " --job " + quote(job.fsName); }

    function workerBase(folder) {
        // Máy có sẵn dự án (photoshop/ hoặc dist/LilyzonoPhotoshop/ trong repo): ưu tiên Python của dự án – luôn chạy
        // code mới nhất và không bị Smart App Control chặn như exe tự build. Không có dự án: dùng exe đóng gói kèm.
        var root = folder.parent, python, entry;
        for (var up = 0; up < 2 && root; up++, root = root.parent) {
            python = new File(root.fsName + "/.venv/Scripts/pythonw.exe");
            entry = new File(root.fsName + "/LilyzonoPhotoshop.py");
            if (python.exists && entry.exists) return quote(python.fsName) + " " + quote(entry.fsName);
        }
        var exe = new File(folder.fsName + "/LilyzonoPhotoshop.exe");
        if (exe.exists) return quote(exe.fsName);
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
    function snapshotBackground(doc, file) { saveLayer(doc, rawLayerOf(doc) || doc.layers[doc.layers.length - 1], file); }

    function saveLayer(doc, source, file) {
        var oldDialogs = app.displayDialogs, oldRuler = app.preferences.rulerUnits, size = pixelSize(doc), tmp = null;
        app.displayDialogs = DialogModes.NO;
        app.preferences.rulerUnits = Units.PIXELS;
        try {
            tmp = app.documents.add(size[0], size[1], doc.resolution, "Lilyzono nền gốc", NewDocumentMode.RGB,
                                    DocumentFill.WHITE);
            app.activeDocument = doc;
            source.duplicate(tmp, ElementPlacement.PLACEATBEGINNING);
            app.activeDocument = tmp;
            tmp.layers[0].visible = true;
            tmp.saveAs(file, new PNGSaveOptions(), true, Extension.LOWERCASE);
        } finally {
            if (tmp) tmp.close(SaveOptions.DONOTSAVECHANGES);
            app.activeDocument = doc;
            app.displayDialogs = oldDialogs; app.preferences.rulerUnits = oldRuler;
        }
    }

    // Trang mở từ bản redraw: raw upload vào thành layer ẩn RAW_LAYER (để nhận diện chữ, ghép câu).
    var RAW_LAYER = "Raw gốc (Lilyzono)";
    function rawLayerOf(doc) {
        for (var i = 0; i < doc.artLayers.length; i++) if (doc.artLayers[i].name === RAW_LAYER) return doc.artLayers[i];
        return null;
    }

    // Có layer nào khác ngoài ảnh raw (và nhóm Lilyzono đã tạo)? Có thì đó là bản redraw (layer Redraw của nhóm).
    function hasRedraw(doc) {
        if (rawLayerOf(doc)) return true;  // trang mở từ bản redraw, raw upload thành layer ẩn
        for (var i = 0; i < doc.layers.length - 1; i++) {
            if (doc.layers[i].visible && doc.layers[i].name.indexOf("Lilyzono") !== 0) return true;
        }
        return false;
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
        {key:"gai_bang", name:"Typeset Gái Băng", fonts:"WildWords2 · AnimeAce3 nghiêng · CE Scriptorama · MTO augie"},
        {key:"isshiki", name:"Typeset Isshiki", fonts:"Chanighter Handwriting (suy nghĩ: nghiêng) · ObelixPro (in đậm) · " +
                                             "SS Rosehip (không khung) · CCComicraft (lúng túng)"},
        {key:"ren_ai_flag_eng", name:"Ren AI Flag Eng", fonts:"MTO Astro City · Spawest (suy nghĩ) · Astounder · MTO Dom (dẫn " +
                                             "truyện) · ObelixPro (hét) · Blambot Casual (ngoài khung) · BillyTheFlyingRobot · " +
                                             "Palooka BB. Bản dịch: “ ” thoại, ( ) suy nghĩ, [ ] dẫn truyện."},
        {key:"custom", name:"Tự chọn font theo loại khung", fonts:"Làm cả chương: tool quét mọi ảnh, hiện các loại khung " +
                                             "có trong chương để bạn chọn font cho từng loại rồi type hàng loạt."}
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

    // Font đã chọn cho từng loại khung (bộ "tự chọn font"): {loại: {family, style, postscript, italic}}.
    function savedFonts() {
        try { return decode(app.getCustomOptions(SETTINGS).getString(stringIDToTypeID("fonts"))); }
        catch (error) { return {}; }
    }

    function saveFonts(fonts) {
        try {
            var desc;
            try { desc = app.getCustomOptions(SETTINGS); } catch (error) { desc = new ActionDescriptor(); }
            desc.putString(stringIDToTypeID("fonts"), encode(fonts));
            app.putCustomOptions(SETTINGS, desc, true);
        } catch (error) {}
    }

    var fontCache = null;
    function installedFonts() {
        if (fontCache) return fontCache;
        fontCache = [];
        for (var i = 0; i < app.fonts.length; i++) {
            var f = app.fonts[i];
            fontCache.push({name:f.name, family:f.family, style:f.style, postscript:f.postScriptName});
        }
        return fontCache;
    }

    function fontLabelOf(choice) {
        return choice && choice.family ? choice.family + (choice.style && choice.style !== "Regular" ? " " + choice.style : "") : "";
    }

    // Chọn font trong danh sách font của Photoshop: gõ để lọc.
    function pickFont(current) {
        var fonts = installedFonts(), shown = [];
        var win = new Window("dialog", "Chọn font");
        win.orientation = "column"; win.alignChildren = "fill";
        var search = win.add("edittext", undefined, current || ""); search.preferredSize = [380, 24];
        var list = win.add("listbox", undefined, []); list.preferredSize = [380, 320];
        function fill() {
            var q = search.text.toLowerCase(), i;
            list.removeAll(); shown = [];
            for (i = 0; i < fonts.length && shown.length < 300; i++) {
                if (!q || fonts[i].name.toLowerCase().indexOf(q) >= 0 || fonts[i].postscript.toLowerCase().indexOf(q) >= 0) {
                    shown.push(fonts[i]); list.add("item", fonts[i].name);
                }
            }
            if (shown.length) list.selection = 0;
        }
        search.onChanging = fill;
        var row = win.add("group"); row.alignment = "right";
        row.add("button", undefined, "Huỷ", {name:"cancel"});
        row.add("button", undefined, "Chọn", {name:"ok"});
        list.onDoubleClick = function () { win.close(1); };
        fill();
        if (win.show() !== 1 || !list.selection) return null;
        return shown[list.selection.index];
    }

    function findFont(text) {
        var all = installedFonts();
        for (var j = 0; j < all.length; j++) if (all[j].name === text || all[j].family === text) return all[j];
        return null;
    }

    // Bước 2 của "tự chọn font": bảng các loại khung tìm thấy (ảnh mẫu + số khung), chọn font cho từng loại.
    function finishScan(data, display) {
        var saved = savedFonts(), rows = [], i, k;
        var win = new Window("dialog", "Lilyzono · Chọn font theo loại khung");
        win.orientation = "column"; win.alignChildren = "fill";
        win.add("statictext", undefined, data.pages.length + " ảnh. Chọn font cho từng loại khung (để trống: font mặc " +
                "định); tool nhớ lựa chọn cho lần sau.", {multiline:true});
        var panel = win.add("panel"); panel.orientation = "column"; panel.alignChildren = "fill";
        for (i = 0; i < data.kinds.length; i++) {
            k = data.kinds[i];
            if (!k.count && k.kind !== "flustered") continue;
            var row = panel.add("group"); row.alignChildren = "center";
            var pics = row.add("group"); pics.preferredSize = [330, 100];
            for (var n = 0; n < k.samples.length; n++) pics.add("image", undefined, new File(k.samples[n]));
            var info = row.add("group"); info.orientation = "column"; info.alignChildren = "left";
            info.add("statictext", undefined, k.label);
            info.add("statictext", undefined, k.kind === "flustered" ? "theo câu dịch nói lắp (e- em…)" :
                     k.count + " khung · " + k.pages + " trang");
            var pick = info.add("group");
            var name = pick.add("edittext", undefined, fontLabelOf(saved[k.kind])); name.preferredSize = [190, 22];
            var choose = pick.add("button", undefined, "Chọn…");
            var italic = info.add("checkbox", undefined, "Nghiêng");
            italic.value = !!(saved[k.kind] && saved[k.kind].italic);
            rows.push(bindRow({kind:k.kind, name:name, italic:italic, choice:saved[k.kind] || null}, choose));
        }
        var buttons = win.add("group"); buttons.alignment = "right";
        buttons.add("button", undefined, "Huỷ", {name:"cancel"});
        buttons.add("button", undefined, "Type cả chương (" + data.pages.length + " ảnh)", {name:"ok"});
        if (display === false) return win;
        if (win.show() !== 1) return "cancel";
        var fonts = {}, missing = [];
        for (i = 0; i < rows.length; i++) {
            var r = rows[i], text = trim(r.name.text);
            if (!text) continue;
            var c = r.choice || findFont(text);  // gõ tay tên font: tìm đúng tên trong danh sách của Photoshop
            if (!c) { missing.push(text); continue; }
            fonts[r.kind] = {family:c.family, style:c.style, postscript:c.postscript, italic:r.italic.value};
        }
        if (missing.length) { alert("Photoshop không có font: " + missing.join(", ") + ". Chọn bằng nút Chọn…"); return "cancel"; }
        saveFonts(fonts);
        var request = {protocol:1, profile:"custom", mode:"batch", right_to_left:data.right_to_left, engine:"simple",
                       custom_fonts:fonts, pages:data.pages};
        if (data.translation_file) request.translation_file = data.translation_file;
        else request.translation_text = data.translation_text || "";
        prepare(app.activeDocument, request, new File(data.script).parent, true);
        return "ok";
    }

    function bindRow(entry, choose) {
        choose.onClick = function () {
            var f = pickFont(entry.name.text);
            if (f) { entry.choice = f; entry.name.text = fontLabelOf(f); }
        };
        entry.name.onChange = function () { if (entry.name.text !== fontLabelOf(entry.choice)) entry.choice = null; };
        return entry;
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
        var load = row.add("button", undefined, "Mở bản dịch (.txt / .docx / .xlsx)…");
        var fileLabel = row.add("statictext", undefined, "", {truncate:"middle"}); fileLabel.preferredSize = [330, 20];
        load.onClick = function () {
            var file = File.openDialog("Chọn bản dịch", "Bản dịch:*.txt;*.docx;*.xlsx");
            if (!file) return;
            // Không dùng regex /i (lỗi treo của ExtendScript với tên tiếng Việt). Word: bộ xử lý đọc.
            var ext = file.name.substr(file.name.length - 5).toLowerCase();
            if (ext === ".docx" || ext === ".xlsx") {  // Word / script Excel: bộ xử lý đọc
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
        for (i = 0; i < PROFILES.length; i++) names.push(PROFILES[i].name);
        var profileBox = pick.add("dropdownlist", undefined, names);
        var fontLabel = win.add("statictext", undefined, "", {multiline:true}); fontLabel.preferredSize = [560, 50];
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
        var sfx = buttons.add("button", undefined, "Upload ảnh redraw…");
        var sfxFolder = buttons.add("button", undefined, "Upload folder redraw…");
        var rawFolder = buttons.add("button", undefined, "Upload folder raw…");
        var gap = buttons.add("group"); gap.alignment = ["fill", "center"];
        buttons.add("button", undefined, "Huỷ", {name:"cancel"});
        var batch = buttons.add("button", undefined, "Làm cả chương (" + app.documents.length + " ảnh)");
        var go = buttons.add("button", undefined, "Nhận diện và xem trước", {name:"ok"});
        function translation(request) {
            if (translationFile) request.translation_file = translationFile;
            else request.translation_text = input.text;
            return request;
        }
        // Ảnh redraw: một ảnh cho trang đang mở, hoặc nhiều ảnh / cả folder – mỗi ảnh vào trang đang mở cùng số trang.
        function uploadRedraw(files) {
            if (!files || !files.length) return;
            if (files.length === 1 && app.documents.length === 1) {
                win.result = {protocol:1, mode:"sfx", redrawn:files[0].fsName};
                win.close(1);
                return;
            }
            var items = [], unmatched = [], used = {}, i, j, d, n;
            for (i = 0; i < files.length; i++) {
                n = pageNumberOf(files[i].name), d = null;
                for (j = 0; j < app.documents.length && n !== null; j++) {
                    if (pageNumberOf(app.documents[j].name) === n && !used[j]) { d = app.documents[j]; used[j] = true; break; }
                }
                if (!d && files.length === 1) d = doc;  // một ảnh không có số trang khớp: vào trang đang mở
                if (d) items.push({redrawn:files[i].fsName, doc_name:d.name, doc_id:docId(d)});
                else unmatched.push(decodeURI(files[i].name));
            }
            if (!items.length) { alert("Không ảnh nào khớp số trang với ảnh đang mở trong Photoshop (vd 012.png ↔ 12.psd)."); return; }
            if (unmatched.length && !confirm("Không có trang đang mở cho: " + unmatched.join(", ") + ". Bỏ qua các ảnh này?")) return;
            win.result = {protocol:1, mode:"sfx_batch", items:items};
            win.close(1);
        }
        sfx.onClick = function () {
            var picked = File.openDialog("Ảnh đã redraw (cả trang, cùng khung) – chọn được nhiều ảnh",
                                         "Ảnh:*.png;*.jpg;*.jpeg;*.webp;*.bmp;*.tif;*.tiff", true);
            uploadRedraw(picked instanceof Array ? picked : (picked ? [picked] : []));
        };
        // Trang đang mở là bản redraw: upload raw (cùng số trang) để tool đọc chữ gốc, ghép câu; chữ type lên bản redraw.
        function pickImages(title) {
            var folder = Folder.selectDialog(title);
            if (!folder) return null;
            var all = folder.getFiles(function (f) { return f instanceof File && IMAGE_EXT.test(f.name); });
            if (!all.length) { alert("Folder không có ảnh PNG / JPG / WEBP / BMP / TIF nào."); return null; }
            return all;
        }
        rawFolder.onClick = function () {
            var all = pickImages("Folder ảnh raw (tên ảnh có số trang, khớp các trang redraw đang mở)");
            if (!all) return;
            // Trang thường lấy raw cùng số; trang đôi ("03-04.jpg") lấy raw 3 và 4, ghép phải sang trái (trang 3 bên phải).
            var byNumber = {}, taken = {}, items = [], unmatched = [], missing = [], i, k, n, d, range, raws;
            for (i = 0; i < all.length; i++) {
                n = pageNumberOf(all[i].name);
                if (n !== null && !byNumber[n]) byNumber[n] = all[i];
            }
            for (i = 0; i < app.documents.length; i++) {
                d = app.documents[i]; range = pageRangeOf(d.name);
                if (!range) continue;
                raws = [];
                for (k = range[1]; k >= range[0]; k--) if (byNumber[k]) raws.push(byNumber[k].fsName);
                if (raws.length !== range[1] - range[0] + 1) {
                    if (raws.length) missing.push(d.name);
                    continue;
                }
                for (k = range[0]; k <= range[1]; k++) taken[k] = true;
                items.push({raws:raws, doc_name:d.name, doc_id:docId(d)});
            }
            for (n in byNumber) if (byNumber.hasOwnProperty(n) && !taken[n]) unmatched.push(decodeURI(byNumber[n].name));
            if (missing.length) unmatched.push("(thiếu raw cho trang đôi: " + missing.join(", ") + ")");
            if (!items.length) { alert("Không ảnh raw nào khớp số trang với ảnh đang mở (vd 012.jpg ↔ 12.png)."); return; }
            if (unmatched.length && !confirm("Không có trang đang mở cho: " + unmatched.join(", ") + ". Bỏ qua các ảnh này?")) return;
            win.result = {protocol:1, mode:"raw_upload", items:items};
            win.close(1);
        };
        sfxFolder.onClick = function () {
            var folder = Folder.selectDialog("Folder ảnh đã redraw (tên ảnh có số trang)");
            if (!folder) return;
            var all = folder.getFiles(function (f) { return f instanceof File && IMAGE_EXT.test(f.name); });
            if (!all.length) { alert("Folder không có ảnh PNG / JPG / WEBP / BMP / TIF nào."); return; }
            uploadRedraw(all);
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
            var key = profile();
            // Tự chọn font: quét các loại khung trước, chọn font cho từng loại rồi mới type (finishScan).
            win.result = translation({protocol:1, profile:key, mode:key === "custom" ? "scan" : "batch",
                                      right_to_left:rtl.value, engine:"simple"});
            win.close(1);
        };
        go.onClick = function () {
            var request = {protocol:1, profile:profile(), mode:"page", right_to_left:rtl.value, engine:"simple"};
            if (request.profile === "custom") request.custom_fonts = savedFonts();
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

    // Viền chữ (Layer Style > Stroke) cho layer đang chọn: chữ dịch đè lên chữ gốc / tranh vẫn đọc được.
    function addStroke(size, hexColor) {
        var c = charIDToTypeID, d = new ActionDescriptor(), r = new ActionReference();
        r.putProperty(c("Prpr"), c("Lefx"));
        r.putEnumerated(c("Lyr "), c("Ordn"), c("Trgt"));
        d.putReference(c("null"), r);
        var fx = new ActionDescriptor(), st = new ActionDescriptor(), rgb = new ActionDescriptor(), color = new SolidColor();
        color.rgb.hexValue = hexColor;
        fx.putUnitDouble(c("Scl "), c("#Prc"), 100);
        st.putBoolean(c("enab"), true);
        st.putEnumerated(c("Styl"), c("FStl"), c("OutF"));
        st.putEnumerated(c("PntT"), c("FrFl"), c("SClr"));
        st.putEnumerated(c("Md  "), c("BlnM"), c("Nrml"));
        st.putUnitDouble(c("Opct"), c("#Prc"), 100);
        st.putUnitDouble(c("Sz  "), c("#Pxl"), size);
        rgb.putDouble(c("Rd  "), color.rgb.red); rgb.putDouble(c("Grn "), color.rgb.green); rgb.putDouble(c("Bl  "), color.rgb.blue);
        st.putObject(c("Clr "), c("RGBC"), rgb);
        fx.putObject(c("FrFX"), c("FrFX"), st);
        d.putObject(c("T   "), c("Lefx"), fx);
        executeAction(c("setd"), d, DialogModes.NO);
    }

    // Ký tự font chính không có (♡ ♪ —, Photoshop không tự mượn font khác mà hiện ô vuông): đổi riêng các đoạn
    // [từ, tới) đó sang font ký hiệu, phần còn lại giữ nguyên kiểu chữ của layer đang chọn.
    function fallbackRuns(runs, font) {
        var c = charIDToTypeID, s = stringIDToTypeID, ref = new ActionReference();
        ref.putEnumerated(c("Lyr "), c("Ordn"), c("Trgt"));
        var textKey = executeActionGet(ref).getObjectValue(s("textKey"));
        var style = textKey.getList(s("textStyleRange")).getObjectValue(0).getObjectValue(s("textStyle"));
        var total = textKey.getString(s("textKey")).length, list = new ActionList(), pos = 0, i;
        function add(from, to, symbol) {
            if (to <= from) return;
            var range = new ActionDescriptor(), st = new ActionDescriptor();
            st.fromStream(style.toStream());
            if (symbol) {
                if (st.hasKey(s("fontName"))) st.erase(s("fontName"));
                if (st.hasKey(s("fontStyleName"))) st.erase(s("fontStyleName"));
                st.putString(s("fontPostScriptName"), font);
            }
            range.putInteger(s("from"), from); range.putInteger(s("to"), to);
            range.putObject(s("textStyle"), s("textStyle"), st);
            list.putObject(s("textStyleRange"), range);
        }
        for (i = 0; i < runs.length; i++) { add(pos, runs[i][0], false); add(runs[i][0], runs[i][1], true); pos = runs[i][1]; }
        add(pos, total, false);
        textKey.putList(s("textStyleRange"), list);
        var set = new ActionDescriptor(), target = new ActionReference();
        target.putEnumerated(c("TxLr"), c("Ordn"), c("Trgt"));
        set.putReference(c("null"), target);
        set.putObject(c("T   "), c("TxLr"), textKey);
        executeAction(c("setd"), set, DialogModes.NO);
    }

    function apply() {
        var doc = context.doc, data = context.data;
        var oldRuler = app.preferences.rulerUnits, oldType = app.preferences.typeUnits, oldDialogs = app.displayDialogs;
        var opened = null;
        try {
            app.preferences.rulerUnits = Units.PIXELS; app.preferences.typeUnits = TypeUnits.PIXELS;
            app.displayDialogs = DialogModes.NO;
            if (context.replace) retireGroup(doc, groupName(data));
            var group = doc.layerSets.add(); group.name = groupName(data);
            if (data.redraw) {  // bảng điều khiển: phần xoá chữ đã là layer riêng (bước 6), nhóm chỉ có chữ
                opened = app.open(new File(data.redraw));
                var redraw = opened.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
                opened.close(SaveOptions.DONOTSAVECHANGES); opened = null;
                app.activeDocument = doc;
                redraw.move(group, ElementPlacement.INSIDE); redraw.name = "Redraw";
            }
            for (var i = 0; i < data.layers.length; i++) {
                var layer = data.layers[i], art = group.artLayers.add();
                art.kind = LayerKind.TEXT;
                var text = art.textItem, slack = layer.width * .12 + 4;
                text.kind = TextType.PARAGRAPHTEXT;
                text.contents = layer.contents;
                text.font = layer.font;
                // Preserve the document's resolution: typography is measured in pixels.
                text.size = new UnitValue(layer.size * 72 / doc.resolution, "pt");
                text.useAutoLeading = true;  // khoảng cách dòng luôn Auto (120%)
                text.autoLeadingAmount = 120;
                text.fauxBold = layer.faux_bold; text.fauxItalic = layer.faux_italic;
                text.autoKerning = AutoKernType[layer.kerning || "OPTICAL"];
                text.justification = Justification[layer.justification];
                text.antiAliasMethod = AntiAlias[layer.antialias] || AntiAlias.STRONG;
                var color = new SolidColor(); color.rgb.hexValue = layer.color; text.color = color;
                // Paragraph dimensions use type units and otherwise interpret
                // UnitValue("px") at 72 ppi, widening the box in 300 ppi files.
                text.width = new UnitValue((layer.width + slack) * 72 / doc.resolution, "pt");
                text.height = new UnitValue((layer.block_height + layer.size * 2) * 72 / doc.resolution, "pt");
                text.position = [new UnitValue(layer.left - slack / 2, "px"), new UnitValue(layer.first_baseline - layer.ascent, "px")];
                text.hyphenation = false;
                art.name = (layer.orphan ? "CHƯA ĐẶT · " : "") + (layer.redraw ? "REDRAW · " : "") +
                           (layer.name || (i + 1) + ". " + layer.contents.replace(/[\r\u0003]/g, " "));
                if (layer.fallback && layer.fallback.length && layer.fallback_font) {
                    doc.activeLayer = art; fallbackRuns(layer.fallback, layer.fallback_font);
                }
                if (layer.stroke > 0) { doc.activeLayer = art; addStroke(layer.stroke, layer.stroke_color || "ffffff"); }
            }
            doc.activeLayer = group;
        } finally {
            if (opened) opened.close(SaveOptions.DONOTSAVECHANGES);
            app.activeDocument = doc;
            app.preferences.rulerUnits = oldRuler; app.preferences.typeUnits = oldType; app.displayDialogs = oldDialogs;
        }
    }

    function docId(doc) { try { return doc.id; } catch (error) { return null; } }

    var REDRAW_LAYER = "Bản redraw";
    var IMAGE_EXT = /\.(png|jpe?g|webp|bmp|tiff?)$/i;

    // Số trang từ tên file: 012.png → 12, page_3.psd → 3 (cụm số cuối cùng); null nếu không có số.
    function pageNumberOf(name) {
        var nums = decodeURI(String(name)).replace(/\.[^.]*$/, "").match(/\d+/g);
        return nums ? parseInt(nums[nums.length - 1], 10) : null;
    }

    // Các trang trong tên file: "05.jpg" → [5, 5]; trang đôi "03-04.jpg" → [3, 4]; null nếu không có số.
    function pageRangeOf(name) {
        var base = decodeURI(String(name)).replace(/\.[^.]*$/, ""), m = base.match(/(\d+)\s*[-–_~]\s*(\d+)\s*$/);
        if (m && parseInt(m[2], 10) > parseInt(m[1], 10) && parseInt(m[2], 10) - parseInt(m[1], 10) < 4) {
            return [parseInt(m[1], 10), parseInt(m[2], 10)];
        }
        var n = pageNumberOf(name);
        return n === null ? null : [n, n];
    }

    function prepare(doc, request, scriptFolder, interactive) {
        var folder = new Folder(Folder.temp.fsName + "/LilyzonoPS-" + new Date().getTime() + "-" + Math.floor(Math.random() * 100000));
        if (!folder.create()) throw new Error("Không tạo được thư mục xử lý tạm.");
        workerCommand(scriptFolder, new File(folder.fsName + "/request.json"));
        if (request.mode === "sfx_batch") {
            // Nhiều ảnh redraw: chụp ảnh gốc (layer dưới cùng) của từng trang khớp số.
            var keep = app.activeDocument, k, target;
            for (k = 0; k < request.items.length; k++) {
                target = findDocument(request.items[k].doc_id, request.items[k].doc_name);
                app.activeDocument = target;
                request.items[k].image = folder.fsName + "/base" + k + ".png";
                request.items[k].size = pixelSize(target);
                snapshotBackground(target, new File(request.items[k].image));
            }
            app.activeDocument = keep;
        } else if ((request.mode === "batch" || request.mode === "scan") && request.pages) {
            // Ảnh đã chụp ở bước quét loại khung: dùng lại, không chụp lại.
        } else if (request.mode === "batch" || request.mode === "scan") {
            // Cả chương: chụp mọi ảnh đang mở (Photoshop bận ~1 giây mỗi trang), rồi giao hết cho bộ xử lý.
            var current = app.activeDocument, pages = [], i, d, f, page;
            for (i = 0; i < app.documents.length; i++) {
                d = app.documents[i];
                f = new File(folder.fsName + "/page" + i + ".png");
                app.activeDocument = d;
                snapshot(d, f);
                page = {image:f.fsName, size:pixelSize(d), doc_name:d.name, doc_id:docId(d)};
                if (hasRedraw(d)) {  // trang đã redraw sẵn: nhận diện chữ trên ảnh raw (layer dưới cùng)
                    page.redrawn = f.fsName; page.image = folder.fsName + "/raw" + i + ".png";
                    snapshotBackground(d, new File(page.image));
                }
                pages.push(page);
            }
            app.activeDocument = current;
            request.pages = pages;
        } else {
            var input = new File(folder.fsName + "/input.png");
            if (request.mode === "sfx") snapshotBackground(doc, input);
            else if (hasRedraw(doc)) {  // PSD có layer Redraw: chữ nhận diện trên raw, type lên bản đã redraw
                var redrawn = new File(folder.fsName + "/redrawn.png");
                snapshot(doc, redrawn); snapshotBackground(doc, input); request.redrawn = redrawn.fsName;
            } else snapshot(doc, input);
            request.image = input.fsName; request.size = pixelSize(doc);
        }
        request.doc_name = doc.name;
        request.doc_id = docId(doc);
        request.callback = interactive !== false;
        request.script = SCRIPT_FILE ? new File(SCRIPT_FILE).fsName : scriptFolder.fsName + "/Lilyzono.jsx";
        return runWorker(scriptFolder, folder, request, interactive);
    }

    // Chạy lại typeset: nhóm cũ cùng tên đổi thành "(bản trước)" và ẩn đi (giữ một bản để so / lấy lại chỗ đã sửa tay).
    function retireGroup(doc, name) {
        var i, g;
        for (i = doc.layerSets.length - 1; i >= 0; i--) if (doc.layerSets[i].name === name + " (bản trước)") doc.layerSets[i].remove();
        for (i = doc.layerSets.length - 1; i >= 0; i--) {
            g = doc.layerSets[i];
            if (g.name === name) { g.name = name + " (bản trước)"; g.visible = false; }
        }
    }

    function applyToDocument(doc, data, replace) {
        var size = pixelSize(doc);
        if (data.size[0] !== size[0] || data.size[1] !== size[1]) throw new Error("Kích thước trang đã thay đổi.");
        for (var i = 0; i < data.layers.length; i++) {
            try { app.fonts.getByName(data.layers[i].font); }
            catch (error) { throw new Error("Photoshop chưa nhận font " + data.layers[i].family + ". Cài font rồi mở lại Photoshop."); }
        }
        context = {doc:doc, data:data, replace:!!replace};
        app.activeDocument = doc;
        var before = doc.activeHistoryState;
        try { doc.suspendHistory(groupName(data), "LilyzonoPS.apply()"); }
        catch (error) { doc.activeHistoryState = before; throw error; }
        finally { context = null; }
    }

    // Bản redraw người dùng upload: layer "Bản redraw" nguyên ảnh, ngay trên layer raw (dưới cùng), ngoài nhóm Lilyzono.
    // Có layer này thì khi type, tool đọc chữ trên raw và đặt chữ lên bản redraw, không tự xoá chữ trên raw nữa
    // (hasRedraw). Layer "Redraw" tool đã xoá chữ trước đó (trong nhóm Lilyzono) được ẩn đi, không xoá.
    function applySfx() {
        var doc = context.doc, data = context.data, opened = null, oldDialogs = app.displayDialogs, i, j, g;
        try {
            app.displayDialogs = DialogModes.NO;
            for (i = doc.artLayers.length - 1; i >= 0; i--) {
                if (doc.artLayers[i].name === REDRAW_LAYER && doc.artLayers[i] !== doc.layers[doc.layers.length - 1]) doc.artLayers[i].remove();
            }
            opened = app.open(new File(data.layer));
            var layer = opened.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
            opened.close(SaveOptions.DONOTSAVECHANGES); opened = null;
            app.activeDocument = doc;
            layer.name = REDRAW_LAYER;
            layer.move(doc.layers[doc.layers.length - 1], ElementPlacement.PLACEBEFORE);
            for (i = 0; i < doc.layerSets.length; i++) {
                g = doc.layerSets[i];
                if (g.name.indexOf("Lilyzono") !== 0) continue;
                for (j = 0; j < g.artLayers.length; j++) if (g.artLayers[j].name === "Redraw") g.artLayers[j].visible = false;
            }
            doc.activeLayer = layer;
        } finally {
            if (opened) opened.close(SaveOptions.DONOTSAVECHANGES);
            app.activeDocument = doc; app.displayDialogs = oldDialogs;
        }
    }

    function addSfxLayer(data) {
        var doc = findDocument(data.doc_id, data.doc_name), size = pixelSize(doc);
        if (data.size[0] !== size[0] || data.size[1] !== size[1]) throw new Error("Kích thước trang đã thay đổi.");
        app.activeDocument = doc;
        context = {doc:doc, data:data};
        try { doc.suspendHistory("Lilyzono · Bản redraw", "LilyzonoPS.applySfx()"); }
        finally { context = null; }
        return doc;
    }

    function finishSfx(data) {
        var doc = addSfxLayer(data);
        try { app.refresh(); } catch (error) {}
        alert("Đã thêm bản redraw vào " + doc.name + " (" + data.changed_percent + "% trang khác ảnh gốc). Bấm type: chữ sẽ đặt trên bản redraw, tool không tự xoá chữ.");
        return "ok";
    }

    function finishSfxBatch(data) {
        var done = [], errors = [], i, p;
        for (i = 0; i < data.pages.length; i++) {
            p = data.pages[i];
            if (!p.ok) { errors.push(p.doc_name + ": " + p.error); continue; }
            try { addSfxLayer(p); done.push(p.doc_name); }
            catch (error) { errors.push(p.doc_name + ": " + error.message); }
        }
        try { app.refresh(); } catch (error) {}
        alert("Đã thêm bản redraw cho " + done.length + "/" + data.pages.length + " trang." +
              (errors.length ? "\nLỗi:\n" + errors.join("\n") : ""));
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
        if (data.mode === "scan") return finishScan(data);
        if (data.mode === "sfx") return finishSfx(data);
        if (data.mode === "sfx_batch") return finishSfxBatch(data);
        var doc = findDocument(data.doc_id, data.doc_name);
        app.activeDocument = doc;
        if (!preview(data)) return "cancel";
        applyToDocument(doc, data);
        // Được gọi từ bộ xử lý (ngoài Photoshop): Photoshop không tự vẽ lại màn hình/bảng Layers cho tới khi người
        // dùng đụng chuột, trông như chưa tạo gì. Buộc vẽ lại ngay.
        try { app.refresh(); } catch (error) {}
        return "ok";
    }

    // Gắn ảnh raw vào trang redraw: layer ẩn RAW_LAYER trên cùng, co về đúng cỡ trang. Thay layer raw cũ nếu có.
    function attachRaw() {
        var doc = context.doc, item = context.data, oldDialogs = app.displayDialogs, old, tmp = null, opened = null;
        var oldRuler = app.preferences.rulerUnits, size = pixelSize(doc);
        var raws = item.raws || [item.raw], k = raws.length, x = 0, i, w, h, part, layer;
        try {
            app.displayDialogs = DialogModes.NO;
            app.preferences.rulerUnits = Units.PIXELS;
            while ((old = rawLayerOf(doc))) old.remove();
            // Ghép các raw (trang đôi: k ảnh, trái sang phải) thành một ảnh đúng cỡ trang.
            tmp = app.documents.add(size[0], size[1], doc.resolution, "Lilyzono raw", NewDocumentMode.RGB, DocumentFill.WHITE);
            for (i = 0; i < k; i++) {
                part = i === k - 1 ? size[0] - x : Math.round(size[0] / k);
                opened = app.open(new File(raws[i]));
                if (opened.layers.length > 1) opened.flatten();
                if (opened.mode !== DocumentMode.RGB) opened.changeMode(ChangeMode.RGB);
                w = Math.round(opened.width.value); h = Math.round(opened.height.value);
                if (Math.abs(Math.log((w / h) / (part / size[1]))) > 0.03) {
                    throw new Error("ảnh raw " + decodeURI(opened.name) + " " + w + "×" + h + " khác tỉ lệ trang " +
                                    (k > 1 ? "(mỗi nửa " + part + "×" + size[1] + ")" : size[0] + "×" + size[1]));
                }
                if (w !== part || h !== size[1]) opened.resizeImage(new UnitValue(part, "px"), new UnitValue(size[1], "px"),
                                                                    doc.resolution, ResampleMethod.BICUBIC);
                layer = opened.layers[0].duplicate(tmp, ElementPlacement.PLACEATBEGINNING);
                opened.close(SaveOptions.DONOTSAVECHANGES); opened = null;
                app.activeDocument = tmp;
                layer.translate(new UnitValue(x - layer.bounds[0].value, "px"), new UnitValue(-layer.bounds[1].value, "px"));
                x += part;
            }
            app.activeDocument = tmp;
            tmp.flatten();
            layer = tmp.layers[0].duplicate(doc, ElementPlacement.PLACEATBEGINNING);
            tmp.close(SaveOptions.DONOTSAVECHANGES); tmp = null;
            app.activeDocument = doc;
            layer.name = RAW_LAYER;
            layer.visible = false;
        } finally {
            if (opened) opened.close(SaveOptions.DONOTSAVECHANGES);
            if (tmp) tmp.close(SaveOptions.DONOTSAVECHANGES);
            app.activeDocument = doc; app.displayDialogs = oldDialogs; app.preferences.rulerUnits = oldRuler;
        }
    }

    function attachRaws(items) {
        var done = [], errors = [], i, doc;
        for (i = 0; i < items.length; i++) {
            try {
                doc = findDocument(items[i].doc_id, items[i].doc_name);
                app.activeDocument = doc;
                context = {doc:doc, data:items[i]};
                try { doc.suspendHistory("Lilyzono · Raw gốc", "LilyzonoPS.attachRaw()"); }
                finally { context = null; }
                done.push(doc.name);
            } catch (error) { errors.push(items[i].doc_name + ": " + error.message); }
        }
        try { app.refresh(); } catch (error) {}
        alert("Đã gắn raw cho " + done.length + "/" + items.length + " trang (layer ẩn \"" + RAW_LAYER + "\")." +
              " Giờ mở bản dịch rồi type: tool đọc chữ trên raw, đặt chữ lên bản redraw." +
              (errors.length ? "\nLỗi:\n" + errors.join("\n") : ""));
        return done.length;
    }

    // --- Bảng điều khiển: cửa sổ Lilyzono chạy riêng, gọi các hàm dưới đây qua scripting của Photoshop (COM) ----------
    var CLEAN_LAYER = "Xoá chữ (Lilyzono)";

    function layerByName(doc, name) {
        for (var i = 0; i < doc.artLayers.length; i++) if (doc.artLayers[i].name === name) return doc.artLayers[i];
        return null;
    }

    function docInfo(d) {
        var path = "";
        try { path = d.fullName.fsName; } catch (error) {}  // ảnh chưa lưu: không có đường dẫn
        return {id:docId(d), name:d.name, path:path, size:pixelSize(d), has_raw:!!rawLayerOf(d),
                has_redraw:!!layerByName(d, REDRAW_LAYER)};
    }

    // Ảnh làm việc (bản redraw upload, hoặc layer dưới cùng = ảnh đã mở) và ảnh raw (layer raw gắn vào, hoặc layer dưới
    // cùng khi bản redraw là layer riêng phía trên) ra PNG, bỏ qua chữ / nhóm Lilyzono.
    function snapshotPage(doc, folder) {
        var bottom = doc.layers[doc.layers.length - 1], redraw = layerByName(doc, REDRAW_LAYER);
        var raw = rawLayerOf(doc) || (redraw ? bottom : null), out = {size:pixelSize(doc), work:folder + "/ps_work.png", raw:null};
        saveLayer(doc, redraw || bottom, new File(out.work));
        if (raw) { out.raw = folder + "/ps_raw.png"; saveLayer(doc, raw, new File(out.raw)); }
        return out;
    }

    // Layer ảnh (PNG trong suốt, cỡ trang) ngay trên ảnh nền / bản redraw; thay layer cùng tên đã có.
    function placeLayer() {
        var doc = context.doc, data = context.data, opened = null, old, oldDialogs = app.displayDialogs;
        try {
            app.displayDialogs = DialogModes.NO;
            while ((old = layerByName(doc, data.layer_name))) old.remove();
            opened = app.open(new File(data.png));
            var layer = opened.activeLayer.duplicate(doc, ElementPlacement.PLACEATBEGINNING);
            opened.close(SaveOptions.DONOTSAVECHANGES); opened = null;
            app.activeDocument = doc;
            layer.name = data.layer_name;
            layer.move(layerByName(doc, REDRAW_LAYER) || doc.layers[doc.layers.length - 1], ElementPlacement.PLACEBEFORE);
        } finally {
            if (opened) opened.close(SaveOptions.DONOTSAVECHANGES);
            app.activeDocument = doc; app.displayDialogs = oldDialogs;
        }
    }

    function inHistory(doc, data, title, call) {
        app.activeDocument = doc;
        context = {doc:doc, data:data};
        try { doc.suspendHistory(title, call); } finally { context = null; }
    }

    // Bảng điều khiển gọi: LilyzonoPS.bridge("tên", "<JSON tham số>") → "<JSON {result} hoặc {error}>".
    function bridge(name, argsText) {
        try {
            var a = argsText ? decode(argsText) : {}, out = null, i, doc = null;
            if (a.name !== undefined) doc = findDocument(a.id, a.name);
            if (name === "docs") {
                out = {docs:[], active:null};
                for (i = 0; i < app.documents.length; i++) out.docs.push(docInfo(app.documents[i]));
                try { out.active = app.activeDocument.name; } catch (error) {}
            } else if (name === "activate") {
                app.activeDocument = doc; out = true;
            } else if (name === "open") {
                out = [];
                for (i = 0; i < a.paths.length; i++) out.push(app.open(new File(a.paths[i])).name);
            } else if (name === "snapshot") {
                out = snapshotPage(doc, a.folder);
            } else if (name === "attach_raw") {
                inHistory(doc, {raws:a.raws}, "Lilyzono · Raw gốc", "LilyzonoPS.attachRaw()"); out = true;
            } else if (name === "place_layer") {
                inHistory(doc, {png:a.png, layer_name:a.layer_name || CLEAN_LAYER}, "Lilyzono · Xoá chữ", "LilyzonoPS.placeLayer()");
                out = true;
            } else if (name === "typeset") {
                applyToDocument(doc, decode(read(new File(a.json))), true); out = true;
            } else if (name === "fonts") {
                out = installedFonts();
            } else throw new Error("Không có lệnh " + name);
            try { app.refresh(); } catch (error) {}
            return encode({result:out});
        } catch (error) {
            return encode({error:String(error.message || error) + (error.line ? " (Lilyzono.jsx dòng " + error.line + ")" : "")});
        }
    }

    function launchPanel(scriptFolder) {
        var folder = new Folder(Folder.temp.fsName + "/LilyzonoPanel");
        if (!folder.exists && !folder.create()) throw new Error("Không tạo được thư mục tạm.");
        var script = SCRIPT_FILE ? new File(SCRIPT_FILE).fsName : scriptFolder.fsName + "/Lilyzono.jsx";
        var launcher = new File(folder.fsName + "/panel.vbs");
        var code = 'On Error Resume Next\r\n' +
            'CreateObject("WScript.Shell").Run ' + vbsString(workerBase(scriptFolder) + " --panel --script " + quote(script)) +
            ', 1, False\r\n' +
            'If Err.Number <> 0 Then MsgBox ' + vbsString("Không mở được bảng điều khiển Lilyzono: ") +
            ' & Err.Description, 16, "Lilyzono"\r\n';
        write(launcher, code, "ASCII");
        if (!launcher.execute()) throw new Error("Không mở được bảng điều khiển (Windows Script Host).");
    }

    function main(scriptFolder) {
        if (!/windows/i.test($.os)) throw new Error("Bản này hỗ trợ Photoshop 2023 trên Windows.");
        // Mặc định mở bảng điều khiển 8 bước; giữ Shift khi chạy script: hộp thoại nhanh như trước.
        var quick = false;
        try { quick = ScriptUI.environment.keyboardState.shiftKey; } catch (error) {}
        if (!quick) return launchPanel(scriptFolder);
        if (!app.documents.length) throw new Error("Mở ảnh cần type trong Photoshop trước.");
        var doc = app.activeDocument, request = options(doc);
        if (!request) return;
        if (request.mode === "raw_upload") return attachRaws(request.items);
        // Không chờ: bộ xử lý chạy ngầm rồi tự gọi finish() – trong lúc đó Photoshop vẫn dùng bình thường.
        prepare(doc, request, scriptFolder, true);
    }
    return {main:main, bridge:bridge, placeLayer:placeLayer, retireGroup:retireGroup, attachRaw:attachRaw, pageRangeOf:pageRangeOf, attachRaws:attachRaws, rawLayerOf:rawLayerOf, finish:finish, finishBatch:finishBatch, finishScan:finishScan, finishSfxBatch:finishSfxBatch, pageNumberOf:pageNumberOf, savedFonts:savedFonts, applySfx:applySfx, snapshotBackground:snapshotBackground, apply:apply, prepare:prepare, applyToDocument:applyToDocument, pageHeader:pageHeader,
            options:options, preview:preview, pixelSize:pixelSize, translationLines:translationLines,
            encode:encode, decode:decode, vbsString:vbsString};
}());

if (typeof LILYZONO_PS_NO_DIALOG === "undefined" || !LILYZONO_PS_NO_DIALOG) {
    try { LilyzonoPS.main(new File($.fileName).parent); }
    catch (error) { alert("Lilyzono: " + error.message); }
}
