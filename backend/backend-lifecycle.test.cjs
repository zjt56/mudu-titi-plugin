const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const vm = require('node:vm');
const net = require('node:net');
const http = require('node:http');
const { EventEmitter } = require('node:events');
const { spawn, execFileSync } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const root = path.resolve(__dirname, '..');
const host = fs.readFileSync(path.join(root, 'lib/index.js'), 'utf8');
const lifecycle = host.slice(host.indexOf('function backendUrl()'), host.indexOf('// 后端代码热重启：'));

function load(overrides = {}) {
  const backend = { proc: null, instanceId: '', port: 18891, ready: false, lastError: '' };
  const context = {
    backend, WORKBENCH_PORT: backend.port, WORKFLOW_DIR: path.join(root, 'app'),
    process, AbortController, fetch, setTimeout, clearTimeout, randomUUID,
    createServer: net.createServer, spawn, join: path.join, resolve: path.resolve, mkdirSync: fs.mkdirSync,
    execFileSync: () => '', readlinkSync: () => '', Buffer,
    resolveDataRoot: () => os.tmpdir(), resolvePython: () => 'python3',
    questionBankSettings: { runtimeEnv: () => ({}) },
    ocrSettings: { runtimeEnv: () => ({ MUDU_PADDLE_OCR_CONFIG: path.join(os.tmpdir(), 'wf-ocr-private-fixture', 'ocr-config.json') }) },
    console: { log() {}, error() {} },
    ...overrides,
  };
  vm.createContext(context);
  return vm.runInContext(lifecycle + '\n({ backend, ensureBackend, spawnBackend, probeBackend, stopBackend, pickPort, reapStaleWorkbenchProcesses, backendWatchdogTick, startBackendWatchdog, stopBackendWatchdog })', context);
}

function fake(options = {}) {
  const bound = new Map(options.foreign ? [[18891, 'foreign']] : []);
  const children = [];
  const signals = [];
  const portChecks = [];
  let fetches = 0;
  const api = load({
    mkdirSync() {},
    setTimeout: (fn, ms) => setTimeout(fn, ms === 500 ? 1 : ms === 3000 ? 30 : ms === 1000 ? 15 : ms),
    createServer: () => {
      const server = new EventEmitter();
      server.listen = (port) => {
        portChecks.push({ port, live: children.filter(c => c.exitCode === null && c.signalCode === null).length });
        queueMicrotask(() => server.emit(bound.has(port) ? 'error' : 'listening'));
      };
      server.close = (done) => done();
      return server;
    },
    fetch: async (url) => {
      fetches++;
      const port = Number(new URL(url).port);
      const identity = options.wrongHealth ? 'old-instance' : bound.get(port);
      return { ok: true, json: async () => ({ ok: true, instance_id: identity }) };
    },
    spawn: (python, args, opts) => {
      assert.equal(opts.env.MUDU_PADDLE_OCR_CONFIG, path.join(os.tmpdir(), 'wf-ocr-private-fixture', 'ocr-config.json'));
      const child = new EventEmitter();
      child.pid = 100 + children.length;
      child.exitCode = null;
      child.signalCode = null;
      child.stdout = new EventEmitter();
      child.stderr = new EventEmitter();
      child.port = Number(args[args.length - 1]);
      child.instanceId = opts.env.WF_BACKEND_INSTANCE_ID;
      child.args = args;
      child.kill = (signal) => {
        signals.push({ child, signal });
        if (options.neverExit || (options.ignoreTerm && signal === 'SIGTERM')) return true;
        setTimeout(() => {
          child.signalCode = signal;
          if (bound.get(child.port) === child.instanceId) bound.delete(child.port);
          child.emit('exit', null, signal);
        }, options.exitDelay || 5);
        return true;
      };
      children.push(child);
      bound.set(child.port, child.instanceId);
      return child;
    },
  });
  return { ...api, children, signals, portChecks, bound, fetches: () => fetches };
}

test('restart waits for the owned exit and reuses the same port repeatedly', async () => {
  const f = fake({ exitDelay: 10 });
  try {
    assert.equal((await f.ensureBackend()).ok, true);
    for (let i = 0; i < 3; i++) {
      assert.equal((await f.ensureBackend({ restart: true })).ok, true);
      assert.equal(f.backend.port, 18891);
      assert.equal(f.children.filter(c => c.signalCode === null).length, 1);
    }
    assert.ok(f.portChecks.every(check => check.live === 0));
    assert.equal(f.children.length, 4);
  } finally { await f.stopBackend(); }
});

