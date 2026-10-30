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
console.log('Photoshop script: JSON, invalid data rejection, Unicode launcher and page translations passed.');
