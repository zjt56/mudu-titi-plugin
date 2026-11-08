const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');
const moduleReady = import(pathToFileURL(resolve(__dirname, '../lib/operations-agent.js')));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const incident = (extra = {}) => ({ incident_id: 'inc-1', batch_name: 'test batch/一', batch_dir: '/mock/batches/test', status: 'queued', eligible: true, ...extra });

async function fixture(options = {}) {
  const { createOperationsAgentManager, OPERATIONS_TOOL_NAMES } = await moduleReady;
  const calls = [], agents = [], events = [], ownerEffects = [];
  const rows = options.rows || [incident()];
  const receipts = [];
  const state = { incident: { ...rows[0] }, receipts };
  const ctx = {
    effect: fn => { ownerEffects.push(fn()); },
    agents: {},
    sessionPersistence: options.persistence,
  };
  async function build(init, mode) {
    if (options.createHook) await options.createHook(init);
    const tools = new Map(), listeners = new Map(), restrictions = [], guards = [], sections = [];
    let work = Promise.resolve(), disposed = false, cancelCount = 0;
    const cancelled = deferred();
    const agent = {
      model: { select: route => { agent.route = route; } },
      inbox: options.pending ? { nextTurn: ['saved'], nextStep: [] } : { nextTurn: [], nextStep: [] },
      followup: message => {
        agent.messages.push(message);
        work = Promise.resolve().then(async () => {
          try { await options.program?.(record); }
          catch (error) { record.emit('agent/error', { agent, error }); }
        });
      },
      messages: [],
      whenIdle: async () => { await work; },
      cancel: () => { cancelCount++; cancelled.resolve(); },
    };
    const scoped = {
      tools: {
        register: definition => { tools.set(definition.name, definition); return () => tools.delete(definition.name); },
        restrict: filter => { restrictions.push(filter); return () => {}; },
        guard: guard => { guards.push(guard); return () => {}; },
      },
      systemPrompt: { section: section => { sections.push(section); return () => {}; } },
      on: (name, callback) => { listeners.set(name, callback); return () => listeners.delete(name); },
    };
    const record = { init, mode, tools, restrictions, guards, sections, agent, cancelled,
      emit: (name, ...args) => listeners.get(name)?.(...args),
      call: (action, input = {}) => tools.get(`operations_${action}`).execute(input, {}),
      get disposed() { return disposed; }, get cancelCount() { return cancelCount; },
    };
    await init.setup(scoped, agent);
    agents.push(record);
    return { agent, dispose: async () => { disposed = true; record.emit('agent/disposed', { agent }); } };
  }
  ctx.agents.create = init => build(init, 'create');
  ctx.agents.resume = init => build(init, 'resume');
  if (options.unavailable) ctx.agents = {};
  async function backendRequest(path, init = {}) {
    calls.push({ path, ...init });
    const custom = await options.backend?.(path, init, state);
    if (custom !== undefined) return custom;
    if (path === '/api/operations/incidents') return { incidents: rows };
    if (path.endsWith('/claim')) {
      Object.assign(state.incident, { attempt_id: 'attempt-1', session_id: init.body.session_id });
      return { attempt_id: 'attempt-1', session_id: init.body.session_id };
    }
    if (init.method === 'GET') return state;
    const { action, request_id, input } = init.body;
    const result = action === 'verify' ? { verified: true, evidence_id: 'evidence-1' }
      : action === 'finish' ? { outcome: input.outcome, verification: { verified: input.outcome === 'resolved' } }
      : action === 'resume' ? { released: true, reclaim_required: true } : { ok: true };
    const receipt = { request_id, status: 'completed', evidence: result };
    receipts.push(receipt);
    return receipt;
  }
  const manager = createOperationsAgentManager(ctx, {
    cwd: '/mock/plugin', backendRequest,
    resolveRoute: options.resolveRoute || (() => ({ provider: 'chosen-provider', model: 'chosen/model' })),
    intervalMs: 60000, heartbeatMs: 60000, requestTimeoutMs: 5000,
    onProgress: e => events.push(e), ...options.managerOptions,
  });
  const run = async () => { manager.start(); await manager.tick(); await manager.drain(); };
  return { manager, calls, agents, events, state, run, ownerEffects, OPERATIONS_TOOL_NAMES };
}

