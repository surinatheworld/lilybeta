const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
let source = fs.readFileSync('photoshop/Lilyzono.jsx', 'utf8').replace(/^#target.*$/m, '');
const sandbox = {LILYZONO_PS_NO_DIALOG:true};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const tool = sandbox.LilyzonoPS;
for (const value of [null, true, false, 0, -1.25e3, 'Ôm chặt\r\u0003"\\', {layers:[{text:'Cảm ơn', font:'[TeddyBear]', size:26}]}]) {
  const encoded = JSON.stringify(value);
  assert.equal(JSON.stringify(tool.decode(encoded)), encoded);
  assert.equal(JSON.stringify(tool.decode(tool.encode(tool.decode(encoded)))), encoded);
}
for (const input of ['{"x":1} trailing', '[1,]', '"\\q"', '(function(){})()', '{"__proto__":{}}', '{x:1}']) {
  assert.throws(() => tool.decode(input));
}
const vb = tool.vbsString('"D:\\Thư mục\\worker.exe" --job "ảnh.json"');
assert.match(vb, /ChrW\(/);
assert.ok(/^[\x00-\x7f]*$/.test(vb));
assert.ok(!source.includes('eval('));
assert.equal(JSON.stringify(tool.translationLines('13\nCảm ơn anh.\n14\nKhông có gì.', '013.psd')), '["Cảm ơn anh."]');
assert.equal(JSON.stringify(tool.translationLines('Ôm chặt\n\nỪ.', 'page.psd')), '["Ôm chặt","Ừ."]');
assert.throws(() => tool.translationLines('13\nCảm ơn anh.\n14\nKhông có gì.', '015.psd'));
// Photoshop 2023 (ExtendScript) treo vô hạn khi regex /i gặp câu "Tr" + chữ có dấu ("Trở nên…"): không dùng regex
// để đọc thoại. Node không tái hiện được lỗi đó nên kiểm tra mã nguồn và kết quả.
assert.ok(!source.includes('(?:trang|page'), 'không dùng lại regex /i nhận dòng số trang');
assert.equal(JSON.stringify(tool.translationLines('Trở nên đáng trân trọng\r\nTrang trí nhà\r\n12 giờ rồi\r\nTrước khi đi', '013.jpg')),
  '["Trở nên đáng trân trọng","Trang trí nhà","12 giờ rồi","Trước khi đi"]');
for (const [s, n] of [['13', '13'], ['Trang 13', '13'], ['TR.13', '13'], ['page 2', '2'], ['p.5', '5'], ['#4', '4'], ['13 –', '13'], ['12345', null]]) {
  assert.equal(tool.pageHeader(s), n);
}
// Bảng điều khiển gọi bridge: lệnh lạ / lỗi trả về JSON {error}, không ném lỗi ra ngoài Photoshop.
assert.equal(JSON.parse(tool.bridge('nope', '{}')).error, 'Không có lệnh nope');
assert.ok(JSON.parse(tool.bridge('docs', '{bad')).error);

function control(kind, label) {
  const widget = {
    kind, label, text: label || '', children: [],
    add(type, _, text) {
      const child = control(type, text);
      this.children.push(child);
      return child;
    },
    close() {},
  };
  Object.defineProperty(widget, 'selection', {
    get() { return this.selected; },
    set(index) { this.selected = {index}; },
  });
  return widget;
}
function controls(widget) {
  return [widget, ...widget.children.flatMap(controls)];
}
const dimensions = {value:480};
const doc = {name:'013.psd', width:dimensions, height:dimensions};
let preferences;
sandbox.Window = function (kind, name) { return control(kind, name); };
sandbox.ActionDescriptor = function () {
  const data = {};
  this.putString = (name, value) => { data[name] = value; };
  this.getString = (name) => data[name];
};
sandbox.stringIDToTypeID = (name) => name;
sandbox.Units = {PIXELS:'px'};
sandbox.app = {
  documents:[doc],
  preferences:{rulerUnits:'pt'},
  getCustomOptions() { if (!preferences) throw new Error('Missing settings'); return preferences; },
  putCustomOptions(_, value) { preferences = value; },
};
let quick = tool.options(doc, false);
let widgets = controls(quick);
let mode = widgets.find((c) => c.kind === 'checkbox' && c.label.includes('Giữ lưới chấm'));
assert.equal(mode.value, false);
mode.value = true;
widgets.find((c) => c.kind === 'edittext').text = 'Cảm ơn anh.';
widgets.find((c) => c.label === 'Nhận diện và xem trước').onClick();
assert.equal(quick.result.engine, 'screentone');
assert.equal(quick.result.mode, 'page');
quick = tool.options(doc, false);
widgets = controls(quick);
mode = widgets.find((c) => c.kind === 'checkbox' && c.label.includes('Giữ lưới chấm'));
assert.equal(mode.value, true);
widgets.find((c) => c.kind === 'edittext').text = '13\nCảm ơn anh.';
const profiles = widgets.find((c) => c.kind === 'dropdownlist');
profiles.selection = profiles.label.indexOf('Tự chọn font theo loại khung');
widgets.find((c) => c.kind === 'button' && c.label.startsWith('Làm cả chương')).onClick();
assert.equal(quick.result.mode, 'scan');
assert.equal(quick.result.engine, 'screentone');
console.log('Photoshop script: JSON, Unicode, page translations, and quick redraw settings passed.');
