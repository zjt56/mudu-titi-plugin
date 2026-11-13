const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');
const ready = import(pathToFileURL(resolve(__dirname, '../lib/operations-agent.js')));
const busy = () => Object.assign(new Error('worker_busy'), { code: 'worker_busy', admitted: false, retryable: true, retry_after_ms: 10 });

async function scenario(options = {}) {
  const { createOperationsAgentManager } = await ready;
  const calls = [], events = [], tools = new Map(), prompts = [], guards = [];
  const incident = { incident_id: 'inc', batch_name: 'paper', batch_dir: '/mock/paper', status: 'queued', eligible: true };
  const state = { incident: { ...incident }, receipts: [] };
  let turns = 0, cancelCount = 0, claim;
  const agent = { model: { select() {} }, inbox: {}, cancel: () => { cancelCount++; },
    followup: message => prompts.push(message), whenIdle: async () => {
      turns++;
      await options.program?.({ turns, tools, guards, state, call: (name, input = {}) => tools.get(`operations_${name}`).execute(input, {}) });
    } };
  const ctx = { agents: { resume() {}, create: async init => {
    init.setup({ tools: {
      restrict: () => () => {}, guard: guard => { guards.push(guard); return () => {}; },
      register: def => { tools.set(def.name, def); return () => tools.delete(def.name); },
    }, systemPrompt: { section: () => () => {} }, on: () => () => {} }, agent);
    return { dispose: async () => {} };
  } } };
  const manager = createOperationsAgentManager(ctx, {
    cwd: '/mock/plugin', intervalMs: 60000, heartbeatMs: 60000, busyWaitMs: 12, maxIdleContinuations: 0,
    resolveRoute: () => ({ provider: 'explicit', model: 'explicit' }), onProgress: e => events.push(e),
    backendRequest: async (path, init = {}) => {
      calls.push({ path, ...init });
      const custom = await options.backend?.(path, init, state);
      if (custom !== undefined) return custom;
      if (path === '/api/operations/incidents') return { incidents: [incident] };
      if (path.endsWith('/claim')) {
        claim = { ...incident, mode: 'investigation', investigation_ready: options.investigationReady === true, attempt_id: 'read-fence', session_id: init.body.session_id, lease_expires_at: Date.now() / 1000 + 180 };
        Object.assign(state.incident, claim);
        return { ...claim };
      }
      if (init.method === 'GET') return state;
      const { request_id, action, input } = init.body;
      const evidence = action === 'promote' ? { incident: { ...claim, mode: 'repair', attempt_id: 'write-fence' } }
        : action === 'verify' ? { verified: true, evidence_id: 'original-check' }
        : action === 'finish' ? { outcome: input.outcome, verified: true }
        : { ok: true };
      const receipt = { request_id, status: 'completed', evidence };
      state.receipts.push(receipt);
      return receipt;
    }, ...options.managerOptions,
  });
  manager.start(); await manager.tick(); await manager.drain();
  const status = manager.status();
  await manager.dispose();
  return { calls, events, prompts, turns, status, cancelCount };
}

