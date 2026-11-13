const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');
const ready = import(pathToFileURL(resolve(__dirname, '../lib/operations-agent.js')));
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const never = () => new Promise(() => {});

async function fixture(options = {}) {
  const { createOperationsAgentManager } = await ready;
  const calls = [], events = [], prompts = [], tools = new Map();
  const inc = { incident_id: 'incident', batch_name: 'offline lesson', batch_dir: '/mock/lesson', status: 'queued', eligible: true };
  let turns = 0, cancellations = 0, disposals = 0, setup;
  const agent = { model: { select() {} }, inbox: {}, followup: message => prompts.push(message),
    cancel: () => { cancellations++; }, whenIdle: () => {
      turns++;
      return options.program?.({ turns, tools, prompts, call: (action, input = {}, exec = {}) => tools.get(`operations_${action}`).execute(input, exec) });
    } };
  const handle = { dispose: async () => { disposals++; if (options.hungDispose) await never(); } };
  const create = async init => {
    setup = () => init.setup({ tools: { restrict: () => () => {}, guard: () => () => {},
      register: def => { tools.set(def.name, def); return () => tools.delete(def.name); } },
      systemPrompt: { section: () => () => {} }, on: () => () => {} }, agent);
    setup();
    if (options.creation) await options.creation.promise;
    return handle;
  };
  const manager = createOperationsAgentManager({ agents: { create, resume: create } }, {
    cwd: '/mock/plugin', intervalMs: 60000, heartbeatMs: 5, requestTimeoutMs: 50, maxAttemptMs: 1000,
    maxIdleContinuations: 0, resolveRoute: () => ({ provider: 'offline', model: 'mock' }),
    onProgress: event => events.push(event), backendRequest: async (path, init = {}) => {
      calls.push({ path, ...init });
      const custom = options.backend?.(path, init);
      if (custom !== undefined) return custom;
      if (path === '/api/operations/incidents') return { incidents: [inc] };
      if (path.endsWith('/claim')) return { mode: 'repair', attempt_id: 'fence', session_id: init.body.session_id };
      const action = init.body?.action;
      const evidence = action === 'verify' ? { verified: true, evidence_id: 'original-check' }
        : action === 'finish' ? { outcome: init.body.input.outcome, verified: true } : { ok: true };
      return { request_id: init.body?.request_id, status: 'completed', evidence };
    }, ...options.managerOptions,
  });
  // Test safety bound also holds the event loop open for deliberately hung promises.
  let watchdog;
  const run = async () => {
    manager.start(); await manager.tick();
    await Promise.race([manager.drain(), new Promise((_, reject) => { watchdog = setTimeout(() => reject(new Error('offline attempt wedged')), 1500); })])
      .finally(() => clearTimeout(watchdog));
  };
  return { manager, run, calls, events, prompts, tools, agent, setup: () => setup(),
    get turns() { return turns; }, get disposals() { return disposals; }, get cancellations() { return cancellations; } };
}

test('hard deadline settles whenIdle that ignores cancel, stops heartbeats, and releases the lease', async () => {
  const f = await fixture({ program: never, managerOptions: { maxAttemptMs: 25 } });
  await f.run();
  assert.ok(f.cancellations > 0);
  assert.equal(f.disposals, 1);
  assert.equal(f.manager.status().activeCount, 0);
  const releaseIndex = f.calls.findIndex(call => call.body?.action === 'release');
  assert.ok(releaseIndex >= 0);
  assert.ok(!f.calls.slice(releaseIndex + 1).some(call => call.body?.action === 'heartbeat'));
  assert.ok(!f.events.some(event => event.state === 'resolved'));
  await f.manager.dispose();
});

test('deadline during dispatched mutation preserves unknown and forbids release or late writes', async () => {
  let pending;
  const f = await fixture({ managerOptions: { maxAttemptMs: 25 }, backend: (_path, init) => {
    if (init.body?.action === 'code_repair') return never();
  }, program: ({ call }) => { pending = call('code_repair', { operation: 'write', path: 'app/workflow/mock.py', content: 'x' }); return pending.catch(() => never()); } });
  await f.run();
  await assert.rejects(pending);
  assert.equal(f.calls.filter(call => call.body?.action === 'code_repair').length, 1);
  assert.ok(!f.calls.some(call => call.body?.action === 'release'));
  assert.ok(f.calls.some(call => call.body?.action === 'progress' && call.body.input.state === 'outcome_unknown'));
  assert.ok(!f.events.some(event => event.state === 'resolved'));
  await f.manager.dispose();
});

test('read response ignoring abort is bounded without inventing an unknown mutation', async () => {
  const f = await fixture({ managerOptions: { requestTimeoutMs: 15 }, backend: (_path, init) => {
    if (init.body?.action === 'code_repair') return never();
  }, program: async ({ call }) => { await assert.rejects(call('code_repair', { operation: 'read_lines', path: 'app/workflow/mock.py', start_line: 1 })); } });
  await f.run();
  assert.ok(f.calls.some(call => call.body?.action === 'release'));
  assert.ok(!f.calls.some(call => call.body?.input?.state === 'outcome_unknown'));
  await f.manager.dispose();
});