test('late exit, error and stderr from an old child cannot clear the replacement', async () => {
  const f = fake();
  try {
    await f.ensureBackend();
    const old = f.backend.proc;
    await f.ensureBackend({ restart: true });
    const current = f.backend.proc;
    old.emit('exit', 7, null);
    old.emit('error', new Error('old error'));
    old.stderr.emit('data', Buffer.from('old stderr'));
    assert.equal(f.backend.proc, current);
    assert.equal(f.backend.ready, true);
    assert.equal(f.backend.lastError, '');
  } finally { await f.stopBackend(); }
});

test('a foreign healthy listener is not adopted or killed', async () => {
  const f = fake({ foreign: true });
  try {
    assert.equal(await f.probeBackend(), false);
    assert.equal(f.fetches(), 0);
    assert.equal((await f.ensureBackend()).ok, true);
    assert.equal(f.backend.port, 18892);
    assert.equal(f.bound.get(18891), 'foreign');
    assert.equal(f.signals.length, 0);
  } finally { await f.stopBackend(); }
});

test('a 200 health response with another instance id cannot mark the child ready', async () => {
  const f = fake({ wrongHealth: true });
  try {
    assert.equal((await f.ensureBackend()).ok, false);
    assert.equal(f.backend.ready, false);
  } finally { await f.stopBackend(); }
});

test('an unhealthy owned child is stopped before recovery instead of drifting ports', async () => {
  const f = fake();
  try {
    await f.ensureBackend();
    f.bound.set(18891, 'wrong-health');
    assert.equal((await f.ensureBackend()).ok, true);
    assert.equal(f.children.length, 2);
    assert.equal(f.backend.port, 18892); // port now belongs to another instance; never kill it
    assert.equal(f.bound.get(18891), 'wrong-health');
    assert.equal(f.signals[0].child, f.children[0]);
  } finally { await f.stopBackend(); }
});

test('concurrent explicit restarts serialize and retain a single live child', async () => {
  const f = fake();
  try {
    await f.ensureBackend();
    const results = await Promise.all([f.ensureBackend({ restart: true }), f.ensureBackend({ restart: true }), f.ensureBackend({ restart: true })]);
    assert.ok(results.every(r => r.ok));
    assert.equal(f.children.length, 4);
    assert.equal(f.children.filter(c => c.signalCode === null).length, 1);
    assert.ok(f.portChecks.every(check => check.live === 0));
  } finally { await f.stopBackend(); }
});

test('a stuck owned child gets bounded escalation, and failure retains ownership', async () => {
  const f = fake({ neverExit: true });
  await f.ensureBackend();
  const child = f.backend.proc;
  const result = await f.ensureBackend({ restart: true });
  assert.equal(result.ok, false);
  assert.match(result.error, /did not exit/);
  assert.equal(f.backend.proc, child);
  assert.equal(f.children.length, 1);
  assert.deepEqual(f.signals.map(s => s.signal), ['SIGTERM', 'SIGKILL']);
  child.signalCode = 'SIGKILL';
  child.emit('exit', null, 'SIGKILL');
});

test('SIGTERM-resistant owned child exits after SIGKILL before port reuse', async () => {
  const f = fake({ ignoreTerm: true });
  try {
    await f.ensureBackend();
    assert.equal((await f.ensureBackend({ restart: true })).ok, true);
    assert.equal(f.backend.port, 18891);
    assert.deepEqual(f.signals.map(s => s.signal), ['SIGTERM', 'SIGKILL']);
  } finally { await f.stopBackend(); }
});

