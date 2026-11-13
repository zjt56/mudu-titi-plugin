const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve, join } = require('node:path');
const fs = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { zstdCompressSync } = require('node:zlib');
const ready = import(pathToFileURL(resolve(__dirname, '../lib/content-agent.js')));
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes; }); return { promise, resolve }; };
const task = extra => ({ task_id: 'task/一', stage: 'knowledge', prompt: 'Match this unit to current knowledge catalog leaves.', ...extra });

async function fixture(options = {}) {
  const { createContentAgentManager, CONTENT_TOOL_NAMES } = await ready;
  const rows = options.rows || [task()];
  const calls = [], agents = [], events = [], effects = [];
  const ctx = { agents: {}, sessions: { get: id => options.liveSession?.(id) }, sessionQuery: options.sessionQuery,
    sessionPersistence: options.persistence, effect: fn => effects.push(fn()) };
  async function build(init, mode) {
    if (options.createHook) await options.createHook(init);
    const tools = new Map(), listeners = new Map(), restrictions = [], guards = [], sections = [];
    const cancelled = deferred();
    let work = Promise.resolve(), disposed = false, cancelCount = 0;
    const record = { init, mode, tools, restrictions, guards, sections, cancelled, turns: 0, concludes: 0,
      emit: (name, ...args) => listeners.get(name)?.(...args),
      call: (action, args = {}, exec = {}) => tools.get(`content_${action}`).execute(args, { concludeTurn: () => { record.concludes++; }, ...exec }),
      get disposed() { return disposed; }, get cancelCount() { return cancelCount; } };
    const schedule = () => {
      work = Promise.resolve().then(async () => {
        record.emit('session/event', {}, { type: 'turn/start' }); record.turns++;
        try { await options.program?.(record); }
        catch (error) { record.emit('agent/error', { error }); }
        record.emit('session/event', {}, { type: 'turn/end', data: { reason: { kind: 'completed' } } });
      });
    };
    const agent = {
      model: { select: route => { agent.route = route; } },
      status: 'idle',
      inbox: { nextTurn: options.pending ? [{ id: 'saved-message', role: 'user', content: [{ type: 'text', text: 'saved prompt' }] }] : [], nextStep: [],
        remove: id => { const index = agent.inbox.nextTurn.findIndex(message => message.id === id); if (index < 0) return false; agent.inbox.nextTurn.splice(index, 1); return true; } },
      messages: [], resumedMessages: [],
      send: (message, target, wakeup) => { agent.inbox[target === 'next-turn' ? 'nextTurn' : 'nextStep'].push(message); agent.resumedMessages.push(message); if (wakeup) schedule(); },
      followup: message => {
        assert.equal(message.role, 'user');
        assert.match(message.id, /^[0-9a-f-]{36}$/i);
        agent.messages.push(message); schedule();
      },
      whenIdle: () => options.hangIdle ? new Promise(() => {}) : work,
      cancel: () => { cancelCount++; cancelled.resolve(); },
    };
    record.agent = agent;
    const scoped = {
      tools: {
        restrict: filter => { restrictions.push(filter); return () => {}; },
        guard: fn => { guards.push(fn); return () => {}; },
        register: definition => { tools.set(definition.name, definition); return () => tools.delete(definition.name); },
      },
      systemPrompt: { section: value => { sections.push(value); return () => {}; } },
      on: (name, fn) => { listeners.set(name, fn); return () => listeners.delete(name); },
    };
    init.setup(scoped, agent);
    agents.push(record);
    return { agent, dispose: async () => { disposed = true; record.emit('agent/disposed'); } };
  }
  ctx.agents.create = init => build(init, 'create');
  ctx.agents.resume = init => build(init, 'resume');
  ctx.agents.get = id => agents.find(record => !record.disposed && (record.init.sessionId || record.init.resumeSessionId) === id)?.agent;
  if (options.unavailable) ctx.agents = {};
  const request = async (path, init = {}) => {
    calls.push({ path, ...init });
    const custom = await options.backend?.(path, init);
    if (custom !== undefined) return custom;
    if (path === '/api/content-agent/tasks') return { tasks: rows };
    if (path.endsWith('/claim')) {
      const found = rows.find(row => path.includes(encodeURIComponent(row.task_id)));
      return { task: { ...found, session_id: init.body.session_id } };
    }
    if (path.endsWith('/tools')) return init.body.action === 'submit_candidate' ? { accepted: true } : { items: [] };
    return { ok: true };
  };
  const manager = createContentAgentManager({ ctx, cwd: '/mock/plugin', request,
    resolveRoute: options.resolveRoute || (row => row.route || { provider: 'chosen', model: 'chosen/model' }),
    intervalMs: 60000, requestTimeoutMs: 1000, onProgress: event => events.push(event), ...options.managerOptions });
  return { manager, rows, calls, agents, events, effects, CONTENT_TOOL_NAMES,
    run: async () => { manager.start(); await manager.tick(); await manager.drain(); } };
}
const states = f => f.calls.filter(call => call.path.endsWith('/state')).map(call => call.body.status);