test('cancelled queued mutation never dispatches or creates unknown receipt', async () => {
  const controller = new AbortController(); controller.abort(new Error('tool cancelled'));
  const f = await fixture({ program: async ({ call }) => {
    await assert.rejects(call('code_repair', { operation: 'write' }, { signal: controller.signal }));
  } });
  await f.run();
  assert.ok(!f.calls.some(call => call.body?.action === 'code_repair'));
  assert.ok(f.calls.some(call => call.body?.action === 'release'));
  await f.manager.dispose();
});

test('late creation handle is disposed, late setup cannot revive fenced tools', async () => {
  const creation = deferred();
  const f = await fixture({ creation, managerOptions: { maxAttemptMs: 20 } });
  await f.run();
  assert.equal(f.turns, 0);
  assert.equal(f.manager.status().activeCount, 0);
  creation.resolve();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.disposals, 1);
  assert.throws(f.setup, /Late agent setup/);
  assert.equal(f.tools.size, 0);
  await f.manager.dispose();
});

test('hung handle disposal cannot retain the lease after model deadline', async () => {
  const f = await fixture({ hungDispose: true, program: never, managerOptions: { maxAttemptMs: 20, requestTimeoutMs: 15 } });
  await f.run();
  assert.ok(f.calls.some(call => call.body?.action === 'release'));
  assert.equal(f.manager.status().activeCount, 0);
  await f.manager.dispose();
});

test('read-only loop gets concrete next action, concludes at cap, and write/test remain available', async () => {
  let concluded = 0;
  const f = await fixture({ managerOptions: { maxReadOnlyActions: 4, maxIdleContinuations: 1 }, program: async ({ turns, call }) => {
    if (turns === 1) {
      await call('request_code_repair', { summary: 'mock failing parser' });
      for (let i = 0; i < 4; i++) {
        const result = await call('code_repair', { operation: 'read', path: 'app/workflow/mock.py', offset: i });
        if (i >= 1) assert.match(result.host_progress.next_action, /search.*read_lines.*write/);
      }
      const result = await call('code_repair', { operation: 'read', path: 'app/workflow/mock.py' }, { concludeTurn: () => { concluded++; } });
      assert.equal(result.code, 'no_substantive_progress');
      assert.match(result.pagination, /next_offset/);
    } else {
      await call('code_repair', { operation: 'write', path: 'app/workflow/mock.py', content: 'fixed' });
      await call('code_repair', { operation: 'test', tests: ['workflow.test_mock'] });
      await call('verify');
      await call('finish', { outcome: 'resolved', summary: 'original check verified' });
    }
  } });
  await f.run();
  assert.equal(concluded, 1);
  assert.equal(f.turns, 2);
  assert.equal(f.calls.filter(call => call.body?.action === 'code_repair' && call.body.input.operation === 'read').length, 4);
  assert.ok(f.events.some(event => event.phase === 'code_write'));
  assert.ok(f.events.some(event => event.phase === 'code_test'));
  assert.ok(f.events.some(event => event.state === 'resolved'));
  assert.match(f.prompts[1].content[0].text, /复用已有 finding/);
  await f.manager.dispose();
});

test('bounded idle without actions releases with structured no-progress state and no success', async () => {
  const f = await fixture({ managerOptions: { maxIdleContinuations: 1 } });
  await f.run();
  assert.equal(f.turns, 2);
  assert.equal(f.calls.find(call => call.body?.action === 'release').body.input.state, 'no_substantive_progress');
  assert.equal(f.manager.status().attempts[0].phase, 'no_substantive_progress');
  assert.ok(!f.events.some(event => event.state === 'resolved'));
  await f.manager.dispose();
});

test('actual write/test progress does not freeze an incomplete workspace as unchanged evidence', async () => {
  const f = await fixture({ program: async ({ call }) => {
    await call('code_repair', { operation: 'write', path: 'app/workflow/mock.py', content: 'fixed' });
    await call('code_repair', { operation: 'test', tests: ['workflow.test_mock'] });
  } });
  await f.run();
  assert.equal(f.calls.find(call => call.body?.action === 'release').body.input.state, 'incomplete');
  assert.ok(!f.events.some(event => event.phase === 'no_substantive_progress'));
  await f.manager.dispose();
});

test('ordinary model thinking is not interrupted by heartbeat or a short no-progress timer', async () => {
  const thinking = deferred(), entered = deferred();
  const f = await fixture({ program: () => { entered.resolve(); return thinking.promise; }, managerOptions: { maxAttemptMs: 500 } });
  f.manager.start(); await f.manager.tick(); await entered.promise;
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(f.cancellations, 0);
  assert.equal(f.prompts.length, 1);
  assert.ok(f.calls.filter(call => call.body?.action === 'heartbeat').length > 1);
  assert.equal(f.manager.status().attempts[0].phase, 'model_wait');
  thinking.resolve(); await f.manager.drain(); await f.manager.dispose();
});

test('tool schema exposes bounded code search and line reads', async () => {
  const f = await fixture({ program: async ({ tools }) => {
    const schema = tools.get('operations_code_repair').parameters;
    assert.ok(schema.properties.operation.enum.includes('search'));
    assert.ok(schema.properties.operation.enum.includes('read_lines'));
    for (const key of ['query', 'cursor', 'start_line', 'max_matches', 'context']) assert.ok(schema.properties[key]);
  } });
  await f.run(); await f.manager.dispose();
});
