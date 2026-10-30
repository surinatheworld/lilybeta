#target photoshop

// Lilyzono: add editable typesetting layers to the current Photoshop document.
var LilyzonoPS = (function () {
    var context = null;

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
        var exe = new File(folder.fsName + "/LilyzonoPhotoshop.exe");
        if (exe.exists) return quote(exe.fsName) + " --job " + quote(job.fsName);
        var root = folder.parent, python = new File(root.fsName + "/.venv/Scripts/pythonw.exe");
        var entry = new File(root.fsName + "/LilyzonoPhotoshop.py");
        if (python.exists && entry.exists) return quote(python.fsName) + " " + quote(entry.fsName) + " --job " + quote(job.fsName);
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

    function pixelSize(doc) {
        // UnitValue.as("px") uses 72 ppi when the ruler is in inches/cm.
        // Ask Photoshop for pixel-valued dimensions before converting them.
        var old = app.preferences.rulerUnits;
        try {
            app.preferences.rulerUnits = Units.PIXELS;
            return [Math.round(doc.width.value), Math.round(doc.height.value)];
        } finally { app.preferences.rulerUnits = old; }
    }

    function translationLines(text, name) {
        var raw = text.replace(/^\uFEFF/, "").split(/\r\n|\r|\n/), pages = {}, page = "0", header = false, i, line, match;
        for (i = 0; i < raw.length; i++) {
            line = raw[i].replace(/^\s+|\s+$/g, "");
            if (!line || line.indexOf("//") === 0) continue;
            match = /^(?:(?:trang|page|p\.?|tr\.?)\s*)?#?\s*(\d{1,4})\s*[:.\-–]?\s*$/i.exec(line);
            if (match) { page = String(Number(match[1])); header = true; }
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

    function options(doc, display) {
        var win = new Window("dialog", "Lilyzono · Typeset Gái Băng");
        win.orientation = "column"; win.alignChildren = "fill";
        var size = pixelSize(doc);
        win.add("statictext", undefined, doc.name + " · " + size[0] + " × " + size[1] + " px");
        win.add("statictext", undefined, "Bản dịch: mỗi câu một dòng, theo thứ tự đọc.");
        var input = win.add("edittext", undefined, "", {multiline:true, scrolling:true});
        input.preferredSize = [540, 230];
        var row = win.add("group");
        var load = row.add("button", undefined, "Mở bản dịch .txt…");
        load.onClick = function () {
            var file = File.openDialog("Chọn bản dịch UTF-8", "Text:*.txt");
            if (file) { try { input.text = translationLines(read(file), doc.name).join("\n"); } catch (error) { alert(error.message); } }
        };
        row.add("statictext", undefined, "Xoá chữ:");
        var engine = row.add("dropdownlist", undefined, ["LaMa (trên máy)", "OpenCV (nhanh)", "Chỉ nền đơn giản"]); engine.selection = 0;
        var rtl = win.add("checkbox", undefined, "Đọc manga: phải → trái"); rtl.value = true;
        win.add("statictext", undefined, "Font: WildWords2 · AnimeAce3 nghiêng · CE Scriptorama · MTO augie");
        var buttons = win.add("group"); buttons.alignment = "right";
        buttons.add("button", undefined, "Huỷ", {name:"cancel"});
        var go = buttons.add("button", undefined, "Nhận diện và xem trước", {name:"ok"});
        go.onClick = function () {
            var lines;
            try { lines = translationLines(input.text, doc.name); } catch (error) { alert(error.message); return; }
            if (!lines.length) { alert("Nhập bản dịch trước khi tiếp tục."); return; }
            win.result = {protocol:1, profile:"gai_bang", lines:lines, engine:["lama", "opencv", "simple"][engine.selection.index], right_to_left:rtl.value};
            win.close(1);
        };
        if (display === false) return win;
        if (win.show() !== 1) return null;
        return win.result;
    }

    function runWorker(scriptFolder, folder, request, interactive) {
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
        if (interactive === false) {
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
        var progress = new Window("dialog", "Lilyzono đang xử lý");
        progress.add("statictext", undefined, "Nhận diện, xoá chữ và dàn bản dịch trên máy…");
        progress.add("statictext", undefined, "Chưa thêm layer vào tài liệu. Có thể huỷ trước khi áp dụng.");
        var cancel = progress.add("button", undefined, "Huỷ", {name:"cancel"});
        var cancelled = false, failure = null;
        cancel.onClick = function () { cancelled = true; progress.close(0); };
        progress.onShow = function () {
            try {
                if (!launcher.execute()) throw new Error("Không khởi động được bộ xử lý cục bộ (Windows Script Host).");
                var start = new Date().getTime();
                while (!result.exists && !cancelled) {
                    checkExit();
                    if (new Date().getTime() - start > 600000) throw new Error("Xử lý quá 10 phút. Xem thư mục: " + folder.fsName);
                    $.sleep(150); progress.update();
                }
                if (!cancelled) progress.close(1);
            } catch (error) { failure = error; progress.close(0); }
        };
        if (progress.show() !== 1) { if (failure) throw failure; return null; }
        var data = decode(read(result));
        if (data.protocol !== 1 || !data.ok) throw new Error(data.error || "Bộ xử lý trả về lỗi.");
        return data;
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
            var group = doc.layerSets.add(); group.name = "Lilyzono · Typeset Gái Băng";
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

    function prepare(doc, request, scriptFolder, interactive) {
        var folder = new Folder(Folder.temp.fsName + "/LilyzonoPS-" + new Date().getTime() + "-" + Math.floor(Math.random() * 100000));
        if (!folder.create()) throw new Error("Không tạo được thư mục xử lý tạm.");
        workerCommand(scriptFolder, new File(folder.fsName + "/request.json"));
        var input = new File(folder.fsName + "/input.png");
        snapshot(doc, input);
        request.image = input.fsName; request.size = pixelSize(doc);
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
        try { doc.suspendHistory("Lilyzono · Typeset Gái Băng", "LilyzonoPS.apply()"); }
        catch (error) { doc.activeHistoryState = before; throw error; }
        finally { context = null; }
    }

    function main(scriptFolder) {
        if (!/windows/i.test($.os)) throw new Error("Bản này hỗ trợ Photoshop 2023 trên Windows.");
        if (!app.documents.length) throw new Error("Mở ảnh cần type trong Photoshop trước.");
        var doc = app.activeDocument, request = options(doc);
        if (!request) return;
        var data = prepare(doc, request, scriptFolder, true);
        if (!data || !preview(data)) return;
        applyToDocument(doc, data);
    }
    return {main:main, apply:apply, prepare:prepare, applyToDocument:applyToDocument,
            options:options, preview:preview, pixelSize:pixelSize, translationLines:translationLines,
            encode:encode, decode:decode, vbsString:vbsString};
}());

if (typeof LILYZONO_PS_NO_DIALOG === "undefined" || !LILYZONO_PS_NO_DIALOG) {
    try { LilyzonoPS.main(new File($.fileName).parent); }
    catch (error) { alert("Lilyzono: " + error.message); }
}
