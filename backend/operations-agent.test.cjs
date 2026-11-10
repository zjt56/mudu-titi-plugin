const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');
const moduleReady = import(pathToFileURL(resolve(__dirname, '../lib/operations-agent.js')));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
test('operations prompt keeps review differences outside content repair authority', async () => {
  const { OPERATIONS_PROMPT } = await moduleReady;
  for (const text of ['审核产出，不是卡点', 'field=knowledge 不是内容故障证据',
    '禁止借 resume upstream_rederive/rejudge_knowledge', '旧 execution_completed、0 卡点、空队列',
    '人工决定不作为本地续跑前置条件']) {
    assert.ok(OPERATIONS_PROMPT.includes(text), text);
  }
});

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
        // The real loop persists this value unchanged; restore rejects anonymous
        // messages before setup, so fixtures must enforce the durable contract.
        assert.equal(message.role, 'user');
        assert.match(message.id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
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

test('only proven pre-setup malformed history requests receipt-fenced session recovery', async () => {
  const sessionId = 'operations-damaged';
  const message = `stored session "${sessionId}" failed validation: Error: session event at seq 10 lacks an identified message`;
  for (const scenario of ['proven', 'ordinary', 'unknown', 'already_recovered']) {
    const proven = scenario !== 'ordinary';
    const error = Object.assign(new Error(message, { cause: new Error('session event at seq 10 lacks an identified message') }), {
      name: proven ? 'SessionPersistenceCorruptionError' : 'Error',
    });
    const f = await fixture({ rows: [incident({ session_id: sessionId,
      ...(scenario === 'already_recovered' ? { session_recovery: { session_id: 'older' } } : {}) })],
      persistence: { stat: async () => ({}) },
      backend: async (path, init, state) => {
        if (scenario === 'unknown' && init.method === 'GET' && !state.receipts.some(row => row.request_id === 'unsettled')) {
          state.receipts.push({ request_id: 'unsettled', status: 'unknown' });
        }
      }, createHook: async () => { throw error; } });
    await f.run();
    assert.equal(f.agents.length, 0);
    const release = f.calls.find(call => call.body?.action === 'release');
    assert.deepEqual(release?.body.input.session_corruption, scenario === 'proven' ? {
      session_id: sessionId, reason: 'missing_user_message_identity',
    } : undefined);
    assert.ok(!f.events.some(event => event.state === 'resolved'));
    await f.manager.stop();
  }
});

test('retired historical identity creates one fresh session on the original explicit route', async () => {
  let statCalls = 0;
  const f = await fixture({ rows: [incident({ session_id: null, queue_generation: 15,
    session_recovery: { session_id: 'operations-retired', reason: 'missing_user_message_identity' } })],
    persistence: { stat: async () => { statCalls++; throw new Error('retired session must not be queried'); } },
    managerOptions: { makeSessionId: () => 'operations-fresh' },
    program: async a => { await a.call('diagnose'); },
  });
  await f.run();
  await f.manager.tick(); await f.manager.drain();
  assert.equal(statCalls, 0);
  assert.equal(f.agents.length, 1);
  assert.equal(f.agents[0].mode, 'create');
  assert.equal(f.agents[0].init.sessionId, 'operations-fresh');
  assert.deepEqual(f.agents[0].agent.route, { provider: 'chosen-provider', model: 'chosen/model' });
  assert.equal(f.agents[0].agent.messages.length, 1);
  assert.ok(f.calls.some(call => call.body?.action === 'diagnose'));
  assert.ok(!f.events.some(event => event.state === 'resolved'));
  await f.manager.dispose();
});

test('creates exactly one eligible incident with scoped tools and backend verified success', async () => {
  const f = await fixture({ program: async a => {
    assert.deepEqual(a.restrictions, [{ allow: [] }]);
    assert.equal(a.guards[0]({ name: 'bash' }), 'Only incident-scoped operations tools are allowed');
    assert.equal(a.guards[0]({ name: 'operations_verify' }), undefined);
    assert.equal(a.tools.size, 12);
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

test('finish can hand a budget-exhausted incident to a person', async () => {
  const f = await fixture({ program: async a => {
    await a.call('finish', { outcome: 'needs_human', summary: '24 attempts exhausted; needs a decision' });
  } });
  await f.run();
  assert.ok(f.events.some(e => e.state === 'needs_human'));
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

test('transport cause survives poorer turn summary and all progress diagnostics redact secrets', async () => {
  const cause = Object.assign(new Error('connect failed https://user:url-password@example.test/v1?api_key=query-secret&token=query-token Authorization: Bearer bearer-secret api_key=key-secret token=token-secret password="password secret"'), {
    code: 'ECONNRESET', payload: 'payload-secret', headers: { authorization: 'header-secret' },
  });
  cause.stack = 'stack-secret';
  const failure = Object.assign(new Error('fetch failed', { cause }), { name: 'TransportError', code: 'TRANSPORT' });
  const f = await fixture({ program: async a => {
    a.emit('agent/error', { agent: a.agent, error: failure });
    a.emit('session/event', {}, { type: 'turn/end', data: { reason: {
      kind: 'error', error: { message: 'fetch failed api_key=summary-secret', code: 'TRANSPORT', stack: 'summary-stack-secret', payload: 'summary-payload-secret' },
    } } });
  } });
  await f.run();
  const final = f.calls.find(x => x.body?.action === 'progress' && x.body.input.state === 'model_failed').body.input;
  assert.match(final.error, /TransportError/);
  assert.match(final.error, /TRANSPORT/);
  assert.match(final.error, /cause: connect failed/);
  assert.match(final.error, /ECONNRESET/);
  assert.equal(final.error, f.events.find(e => e.state === 'model_failed').error);
  assert.equal(typeof final.reason.error.message, 'string');
  const published = JSON.stringify({ events: f.events, inputs: f.calls.map(x => x.body?.input) });
  for (const secret of ['url-password', 'query-secret', 'query-token', 'bearer-secret', 'key-secret', 'token-secret', 'password secret', 'payload-secret', 'header-secret', 'stack-secret', 'summary-secret', 'summary-stack-secret', 'summary-payload-secret']) {
    assert.ok(!published.includes(secret), `leaked ${secret}`);
  }
  assert.ok(!f.events.some(e => e.state === 'resolved'));
  assert.equal(f.calls.find(x => x.body?.action === 'release').body.input.outcome, 'incomplete');
  await f.manager.dispose();
});

test('serialized turn error alone retains sanitized code and nested cause without implying success', async () => {
  const f = await fixture({ program: async a => {
    a.emit('session/event', {}, { type: 'turn/end', data: { reason: { kind: 'error', error: {
      message: 'transport unavailable', code: 'TRANSPORT', cause: { name: 'SocketError', code: 'ETIMEDOUT', message: 'Basic basic-secret client_secret=client-secret sk-private-key' },
    } } } });
  } });
  await f.run();
  const diagnostic = f.events.find(e => e.state === 'model_failed').error;
  assert.match(diagnostic, /TRANSPORT/); assert.match(diagnostic, /SocketError/); assert.match(diagnostic, /ETIMEDOUT/);
  assert.doesNotMatch(JSON.stringify(f.events), /basic-secret|client-secret|sk-private-key/);
  assert.ok(!f.events.some(e => e.state === 'resolved'));
  await f.manager.dispose();
});

test('error diagnostics bound huge and cyclic causes and omit stacks and arbitrary objects', async () => {
  const cause = { message: 'socket closed', code: 'ECONNRESET' };
  cause.cause = cause;
  const failure = { name: 'TransportError', code: 'TRANSPORT', message: 'x'.repeat(100000), cause, stack: 'stack-secret', payload: { message: 'payload-secret' } };
  const f = await fixture({ program: async () => { throw failure; } });
  await f.run();
  const diagnostic = f.events.find(e => e.state === 'model_failed').error;
  assert.ok(diagnostic.length <= 1500); assert.match(diagnostic, /ECONNRESET/); assert.match(diagnostic, /circular cause/);
  assert.doesNotMatch(diagnostic, /stack-secret|payload-secret/);
  await f.manager.dispose();
  const unknown = await fixture({ program: async () => { throw { payload: 'private', toString() { throw new Error('must not stringify'); } }; } });
  await unknown.run();
  assert.equal(unknown.events.find(e => e.state === 'model_failed').error, 'Unknown error');
  await unknown.manager.dispose();
});

test('quoted credentials cut off by the input bound are fully redacted', async () => {
  const f = await fixture({ program: async () => { throw new Error(`transport failed password="${'quoted-secret '.repeat(1000)}"`); } });
  await f.run();
  const diagnostic = f.events.find(e => e.state === 'model_failed').error;
  assert.match(diagnostic, /password=\[redacted\]/); assert.doesNotMatch(diagnostic, /quoted-secret/);
  await f.manager.dispose();
});

test('diagnostics cap cause depth and tolerate inaccessible error properties', async () => {
  let failure = { message: 'beyond-depth-secret' };
  for (let i = 0; i < 5; i++) failure = { message: `level ${i}`, cause: failure };
  Object.defineProperty(failure, 'code', { get() { throw new Error('getter-secret'); } });
  const f = await fixture({ program: async () => { throw failure; } });
  await f.run();
  const diagnostic = f.events.find(e => e.state === 'model_failed').error;
  assert.match(diagnostic, /cause limit/); assert.doesNotMatch(diagnostic, /beyond-depth-secret|getter-secret/);
  await f.manager.dispose();
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

test('idle work-state skips incident inspection and model takeover', async () => {
  const calls = [];
  const f = await fixture({ backend: async (path) => { calls.push(path); return undefined; }, hasWork: async () => false });
  await f.manager.tick();
  assert.deepEqual(calls, []);
  assert.equal(f.manager.status().incidentCount, 0);
  await f.manager.dispose();
});

test('batches still advancing show healing instead of standby, without inventing incidents', async () => {
  const calls = [];
  const f = await fixture({
    backend: async (path) => { calls.push(path); return path === '/api/operations/incidents' ? { incidents: [] } : undefined; },
    managerOptions: { hasWork: async () => ({ has_work: true,
      batches: ['23年安徽中考英语', '22年'] }) },
  });
  await f.run();
  assert.deepEqual(calls, ['/api/operations/incidents']);
  const status = f.manager.status();
  assert.equal(status.incidentCount, 0);
  assert.equal(status.healingCount, 2);
  assert.deepEqual(status.healingBatches, ['23年安徽中考英语', '22年']);
  await f.manager.dispose();
});

test('idle batch list clears healing so the panel can say standby again', async () => {
  let work = { has_work: true, batches: [{ batch_name: 'a' }] };
  const f = await fixture({ backend: async () => ({ incidents: [] }),
    managerOptions: { hasWork: async () => work } });
  await f.run();
  assert.equal(f.manager.status().healingCount, 1);
  work = { has_work: false, batches: [] };
  await f.manager.tick();
  assert.equal(f.manager.status().healingCount, 0);
  await f.manager.dispose();
});

test('temporary list failure is retried on later tick without disabling manager', async () => {
  let offline = true;
  const f = await fixture({ backend: path => { if (path === '/api/operations/incidents' && offline) throw new Error('404 backend starting'); } });
  await f.run(); assert.equal(f.agents.length, 0); offline = false;
  await f.manager.tick(); await f.manager.drain(); assert.equal(f.agents.length, 1); await f.manager.dispose();
});

test('scoped regeneration and code repair stay incident-scoped and forward exact input', async () => {
  const f = await fixture({ program: async a => {
    await a.call('regenerate_units', { target_stage: 'answers', unit_ids: ['u1'], reason: '答案与原文不符' });
    await a.call('request_code_repair', { summary: '表格行空白列解析崩溃' });
    await a.call('code_repair', { operation: 'read', path: 'app/workflow/checks.py' });
  } });
  await f.run();
  for (const name of ['operations_regenerate_units', 'operations_request_code_repair', 'operations_code_repair', 'operations_install_code_repair']) {
    assert.equal(f.agents[0].guards[0]({ name }), undefined, name);
  }
  assert.deepEqual(f.calls.find(x => x.body?.action === 'regenerate_units').body.input,
    { target_stage: 'answers', unit_ids: ['u1'], reason: '答案与原文不符' });
  assert.deepEqual(f.calls.find(x => x.body?.action === 'code_repair').body.input,
    { operation: 'read', path: 'app/workflow/checks.py' });
  assert.equal(f.calls.filter(x => x.body?.action === 'install_code_repair').length, 0);
  await f.manager.dispose();
});

test('status exposes successful polling, active work and separate scan errors', async () => {
  const entered = deferred(), release = deferred();
  const f = await fixture({ program: async a => { await a.call('diagnose'); entered.resolve(); await release.promise; } });
  f.manager.start();
  try {
    await entered.promise;
    const status = f.manager.status();
    assert.equal(status.running, true);
    assert.equal(status.activeCount, 1);
    assert.equal(status.pollCount, 1);
    assert.ok(Date.parse(status.lastPollAt));
    assert.equal(status.lastPollError, '');
    assert.equal(status.attempts[0].action, 'diagnose');
    assert.equal(status.attempts[0].active, true);
  } finally { release.resolve(); await f.manager.drain(); await f.manager.dispose(); }
  assert.equal(f.manager.status().activeCount, 0);
});

test('install awaits host restart and cannot be reported as a fix in the same attempt', async () => {
  const f = await fixture({ backend: (_path, init) => init.body?.action === 'install_code_repair'
      ? { status: 'installed_pending_restart', restart_required: true } : undefined,
    program: async a => {
      await a.call('verify');
      const installed = await a.call('install_code_repair', { confirm: true });
      assert.equal(installed.restart_required, true);
      await assert.rejects(a.call('verify'), { code: 'FINISHED' });
      await assert.rejects(a.call('finish', { outcome: 'resolved', summary: 'installed' }), { code: 'FINISHED' });
    } });
  await f.run();
  assert.ok(f.events.some(e => e.state === 'awaiting_restart'));
  assert.ok(!f.events.some(e => e.state === 'resolved'));
  const released = f.calls.find(x => x.body?.action === 'release');
  assert.match(released.body.input.reason, /host restart/);
  await f.manager.dispose();
});

test('transient poll errors are recognised and never pinned as a standing failure', async () => {
  const mod = await moduleReady;
  assert.equal(typeof mod.isTransientPollError, 'function');
  const socket = Object.assign(new Error('fetch failed'), {
    name: 'TypeError',
    cause: Object.assign(new Error('other side closed'), { name: 'SocketError', code: 'UND_ERR_SOCKET' }),
  });
  assert.equal(mod.isTransientPollError(socket), true);
  assert.equal(mod.isTransientPollError(Object.assign(new Error('aborted'), { code: 'ABORT_ERR' })), true);
  assert.equal(mod.isTransientPollError(Object.assign(new Error('boom'), { code: 'INVALID_INCIDENT_LIST' })), false);

  // A dropped socket is retried; a later successful scan clears the banner.
  const { createOperationsAgentManager } = mod;
  const scripts = [
    () => { throw socket; },
    () => ({ incidents: [] }),
  ];
  let call = 0;
  const manager = createOperationsAgentManager({ effect: () => {}, agents: { create() {}, resume() {} } }, {
    backendRequest: async () => {
      const script = scripts[Math.min(call++, scripts.length - 1)];
      return script();
    },
    resolveRoute: () => ({ provider: 'p', model: 'm' }),
    cwd: '/tmp/ops-prompt',
    enabled: () => true,
    hasWork: () => ({ has_work: true, batches: [] }),
  });
  manager.start();
  await manager.tick();
  assert.equal(call, 1, 'the failing scan actually ran');
  assert.equal(manager.status().lastPollError, '');
  await manager.tick();
  assert.equal(call, 2, 'a later scan recovered');
  assert.equal(manager.status().lastPollError, '');
  await manager.dispose();
});

test('a long-ago non-transient scan error does not stay pinned on the panel', async () => {
  const { createOperationsAgentManager } = await moduleReady;
  let fail = true;
  const manager = createOperationsAgentManager({ effect: () => {}, agents: { create() {}, resume() {} } }, {
    backendRequest: async () => {
      if (fail) { const e = new Error('INVALID_INCIDENT_LIST'); e.code = 'INVALID_INCIDENT_LIST'; throw e; }
      return { incidents: [] };
    },
    resolveRoute: () => ({ provider: 'p', model: 'm' }),
    cwd: '/tmp/ops-prompt',
    enabled: () => true,
    hasWork: () => ({ has_work: true, batches: [] }),
  });
  manager.start();
  await manager.tick();
  const stuck = manager.status().lastPollError;
  assert.match(stuck, /INVALID_INCIDENT_LIST/, 'the failed scan must actually run');
  // Simulate a later successful scan that clears the error.
  fail = false;
  await manager.tick();
  assert.equal(manager.status().lastPollError, '', 'successful scan must clear: ' + stuck);
  await manager.dispose();
});

test('polling loop re-arms itself and a backend-not-ready error never wedges it', async () => {
  const { createOperationsAgentManager } = await moduleReady;
  let readyCalls = 0, listCalls = 0;
  const manager = createOperationsAgentManager({ effect: () => {}, agents: { create() {}, resume() {} } }, {
    backendRequest: async (path) => {
      listCalls++;
      if (path === '/api/operations/incidents') return { incidents: [] };
      return {};
    },
    resolveRoute: () => ({ provider: 'p', model: 'm' }),
    cwd: '/tmp/ops-loop',
    enabled: () => true,
    hasWork: async () => ({ has_work: true, batches: ['b'] }),
    ensureBackendReady: async () => { readyCalls++; if (readyCalls === 1) throw new Error('fetch failed'); },
  });
  manager.start();
  await manager.tick();
  await manager.tick();
  assert.ok(readyCalls >= 2, 'loop keeps retrying readiness: ' + readyCalls);
  assert.equal(manager.status().loopAlive, true);
  assert.equal(listCalls, 1, 'failed readiness does not masquerade as a successful scan');
  await manager.dispose();
});

test('work-state scan timeout releases single flight and stop/start restores the timer', async () => {
  let reads = 0, firstSignal;
  const f = await fixture({ rows: [], managerOptions: {
    intervalMs: 10, requestTimeoutMs: 20,
    hasWork: ({ signal }) => {
      reads++;
      if (reads === 1) { firstSignal = signal; return new Promise(() => {}); }
      return { has_work: false, batches: [] };
    },
  } });
  // Keep the test alive while the production deadline timer is unref'ed.
  const keepAlive = setTimeout(() => {}, 1000);
  try {
    f.manager.start();
    await f.manager.tick();
    assert.equal(firstSignal.aborted, true);
    await f.manager.tick();
    assert.ok(f.manager.status().pollCount > 0, 'scan proceeds after a hung work-state read');
    await f.manager.stop();
    assert.equal(f.manager.status().loopAlive, false);
    const before = reads;
    f.manager.start();
    await f.manager.tick();
    await new Promise(resolve => setTimeout(resolve, 35));
    assert.ok(reads > before + 1, 'timer continues without status reads or manual ticks');
    assert.equal(f.calls.length, 0, 'no incident scans when there is no work');
    assert.equal(f.agents.length, 0);
  } finally { clearTimeout(keepAlive); await f.manager.dispose(); }
});