test('native tools use parameters, no builtins, scoped backend actions and strict accepted completion', async () => {
  const candidate = { task_id: 'task/一', items: [{ unit_id: 'u1', knowledge: [] }] };
  const f = await fixture({ program: async a => {
    assert.deepEqual(a.restrictions, [{ allow: [] }]);
    assert.equal(a.guards[0]({ name: 'bash' }), 'Only task-scoped content tools are allowed');
    assert.equal(a.guards[0]({ name: 'arbitrary_plugin_tool' }), 'Only task-scoped content tools are allowed');
    assert.equal(a.guards[0]({ name: 'content_read_unit' }), undefined);
    assert.equal(a.tools.size, 7);
    for (const definition of a.tools.values()) {
      assert.equal(definition.parameters.type, 'object'); assert.equal(definition.parameters.additionalProperties, false);
      assert.equal(definition.input, undefined); assert.equal(definition.isConcurrencySafe(), false);
    }
    await a.call('read_unit', { unit_id: 'u1' });
    await a.call('search_catalog', { query: 'tense', offset: 0, limit: 5 });
    await a.call('read_catalog', {});
    await a.call('read_source', { path: 'source.txt', start_line: 1, end_line: 5 });
    await a.call('list_media', {});
    await a.call('inspect_media', { media_id: 'source-image-1', question: 'Read the printed section heading.' });
    await a.call('submit_candidate', { candidate });
  } });
  await f.run();
  assert.deepEqual(states(f), ['running', 'completed']);
  assert.equal(f.agents[0].init.meta.cwd, '/mock/plugin');
  assert.deepEqual(f.agents[0].agent.route, { provider: 'chosen', model: 'chosen/model' });
  assert.equal(f.agents[0].concludes, 1); assert.equal(f.agents[0].disposed, true);
  assert.equal(f.agents[0].tools.size, 0);
  const submission = f.calls.find(call => call.body?.action === 'submit_candidate');
  assert.deepEqual(submission.body.args, { candidate });
  assert.match(submission.path, /task%2F%E4%B8%80\/tools$/);
  assert.ok(f.calls.find(call => call.path.endsWith('/claim')).body.session_id.startsWith('content-'));
  await f.manager.dispose();
});

test('media inspection may exceed the ordinary HTTP timeout and still submit an accepted candidate', async () => {
  let mediaSignal;
  const f = await fixture({ managerOptions: { requestTimeoutMs: 10, mediaRequestTimeoutMs: 200 },
    backend: async (_path, init) => {
      if (init.body?.action !== 'inspect_media') return;
      mediaSignal = init.signal;
      await new Promise(resolve => setTimeout(resolve, 35));
      return { evidence: 'Printed heading from the source image' };
    }, program: async a => {
      assert.deepEqual(await a.call('inspect_media', { media_id: 'image-1', question: 'Read the printed heading' }),
        { evidence: 'Printed heading from the source image' });
      await a.call('submit_candidate', { candidate: {} });
    } });
  await f.run();
  assert.equal(mediaSignal.aborted, false);
  assert.deepEqual(states(f), ['running', 'completed']);
  assert.equal(f.manager.status().tasks[0].status, 'completed');
  await f.manager.dispose();
});

test('ordinary requests retain their timeout and unknown tool POSTs are never replayed', async () => {
  let toolSignal;
  const f = await fixture({ managerOptions: { requestTimeoutMs: 10, mediaRequestTimeoutMs: 200 },
    backend: async (_path, init) => {
      if (init.body?.action !== 'read_unit') return;
      toolSignal = init.signal;
      return new Promise(() => {});
    }, program: a => a.call('read_unit', { unit_id: 'u1' }) });
  await f.run(); await f.manager.tick(); await f.manager.drain();
  assert.equal(toolSignal.aborted, true);
  assert.match(toolSignal.reason.message, /backend request timed out/);
  assert.equal(f.calls.filter(call => call.body?.action === 'read_unit').length, 1);
  assert.equal(f.manager.status().tasks[0].status, 'outcome_unknown');
  assert.deepEqual(states(f), ['running']);
  await f.manager.dispose();
});

test('media inspection remains bounded by task deadline and explicit cancellation', async () => {
  for (const stop of [false, true]) {
    const entered = deferred(); let mediaSignal;
    const f = await fixture({ rows: [task({ budgets: { max_attempt_ms: 35 } })],
      managerOptions: { requestTimeoutMs: 10, mediaRequestTimeoutMs: 200 },
      backend: async (_path, init) => {
        if (init.body?.action !== 'inspect_media') return;
        mediaSignal = init.signal; entered.resolve();
        return new Promise(() => {});
      }, program: a => a.call('inspect_media', { media_id: 'image-1', question: 'Read the heading' }) });
    f.manager.start(); await f.manager.tick(); await entered.promise;
    if (stop) await f.manager.stop();
    await f.manager.drain();
    assert.equal(mediaSignal.aborted, true);
    assert.match(mediaSignal.reason.message, stop ? /stopped/ : /time budget exceeded/);
    assert.equal(f.calls.filter(call => call.body?.action === 'inspect_media').length, 1);
    assert.equal(f.calls.some(call => call.body?.action === 'submit_candidate'), false);
    assert.equal(f.manager.status().tasks[0].status, stop ? 'outcome_unknown' : 'reconcile_exhausted');
    assert.deepEqual(states(f), ['running']);
    assert.equal(f.agents[0].disposed, true);
    await f.manager.dispose();
  }
});

