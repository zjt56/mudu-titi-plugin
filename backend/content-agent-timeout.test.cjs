const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const { resolve } = require('node:path');
const ready = import(pathToFileURL(resolve(__dirname, '../lib/content-agent.js')));

async function fixture(program) {
  const { createContentAgentManager } = await ready;
  const calls = [], tools = new Map(), listeners = new Map();
  const session = { id: 'timeout-session' };
  let disposed = false;
  const agent = {
    model: { select() {} }, inbox: {},
    followup() { void program({
      emit: (type, data) => listeners.get('session/event')?.(session, { type, time: Date.now(), data }),
      live: frame => listeners.get('agent/assistant-stream')?.({ frame }),
      call: (action, args) => tools.get(`content_${action}`).execute(args, {}),
    }); },
    whenIdle: () => new Promise(() => {}), cancel() {},
  };
  const manager = createContentAgentManager({
    ctx: { agents: {
      resume() { throw new Error('Unexpected resume'); },
      async create(init) {
        init.setup({
          tools: { restrict: () => () => {}, guard: () => () => {},
            register: tool => { tools.set(tool.name, tool); return () => tools.delete(tool.name); } },
          systemPrompt: { section: () => () => {} },
          on: (name, listener) => { listeners.set(name, listener); return () => listeners.delete(name); },
        }, agent);
        return { dispose: async () => { disposed = true; } };
      },
    } },
    cwd: '/mock/plugin', intervalMs: 60000, maxAttemptMs: 80,
    makeSessionId: () => session.id,
    resolveRoute: () => ({ provider: 'mock-provider', model: 'mock-model' }),
    async request(path, init = {}) {
      calls.push({ path, ...init });
      const task = { task_id: 'mock-task', stage: 'answers', prompt: 'Use scoped tools and submit a candidate.',
        budgets: { max_attempt_ms: 1500000 }, deadline: Date.now() / 1000 + 1500 };
      if (path === '/api/content-agent/tasks') return { tasks: [task] };
      if (path.endsWith('/claim')) return { task: { ...task, session_id: session.id } };
      if (path.endsWith('/tools')) return init.body.action === 'submit_candidate'
        ? { accepted: false, retryable: false, errors: ['source evidence mismatch'] } : { text: 'Source evidence' };
      return { ok: true };
    },
  });
  manager.start(); await manager.tick(); await manager.drain();
  const failed = calls.find(call => call.body?.status === 'failed')?.body;
  await manager.dispose();
  return { calls, failed, disposed };
}
const timeout = { code: 'TIMEOUT', message: 'pi-ai stream idle timeout after 300000ms https://private.example/?token=secret' };
const attempt = (last) => ({ stream: [
  { type: 'reasoning-chunks', time0: last - 10, dt: [4, 6], texts: ['private reasoning', 'must not be persisted'] },
  { type: 'chunk', time: Date.now(), chunk: { type: 'finish', reason: { kind: 'error', failure: timeout } } },
] });

test('two successful evidence reads then a stream timeout retain model failure at total budget exhaustion', async () => {
  let last;
  const f = await fixture(async a => {
    await a.call('read_source', { path: 'body.txt' });
    await a.call('read_source', { path: 'answers.txt' });
    a.emit('step/start', { turn: 1, step: 2 });
    last = Date.now();
    a.emit('assistant/attempt', attempt(last));
    a.emit('llm/retry', { failure: timeout, retry: 1 });
  });
  assert.equal(f.failed.error_code, 'timeout');
  assert.equal(f.failed.failure_class, 'model_timeout');
  assert.equal(f.failed.transport, 'native_content');
  assert.equal(f.failed.phase, 'model_stream');
  assert.equal(f.failed.retryable, false);
  assert.equal(f.failed.last_model_activity_at, last);
  assert.ok(f.failed.last_tool_at <= last);
  assert.deepEqual(f.failed.last_provider_error, { code: 'TIMEOUT', message: 'Provider stream idle timeout' });
  assert.match(f.failed.error, /Content task time budget exceeded; model stream timed out/);
  assert.doesNotMatch(JSON.stringify(f.failed), /secret|private reasoning|private\.example|source evidence mismatch/);
  assert.equal(f.calls.filter(call => call.body?.action === 'read_source').length, 2);
  assert.equal(f.calls.filter(call => call.body?.action === 'submit_candidate').length, 0);
  assert.equal(f.calls.filter(call => call.path.endsWith('/claim')).length, 1);
  assert.equal(f.disposed, true);
});

test('retry event alone provides timeout evidence and live chunks supply activity time', async () => {
  const f = await fixture(async a => {
    a.emit('step/start', { turn: 1, step: 1 });
    a.live({ type: 'chunk', chunk: { type: 'reasoning-delta', text: 'private reasoning' } });
    a.emit('llm/retry', { failure: timeout });
  });
  assert.equal(f.failed.failure_class, 'model_timeout');
  assert.ok(Number.isFinite(f.failed.last_model_activity_at));
  assert.equal(f.failed.last_tool_at, undefined);
});

test('ordinary time budget exhaustion and a recovered provider timeout stay distinct', async () => {
  for (const recovered of [false, true]) {
    const f = await fixture(async a => {
      a.emit('step/start', { turn: 1, step: 1 });
      if (recovered) {
        a.emit('assistant/attempt', attempt(Date.now()));
        a.emit('assistant/message', { stream: [{ type: 'chunk', time: Date.now(), chunk: { type: 'finish', reason: { kind: 'tool-calls' } } }] });
      }
    });
    assert.equal(f.failed.failure_class, undefined);
    assert.equal(f.failed.error_code, undefined);
    assert.equal(f.failed.error, 'Content task time budget exceeded');
  }
});

test('a later candidate rejection is not reclassified as a prior provider timeout', async () => {
  const f = await fixture(async a => {
    a.emit('step/start', { turn: 1, step: 1 });
    a.emit('assistant/attempt', attempt(Date.now()));
    await a.call('submit_candidate', { candidate: {} });
  });
  assert.equal(f.failed.failure_class, undefined);
  assert.match(f.failed.error, /Candidate rejected/);
});
