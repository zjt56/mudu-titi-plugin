const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../lib/index.js'), 'utf8');

function load(root) {
  const start = source.indexOf('const WORKER_LIFECYCLE_MAX_BYTES');
  const end = source.indexOf('function spawnWorkerDaemon()');
  assert.ok(start >= 0 && end > start);
  const ctx = vm.createContext({ join: path.join, resolveDataRoot: () => root, mkdirSync: fs.mkdirSync,
    statSync: fs.statSync, renameSync: fs.renameSync, appendFileSync: fs.appendFileSync,
    JSON, Date, String, process: { pid: 4242 } });
  vm.runInContext(source.slice(start, end) + '\nthis.record = recordWorkerLifecycle;', ctx);
  return ctx;
}

test('写入一行 JSONL，包含事件与字段', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wl-'));
  load(root).record('exit', { pid: 7, code: null, signal: 'SIGKILL', stop_reason: 'release_idle_restart' });
  const lines = fs.readFileSync(path.join(root, 'runtime/worker-lifecycle.jsonl'), 'utf8').trim().split('\n');
  assert.equal(lines.length, 1);
  const row = JSON.parse(lines[0]);
  assert.equal(row.event, 'exit');
  assert.equal(row.signal, 'SIGKILL');
  assert.equal(row.host_pid, 4242);
  assert.ok(row.at);
});

test('超过上限轮转为 .1', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'wl-'));
  const file = path.join(root, 'runtime/worker-lifecycle.jsonl');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, 'x'.repeat(1024 * 1024 + 10));
  load(root).record('spawn', { pid: 1 });
  assert.ok(fs.existsSync(file + '.1'));
  assert.equal(fs.readFileSync(file, 'utf8').trim().split('\n').length, 1);
});

test('数据根不可写时静默，不抛出', () => {
  const ctx = load('/dev/null/nope');
  assert.doesNotThrow(() => ctx.record('spawn', { pid: 1 }));
});

test('spawn/exit/stop/drain/reap 都已接入留痕', () => {
  for (const ev of ['spawn', 'exit', 'stop_requested', 'drain_requested', 'reaped_stale']) {
    assert.ok(source.includes("recordWorkerLifecycle('" + ev + "'"), ev);
  }
  assert.ok(source.includes("stopWorkerDaemon('release_idle_restart')"));
});