test('retryable validator feedback reaches the tool and automatic continuation', async () => {
  let submits = 0;
  const feedback = { accepted: false, retryable: true, errors: [{ code: 'MISSING_EVIDENCE', unit_id: 'u1' }] };
  const f = await fixture({ backend: (_path, init) => init.body?.action === 'submit_candidate' ? ++submits === 1 ? feedback : { accepted: true } : undefined,
    program: async a => {
      const result = await a.call('submit_candidate', { candidate: { attempt: a.turns } });
      if (a.turns === 1) assert.deepEqual(result, feedback);
    } });
  await f.run();
  assert.equal(f.agents[0].turns, 2);
  assert.match(f.agents[0].agent.messages[1].content[0].text, /MISSING_EVIDENCE/);
  assert.deepEqual(states(f), ['running', 'completed']);
  await f.manager.dispose();
});

test('nonretryable validator rejection concludes failed without more model turns', async () => {
  const f = await fixture({ backend: (_path, init) => init.body?.action === 'submit_candidate'
    ? { accepted: false, retryable: false, failure_class: 'source_binding', errors: ['bad source'] } : undefined,
    program: a => a.call('submit_candidate', { candidate: {} }) });
  await f.run(); assert.deepEqual(states(f), ['running', 'failed']);
  assert.equal(f.agents[0].turns, 1); assert.equal(f.agents[0].concludes, 1);
  assert.match(f.calls.find(call => call.body?.status === 'failed').body.error, /source_binding/);
  await f.manager.dispose();
});

test('truthy accepted values and idle prose cannot become success', async () => {
  for (const accepted of [undefined, 'true', 1, false]) {
    const f = await fixture({ managerOptions: { maxTurns: 2 },
      backend: (_path, init) => init.body?.action === 'submit_candidate' ? { accepted } : undefined,
      program: accepted === undefined ? undefined : a => a.call('submit_candidate', { candidate: {} }) });
    await f.run(); assert.deepEqual(states(f), ['running', 'failed']); assert.equal(f.agents[0].turns, 2);
    assert.match(f.calls.at(-1).body.error, /without an accepted candidate/); await f.manager.dispose();
  }
});

test('persisted identity resumes and pending inbox is not duplicated', async () => {
  const stat = [];
  const f = await fixture({ rows: [task({ session_id: 'saved-session' })], pending: true,
    persistence: { stat: async (...args) => { stat.push(args); return {}; } },
    program: a => a.call('submit_candidate', { candidate: {} }) });
  await f.run(); assert.equal(f.agents[0].mode, 'resume');
  assert.equal(f.agents[0].init.resumeSessionId, 'saved-session');
  assert.equal(f.agents[0].agent.messages.length, 0); assert.equal(stat[0][0], 'saved-session');
  assert.equal(f.agents[0].agent.resumedMessages.length, 1);
  assert.equal(f.agents[0].agent.resumedMessages[0].id, 'saved-message');
  assert.equal(f.agents[0].agent.inbox.nextTurn.length, 1);
  assert.deepEqual(states(f), ['running', 'completed']); await f.manager.dispose();
});

test('missing persisted session creates the same claimed identity', async () => {
  const f = await fixture({ rows: [task({ session_id: 'saved-missing' })], persistence: { stat: async () => null },
    program: a => a.call('submit_candidate', { candidate: {} }) });
  await f.run(); assert.equal(f.agents[0].mode, 'create'); assert.equal(f.agents[0].init.sessionId, 'saved-missing');
  await f.manager.dispose();
});

test('persistence errors do not mint replacement identities or call a model', async () => {
  const f = await fixture({ rows: [task({ session_id: 'saved' })], persistence: { stat: async () => { throw new Error('history corrupt'); } } });
  await f.run(); assert.equal(f.agents.length, 0); assert.deepEqual(states(f), ['running', 'failed']);
  assert.equal(f.calls.filter(call => call.path.endsWith('/claim')).length, 1); await f.manager.dispose();
});

test('claim failure or mismatched identity never creates a session or sends lifecycle writes', async () => {
  for (const mismatch of [false, true]) {
    const f = await fixture({ backend: path => {
      if (path.endsWith('/claim')) { if (mismatch) return { task: task({ session_id: 'other' }) }; throw new Error('claim response lost'); }
    } });
    await f.run(); await f.manager.tick(); await f.manager.drain();
    assert.equal(f.agents.length, 0); assert.equal(f.calls.filter(call => call.path.endsWith('/claim')).length, 1);
    assert.deepEqual(states(f), []); assert.equal(f.manager.status().tasks[0].status, 'outcome_unknown'); await f.manager.dispose();
  }
});

test('default global concurrency is two and repeated polls never duplicate tasks', async () => {
  const gate = deferred(), entered = deferred(); let starts = 0;
  const f = await fixture({ rows: [task(), task({ task_id: 'two' }), task({ task_id: 'three' })],
    program: async a => { if (++starts === 2) entered.resolve(); await gate.promise; await a.call('submit_candidate', { candidate: {} }); } });
  f.manager.start(); await entered.promise;
  await Promise.all([f.manager.tick(), f.manager.tick(), f.manager.tick()]);
  assert.equal(f.agents.length, 2); assert.equal(f.manager.status().activeCount, 2);
  gate.resolve(); await f.manager.drain(); await f.manager.tick(); await f.manager.drain();
  assert.equal(f.agents.length, 3); assert.equal(f.calls.filter(call => call.path.endsWith('/claim')).length, 3);
  await f.manager.dispose();
});

