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
function loadRestartShouldDefer() {
  const start = host.indexOf('const RESTART_MAX_DEFER_MS');
  const end = host.indexOf('async function deferRestart');
  assert.ok(start > 0 && end > start, 'restartShouldDefer must exist');
  return vm.runInNewContext(host.slice(start, end) + '\nrestartShouldDefer', {});
}
test('restartShouldDefer: 忙且在等待窗口内延后，超时或空闲立即重启', () => {
  const f = loadRestartShouldDefer();
  assert.equal(f(true, 0), true);
  assert.equal(f(true, 29 * 60 * 1000), true);
  // 超过最大延后窗口必须放行，否则代码更新可能永远不生效。
  assert.equal(f(true, 30 * 60 * 1000), false);
  assert.equal(f(true, 31 * 60 * 1000), false);
  assert.equal(f(false, 0), false);
});
test('restart avoids killing a busy backend', () => {
  // 有在途模型调用时不得整树回收后端与 agent 子进程。
  assert.match(host, /if \(!restarting && await backendBusy\(\)\)/);
  assert.match(host, /await deferRestart\(reason \|\| ''\)/);
  // 手动重启同样要避开在途任务。
  assert.match(host, /if \(await backendBusy\(\)\) \{\s*await deferRestart\('manual'\)/);
  // 健康探测失败时给两次更长窗口，不把繁忙误判为死亡。
  assert.match(host, /const slow = await probeBackend\(4000\) \|\| await probeBackend\(4000\)/);
});
test('health endpoint reports a busy flag for the plugin lifecycle', () => {
  const server = fs.readFileSync(path.join(root, 'app/workbench/server.py'), 'utf8');
  assert.match(server, /"busy": work_in_flight\(\)/);
  assert.match(server, /def work_in_flight\(\) -> bool:/);
});
