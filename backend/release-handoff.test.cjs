const test = require('node:test');
const assert = require('node:assert/strict');
const { mkdtempSync, mkdirSync, writeFileSync, readFileSync, existsSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join, resolve } = require('node:path');
const { pathToFileURL } = require('node:url');
const { createHash } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const vm = require('node:vm');

// Exercise the actual Python marker producer, without invoking install, restart,
// operations sessions, profile state, or any batch. Everything writable is temp.
test('actual producer marker reaches host at repository root and restores through constrained handoff', async (t) => {
  const dir = mkdtempSync(join(tmpdir(), 'release-handoff-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const root = join(dir, 'plugin');
  const workflowDir = join(root, 'app');
  mkdirSync(join(workflowDir, 'workflow'), { recursive: true });
  const producer = resolve(__dirname, '../app/workflow/operations_development.py');
  const emit = (state, extra = {}) => {
    const result = spawnSync('python3', ['-B', '-c',
      'import importlib.util,json,sys\ns=importlib.util.spec_from_file_location("release_producer",sys.argv[1])\nm=importlib.util.module_from_spec(s)\ns.loader.exec_module(m)\nm._release_marker(sys.argv[2],sys.argv[3],**json.loads(sys.argv[4]))',
      producer, root, state, JSON.stringify(extra)], { encoding: 'utf8', timeout: 5000, maxBuffer: 100000 });
    assert.equal(result.status, 0, result.stderr || result.error?.message);
  };
  const { createReleaseCoordinator } = await import(pathToFileURL(resolve(__dirname, '../lib/release-coordinator.js')));
  const { restoreRelease } = await import(pathToFileURL(resolve(__dirname, '../lib/code-restore.js')));
  // Load exactly the host's release wiring with side effects replaced by fakes.
  const source = readFileSync(resolve(__dirname, '../lib/index.js'), 'utf8');
  const start = source.slice(source.indexOf('function startReleaseCoordinator()'), source.indexOf('async function restartBackendSoon('));
  const stop = source.slice(source.indexOf('function stopBackendWatch()'), source.indexOf('// 从 profile'));
  let options;
  let boots = 0;
  let stops = 0;
  const context = vm.createContext({ WORKFLOW_DIR: workflowDir, join, resolve, restoreRelease,
    console: { log() {} }, clearTimeout,
    ensureBackend: async () => ({ ok: ++boots > 1, error: 'simulated_unhealthy_boot' }),
    createReleaseCoordinator: (supplied) => {
      options = supplied;
      const c = createReleaseCoordinator(supplied);
      const originalStop = c.stop;
      c.stop = () => { stops++; originalStop(); };
      return c;
    } });
  vm.runInContext('let releaseCoordinator = null, backendWatcher = null, backendWatchTimer = null, restartTimer = null;\n' + start + '\n' + stop + '\nthis.start = startReleaseCoordinator; this.stop = stopBackendWatch;', context);
  const coordinator = context.start();
  t.after(() => context.stop());
  const markerPath = join(root, '.operations-release.json');
  assert.equal(options.markerPath, markerPath);
  emit('prepared', { plan_id: 'fixture-plan', files: ['app/workflow/example.py'] });
  const body = JSON.parse(readFileSync(markerPath, 'utf8'));
  assert.match(body.at, /^\d{4}-\d{2}-\d{2}T.*[+-]\d{4}$/);
  await coordinator.check();
  assert.equal(coordinator.status.paused, true);
  assert.equal(boots, 0);
  const hash = (text) => createHash('sha256').update(text).digest('hex');
  const target = join(root, 'app/workflow/example.py');
  const backup = join(dir, 'example.backup');
  const journal = join(dir, 'journal.json');
  writeFileSync(target, 'installed-code');
  writeFileSync(backup, 'previous-code');
  writeFileSync(journal, JSON.stringify({ status: 'installed', source_root: root,
    changes: [{ path: 'app/workflow/example.py', base_sha256: hash('previous-code'), new_sha256: hash('installed-code') }],
    backups: { 'app/workflow/example.py': backup } }));
  emit('installed', { plan_id: 'fixture-plan', journal, files: ['app/workflow/example.py'] });
  await coordinator.check();
  assert.equal(boots, 2);
  assert.equal(coordinator.status.state, 'rolled_back');
  assert.equal(readFileSync(target, 'utf8'), 'previous-code');
  assert.equal(existsSync(markerPath), false);
  context.stop();
  assert.equal(stops, 1);
  // Unload leaves no polling or subsequent restart work alive.
  await coordinator.check();
  assert.equal(boots, 2);
});

test('host unload cancels pending source-watch debounce before it can restart', async () => {
  const source = readFileSync(resolve(__dirname, '../lib/index.js'), 'utf8');
  const block = source.slice(source.indexOf('function isBackendSource('), source.indexOf('// 从 profile'));
  const timers = new Map();
  let callback;
  let restarts = 0;
  const context = vm.createContext({ WORKFLOW_DIR: '/fixture/app', join, resolve,
    existsSync: () => true, console: { log() {}, error() {} },
    watch: (_path, _opts, cb) => { callback = cb; return { on() {}, close() {} }; },
    setTimeout: (fn) => { const id = { unref() {} }; timers.set(id, fn); return id; },
    clearTimeout: (id) => timers.delete(id),
    createReleaseCoordinator: () => ({ status: { paused: false }, start() {}, stop() {} }),
    ensureBackend: async () => { restarts++; return { ok: true }; } });
  vm.runInContext(block + '\nthis.start = startBackendWatch; this.stop = stopBackendWatch;', context);
  context.start();
  callback('change', 'workflow/example.py');
  assert.equal(timers.size, 1);
  context.stop();
  assert.equal(timers.size, 0);
  callback('change', 'workflow/example.py');
  assert.equal(timers.size, 0);
  for (const fn of timers.values()) await fn();
  assert.equal(restarts, 0);
});