test('task disappearance, cancellation and ownership change cancel pending native work', async () => {
  for (const scenario of ['missing', 'cancelled', 'owner']) {
    const entered = deferred();
    const f = await fixture({ program: async a => { entered.resolve(); await a.cancelled.promise; } });
    f.manager.start(); await entered.promise;
    if (scenario === 'missing') f.rows.splice(0);
    else Object.assign(f.rows[0], scenario === 'owner' ? { session_id: 'another-session' } : { status: 'cancelled' });
    await f.manager.tick(); await f.manager.drain();
    assert.deepEqual(states(f), ['running', 'cancelled']); assert.equal(f.agents[0].disposed, true);
    await f.manager.dispose();
  }
});

test('stop cancels hanging idle and ignores late acceptance', async () => {
  const entered = deferred();
  const f = await fixture({ hangIdle: true, program: async a => { entered.resolve(); await a.cancelled.promise;
    await assert.rejects(a.call('submit_candidate', { candidate: {} }), { code: 'TASK_CLOSED' }); } });
  f.manager.start(); await entered.promise; await f.manager.stop();
  assert.deepEqual(states(f), ['running', 'cancelled']); assert.equal(f.manager.status().activeCount, 0);
  assert.equal(f.calls.filter(call => call.body?.action === 'submit_candidate').length, 0);
  assert.equal(f.agents[0].disposed, true); await f.manager.dispose();
});

test('time budget bounds a host that never resolves whenIdle', async () => {
  const f = await fixture({ hangIdle: true, managerOptions: { maxAttemptMs: 20 } });
  await f.run(); assert.deepEqual(states(f), ['running', 'failed']);
  assert.match(f.calls.at(-1).body.error, /time budget/); await f.manager.dispose();
});

test('tool budget blocks the excess call and reports failure', async () => {
  const f = await fixture({ managerOptions: { maxToolCalls: 1 }, program: async a => {
    await a.call('read_unit', { unit_id: 'u1' });
    await assert.rejects(a.call('submit_candidate', { candidate: {} }), { code: 'BUDGET_EXCEEDED' });
  } });
  await f.run(); assert.deepEqual(states(f), ['running', 'failed']);
  assert.equal(f.calls.filter(call => call.path.endsWith('/tools')).length, 1); await f.manager.dispose();
});

test('native spontaneous turns cannot exceed the model turn budget', async () => {
  const f = await fixture({ managerOptions: { maxTurns: 1 }, program: async a => {
    a.emit('session/event', {}, { type: 'turn/start' });
    await assert.rejects(a.call('submit_candidate', { candidate: {} }), { code: 'TASK_CLOSED' });
  } });
  await f.run(); assert.deepEqual(states(f), ['running', 'failed']); await f.manager.dispose();
});

test('tool input validation rejects arbitrary keys and invalid shapes before backend dispatch', async () => {
  const f = await fixture({ program: async a => {
    for (const [action, args] of [['read_unit', {}], ['read_source', { path: 'x', shell: 'cat' }],
      ['read_catalog', { offset: -1 }], ['search_catalog', { query: 'x', limit: 1.5 }],
      ['read_source', { path: 'x', start_line: 5, end_line: 2 }], ['submit_candidate', { candidate: [] }]]) {
      await assert.rejects(a.call(action, args), { code: 'INVALID_ARGUMENTS' });
    }
    await a.call('submit_candidate', { candidate: {} });
  } });
  await f.run(); assert.equal(f.calls.filter(call => call.path.endsWith('/tools')).length, 1);
  assert.deepEqual(states(f), ['running', 'completed']); await f.manager.dispose();
});

test('uncertain candidate submission is not replayed on later model attempts or polls', async () => {
  const f = await fixture({ backend: (_path, init) => { if (init.body?.action === 'submit_candidate') throw new Error('response lost after validator'); },
    program: async a => {
      await assert.rejects(a.call('submit_candidate', { candidate: {} }), /response lost/);
      await assert.rejects(a.call('submit_candidate', { candidate: {} }), { code: 'TASK_CLOSED' });
    } });
  await f.run(); await f.manager.tick(); await f.manager.drain();
  assert.equal(f.calls.filter(call => call.body?.action === 'submit_candidate').length, 1);
  assert.deepEqual(states(f), ['running']);
  assert.equal(f.manager.status().tasks[0].status, 'outcome_unknown'); await f.manager.dispose();
});

test('uncertain committed submission reconciles acceptance without overwriting backend state', async () => {
  let committed = false;
  const f = await fixture({ backend: (path, init) => {
    if (path === '/api/content-agent/tasks' && committed) return { tasks: [] };
    if (path === '/api/content-agent/tasks/' + encodeURIComponent(task().task_id) && committed) return { task: task({ session_id: 'bound', accepted: true, status: 'completed' }) };
    if (init.body?.action === 'submit_candidate') { committed = true; throw new Error('committed but response lost'); }
  }, managerOptions: { makeSessionId: () => 'bound' }, program: a => a.call('submit_candidate', { candidate: {} }) });
  await f.run(); assert.deepEqual(states(f), ['running']);
  assert.equal(f.manager.status().tasks[0].status, 'completed'); await f.manager.dispose();
});