test('stop cancels startup and queued restarts before they can respawn a child', async () => {
  let release;
  let spawns = 0;
  const api = load({
    createServer: () => {
      const server = new EventEmitter();
      server.listen = () => { release = () => server.emit('listening'); };
      server.close = done => done();
      return server;
    },
    spawn: () => { spawns++; throw new Error('must not spawn after stop'); },
  });
  const starting = api.ensureBackend();
  const queued = api.ensureBackend({ restart: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(await api.stopBackend(), true);
  release();
  assert.equal((await starting).ok, false);
  assert.equal((await queued).ok, false);
  assert.equal(spawns, 0);
});

test('a health response that finishes after stop cannot restore ready', async () => {
  let release;
  const api = load({ fetch: () => new Promise(resolve => { release = resolve; }) });
  const child = new EventEmitter();
  child.pid = 123;
  child.exitCode = null;
  child.signalCode = null;
  child.kill = signal => { setImmediate(() => { child.signalCode = signal; child.emit('exit', null, signal); }); return true; };
  api.backend.proc = child;
  api.backend.instanceId = 'owned';
  const starting = api.ensureBackend();
  const stopped = api.stopBackend();
  release({ ok: true, json: async () => ({ ok: true, instance_id: 'owned' }) });
  assert.equal((await starting).ok, false);
  await stopped;
  assert.equal(api.backend.ready, false);
  assert.equal(api.backend.proc, null);
});

test('real Python backend health identity and repeated restart keep one owned port', { timeout: 20000 }, async () => {
  // Isolated data, an OS-assigned port, and only processes spawned by this test.
  const python = process.env.WF_PYTHON || 'python3';
  execFileSync(python, ['-c', 'import sys; assert sys.version_info >= (3, 9)']);
  const foreign = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ ok: true, instance_id: 'foreign' }));
  });
  await new Promise(resolve => foreign.listen(0, '127.0.0.1', resolve));
  const port = foreign.address().port;
  const data = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-backend-lifecycle-'));
  let ocrEnvChecks = 0;
  const privateOCR = path.join(data, 'private-ocr', 'ocr-config.json');
  const f = load({ resolvePython: () => python, resolveDataRoot: () => data, WORKBENCH_PORT: port,
    ocrSettings: { runtimeEnv: () => ({ MUDU_PADDLE_OCR_CONFIG: privateOCR }) },
    spawn: (executable, args, opts) => {
      assert.equal(opts.env.MUDU_PADDLE_OCR_CONFIG, privateOCR);
      ocrEnvChecks++;
      return spawn(executable, args, opts);
    },
  });
  f.backend.port = port;
  const owned = [];
  try {
    assert.equal(await f.probeBackend(), false);
    assert.equal((await f.ensureBackend()).ok, true);
    assert.notEqual(f.backend.port, port);
    const reusablePort = f.backend.port;
    for (let i = 0; i < 3; i++) {
      const child = f.backend.proc;
      owned.push(child);
      const health = await (await fetch(`http://127.0.0.1:${reusablePort}/api/health`)).json();
      assert.equal(health.instance_id, f.backend.instanceId);
      assert.equal((await f.ensureBackend({ restart: true })).ok, true);
      assert.equal(f.backend.port, reusablePort);
      assert.ok(child.exitCode !== null || child.signalCode !== null);
      assert.ok(owned.every(c => c.exitCode !== null || c.signalCode !== null));
    }
    assert.equal(ocrEnvChecks, 4);
    assert.equal((await (await fetch(`http://127.0.0.1:${port}/api/health`)).json()).instance_id, 'foreign');
    assert.equal(await f.stopBackend(), true);
    assert.equal(f.backend.proc, null);
  } finally {
    await f.stopBackend();
    await new Promise(resolve => foreign.close(resolve));
    fs.rmSync(data, { recursive: true, force: true });
  }
});

test('port scan skips a long run of occupied ports instead of reusing a busy one', async () => {
  const busy = new Map(Array.from({ length: 30 }, (_, i) => [18891 + i, 'busy']));
  const api = load({
    createServer: () => {
      const server = new EventEmitter();
      server.listen = (port) => queueMicrotask(() => server.emit(busy.has(port) ? 'error' : 'listening'));
      server.close = (done) => done();
      return server;
    },
  });
  assert.equal(await api.pickPort(18891), 18921);
});

test('lifecycle kills the process tree and reaps this plugin\'s orphans', () => {
  assert.match(host, /detached: useGroup/);
  assert.match(host, /process\.kill\(-child\.pid, signal\)/);
  assert.match(host, /taskkill/);
  assert.match(host, /function reapStaleWorkbenchProcesses/);
  assert.match(host, /process\.kill\(pid, 'SIGTERM'\)/);
  assert.match(host, /start \+ 100/);
});

test('watchdog restarts a backend that exited without anyone asking', async () => {
  const f = fake({ exitDelay: 5 });
  try {
    assert.equal((await f.ensureBackend()).ok, true);
    const dead = f.backend.proc;
    dead.exitCode = 1;
    f.bound.delete(dead.port);
    dead.emit('exit', 1, null);
    assert.equal(f.backend.proc, null);
    const result = await f.backendWatchdogTick();
    assert.equal(result.ok, true);
    assert.notEqual(f.backend.proc, null);
    assert.notEqual(f.backend.proc, dead);
    assert.equal(f.backend.ready, true);
    assert.equal(f.children.length, 2);
  } finally { await f.stopBackend(); }
});

