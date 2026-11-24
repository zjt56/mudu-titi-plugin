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
test('backend startup gives slow health probes time to respond', () => {
  // 健康探测失败时给两次更长窗口，不把繁忙误判为死亡。
  assert.match(host, /const slow = await probeBackend\(4000\) \|\| await probeBackend\(4000\)/);
});
test('manual and automatic restarts use the health and operations state', async () => {
  const healthSource = host.slice(host.indexOf('let restarting = false;'), host.indexOf('function restartShouldDefer('));
  const automatic = host.slice(host.indexOf('async function restartBackendSoon('), host.indexOf('function startBackendWatch('));
  const routeSource = host.slice(host.indexOf('function registerRoutes('), host.indexOf('// 把请求原样代理'));
  let handler;
  let health = 'idle';
  let operationsBusy = false;
  let forced = false;
  const calls = [];
  const context = vm.createContext({
    URL, Buffer, AbortSignal, console,
    backend: { ready: false, proc: { pid: 1 }, port: 8791 },
    backendUrl: () => 'http://127.0.0.1:8791/',
    backendAuthHeaders: () => ({}),
    fetch: async () => {
      if (health === 'unknown') throw new Error('timeout');
      return { ok: true, json: async () => ({ busy: health === 'busy' }) };
    },
    ensureBackend: async opts => { calls.push(['restart', opts.restart]); return { ok: true }; },
    deferRestart: async reason => { calls.push(['defer', reason]); },
    readBody: async () => JSON.stringify({ force: forced }),
  });
  const api = vm.runInContext([healthSource, automatic, routeSource,
    '({ registerRoutes, restartBackendSoon, setOperations: value => { operationsManager = value; } })',
  ].join('\n'), context);
  api.setOperations({ busyForRestart: () => operationsBusy });
  const ctx = { webServer: { register(route) { handler = route.handler; } }, effect(fn) { fn(); } };
  api.registerRoutes(ctx, async () => [], {}, {});
  async function restart() {
    const res = { setHeader() {}, writeHead(status) { this.status = status; }, end(body) { this.body = JSON.parse(body); } };
    await handler({ method: 'POST', url: '/wf-plugin/workbench/restart', headers: {} }, res);
    assert.equal(res.status, 200);
    return res.body;
  }
  for (const state of ['busy', 'unknown']) {
    health = state;
    const result = await restart();
    assert.equal(result.deferred, true);
    assert.equal(result.health, state);
  }
  health = 'idle';
  operationsBusy = true;
  assert.equal((await restart()).health, 'busy');
  operationsBusy = false;
  assert.equal((await restart()).restarted, true);
  forced = true;
  health = 'unknown';
  assert.equal((await restart()).restarted, true);
  assert.deepEqual(calls, [['defer', 'manual'], ['defer', 'manual'], ['defer', 'manual'],
    ['restart', true], ['restart', true]]);
  calls.length = 0;
  health = 'busy';
  await api.restartBackendSoon('source change');
  health = 'idle';
  await api.restartBackendSoon('source change');
  assert.deepEqual(calls, [['defer', 'source change'], ['restart', true]]);
});
test('health endpoint reports a busy flag for the plugin lifecycle', () => {
  const server = fs.readFileSync(path.join(root, 'app/workbench/server.py'), 'utf8');
  assert.match(server, /"busy": work_in_flight\(\)/);
  assert.match(server, /def work_in_flight\(\) -> bool:/);
});
test('operationsManager is declared at module scope for backendBusy and releaseCoordinator', async () => {
  const declIdx = host.indexOf('let operationsManager = null;');
  const coordIdx = host.indexOf('function startReleaseCoordinator(');
  const busyIdx = host.indexOf('async function backendBusy(');
  const applyIdx = host.indexOf('export function apply(');
  assert.ok(declIdx >= 0 && declIdx < coordIdx && declIdx < busyIdx, 'operationsManager 必须在模块顶层声明，早于 startReleaseCoordinator 与 backendBusy');
  assert.equal(host.slice(applyIdx).includes('let operationsManager'), false, 'apply() 内不得重新用 let 遮蔽模块级 operationsManager');
  // 直接在模块顶层切片执行 backendBusy，确认未挂载 apply() 时也不会抛出 ReferenceError。
  const slice = host.slice(host.indexOf('let operationsManager = null;'), host.indexOf('function restartShouldDefer('));
  const ctx = vm.createContext({
    backend: { ready: false, proc: null },
    createReleaseCoordinator: () => ({ start() {}, stop() {} }),
    WORKFLOW_DIR: '/tmp', join: (...a) => a.join('/'), resolve: (a) => a,
    restoreRelease: () => ({ ok: false }),
    ensureBackend: async () => ({ ok: true }),
    backendUrl: () => 'http://127.0.0.1:0/',
    backendAuthHeaders: () => ({}),
    fetch: async () => ({ ok: false }),
    AbortSignal: { timeout: () => undefined },
    console: { log() {} },
  });
  const fn = vm.runInContext(slice + '\nbackendBusy', ctx);
  await assert.doesNotReject(fn());
});
test('stopBackendWatch clears deferredRestartTimer and runDeferredRestart breaks loop after max defer', async () => {
  assert.match(host, /stopBackendWatch\(\)[\s\S]*clearTimeout\(deferredRestartTimer\)/);
  const slice = host.slice(host.indexOf('let backendWatcher = null;'), host.indexOf('// 从 profile'));
  let ensureCalls = 0;
  let clearedTimers = 0;
  const ctx = vm.createContext({
    WORKFLOW_DIR: '/tmp/nonexistent-wf-dir-xyz',
    existsSync: () => false,
    join: (...a) => a.join('/'), resolve: (a) => a,
    createReleaseCoordinator: () => ({ start() {}, stop() {}, blocksAdmission: () => false, status: { paused: false } }),
    restoreRelease: () => ({ ok: false }),
    backend: { ready: true, proc: { pid: 1, exitCode: null, signalCode: null }, instanceId: 'x', port: 8791 },
    backendChildAlive: () => true,
    backendUrl: () => 'http://127.0.0.1:8791/',
    backendAuthHeaders: () => ({}),
    ensureBackend: async () => { ensureCalls++; return { ok: true, url: 'http://127.0.0.1:8791/' }; },
    fetch: async () => ({ ok: true, json: async () => ({ busy: true }) }),
    AbortSignal: { timeout: () => undefined },
    setTimeout: () => ({ unref() {} }),
    clearTimeout: () => { clearedTimers++; },
    console: { log() {}, error() {} },
  });
  const api = vm.runInContext(
    slice + '\n({ deferRestart, runDeferredRestart, stopBackendWatch, setExpired: () => { deferredRestart = { reason: "x.py", since: Date.now() - RESTART_MAX_DEFER_MS - 1 }; }, getState: () => ({ deferredRestart, deferredRestartTimer }) })',
    ctx,
  );
  await api.deferRestart('x.py');
  assert.ok(api.getState().deferredRestartTimer);
  api.stopBackendWatch();
  assert.equal(api.getState().deferredRestartTimer, null);
  assert.equal(api.getState().deferredRestart, null);
  assert.ok(clearedTimers >= 1);
  // 超出最大等待窗口后再次检查，应放行重启而非重新进入新一轮 30 分钟等待。
  api.setExpired();
  await api.runDeferredRestart();
  assert.equal(ensureCalls, 1);
  assert.equal(api.getState().deferredRestart, null);
});
