const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const host = fs.readFileSync(path.join(root, 'lib/index.js'), 'utf8');
function loadIsBackendSource() {
  const code = host.slice(host.indexOf('function isBackendSource('), host.indexOf('let backendWatcher'));
  return vm.runInNewContext(code + '\nisBackendSource', {});
}
test('isBackendSource: only real backend .py sources trigger auto-restart', () => {
  const f = loadIsBackendSource();
  // 应重启：真正的后端源码
  assert.equal(f('workflow/review.py'), true);
  assert.equal(f('workbench/server.py'), true);
  assert.equal(f('test_x.py'), true);
  // 不应重启：字节码缓存 / 隐藏 / 编辑器临时 / 非 py
  assert.equal(f('workflow/__pycache__/review.cpython-312.pyc'), false);
  assert.equal(f('workflow/review.pyc'), false);
  assert.equal(f('.hidden/x.py'), false);
  assert.equal(f('workflow/.review.py.swp'), false);
  assert.equal(f('workflow/review.py.swp'), false);
  assert.equal(f('workflow/review.py.bak'), false);
  assert.equal(f('workflow/review.py~'), false);
  assert.equal(f('workflow/notes.txt'), false);
  assert.equal(f(''), false);
});
test('auto-restart watcher is registered through ctx.effect in apply()', () => {
  assert.match(host, /ctx\.effect\(\(\) => \{ startBackendWatch\(\); return \(\) => stopBackendWatch\(\);/);
  assert.match(host, /function startBackendWatch\(\)/);
  assert.match(host, /function stopBackendWatch\(\)/);
});