test('committed claim and submit recover after consecutive failed receipt GETs with one side effect', async () => {
  for (const phase of ['claim', 'malformed', 'submit']) {
    let claims = 0, submits = 0, reads = 0, committed = false;
    const f = await fixture({ managerOptions: { makeSessionId: () => 'exact-session', intervalMs: 1 },
      backend: (path, init) => {
        if (path.endsWith('/claim')) {
          claims++;
          if (phase === 'malformed') { committed = true; return {}; }
          if (phase === 'claim') { committed = true; throw new Error('claim persisted, response lost'); }
        }
        if (init.body?.action === 'submit_candidate') {
          submits++;
          if (phase === 'submit') { committed = true; throw new Error('candidate persisted, response lost'); }
        }
        if (path === '/api/content-agent/tasks/' + encodeURIComponent(task().task_id) && committed) {
          if (++reads <= 3) throw new Error('temporary receipt outage');
          return { task: task({ session_id: 'exact-session', status: phase !== 'submit' ? 'claimed' : 'completed', accepted: phase === 'submit' }) };
        }
      }, program: a => a.call('submit_candidate', { candidate: {} }) });
    await f.run();
    for (let i = 0; i < 5; i++) { await new Promise(resolve => setTimeout(resolve, 10)); await f.manager.tick(); await f.manager.drain(); }
    assert.equal(claims, 1); assert.equal(submits, 1, JSON.stringify({ phase, status: f.manager.status(), events: f.events }));
    assert.equal(f.agents.length, 1); assert.equal(f.agents[0].init.sessionId, 'exact-session');
    assert.equal(f.manager.status().tasks[0].status, 'completed');
    assert.equal(f.calls.some(call => call.body?.status === 'failed'), false);
    await f.manager.dispose();
  }
});

test('unknown claim reconciliation is finite and cannot revive stopped, deleted, cancelled or foreign tasks', async () => {
  for (const scenario of ['finite', 'stop', 'deleted', 'cancelled', 'foreign', 'queued', 'deadline']) {
    let reads = 0;
    const f = await fixture({ managerOptions: { makeSessionId: () => 'exact-session', ...(scenario === 'deadline' ? { maxAttemptMs: 15 } : {}) },
      backend: path => {
        if (path.endsWith('/claim')) throw new Error('claim response lost');
        if (path === '/api/content-agent/tasks/' + encodeURIComponent(task().task_id)) {
          reads++;
          if (scenario === 'deleted') return { code: 'not_found' };
          if (scenario === 'cancelled') return { task: task({ session_id: 'exact-session', status: 'cancelled', accepted: true }) };
          if (scenario === 'queued') return { task: task({ session_id: 'exact-session', status: 'queued' }) };
          if (scenario === 'foreign') return { task: task({ session_id: 'foreign', status: 'claimed', accepted: true }) };
          throw new Error('receipt unavailable');
        }
      } });
    await f.run();
    if (scenario === 'stop') { await f.manager.stop(); f.manager.start(); await f.manager.tick(); }
    if (scenario === 'deadline') await new Promise(resolve => setTimeout(resolve, 25));
    for (let i = 0; i < 12; i++) await f.manager.tick();
    await f.manager.drain();
    assert.equal(f.agents.length, 0); assert.equal(f.calls.filter(call => call.path.endsWith('/claim')).length, 1);
    assert.ok(reads <= 8);
    if (scenario === 'deleted' || scenario === 'cancelled') assert.equal(reads, 1);
    if (scenario === 'stop' || scenario === 'deadline') assert.equal(reads, 0);
    if (scenario === 'deadline') assert.equal(f.manager.status().tasks[0].status, 'reconcile_exhausted');
    assert.notEqual(f.manager.status().tasks[0].status, 'completed');
    await f.manager.dispose();
  }
});

test('semantic state rejection in HTTP 200 stops before creating a model', async () => {
  const f = await fixture({ backend: path => path.endsWith('/state') ? { error: 'owner invalid', accepted: false, retryable: false } : undefined });
  await f.run(); assert.equal(f.agents.length, 0);
  assert.equal(f.manager.status().tasks[0].status, 'state_unknown'); await f.manager.dispose();
});

test('prompt supports source-bound text and types without generating absent originals', async () => {
  const { CONTENT_PROMPT } = await ready;
  assert.match(CONTENT_PROMPT, /answers, explanations and article summaries verbatim/);
  assert.match(CONTENT_PROMPT, /classify question types/);
  assert.match(CONTENT_PROMPT, /Never invent missing/);
});

test('state report failure does not falsely confirm done in local status', async () => {
  const f = await fixture({ backend: (path, init) => { if (path.endsWith('/state') && init.body.status === 'completed') throw new Error('state response lost'); },
    program: a => a.call('submit_candidate', { candidate: {} }) });
  await f.run(); assert.equal(f.manager.status().tasks[0].status, 'state_unknown');
  assert.match(f.manager.status().tasks[0].stateError, /state response lost/); await f.manager.dispose();
});

test('invalid route fails without silently selecting another provider', async () => {
  const f = await fixture({ resolveRoute: () => ({ provider: 'chosen' }) });
  await f.run(); assert.equal(f.agents.length, 0); assert.deepEqual(states(f), ['failed']); await f.manager.dispose();
});