test('early investigation exposes only read/record, then fenced promotion unlocks repair and original verification', async () => {
  let savedReadTool;
  const result = await scenario({ program: async ({ turns, tools, guards, call }) => {
    if (turns === 1) {
      assert.deepEqual([...tools.keys()].sort(), ['operations_diagnose', 'operations_read_evidence', 'operations_record_finding']);
      assert.ok(guards[0]({ name: 'operations_apply_repair' }));
      assert.ok(guards[0]({ name: 'operations_promote' }));
      savedReadTool = tools.get('operations_diagnose');
      await call('diagnose'); await call('read_evidence', { path: 'report.json' });
      await call('record_finding', { summary: 'paper /mock/paper ORIGINAL_CHECK failed; fix parser evidence' });
    } else {
      assert.equal(tools.size, 12);
      assert.equal(guards[0]({ name: 'operations_apply_repair' }), undefined);
      assert.notEqual(tools.get('operations_diagnose'), savedReadTool);
      await call('diagnose'); await call('apply_repair', { repair: 'repair_source_options' });
      await call('verify'); await call('finish', { outcome: 'resolved', summary: 'original-check verified' });
    }
  } });
  assert.equal(result.turns, 2);
  assert.equal(result.calls.find(c => c.path.endsWith('/claim')).body.mode, 'investigation');
  const promotion = result.calls.find(c => c.body?.action === 'promote');
  assert.equal(promotion.body.attempt_id, 'read-fence');
  assert.equal(result.calls.find(c => c.body?.action === 'apply_repair').body.attempt_id, 'write-fence');
  assert.ok(result.events.some(e => e.state === 'investigating' && e.mode === 'investigation'));
  assert.ok(result.events.some(e => e.state === 'waiting_repair' && e.mode === 'investigation'));
  assert.ok(result.events.some(e => e.state === 'repairing' && e.mode === 'repair'));
  assert.ok(result.events.some(e => e.state === 'resolved'));
});

test('prose without recorded evidence never promotes and never becomes repair', async () => {
  const r = await scenario();
  assert.equal(r.turns, 1);
  assert.equal(r.calls.filter(c => c.body?.action === 'promote').length, 0);
  assert.ok(!r.events.some(e => e.state === 'repairing' || e.state === 'resolved'));
});

test('only explicit pre-admission busy permits bounded promotion retry with one id', async () => {
  const r = await scenario({ backend: (_path, init) => { if (init.body?.action === 'promote') throw busy(); },
    program: async ({ call }) => { await call('record_finding', { summary: 'ready to repair' }); } });
  const attempts = r.calls.filter(c => c.body?.action === 'promote');
  assert.ok(attempts.length >= 2);
  assert.equal(new Set(attempts.map(c => c.body.request_id)).size, 1);
  assert.equal(r.turns, 1);
  assert.ok(!r.events.some(e => e.state === 'repairing' || e.state === 'resolved'));
  assert.equal(r.status.attempts[0].state, 'waiting_repair');
  assert.equal(r.calls.find(c => c.body?.action === 'release').body.attempt_id, 'read-fence');
});

test('unknown promotion is queried once and never replayed or followed by repair', async () => {
  const r = await scenario({ backend: (_path, init) => { if (init.body?.action === 'promote') throw new Error('connection lost after promotion'); },
    program: async ({ call }) => { await call('record_finding', { summary: 'ready to repair' }); } });
  assert.equal(r.calls.filter(c => c.body?.action === 'promote').length, 1);
  assert.equal(r.calls.filter(c => c.method === 'GET' && c.path !== '/api/operations/incidents').length, 1);
  assert.equal(r.calls.filter(c => c.body?.action === 'release').length, 0);
  assert.equal(r.turns, 1);
  assert.ok(!r.events.some(e => e.state === 'repairing' || e.state === 'resolved'));
  assert.equal(r.status.attempts[0].state, 'outcome_unknown');
});

test('committed promotion receipt after response loss restores the new fence without POST replay', async () => {
  const r = await scenario({ backend: (_path, init, state) => {
    if (init.body?.action === 'promote') {
      state.receipts.push({ request_id: init.body.request_id, status: 'completed', evidence: { incident: {
        ...state.incident, mode: 'repair', attempt_id: 'receipt-fence',
      } } });
      throw new Error('lost promotion response');
    }
  }, program: async ({ turns, call }) => {
    if (turns === 1) await call('record_finding', { summary: 'ready to repair' });
    else { await call('verify'); await call('finish', { outcome: 'resolved', summary: 'verified' }); }
  } });
  assert.equal(r.calls.filter(c => c.body?.action === 'promote').length, 1);
  assert.equal(r.calls.find(c => c.body?.action === 'verify').body.attempt_id, 'receipt-fence');
  assert.equal(r.turns, 2);
  assert.ok(r.events.some(e => e.state === 'resolved'));
});

