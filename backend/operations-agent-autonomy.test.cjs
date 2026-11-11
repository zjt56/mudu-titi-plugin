const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');
const ready = import(pathToFileURL(resolve(__dirname, '../lib/operations-agent.js')));

async function runScenario(mode) {
  const { createOperationsAgentManager } = await ready;
  const calls = [], prompts = [], tools = new Map(), events = [];
  let turns = 0;
  const inc = { incident_id: 'inc', batch_name: 'paper', batch_dir: '/mock/paper', status: 'queued', eligible: true };
  const agent = { model: { select() {} }, inbox: {}, cancel() {}, followup: m => prompts.push(m),
    whenIdle: async () => {
      turns++;
      if (mode === 'prose' && turns === 2) await tools.get('operations_resume').execute({ strategy: 'retry_stage_with_fresh_context', reason: 'verified local repair changed evidence' }, {});
      if (mode === 'queue') await tools.get('operations_finish').execute({ outcome: 'recovery_queued', summary: 'same strategy failed; isolated repair required' }, {});
      if (mode === 'unknown' && turns === 1) await assert.rejects(tools.get('operations_apply_repair').execute({ repair: 'repair_source_options' }, {}));
    } };
  const ctx = { agents: { resume() {}, create: async init => {
    init.setup({ tools: { restrict: () => () => {}, guard: () => () => {}, register: def => { tools.set(def.name, def); return () => {}; } }, systemPrompt: { section: () => () => {} }, on: () => () => {} }, agent);
    return { dispose: async () => {} };
  } } };
  const manager = createOperationsAgentManager(ctx, { cwd: '/mock/plugin', intervalMs: 60000, heartbeatMs: 60000,
    resolveRoute: () => ({ provider: 'explicit', model: 'explicit' }), onProgress: e => events.push(e),
    backendRequest: async (path, init) => {
      calls.push({ path, ...init });
      if (path === '/api/operations/incidents') return { incidents: [inc] };
      if (path.endsWith('/claim')) return { attempt_id: 'attempt', session_id: init.body.session_id };
      const action = init.body?.action;
      if (mode === 'unknown' && action === 'apply_repair') throw new Error('socket closed after dispatch');
      const evidence = action === 'resume' ? { released: true, reclaim_required: true }
        : action === 'finish' ? { outcome: 'recovery_queued', status: 'queued', verified: false } : { ok: true };
      return { status: 'completed', request_id: init.body?.request_id, evidence };
    } });
  manager.start(); await manager.tick(); await manager.drain(); await manager.dispose();
  return { calls, prompts, events, turns };
}

test('prose-only idle turn immediately continues same fenced attempt then resumes execution', async () => {
  const r = await runScenario('prose');
  assert.equal(r.turns, 2);
  assert.equal(r.calls.filter(c => c.path.endsWith('/claim')).length, 1);
  assert.equal(r.calls.filter(c => c.body?.action === 'resume').length, 1);
  assert.equal(r.calls.filter(c => c.body?.action === 'release').length, 0);
  assert.ok(r.events.some(e => e.state === 'awaiting_execution'));
});
test('idle continuation is bounded and never fabricates resolved', async () => {
  const r = await runScenario('idle');
  assert.equal(r.turns, 3);
  assert.equal(r.calls.filter(c => c.body?.action === 'release').length, 1);
  assert.ok(!r.events.some(e => e.state === 'resolved'));
});
test('automatic escalation closes lease without claiming recovery or asking a person', async () => {
  const r = await runScenario('queue');
  assert.equal(r.turns, 1);
  assert.ok(r.events.some(e => e.state === 'recovery_queued'));
  assert.ok(!r.events.some(e => e.state === 'resolved'));
});
test('unknown mutation receipt forbids idle followup and blind repeat', async () => {
  const r = await runScenario('unknown');
  assert.equal(r.turns, 1);
  assert.equal(r.calls.filter(c => c.body?.action === 'apply_repair').length, 1);
  assert.equal(r.calls.filter(c => c.body?.action === 'release').length, 0);
});
