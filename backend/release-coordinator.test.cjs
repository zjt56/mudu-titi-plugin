const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');
const { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const moduleReady = import(pathToFileURL(resolve(__dirname, '../lib/release-coordinator.js')));

function marker(dir, body) {
  const path = join(dir, '.operations-release.json');
  writeFileSync(path, JSON.stringify(body));
  return path;
}

test('prepared release pauses the watcher and installed release restarts exactly once', async () => {
  const { createReleaseCoordinator } = await moduleReady;
  const dir = mkdtempSync(join(tmpdir(), 'release-'));
  const path = marker(dir, { state: 'prepared', at: Math.floor(Date.now() / 1000), plan_id: 'p1', files: ['a.py'] });
  let restarts = 0;
  const c = createReleaseCoordinator({ markerPath: path, restart: async () => { restarts++; return { ok: true }; } });
  await c.check();
  assert.equal(c.status.paused, true);
  assert.equal(restarts, 0);
  marker(dir, { state: 'installed', at: Math.floor(Date.now() / 1000), plan_id: 'p1', files: ['a.py'] });
  await c.check();
  assert.equal(restarts, 1);
  assert.equal(c.status.paused, false);
  assert.equal(c.status.state, 'active');
  assert.equal(existsSync(path), false);
  // A cleared marker never restarts again.
  await c.check();
  assert.equal(restarts, 1);
  rmSync(dir, { recursive: true, force: true });
});

test('failed restart with an unusable rollback leaves a failed marker and never loops', async () => {
  const { createReleaseCoordinator } = await moduleReady;
  const dir = mkdtempSync(join(tmpdir(), 'release-'));
  const path = marker(dir, { state: 'installed', at: Math.floor(Date.now() / 1000), plan_id: 'p2', files: [] });
  let restarts = 0;
  const c = createReleaseCoordinator({ markerPath: path, restart: async () => { restarts++; return { ok: false, error: 'port busy' }; } });
  await c.check();
  assert.equal(c.status.state, 'failed');
  assert.match(c.status.lastError, /rollback_failed/);
  const body = JSON.parse(readFileSync(path, 'utf8'));
  assert.equal(body.state, 'failed');
  await c.check();
  await c.check();
  assert.equal(restarts, 1);
  assert.equal(c.status.paused, false);
  rmSync(dir, { recursive: true, force: true });
});

test('unhealthy boot restores the previous release and boots it once', async () => {
  const { createReleaseCoordinator } = await moduleReady;
  const dir = mkdtempSync(join(tmpdir(), 'release-'));
  const path = marker(dir, { state: 'installed', at: Math.floor(Date.now() / 1000), plan_id: 'p5', journal: 'j.json', files: ['a.py'] });
  const restarts = [];
  const c = createReleaseCoordinator({
    markerPath: path,
    restart: async () => {
      restarts.push(restarts.length);
      return restarts.length === 1 ? { ok: false, error: 'health timeout' } : { ok: true };
    },
    rollback: async (m) => ({ ok: true, restored: ['app/workflow/a.py'] }),
  });
  await c.check();
  assert.equal(restarts.length, 2, 'exactly one retry after restoring the old files');
  assert.equal(c.status.state, 'rolled_back');
  assert.equal(c.status.paused, false);
  assert.equal(existsSync(path), false, 'healthy restored boot clears the marker');
  await c.check();
  assert.equal(restarts.length, 2, 'no further restarts');
  rmSync(dir, { recursive: true, force: true });
});

test('rollback that cannot restore files stops and reports instead of rebooting broken code', async () => {
  const { createReleaseCoordinator } = await moduleReady;
  const dir = mkdtempSync(join(tmpdir(), 'release-'));
  const path = marker(dir, { state: 'installed', at: Math.floor(Date.now() / 1000), plan_id: 'p6', journal: 'j.json' });
  let restarts = 0;
  const c = createReleaseCoordinator({
    markerPath: path,
    restart: async () => { restarts++; return { ok: false, error: 'health timeout' }; },
    rollback: async () => ({ ok: false, error: 'source_changed_after_install: app/workflow/a.py' }),
  });
  await c.check();
  assert.equal(restarts, 1);
  assert.equal(c.status.state, 'failed');
  assert.match(c.status.lastError, /rollback_failed/);
  const body = JSON.parse(readFileSync(path, 'utf8'));
  assert.equal(body.state, 'failed');
  assert.match(body.error, /source_changed_after_install/);
  rmSync(dir, { recursive: true, force: true });
});

test('stale prepared marker cannot pause the watcher forever', async () => {
  const { createReleaseCoordinator } = await moduleReady;
  const dir = mkdtempSync(join(tmpdir(), 'release-'));
  const path = marker(dir, { state: 'prepared', at: Math.floor((Date.now() - 10 * 60 * 1000) / 1000), plan_id: 'p3' });
  const c = createReleaseCoordinator({ markerPath: path, restart: async () => ({ ok: true }) });
  await c.check();
  assert.equal(c.status.paused, false);
  rmSync(dir, { recursive: true, force: true });
});

test('rolled_back marker is discarded and releases the pause', async () => {
  const { createReleaseCoordinator } = await moduleReady;
  const dir = mkdtempSync(join(tmpdir(), 'release-'));
  const path = marker(dir, { state: 'rolled_back', at: Math.floor(Date.now() / 1000), plan_id: 'p4' });
  const c = createReleaseCoordinator({ markerPath: path, restart: async () => ({ ok: true }) });
  await c.check();
  assert.equal(c.status.paused, false);
  assert.equal(existsSync(path), false);
  rmSync(dir, { recursive: true, force: true });
});

for (const [at, paused] of [
  ['2026-11-09T12:00:00+0800', true],
  ['2026-11-09T11:50:00+0800', false],
  ['not-a-time', false],
  [null, false],
  ['2026-11-10T12:00:00+0800', false],
  [1794196800, true],
  [1794196800000, true],
]) {
  test(`prepared timestamp ${at} has bounded pause=${paused}`, async (t) => {
    const { createReleaseCoordinator } = await moduleReady;
    const dir = mkdtempSync(join(tmpdir(), 'release-'));
    t.after(() => rmSync(dir, { recursive: true, force: true }));
    const path = marker(dir, { state: 'prepared', at });
    const c = createReleaseCoordinator({ markerPath: path, now: () => Date.parse('2026-11-09T12:01:00+0800') });
    await c.check();
    assert.equal(c.status.paused, paused);
  });
}

test('thrown rollback becomes a failed marker and never restarts again', async (t) => {
  const { createReleaseCoordinator } = await moduleReady;
  const dir = mkdtempSync(join(tmpdir(), 'release-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = marker(dir, { state: 'installed', journal: 'fake' });
  let restarts = 0;
  const c = createReleaseCoordinator({ markerPath: path,
    restart: async () => { restarts++; return { ok: false }; },
    rollback: async () => { throw new Error('unreadable_journal'); } });
  await c.check();
  await c.check();
  assert.equal(restarts, 1);
  assert.equal(c.status.state, 'failed');
  assert.match(JSON.parse(readFileSync(path, 'utf8')).error, /unreadable_journal/);
});

test('stop cancels timer and prevents rollback or another restart after an in-flight boot', async (t) => {
  const { createReleaseCoordinator } = await moduleReady;
  const dir = mkdtempSync(join(tmpdir(), 'release-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = marker(dir, { state: 'installed' });
  let finish;
  let restarts = 0;
  let rollbacks = 0;
  const c = createReleaseCoordinator({ markerPath: path,
    restart: () => { restarts++; return new Promise((resolve) => { finish = resolve; }); },
    rollback: async () => { rollbacks++; return { ok: true }; } });
  c.start();
  const pending = c.check();
  c.stop();
  finish({ ok: false, error: 'stopped' });
  await pending;
  await c.check();
  assert.equal(restarts, 1);
  assert.equal(rollbacks, 0);
  assert.equal(JSON.parse(readFileSync(path, 'utf8')).state, 'installed');
});
