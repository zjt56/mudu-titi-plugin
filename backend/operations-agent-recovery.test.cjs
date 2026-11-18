const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');
const ready = import(pathToFileURL(resolve(__dirname, '../lib/operations-agent.js')));

async function fixture(options = {}) {
  const { createOperationsAgentManager } = await ready;
  const calls = [], events = [], prompts = [], tools = new Map();
  const incident = { incident_id: 'recover-incident', batch_name: 'paper', batch_dir: '/mock/paper', status: 'queued', eligible: true };
  const state = { incident: { ...incident }, receipts: [] };
  let created = 0, turns = 0;
  const agent = { model: { select() {} }, inbox: {}, cancel() {},
    followup: message => prompts.push(message), whenIdle: async () => {
      turns++;
      await options.program?.({ tools, prompts, turns, call: (name, input = {}) => tools.get(`operations_${name}`).execute(input, {}) });
    } };
  const create = async init => {
    created++;
    init.setup({ tools: { restrict: () => () => {}, guard: () => () => {},
      register: definition => { tools.set(definition.name, definition); return () => tools.delete(definition.name); } },
      systemPrompt: { section: () => () => {} }, on: () => () => {} }, agent);
    return { dispose: async () => {} };
  };
  const manager = createOperationsAgentManager({ agents: { create, resume: create } }, {
    cwd: '/mock/plugin', intervalMs: 60000, heartbeatMs: 60000, maxIdleContinuations: 0,
    resolveRoute: () => ({ provider: 'explicit', model: 'selected' }), onProgress: e => events.push(e),
    backendRequest: async (path, init = {}) => {
      calls.push({ path, ...init });
      const custom = await options.backend?.(path, init, state);
      if (custom !== undefined) return custom;
      if (path === '/api/operations/incidents') return { incidents: [incident] };
      if (path.endsWith('/claim')) {
        const mode = options.claimMode || 'repair';
        Object.assign(state.incident, { mode, attempt_id: 'repair-fence', session_id: init.body.session_id,
          lease_expires_at: Date.now() / 1000 + 180,
          ...(options.legacy || mode !== 'repair' ? {} : { recovery_ready: true, recovery_protocol: 'operations-recovery/v1' }) });
        return { ...state.incident };
      }
      if (init.method === 'GET') return state;
      const { action, request_id, input } = init.body;
      if (action === 'promote') Object.assign(state.incident, { mode: 'repair', attempt_id: 'promoted-fence' });
      const evidence = action === 'recover' ? options.recovery || { phase: 'code_repair', next_action: 'request_code_repair' }
        : action === 'promote' ? { incident: { ...state.incident } }
        : action === 'resume' ? { released: true, reclaim_required: true }
        : action === 'verify' ? { verified: true, evidence_id: 'original-check' }
        : action === 'finish' ? { outcome: input.outcome, verified: input.outcome === 'resolved' }
        : { ok: true };
      const receipt = { request_id, status: 'completed', evidence };
      state.receipts.push(receipt);
      return receipt;
    },
  });
  manager.start(); await manager.tick(); await manager.drain();
  const status = manager.status();
  await manager.dispose();
  return { calls, events, prompts, created, turns, status };
}

const verified = () => ({ resolved: true, verified: true,
  verification: { verified: true, evidence_id: 'current-original-check' } });

test('verified local recovery completes without creating a model or investigation turn', async () => {
  const r = await fixture({ recovery: verified() });
  const claim = r.calls.find(c => c.path.endsWith('/claim'));
  assert.equal(claim.body.mode, 'repair');
  assert.equal(claim.body.execution_first, true);
  assert.equal(r.created, 0);
  assert.equal(r.turns, 0);
  assert.equal(r.calls.filter(c => c.body?.action === 'recover').length, 1);
  assert.ok(!r.calls.some(c => ['diagnose', 'record_finding', 'promote', 'finish', 'release'].includes(c.body?.action)));
  assert.ok(r.events.some(e => e.state === 'resolved' && e.phase === 'verified_recovery'));
});

test('backend-admitted resume queues the real pipeline without a model or false success', async () => {
  const input = { strategy: 'retry_stage_with_fresh_context' };
  const r = await fixture({ recovery: { resume_required: true, resume_input: input } });
  assert.equal(r.created, 0);
  assert.deepEqual(r.calls.find(c => c.body?.action === 'resume').body.input, input);
  assert.ok(r.events.some(e => e.state === 'awaiting_execution'));
  assert.ok(!r.events.some(e => e.state === 'resolved'));
});

test('backend-settled waiting evidence releases the batch without spending a model turn', async () => {
  const r = await fixture({ recovery: { settled: true, released: true, status: 'waiting_evidence',
    reason: '等待真实输入或代码变化' } });
  assert.equal(r.created, 0);
  assert.equal(r.turns, 0);
  assert.ok(r.events.some(e => e.state === 'waiting_evidence' && e.phase === 'recovery_settled'));
  assert.ok(!r.events.some(e => e.state === 'resolved'));
});