test('creates exactly one eligible incident with scoped tools and backend verified success', async () => {
  const f = await fixture({ program: async a => {
    assert.deepEqual(a.restrictions, [{ allow: [] }]);
    assert.equal(a.guards[0]({ name: 'bash' }), 'Only incident-scoped operations tools are allowed');
    assert.equal(a.guards[0]({ name: 'operations_verify' }), undefined);
    assert.equal(a.tools.size, 8);
    await a.call('diagnose'); await a.call('preview_repair', { strategy: 'known' });
    await a.call('apply_repair', { preview_id: 'preview-1' });
    await a.call('verify');
    await a.call('finish', { outcome: 'resolved', summary: 'Backend evidence confirms repair' });
  } });
  await f.run();
  assert.equal(f.agents.length, 1);
  assert.equal(f.agents[0].init.meta.cwd, '/mock/plugin');
  assert.deepEqual(f.agents[0].agent.route, { provider: 'chosen-provider', model: 'chosen/model' });
  assert.equal(f.agents[0].agent.messages[0].source.kind, 'user');
  assert.ok(f.calls.some(x => x.path.includes('test%20batch%2F%E4%B8%80')));
  assert.ok(f.events.some(e => e.state === 'resolved'));
  assert.ok(f.agents[0].disposed);
  assert.equal(f.agents[0].tools.size, 0);
  assert.equal(f.calls.filter(x => x.body?.action === 'release').length, 0);
  await f.manager.dispose();
});

test('repeated and concurrent scheduling never duplicates a session or mutating action', async () => {
  const gate = deferred(), entered = deferred();
  const f = await fixture({ program: async () => { entered.resolve(); await gate.promise; } });
  f.manager.start(); await entered.promise;
  await Promise.all([f.manager.tick(), f.manager.tick(), f.manager.tick()]);
  assert.equal(f.agents.length, 1);
  gate.resolve(); await f.manager.drain();
  await f.manager.tick(); await f.manager.drain();
  assert.equal(f.agents.length, 1);
  assert.equal(f.calls.filter(x => x.path.endsWith('/claim')).length, 1);
  await f.manager.dispose();
});

test('ignores ineligible and nonqueued incidents and admits only default concurrency one', async () => {
  const f = await fixture({ rows: [incident({ incident_id: 'no', eligible: false }), incident({ incident_id: 'running', status: 'running' }), incident(), incident({ incident_id: 'inc-2' })] });
  await f.run();
  assert.equal(f.agents.length, 1);
  assert.equal(f.manager.status().incidentCount, 4);
  await f.manager.dispose();
});

test('idle and assistant claims never report resolution, release records incomplete', async () => {
  const f = await fixture(); await f.run();
  assert.ok(f.events.some(e => e.state === 'idle_incomplete'));
  assert.ok(!f.events.some(e => e.state === 'resolved'));
  assert.equal(f.calls.find(x => x.body?.action === 'release').body.input.outcome, 'incomplete');
  await f.manager.dispose();
});

test('finish requires verification; applying repair invalidates older verification', async () => {
  const f = await fixture({ program: async a => {
    await assert.rejects(a.call('finish', { outcome: 'resolved' }), { code: 'VERIFICATION_REQUIRED' });
    await a.call('verify'); await a.call('apply_repair');
    await assert.rejects(a.call('finish', { outcome: 'resolved' }), { code: 'VERIFICATION_REQUIRED' });
    await a.call('record_finding', { check: 'unknown defect', evidence: 'trace' });
    await a.call('finish', { outcome: 'needs_deep_repair', summary: 'Requires code investigation' });
  } });
  await f.run();
  assert.ok(f.events.some(e => e.state === 'needs_deep_repair'));
  assert.equal(f.calls.filter(x => x.body?.action === 'finish').length, 1);
  await f.manager.dispose();
});