test('poll timeout is bounded even if backend ignores abort and can recover', async () => {
  let hang = true;
  const f = await fixture({ managerOptions: { requestTimeoutMs: 15 },
    backend: path => path === '/api/content-agent/tasks' && hang ? new Promise(() => {}) : undefined,
    program: a => a.call('submit_candidate', { candidate: {} }) });
  f.manager.start(); await f.manager.tick(); assert.match(f.manager.status().lastPollError, /timed out/);
  hang = false; await f.manager.tick(); await f.manager.drain(); assert.equal(f.manager.status().lastPollError, '');
  assert.deepEqual(states(f), ['running', 'completed']); await f.manager.dispose();
});

test('task route is authoritative, including profile, and invalid explicit routes never fall back', async () => {
  const route = { provider: 'bound', model: 'bound/model', profile: 'bound-profile' };
  const f = await fixture({ rows: [task({ route })], resolveRoute: () => { throw new Error('must use bound route'); },
    program: a => a.call('submit_candidate', { candidate: {} }) });
  await f.run(); assert.deepEqual(f.agents[0].agent.route, route); await f.manager.dispose();
  const bad = await fixture({ rows: [task({ route: { provider: 'incomplete' } })] });
  await bad.run(); assert.equal(bad.agents.length, 0); assert.deepEqual(states(bad), ['failed']); await bad.manager.dispose();
});

test('task budgets lower manager caps but cannot raise them', async () => {
  const f = await fixture({ rows: [task({ budgets: { max_turns: 1, max_tool_calls: 1, max_attempt_ms: 2000 } })],
    program: async a => { await a.call('read_catalog'); await assert.rejects(a.call('submit_candidate', { candidate: {} }), { code: 'BUDGET_EXCEEDED' }); } });
  await f.run(); assert.equal(f.agents[0].turns, 1); assert.deepEqual(states(f), ['running', 'failed']); await f.manager.dispose();
  const higher = await fixture({ rows: [task({ budgets: { max_turns: 999 } })], managerOptions: { maxTurns: 1 } });
  await higher.run(); assert.equal(higher.agents[0].turns, 1); await higher.manager.dispose();
  const malformed = await fixture({ rows: [task({ budgets: { max_turns: 0 } })] });
  await malformed.run(); assert.equal(malformed.agents.length, 0); assert.deepEqual(states(malformed), ['failed']); await malformed.manager.dispose();
});

test('explicit task time budget survives ten minutes and expires at its bound deadline', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000000 });
  const entered = deferred();
  const f = await fixture({ rows: [task({ budgets: { max_attempt_ms: 1500000 } })],
    hangIdle: true, program: () => entered.resolve() });
  t.after(() => f.manager.dispose());
  f.manager.start(); await f.manager.tick(); await entered.promise;
  assert.equal(f.manager.status().tasks[0].attemptMs, 1500000);
  assert.equal(f.manager.status().tasks[0].maxAttemptMs, 3600000);
  t.mock.timers.tick(600000);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(states(f), ['running']);
  assert.equal(f.agents[0].cancelCount, 0);
  t.mock.timers.tick(899999);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.agents[0].cancelCount, 0);
  t.mock.timers.tick(1); await f.manager.drain();
  assert.deepEqual(states(f), ['running', 'failed']);
  assert.match(f.calls.at(-1).body.error, /time budget exceeded/);
  assert.equal(f.calls.filter(call => call.path.endsWith('/claim')).length, 1);
});

test('task time budget preserves legacy fallback, host hard cap and backend deadline', async t => {
  for (const scenario of [
    { name: 'legacy fallback', extra: {}, expected: 600000 },
    { name: 'short task', extra: { budgets: { max_attempt_ms: 120000 } }, expected: 120000 },
    { name: 'absolute host cap', extra: { budgets: { max_attempt_ms: Number.MAX_SAFE_INTEGER } }, expected: 3600000 },
    { name: 'explicit lower host cap', extra: { budgets: { max_attempt_ms: 1500000 } }, cap: 700000, expected: 700000 },
    { name: 'backend deadline', extra: { budgets: { max_attempt_ms: 1500000 }, deadline: 1300 }, expected: 300000 },
  ]) await t.test(scenario.name, async sub => {
    sub.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000000 });
    const entered = deferred();
    const f = await fixture({ rows: [task(scenario.extra)], hangIdle: true,
      managerOptions: scenario.cap ? { maxAttemptMs: scenario.cap } : {}, program: () => entered.resolve() });
    sub.after(() => f.manager.dispose());
    f.manager.start(); await f.manager.tick(); await entered.promise;
    assert.equal(f.manager.status().tasks[0].deadlineAt, 1000000 + scenario.expected);
    sub.mock.timers.tick(scenario.expected - 1);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(f.agents[0].cancelCount, 0);
    sub.mock.timers.tick(1); await f.manager.drain();
    assert.deepEqual(states(f), ['running', 'failed']);
  });
});

test('claimed task time budget is authoritative and claim time does not renew the attempt', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1000000 });
  const entered = deferred(), claiming = deferred(), claimed = deferred();
  const f = await fixture({ hangIdle: true, backend: async (path, init) => {
    if (!path.endsWith('/claim')) return;
    claiming.resolve(); await claimed.promise;
    return { task: task({ session_id: init.body.session_id, budgets: { max_attempt_ms: 1500000 } }) };
  }, program: () => entered.resolve() });
  t.after(() => f.manager.dispose());
  f.manager.start(); await f.manager.tick(); await claiming.promise;
  t.mock.timers.tick(100); claimed.resolve(); await entered.promise;
  assert.equal(f.manager.status().tasks[0].deadlineAt, 2500000);
  t.mock.timers.tick(1499899);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.agents[0].cancelCount, 0);
  t.mock.timers.tick(1); await f.manager.drain();
  assert.deepEqual(states(f), ['running', 'failed']);
});