test('unresolved recovery passes evidence into one direct repair turn without mandatory finding', async () => {
  const recovery = { phase: 'code_repair', next_action: 'request_code_repair',
    packet: { stage: 'knowledge', failed_checks: ['CATALOG_SCOPE'], affected_unit_ids: ['q-8'] } };
  const r = await fixture({ recovery, program: async ({ prompts, call }) => {
    const task = JSON.parse(prompts[0].content[0].text);
    assert.equal(task.mode, 'repair');
    assert.deepEqual(task.recovery, recovery);
    await call('request_code_repair', { summary: 'fix current catalog scope' });
    await call('verify');
    await call('finish', { outcome: 'resolved', summary: 'current original check passed' });
  } });
  assert.equal(r.created, 1);
  assert.equal(r.turns, 1);
  assert.ok(!r.calls.some(c => ['record_finding', 'diagnose', 'promote'].includes(c.body?.action)));
  assert.ok(r.events.some(e => e.state === 'resolved'));
});

test('claimed resolved recovery without original check evidence is rejected', async () => {
  for (const recovery of [{ resolved: true }, { resolved: true, verified: true },
    { resolved: true, verified: true, verification: { verified: false, evidence_id: 'wrong' } }]) {
    const r = await fixture({ recovery });
    assert.equal(r.created, 0);
    assert.ok(!r.events.some(e => e.state === 'resolved'));
    assert.ok(r.calls.some(c => c.body?.action === 'release'));
  }
});

test('resume requires a backend-supplied input and a definite pipeline handoff', async () => {
  const missing = await fixture({ recovery: { resume_required: true } });
  assert.equal(missing.created, 0);
  assert.ok(!missing.calls.some(c => c.body?.action === 'resume'));
  const unconfirmed = await fixture({ recovery: { resume_required: true, resume_input: {} },
    backend: (_path, init) => init.body?.action === 'resume'
      ? { request_id: init.body.request_id, status: 'completed', evidence: { queued: true } } : undefined });
  assert.equal(unconfirmed.created, 0);
  assert.ok(!unconfirmed.events.some(e => e.state === 'resolved' || e.state === 'awaiting_execution'));
});

test('lost local recovery response is reconciled from its exact receipt without replay', async () => {
  const r = await fixture({ backend: (_path, init, state) => {
    if (init.body?.action === 'recover') {
      state.receipts.push({ request_id: init.body.request_id, status: 'completed', evidence: verified() });
      throw new Error('response lost after recovery');
    }
  } });
  assert.equal(r.created, 0);
  assert.equal(r.calls.filter(c => c.body?.action === 'recover').length, 1);
  assert.equal(r.calls.filter(c => c.method === 'GET' && c.path !== '/api/operations/incidents').length, 1);
  assert.ok(r.events.some(e => e.state === 'resolved'));
});

test('unknown local recovery never starts a model, releases for replay, or invents success', async () => {
  const r = await fixture({ backend: (_path, init) => {
    if (init.body?.action === 'recover') throw new Error('recovery result unknown');
  } });
  assert.equal(r.created, 0);
  assert.equal(r.calls.filter(c => c.body?.action === 'recover').length, 1);
  assert.ok(!r.calls.some(c => c.body?.action === 'release'));
  assert.ok(!r.events.some(e => e.state === 'resolved'));
  assert.ok(r.events.some(e => e.state === 'outcome_unknown'));
});

test('legacy backend without recovery capability retains its ordinary repair tools', async () => {
  const r = await fixture({ backend: (path, init) => path.endsWith('/claim')
    ? { mode: 'repair', attempt_id: 'legacy-fence', session_id: init.body.session_id } : undefined,
  program: async ({ call }) => { await call('verify'); await call('finish', { outcome: 'resolved', summary: 'verified legacy repair' }); } });
  assert.equal(r.created, 1);
  assert.ok(!r.calls.some(c => c.body?.action === 'recover'));
  assert.ok(r.events.some(e => e.state === 'resolved'));
});

test('older backend investigation response retains its read-only then promoted compatibility path', async () => {
  const r = await fixture({ legacy: true, claimMode: 'investigation',
    program: async ({ turns, call }) => {
      if (turns === 1) await call('record_finding', { summary: 'paper /mock/paper failed catalog check; repair required' });
      else { await call('verify'); await call('finish', { outcome: 'resolved', summary: 'original check verified' }); }
    } });
  assert.equal(r.created, 1);
  assert.equal(r.turns, 2);
  assert.ok(!r.calls.some(c => c.body?.action === 'recover'));
  assert.equal(r.calls.find(c => c.path.endsWith('/claim')).body.mode, 'repair');
  assert.ok(r.calls.some(c => c.body?.action === 'promote'));
  assert.ok(r.events.some(e => e.state === 'resolved'));
});