test('backend refusing verified finish cannot produce false success', async () => {
  const f = await fixture({ backend: (_path, init) => init.body?.action === 'finish' ? { outcome: 'resolved', verified: false } : undefined,
    program: async a => { await a.call('verify'); await assert.rejects(a.call('finish', { outcome: 'resolved' }), { code: 'UNVERIFIED_FINISH' }); } });
  await f.run(); assert.ok(!f.events.some(e => e.state === 'resolved')); await f.manager.dispose();
});

test('uncertain mutation is never replayed and later mutation waits for a durable receipt', async () => {
  let applyId;
  const f = await fixture({ backend: (_path, init) => {
    if (init.body?.action === 'apply_repair') { applyId = init.body.request_id; throw new Error('connection lost after commit'); }
  }, program: async a => {
    await assert.rejects(a.call('apply_repair'), /connection lost/);
    await assert.rejects(a.call('apply_repair'), { code: 'OUTCOME_UNKNOWN' });
    await a.call('read_evidence', { request_id: applyId });
    await assert.rejects(a.call('resume'), { code: 'OUTCOME_UNKNOWN' });
  } });
  await f.run();
  assert.equal(f.calls.filter(x => x.body?.action === 'apply_repair').length, 1);
  assert.equal(f.calls.filter(x => x.body?.action === 'release').length, 0);
  assert.ok(f.calls.some(x => x.body?.action === 'progress' && x.body.input.outcome === 'unknown'));
  assert.ok(f.events.some(e => e.state === 'backend_disconnected'));
  await f.manager.dispose();
});

test('confirmed receipt permits new work without resending unknown action', async () => {
  const f = await fixture({ backend: (_path, init, state) => {
    if (init.body?.action === 'apply_repair') {
      state.receipts.push({ request_id: init.body.request_id, status: 'completed', evidence: { applied: true } });
      throw new Error('response lost');
    }
  }, program: async a => { await assert.rejects(a.call('apply_repair')); await a.call('verify'); await a.call('finish', { outcome: 'resolved' }); } });
  await f.run();
  assert.equal(f.calls.filter(x => x.body?.action === 'apply_repair').length, 1);
  assert.ok(f.events.some(e => e.state === 'reconciled'));
  await f.manager.dispose();
});

test('claim response loss is reconciled before creation and never blindly reclaimed', async () => {
  const f = await fixture({ backend: (path, init, state) => {
    if (path.endsWith('/claim')) { Object.assign(state.incident, { session_id: init.body.session_id, attempt_id: 'attempt-committed' }); throw new Error('claim response lost'); }
  } });
  await f.run();
  assert.equal(f.agents.length, 1);
  assert.equal(f.calls.filter(x => x.path.endsWith('/claim')).length, 1);
  assert.ok(f.calls.some(x => x.body?.attempt_id === 'attempt-committed'));
  await f.manager.dispose();
});

test('unconfirmed claim never creates or retries', async () => {
  const f = await fixture({ backend: path => { if (path.endsWith('/claim')) throw new Error('offline'); } });
  await f.run(); await f.manager.tick(); await f.manager.drain();
  assert.equal(f.agents.length, 0);
  assert.equal(f.calls.filter(x => x.path.endsWith('/claim')).length, 1);
  await f.manager.dispose();
});

test('persisted session resumes its identity and selects explicit route, pending inbox is not duplicated', async () => {
  const f = await fixture({ rows: [incident({ session_id: 'persisted-session' })], pending: true });
  await f.run();
  assert.equal(f.agents[0].mode, 'resume');
  assert.equal(f.agents[0].init.resumeSessionId, 'persisted-session');
  assert.equal(f.agents[0].agent.route.model, 'chosen/model');
  assert.equal(f.agents[0].agent.messages.length, 0);
  await f.manager.dispose();
});