test('invalid task time budgets fail before model creation and host cap cannot exceed one hour', async () => {
  for (const value of [0, -1, 1.5, '1500000', null, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    const f = await fixture({ rows: [task({ budgets: { max_attempt_ms: value } })] });
    await f.run();
    assert.equal(f.agents.length, 0);
    assert.deepEqual(states(f), ['failed']);
    assert.match(f.calls.at(-1).body.error, /max_attempt_ms must be a positive integer/);
    await f.manager.dispose();
  }
  await assert.rejects(fixture({ managerOptions: { maxAttemptMs: 3600001 } }), /host hard limit/);
});

test('task time budget and live disable cancel pending native sessions', async () => {
  const timed = await fixture({ rows: [task({ budgets: { max_attempt_ms: 15 } })], hangIdle: true });
  await timed.run(); assert.deepEqual(states(timed), ['running', 'failed']); await timed.manager.dispose();
  let enabled = true;
  const entered = deferred();
  const disabled = await fixture({ managerOptions: { enabled: () => enabled }, program: async a => { entered.resolve(); await a.cancelled.promise; } });
  disabled.manager.start(); await entered.promise; enabled = false; await disabled.manager.tick(); await disabled.manager.drain();
  assert.deepEqual(states(disabled), ['running', 'cancelled']); await disabled.manager.dispose();
});

test('late host creation after cancellation cannot install tools', async () => {
  const entered = deferred(), gate = deferred();
  const f = await fixture({ createHook: async () => { entered.resolve(); await gate.promise; } });
  f.manager.start(); await entered.promise; await f.manager.stop(); gate.resolve();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.agents.length, 0); assert.deepEqual(states(f), ['running', 'cancelled']); await f.manager.dispose();
});

test('unavailable or disabled manager never claims, construction is inert, effect disposes', async () => {
  for (const options of [{ unavailable: true }, { managerOptions: { enabled: false } }]) {
    const f = await fixture(options); assert.equal(f.calls.length, 0); await f.run();
    assert.equal(f.calls.filter(call => call.path.includes('/tasks')).length, 0);
    assert.equal(typeof f.effects[0], 'function'); await f.effects[0](); assert.equal(f.manager.status().running, false);
    assert.throws(() => f.manager.start(), { code: 'DISPOSED' });
  }
});

async function storedFixture(t, options = {}) {
  const root = await fs.realpath(await fs.mkdtemp(join(tmpdir(), 'content-agent-purge-')));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const cwd = join(root, 'plugin'), sessionId = options.sessionId || 'stored-session';
  const owned = join(root, 'sessions', 'project', sessionId), artifact = join(owned, 'session.v4.jsonl' + (options.zstd ? '.zstd' : ''));
  await fs.mkdir(cwd, { recursive: true }); await fs.mkdir(owned, { recursive: true });
  const header = { type: 'session', version: 4, id: sessionId, cwd, createdAt: 1, isSeeded: false, delegationDepth: 0, ...options.header };
  const text = JSON.stringify(header) + '\n';
  await fs.writeFile(artifact, options.zstd ? zstdCompressSync(Buffer.from(text)) : text);
  await fs.writeFile(join(owned, 'session.v3.jsonl'), text);
  await fs.writeFile(join(owned, '.write-lock'), '');
  const binding = { task_id: 'purge-task', session_id: sessionId, batch_dir: join(root, 'batch') };
  const persistence = { stat: async () => {
    try { await fs.access(artifact); } catch (error) { if (error.code === 'ENOENT') return undefined; throw error; }
    return { header: { id: sessionId, cwd }, location: { kind: 'jsonl', path: artifact }, ...options.snapshot };
  } };
  return { root, cwd, owned, artifact, header, binding, persistence };
}

test('trusted purge removes only verified owned session artifacts and sweeps public query index', async t => {
  for (const zstd of [false, true]) {
    const stored = await storedFixture(t, { zstd }); let sweeps = 0;
    const sibling = join(stored.root, 'sessions', 'project', 'unrelated'); await fs.mkdir(sibling);
    await fs.writeFile(join(sibling, 'keep'), 'keep');
    const f = await fixture({ rows: [], persistence: stored.persistence, managerOptions: { cwd: stored.cwd },
      sessionQuery: { searchSessions: async request => { assert.deepEqual(request, { query: '', limit: 1 }); sweeps++; } } });
    assert.deepEqual(await f.manager.purgeSessions([stored.binding]), { removed: 1 });
    await assert.rejects(fs.access(stored.owned), { code: 'ENOENT' });
    assert.equal(await fs.readFile(join(sibling, 'keep'), 'utf8'), 'keep'); assert.equal(sweeps, 1);
    assert.deepEqual(await f.manager.purgeSessions([stored.binding]), { removed: 1 });
    assert.equal(f.CONTENT_TOOL_NAMES.some(name => /purge/.test(name)), false); await f.manager.dispose();
  }
});