test('watchdog leaves a healthy backend alone and spawns nothing', async () => {
  const f = fake();
  try {
    await f.ensureBackend();
    const current = f.backend.proc;
    const result = await f.backendWatchdogTick();
    assert.equal(result.ok, true);
    assert.equal(result.skipped, 'alive');
    assert.equal(f.backend.proc, current);
    // 分离 worker 开启时，backendWatchdogTick 对健康后端会补启缺失的 worker（有意设计）；
    // 这里只要求不再起第二个后端，worker 至多补启一个。
    const workers = f.children.filter(c => (c.args || []).includes('workbench.worker'));
    assert.equal(f.children.length - workers.length, 1);
    assert.ok(workers.length <= 1);
  } finally { await f.stopBackend(); }
});

test('watchdog reuses a running unmanaged workbench instead of spawning a duplicate', async () => {
  let spawned = 0;
  const api = load({
    fetch: async () => ({ ok: true, json: async () => ({ workflow_runtime_version: 'workflow-runtime-v2' }) }),
    spawn: () => { spawned += 1; throw new Error('must not spawn a second backend'); },
  });
  const result = await api.backendWatchdogTick();
  assert.equal(result.ok, true);
  assert.equal(result.skipped, 'unmanaged');
  assert.equal(spawned, 0);
  assert.equal(api.backend.ready, true); // 代理直接复用已在服务的后端
  assert.equal(api.backend.proc, null);
});

test('watchdog scheduler is idempotent and stops cleanly', () => {
  const f = fake();
  try {
    assert.equal(f.startBackendWatchdog({ intervalMs: 60000 }), true);
    assert.equal(f.startBackendWatchdog(), false);
    f.stopBackendWatchdog();
    assert.equal(f.startBackendWatchdog(), true);
  } finally { f.stopBackendWatchdog(); }
});

test('Windows shutdown kills both backend and split worker trees via taskkill and reaps stale worker', async () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-win-lifecycle-'));
  const runtimeDir = path.join(dataDir, 'runtime');
  fs.mkdirSync(runtimeDir, { recursive: true });
  fs.writeFileSync(path.join(runtimeDir, 'worker-endpoint.json'), JSON.stringify({
    pid: 4242, port: 19999, host: '127.0.0.1', token: 't',
  }));
  const execCalls = [];
  const winProcess = {
    platform: 'win32',
    arch: 'x64',
    pid: 1,
    env: { ...process.env, WF_SPLIT_WORKER: '1' },
    kill: () => true,
  };
  const spawned = [];
  const api = (() => {
    const backend = { proc: null, instanceId: '', port: 18891, ready: false, lastError: '' };
    const context = {
      backend, WORKBENCH_PORT: backend.port, WORKFLOW_DIR: path.join(root, 'app'),
      process: winProcess, AbortController, fetch: async () => ({ ok: true, json: async () => ({ ok: true }) }),
      setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 20)), clearTimeout, randomUUID,
      createServer: net.createServer,
      join: path.join, resolve: path.resolve, mkdirSync: fs.mkdirSync,
      existsSync: fs.existsSync, readFileSync: fs.readFileSync, statSync: fs.statSync,
      renameSync: fs.renameSync, appendFileSync: fs.appendFileSync, readlinkSync: () => '', Buffer,
      resolveDataRoot: () => dataDir, resolvePython: () => 'C:\\Python312\\python.exe',
      materializeAgentConfig: () => '',
      questionBankSettings: { runtimeEnv: () => ({}) },
      ocrSettings: { runtimeEnv: () => ({}) },
      console: { log() {}, error() {} },
      execFileSync: (cmd, args) => {
        execCalls.push({ cmd, args });
        if (cmd === 'tasklist') return '"python.exe","4242","Console","1","10,000 K"\r\n';
        if (cmd === 'taskkill') {
          const pid = Number(args[1]);
          const child = spawned.find(c => c.pid === pid);
          if (child && child.exitCode === null) {
            child.exitCode = 0;
            queueMicrotask(() => child.emit('exit', 0, null));
          }
          return '';
        }
        return '';
      },
      spawn: (python, args) => {
        const child = new EventEmitter();
        child.pid = 5000 + spawned.length;
        child.exitCode = null;
        child.signalCode = null;
        child.stdout = new EventEmitter();
        child.stderr = new EventEmitter();
        child.args = args;
        child.kill = () => true;
        spawned.push(child);
        return child;
      },
    };
    vm.createContext(context);
    return vm.runInContext(
      lifecycle + '\n({ backend, workerDaemon, spawnBackend, ensureWorkerDaemon, stopBackend, stopWorkerDaemon, reapStaleWorkerProcesses })',
      context,
    );
  })();
  try {
    // 1) Windows 上启动 worker 前应按 worker-endpoint.json 清理残留孤儿 worker（pid 4242）
    const worker = api.ensureWorkerDaemon();
    assert.ok(worker);
    assert.ok(execCalls.some(c => c.cmd === 'tasklist' && c.args.includes('PID eq 4242')));
    assert.ok(execCalls.some(c => c.cmd === 'taskkill' && c.args.includes('4242') && c.args.includes('/T')));

    // 2) 同时停止 backend 与 split worker 时，均走 taskkill /T /F 且无残留存活子进程
    const srv = api.spawnBackend();
    assert.ok(srv);
    assert.equal(await api.stopBackend(), true);
    assert.equal(await api.stopWorkerDaemon('test_shutdown'), true);
    assert.ok(execCalls.some(c => c.cmd === 'taskkill' && c.args.includes(String(srv.pid)) && c.args.includes('/T')));
    assert.ok(execCalls.some(c => c.cmd === 'taskkill' && c.args.includes(String(worker.pid)) && c.args.includes('/T')));
    assert.equal(spawned.filter(c => c.exitCode === null && c.signalCode === null).length, 0);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});

