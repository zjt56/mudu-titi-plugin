const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../lib/index.js'), 'utf8');
const routes = source.slice(source.indexOf('function registerRoutes('), source.indexOf('// 把请求原样代理'));

async function request(method, url, payload, operations) {
  let route;
  const ctx = { webServer: { register(value) { route = value; return () => {}; } }, effect(fn) { fn(); } };
  const env = { URL, Buffer, console, questionBankSettings: {}, ocrSettings: {}, readBody: async () => JSON.stringify(payload) };
  vm.createContext(env);
  vm.runInContext(routes + '\nthis.register = registerRoutes;', env);
  env.register(ctx, async () => [], {}, {}, operations);
  const response = { headers: {}, setHeader(k, v) { this.headers[k] = v; }, writeHead(status) { this.status = status; }, end(value) { this.body = JSON.parse(value); } };
  await route.handler({ method, url, headers: { 'content-type': 'application/json' } }, response);
  return response;
}

test('operations status remains explicit when optional Harness services are absent', async () => {
  const result = await request('GET', '/wf-plugin/operations/status', null, null);
  assert.equal(result.status, 200);
  assert.equal(result.body.available, false);
  assert.match(result.body.error, /原有录题/);
});

test('operations configuration goes through shared owner and reports actual failures', async () => {
  let value = true;
  const operations = { status: () => ({ available: true, enabled: value }), configure: async input => {
    if (typeof input.enabled !== 'boolean') throw new Error('invalid enabled');
    value = input.enabled;
    return { available: true, enabled: value };
  } };
  let response = await request('POST', '/wf-plugin/operations/config', { enabled: false }, operations);
  assert.equal(response.status, 200);
  assert.equal(response.body.enabled, false);
  response = await request('GET', '/wf-plugin/operations/status', null, operations);
  assert.equal(response.body.enabled, false);
  response = await request('POST', '/wf-plugin/operations/config', { enabled: 'yes' }, operations);
  assert.equal(response.status, 400);
  assert.match(response.body.error, /invalid/);
});

function nativeOperations({ available = true, routeError = false, ready = true, stop = async () => {} } = {}) {
  const calls = [], backend = { ready, port: 12345 };
  let enabled = true, options, ensureCount = 0;
  const manager = { start() {}, stop, status: () => ({ available }) };
  const env = {
    AbortSignal, console, backend, PLUGIN_DIR: '/mock/plugin',
    operationsSettings: { read: () => ({ enabled }), save: value => { enabled = value.enabled; return { enabled }; } },
    ensureBackend: async () => { ensureCount++; backend.ready = true; return { ok: true }; },
    readRouter: () => ({}), harnessDefaultModel: () => ({}),
    operationsRouteFor: () => { if (routeError) throw new Error('invalid explicit route'); return { provider: 'explicit', model: 'selected' }; },
    createOperationsAgentManager: (_scope, value) => { options = value; return manager; },
    fetch: async (url, init) => {
      calls.push({ url, ...init, body: init.body ? JSON.parse(init.body) : undefined });
      return { ok: true, json: async () => ({ has_work: true, batches: ['paper'] }) };
    },
  };
  const ctx = { inject: (_dependencies, callback) => callback({
    agents: available ? { create() {}, resume() {} } : {}, effect() {},
  }) };
  const body = source.slice(source.indexOf('export function apply(ctx)'), source.indexOf('  const modelsCache ='))
    .replace('export function apply(ctx)', 'function apply(ctx)');
  vm.createContext(env);
  vm.runInContext(body + '\nreturn operations; }\nthis.operations = apply;', env);
  const operations = env.operations(ctx);
  return { calls, operations, options, get ensureCount() { return ensureCount; } };
}

test('native operations poll advertises usable service before inspecting work with the same bounded signal', async () => {
  const f = nativeOperations({ ready: false });
  const signal = AbortSignal.timeout(1000);
  const work = await f.options.hasWork({ signal });
  assert.equal(f.ensureCount, 1);
  assert.deepEqual(f.calls.map(row => row.url.split('/api/')[1]), ['operations/service', 'operations/work-state']);
  assert.deepEqual(JSON.parse(JSON.stringify(f.calls[0].body)), { available: true });
  assert.equal(f.calls[0].method, 'POST');
  assert.ok(f.calls.every(row => row.signal === signal));
  assert.equal(work.has_work, true);
  assert.equal(work.batches[0], 'paper');
});

test('invalid route or unavailable native agents revoke admission without inspecting incidents', async () => {
  for (const scenario of [{ routeError: true }, { available: false }]) {
    const f = nativeOperations(scenario);
    const scan = f.options.hasWork({ signal: AbortSignal.timeout(1000) });
    if (scenario.routeError) await assert.rejects(scan, /invalid explicit route/);
    else assert.equal((await scan).has_work, false);
    assert.equal(f.calls.length, 1);
    assert.ok(f.calls[0].url.endsWith('/api/operations/service'));
    assert.equal(f.calls[0].body.available, false);
  }
});

test('disabling native operations revokes immediately and again after draining positive polls', async () => {
  let finishStop;
  const f = nativeOperations({ stop: () => new Promise(resolve => { finishStop = resolve; }) });
  const pending = f.operations.configure({ enabled: false });
  assert.equal(f.calls.length, 1, 'revoke does not wait for active attempts to stop');
  assert.equal(f.calls[0].body.available, false);
  finishStop();
  const status = await pending;
  assert.equal(status.enabled, false);
  assert.equal(f.calls.length, 2, 'final revoke fences any positive heartbeat already dispatched');
  assert.ok(f.calls[0].signal instanceof AbortSignal);
  await f.options.hasWork({ signal: AbortSignal.timeout(1000) });
  assert.equal(f.calls.length, 3);
  assert.ok(f.calls.every(row => row.body.available === false));
});

test('aborted native availability scan cannot advertise service or inspect work', async () => {
  const f = nativeOperations();
  const controller = new AbortController();
  controller.abort(new Error('scan cancelled'));
  await assert.rejects(f.options.hasWork({ signal: controller.signal }), /scan cancelled/);
  assert.equal(f.calls.length, 0);
});