test('missing route or service does not fall back to current model', async () => {
  const f = await fixture({ resolveRoute: () => ({ model: 'incomplete' }) }); await f.run();
  assert.equal(f.agents.length, 0); assert.match(f.manager.status().configError, /explicitly/);
  await f.manager.dispose();
  const missing = await fixture({ unavailable: true }); await missing.run();
  assert.equal(missing.manager.status().available, false); assert.equal(missing.calls.length, 0); await missing.manager.dispose();
});

test('model failures and session disconnection are separately reported', async () => {
  const model = await fixture({ program: async () => { throw new Error('model route unavailable'); } }); await model.run();
  assert.ok(model.events.some(e => e.state === 'model_failed'));
  assert.ok(!model.events.some(e => e.state === 'backend_disconnected')); await model.manager.dispose();
  const disconnected = await fixture({ program: async a => { a.emit('agent/disposed', { agent: a.agent }); } }); await disconnected.run();
  assert.ok(disconnected.events.some(e => e.state === 'session_disconnected')); await disconnected.manager.dispose();
});

test('lease renewal failure cancels and blocks mutations', async () => {
  const f = await fixture({ backend: (_path, init) => { if (init.body?.action === 'heartbeat') throw new Error('lease unavailable'); } });
  await f.run();
  assert.ok(f.events.some(e => e.state === 'lease_uncertain'));
  assert.ok(!f.events.some(e => e.state === 'resolved')); await f.manager.dispose();
});

test('stop and owning-context disposal cancel active work and drain teardown', async () => {
  const entered = deferred();
  const f = await fixture({ program: async a => { entered.resolve(); await a.cancelled.promise; } });
  f.manager.start(); await entered.promise;
  await Promise.all([f.manager.stop(), f.manager.stop()]);
  assert.ok(f.agents[0].cancelCount > 0); assert.equal(f.agents[0].disposed, true);
  assert.equal(f.calls.filter(x => x.body?.action === 'release').length, 1);
  await f.ownerEffects[0]();
  assert.throws(() => f.manager.start(), { code: 'DISPOSED' });
});

test('stop aborts factory initialization and waits for rollback', async () => {
  const entered = deferred();
  let rolledBack = false;
  const f = await fixture({ createHook: async init => {
    entered.resolve();
    await new Promise(resolve => init.signal.addEventListener('abort', resolve, { once: true }));
    rolledBack = true; throw new Error('creation cancelled');
  } });
  f.manager.start(); await entered.promise; await f.manager.stop();
  assert.equal(rolledBack, true); assert.equal(f.agents.length, 0); await f.manager.dispose();
});

test('resume concludes attempt awaiting execution without finish or release', async () => {
  const f = await fixture({ program: async a => {
    await a.call('resume');
    await assert.rejects(a.call('verify'), { code: 'FINISHED' });
  } });
  await f.run();
  assert.ok(f.events.some(e => e.state === 'awaiting_execution'));
  assert.ok(!f.events.some(e => e.state === 'resolved'));
  assert.equal(f.calls.filter(x => ['release', 'finish'].includes(x.body?.action)).length, 0);
  await f.manager.dispose();
});

test('explicit queue generation permits reentry after stop and start', async () => {
  const row = incident({ session_id: 'saved', queue_generation: 1 });
  const f = await fixture({ rows: [row] }); await f.run(); await f.manager.stop();
  row.queue_generation = 2;
  await f.run();
  assert.equal(f.agents.length, 2);
  assert.ok(f.agents.every(a => a.init.resumeSessionId === 'saved'));
  await f.manager.dispose();
});

test('persisted unknown receipt blocks model publication after restart', async () => {
  const f = await fixture({ rows: [incident({ session_id: 'saved' })], backend: (path, init, state) => {
    if (init.method === 'GET' && path !== '/api/operations/incidents') return { ...state, receipts: [{ request_id: 'old-write', status: 'unknown' }] };
  } });
  await f.run(); assert.equal(f.agents.length, 0);
  assert.ok(f.events.some(e => e.state === 'outcome_unknown'));
  assert.equal(f.calls.filter(x => x.body?.action === 'release').length, 0);
  await f.manager.dispose();
});