test('trusted purge refuses invalid bindings, live sessions, wrong headers and unsafe locations', async t => {
  const stored = await storedFixture(t);
  const f = await fixture({ rows: [], persistence: stored.persistence, managerOptions: { cwd: stored.cwd } });
  for (const bindings of [[], [{}], [{ ...stored.binding, batch_dir: '../relative' }], [stored.binding, { ...stored.binding, task_id: 'other' }]]) {
    await assert.rejects(f.manager.purgeSessions(bindings), { code: 'PURGE_BINDING_INVALID' });
  }
  await f.manager.dispose();
  const live = await fixture({ rows: [], persistence: stored.persistence, liveSession: () => ({}), managerOptions: { cwd: stored.cwd } });
  await assert.rejects(live.manager.purgeSessions([stored.binding]), { code: 'PURGE_LIVE_SESSION' }); await live.manager.dispose();
  for (const snapshot of [{ header: { id: 'other', cwd: stored.cwd } }, { header: { id: stored.binding.session_id, cwd: join(stored.root, 'other') } },
    { location: { kind: 'jsonl', path: 'relative.jsonl' } }, { location: { kind: 'other', path: stored.artifact } },
    { location: { kind: 'jsonl', path: join(stored.owned, 'unexpected.jsonl') } }]) {
    const wrong = await fixture({ rows: [], persistence: { stat: async () => ({ header: stored.header, location: { kind: 'jsonl', path: stored.artifact }, ...snapshot }) }, managerOptions: { cwd: stored.cwd } });
    await assert.rejects(wrong.manager.purgeSessions([stored.binding]), /Stored session|Artifact/); await wrong.manager.dispose();
  }
  await fs.writeFile(stored.artifact, JSON.stringify({ ...stored.header, id: 'other' }) + '\n');
  await assert.rejects(f.manager.purgeSessions([stored.binding]), { code: 'PURGE_IDENTITY_INVALID' });
  assert.equal((await fs.stat(stored.owned)).isDirectory(), true);
});

test('trusted purge rejects symlink ancestors and nested artifacts without touching targets', async t => {
  const stored = await storedFixture(t), outside = join(stored.root, 'outside'); await fs.mkdir(outside);
  await fs.writeFile(join(outside, 'keep'), 'keep');
  await fs.symlink(outside, join(stored.owned, 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
  const f = await fixture({ rows: [], persistence: stored.persistence, managerOptions: { cwd: stored.cwd } });
  await assert.rejects(f.manager.purgeSessions([stored.binding]), { code: 'PURGE_UNSAFE_PATH' });
  assert.equal(await fs.readFile(join(outside, 'keep'), 'utf8'), 'keep'); await f.manager.dispose();
});

test('purge cancels and awaits an active attempt before removing persistence', async t => {
  const stored = await storedFixture(t), entered = deferred();
  const f = await fixture({ rows: [task({ ...stored.binding, task_id: stored.binding.task_id })], persistence: stored.persistence,
    managerOptions: { cwd: stored.cwd }, program: async a => { entered.resolve(); await a.cancelled.promise; } });
  f.manager.start(); await entered.promise; await f.manager.purgeSessions([stored.binding]);
  assert.equal(f.agents[0].disposed, true); await assert.rejects(fs.access(stored.owned), { code: 'ENOENT' });
  assert.equal(f.manager.status().tasks.length, 0); await f.manager.tick(); await f.manager.drain();
  assert.equal(f.agents.length, 1); await f.manager.dispose();
});

test('purge polling acknowledges tickets once, retries only ack and supports missing endpoint', async t => {
  const stored = await storedFixture(t); let acks = 0, stats = 0;
  const f = await fixture({ rows: [], persistence: { stat: async (...args) => { stats++; return stored.persistence.stat(...args); } },
    managerOptions: { cwd: stored.cwd }, backend: (path, init) => {
      if (path === '/api/content-agent/purges') return { purges: [{ purge_id: 'purge/one', sessions: [stored.binding] }] };
      if (path === '/api/content-agent/purges/purge%2Fone') { acks++; assert.deepEqual(init.body, { status: 'completed' }); if (acks === 1) throw new Error('ack lost'); return { ok: true }; }
    } });
  f.manager.start(); await f.manager.tick(); assert.match(f.manager.status().lastPurgeError, /ack lost/);
  await f.manager.tick(); assert.equal(acks, 2); assert.equal(stats, 2); assert.equal(f.manager.status().purgeCount, 1);
  await f.manager.dispose();
  for (const response of [null, {}, { error: 'not_found' }, { purges: [] }]) {
    const old = await fixture({ rows: [], backend: path => path.endsWith('/purges') ? response : undefined });
    await old.run(); assert.equal(old.manager.status().lastPurgeError, ''); await old.manager.dispose();
  }
});

test('failed purge ticket is reported and never enables arbitrary model tools', async t => {
  const stored = await storedFixture(t); let receipt;
  const f = await fixture({ rows: [], persistence: stored.persistence, managerOptions: { cwd: stored.cwd }, backend: (path, init) => {
    if (path.endsWith('/purges')) return { purges: [{ purge_id: 'bad', sessions: [{ ...stored.binding, batch_dir: 'relative' }] }] };
    if (path.endsWith('/purges/bad')) { receipt = init.body; return { ok: true }; }
  } });
  await f.run(); assert.equal(receipt.status, 'failed'); assert.match(receipt.error, /absolute batch_dir/);
  assert.equal((await fs.stat(stored.owned)).isDirectory(), true); await f.manager.dispose();
});