test('backend-bound ready finding skips another investigation model turn', async () => {
  const r = await scenario({ investigationReady: true, program: async ({ tools, call }) => {
    assert.equal(tools.size, 12);
    await call('verify'); await call('finish', { outcome: 'resolved', summary: 'original check passed' });
  } });
  assert.equal(r.turns, 1);
  assert.equal(r.calls.filter(c => c.body?.action === 'promote').length, 1);
  assert.equal(r.calls.filter(c => c.body?.action === 'record_finding').length, 0);
  assert.equal(r.calls.find(c => c.body?.action === 'verify').body.attempt_id, 'write-fence');
});

test('inputs_changed releases and reclaims a fresh investigation fence before repair', async () => {
  let claims = 0;
  const r = await scenario({ backend: (_path, init, state) => {
    if (_path.endsWith('/claim')) {
      const attempt_id = claims++ === 0 ? 'read-fence' : 'fresh-read-fence';
      Object.assign(state.incident, { mode: 'investigation', attempt_id, session_id: init.body.session_id });
      return { ...state.incident, mode: 'investigation', investigation_ready: false, attempt_id, session_id: init.body.session_id };
    }
    if (init.body?.action === 'promote' && claims === 1 && !state.promotedOnce) {
      state.promotedOnce = true;
      return { request_id: init.body.request_id, status: 'failed', evidence: { code: 'inputs_changed', error: 'inputs_changed: reclaim fresh snapshot' } };
    }
    if (init.body?.action === 'promote') return { request_id: init.body.request_id, status: 'completed', evidence: { incident: { ...state.incident, mode: 'repair', attempt_id: 'fresh-write-fence' } } };
  }, program: async ({ turns, call }) => {
    if (turns === 1) await call('record_finding', { summary: 'old evidence' });
    else { await call('verify'); await call('finish', { outcome: 'resolved', summary: 'fresh snapshot verified' }); }
  } });
  assert.equal(r.turns, 2);
  assert.equal(r.calls.filter(c => c.path.endsWith('/claim')).length, 2);
  assert.equal(r.calls.filter(c => c.body?.action === 'release').length, 1);
  assert.equal(r.calls.filter(c => c.body?.action === 'promote').length, 2);
  assert.equal(r.calls.find(c => c.body?.action === 'verify').body.attempt_id, 'fresh-write-fence');
  assert.ok(r.events.some(e => e.state === 'resolved'));
});

test('investigation heartbeat failure cancels before any model or promotion', async () => {
  const r = await scenario({ backend: (_path, init) => { if (init.body?.action === 'heartbeat') throw new Error('lease revoked'); } });
  assert.equal(r.turns, 0);
  assert.equal(r.calls.filter(c => c.body?.action === 'promote').length, 0);
  assert.ok(r.events.some(e => e.state === 'lease_uncertain'));
  assert.ok(!r.events.some(e => e.state === 'resolved'));
});

test('failed finding receipt and action budget cannot upgrade investigation', async () => {
  const failed = await scenario({ backend: (_path, init) => init.body?.action === 'record_finding'
    ? { request_id: init.body.request_id, status: 'failed', evidence: { error: 'record failed' } } : undefined,
  program: async ({ call }) => { await assert.rejects(call('record_finding', { summary: 'unpersisted' })); } });
  const budget = await scenario({ managerOptions: { maxActions: 1 }, program: async ({ call }) => {
    await call('record_finding', { summary: 'recorded but no promotion budget' });
  } });
  for (const r of [failed, budget]) {
    assert.equal(r.calls.filter(c => c.body?.action === 'promote').length, 0);
    assert.ok(!r.events.some(e => e.state === 'repairing' || e.state === 'resolved'));
  }
  assert.ok(budget.cancelCount > 0);
});