test('claim-before-create crash recovers same identity after authoritative stat absence', async () => {
  const f = await fixture({ rows: [incident({ session_id: 'claimed-not-created' })], persistence: { stat: async id => { assert.equal(id, 'claimed-not-created'); return undefined; } } });
  await f.run(); assert.equal(f.agents[0].mode, 'create');
  assert.equal(f.agents[0].init.sessionId, 'claimed-not-created'); await f.manager.dispose();
});

test('failed backend receipt is not success and permits diagnostic next action', async () => {
  const f = await fixture({ backend: (_path, init) => init.body?.action === 'apply_repair'
    ? { request_id: init.body.request_id, status: 'failed', evidence: { error: 'source proof missing' } } : undefined,
    program: async a => { await assert.rejects(a.call('apply_repair'), { code: 'ACTION_FAILED' }); await a.call('diagnose'); } });
  await f.run(); assert.ok(!f.events.some(e => e.state === 'resolved'));
  assert.ok(f.calls.some(x => x.body?.action === 'diagnose')); await f.manager.dispose();
});

test('reconciled identical action returns receipt instead of replaying it', async () => {
  const f = await fixture({ backend: (_path, init, state) => {
    if (init.body?.action === 'apply_repair') { state.receipts.push({ request_id: init.body.request_id, status: 'completed', evidence: { applied: true } }); throw new Error('lost response'); }
  }, program: async a => { await assert.rejects(a.call('apply_repair', { repair: 'repair_source_tables' })); assert.deepEqual(await a.call('apply_repair', { repair: 'repair_source_tables' }), { applied: true }); } });
  await f.run(); assert.equal(f.calls.filter(x => x.body?.action === 'apply_repair').length, 1); await f.manager.dispose();
});

test('action budget cancels execution and releases with cooldown reason', async () => {
  const f = await fixture({ managerOptions: { maxActions: 1 }, program: async a => { await a.call('diagnose'); await assert.rejects(a.call('verify'), { code: 'BUDGET_EXCEEDED' }); } });
  await f.run(); assert.match(f.calls.find(x => x.body?.action === 'release').body.input.reason, /cooldown/); await f.manager.dispose();
});

test('attempt deadline cancels an otherwise unbounded model turn', async () => {
  const f = await fixture({ managerOptions: { maxAttemptMs: 15 }, program: async a => { await a.cancelled.promise; } });
  // Keep the test process alive: production timers are intentionally unref'ed.
  const keepAlive = setTimeout(() => {}, 1000);
  try { await f.run(); assert.ok(f.events.some(e => e.state === 'budget_exceeded')); assert.ok(f.agents[0].disposed); }
  finally { clearTimeout(keepAlive); await f.manager.dispose(); }
});

test('route error is visible and stop/start retries corrected route without fallback', async () => {
  let fixed = false;
  const f = await fixture({ resolveRoute: () => { if (!fixed) throw new Error('missing operations route'); return { provider: 'explicit', model: 'fixed' }; } });
  await f.run(); assert.match(f.manager.status().configError, /missing operations route/);
  await f.manager.stop(); fixed = true; await f.run();
  assert.equal(f.agents.length, 1); assert.equal(f.agents[0].agent.route.model, 'fixed'); await f.manager.dispose();
});

test('temporary list failure is retried on later tick without disabling manager', async () => {
  let offline = true;
  const f = await fixture({ backend: path => { if (path === '/api/operations/incidents' && offline) throw new Error('404 backend starting'); } });
  await f.run(); assert.equal(f.agents.length, 0); offline = false;
  await f.manager.tick(); await f.manager.drain(); assert.equal(f.agents.length, 1); await f.manager.dispose();
});
