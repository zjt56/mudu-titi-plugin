const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../lib/index.js'), 'utf8');

function load(overrides) {
  const start = source.indexOf('const RELEASE_WORKER_RELOAD_RETRY_MS');
  const end = source.indexOf('function startReleaseCoordinator()');
  assert.ok(start >= 0 && end > start, 'reloadWorkerAfterRelease 必须位于 startReleaseCoordinator 之前');
  const calls = [];
  const timers = [];
  const ctx = vm.createContext({
    console: { log() {}, error() {} },
    SPLIT_WORKER_ENABLED: true,
    workerDaemon: { proc: { pid: 1 }, draining: false },
    backendChildAlive: () => true,
    drainAndRestartWorker: async () => { calls.push('drain'); },
    workerBusy: async () => false,
    stopWorkerDaemon: async () => { calls.push('stop'); return true; },
    ensureWorkerDaemon: () => { calls.push('ensure'); },
    setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    clearTimeout: () => {},
    ...overrides(calls),
  });
  vm.runInContext(source.slice(start, end) + '\nthis.reload = reloadWorkerAfterRelease;', ctx);
  return { ctx, calls, timers };
}

test('发布重启块不再直接硬杀 worker', () => {
  const block = source.slice(source.indexOf('function startReleaseCoordinator()'), source.indexOf('async function restartBackendSoon('));
  const restart = block.slice(block.indexOf('restart: async'), block.indexOf('rollback:'));
  assert.ok(restart.includes('reloadWorkerAfterRelease()'));
  assert.ok(!restart.includes('stopWorkerDaemon'), '发布重启不得直接 stopWorkerDaemon');
});

test('worker 在跑时走排空，不停止进程', async () => {
  const { ctx, calls } = load(() => ({}));
  ctx.drainAndRestartWorker = async () => { calls.push('drain'); ctx.workerDaemon.draining = true; };
  const r = await ctx.reload();
  assert.equal(r.mode, 'draining');
  assert.deepEqual(calls, ['drain']);
});

test('排空失败且 worker 忙：延后重试，绝不硬杀', async () => {
  const { ctx, calls, timers } = load(() => ({ workerBusy: async () => true }));
  const r = await ctx.reload();
  assert.equal(r.mode, 'deferred');
  assert.deepEqual(calls, ['drain']);
  assert.equal(timers.length, 1);
});

test('排空失败且 worker 空闲：才停止并重拉', async () => {
  const { ctx, calls } = load(() => ({}));
  const r = await ctx.reload();
  assert.equal(r.mode, 'idle_restart');
  assert.deepEqual(calls, ['drain', 'stop', 'ensure']);
});

test('worker 不在时直接拉起，不排空', async () => {
  const { ctx, calls } = load(() => ({ backendChildAlive: () => false }));
  const r = await ctx.reload();
  assert.equal(r.mode, 'spawned');
  assert.deepEqual(calls, ['ensure']);
});

test('持续忙到上限后放弃但不杀进程', async () => {
  const { ctx, calls } = load(() => ({ workerBusy: async () => true }));
  const r = await ctx.reload(359);
  assert.equal(r.mode, 'busy_gave_up');
  assert.ok(!calls.includes('stop'));
});