test('Windows resolvePython prefers py launcher / standard install paths and detects Microsoft Store alias', () => {
  const pySection = host.slice(host.indexOf('const PYTHON_MIN ='), host.indexOf('// 数据根：'));
  function runResolve({ execFileSyncImpl, existsImpl, env = {} }) {
    const backend = { lastError: '' };
    const winProcess = {
      platform: 'win32',
      arch: 'x64',
      env: { LOCALAPPDATA: 'C:\\Users\\demo\\AppData\\Local', ...env },
    };
    const context = {
      backend,
      PLUGIN_DIR: 'C:\\plugin',
      process: winProcess,
      join: path.win32.join,
      homedir: () => 'C:\\Users\\demo',
      existsSync: existsImpl || (() => false),
      execFileSync: execFileSyncImpl,
    };
    vm.createContext(context);
    return {
      result: vm.runInContext(pySection + '\nresolvePython()', context),
      backend,
    };
  }

  // Case 1: py -3.12 succeeds and resolves to real sys.executable
  const tried1 = [];
  const r1 = runResolve({
    execFileSyncImpl: (cmd, args) => {
      tried1.push([cmd, args[0]]);
      if (cmd === 'py' && args[0] === '-3.12') {
        return '3.12|C:\\Users\\demo\\AppData\\Local\\Programs\\Python\\Python312\\python.exe\n';
      }
      throw new Error('not found');
    },
  });
  assert.equal(r1.result, 'C:\\Users\\demo\\AppData\\Local\\Programs\\Python\\Python312\\python.exe');
  assert.deepEqual(tried1[0], ['py', '-3.12']);

  // Case 2: py unavailable, falls back to %LOCALAPPDATA%\Programs\Python\Python312\python.exe
  const localPy = 'C:\\Users\\demo\\AppData\\Local\\Programs\\Python\\Python312\\python.exe';
  const r2 = runResolve({
    existsImpl: (p) => p === localPy,
    execFileSyncImpl: (cmd) => {
      if (cmd === localPy) return '3.12|' + localPy + '\n';
      throw new Error('not found');
    },
  });
  assert.equal(r2.result, localPy);

  // Case 3: only Microsoft Store alias exists -> reports dedicated actionable hint
  const r3 = runResolve({
    execFileSyncImpl: (cmd) => {
      if (cmd === 'python') {
        return '3.12|C:\\Users\\demo\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe\n';
      }
      const err = new Error('not found');
      if (cmd === 'python3') {
        err.stderr = 'Python was not found; run without arguments to install from the Microsoft Store';
      }
      throw err;
    },
  });
  assert.equal(r3.result, null);
  assert.match(r3.backend.lastError, /Microsoft Store 应用执行别名/);
});
