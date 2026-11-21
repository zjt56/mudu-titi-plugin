'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { pathToFileURL } = require('node:url');
const path = require('node:path');

async function loadAgentModule() {
  return import(pathToFileURL(path.resolve(__dirname, '../lib/operations-agent.js')).href);
}

test('busyDeferred release passes busy_retry and worker_busy reason_code', async () => {
  const { createOperationsAgentManager, operationsHttpError } = await loadAgentModule();
  const actions = [];
  const incident = {
    incident_id: 'inc-busy-1',
    batch_name: 'paper-a',
    batch_dir: '/tmp/paper-a',
    status: 'queued',
    eligible: true,
    queue_generation: 1,
  };
  let delivered = false;
  const ctx = {
    agents: {
      create: async ({ setup }) => {
        const registered = new Map();
        const agent = {
          model: { select() {} },
          cancel() {},
          followup() {},
          whenIdle: async () => {
            const diag = registered.get('operations_diagnose');
            await diag.execute({}, { concludeTurn() {} });
          },
        };
        const agentCtx = {
          tools: {
            restrict: () => () => {},
            guard: () => () => {},
            register: (tool) => { registered.set(tool.name, tool); return () => {}; },
          },
          systemPrompt: { section: () => () => {} },
          on: () => () => {},
        };
        setup(agentCtx, agent);
        return { dispose: async () => {} };
      },
      resume: async () => { throw new Error('unexpected resume'); },
    },
  };
  const manager = createOperationsAgentManager(ctx, {
    cwd: '/tmp',
    intervalMs: 5000,
    activeIntervalMs: 50,
    busyWaitMs: 15,
    resolveRoute: () => ({ provider: 'p', model: 'm' }),
    backendRequest: async (url, { body } = {}) => {
      if (url === '/api/operations/incidents') {
        if (delivered) return { incidents: [] };
        delivered = true;
        return { incidents: [incident] };
      }
      if (url.endsWith('/claim')) {
        return { ...incident, status: 'claimed', attempt_id: 'att-1', session_id: body.session_id, mode: 'repair', lease_expires_at: Date.now() / 1000 + 180 };
      }
      if (url.endsWith('/action')) {
        actions.push(body);
        if (body.action === 'diagnose') {
          throw operationsHttpError({ code: 'worker_busy', admitted: false, retryable: true, retry_after_ms: 10, error: 'worker_busy: 批次执行锁已占用' }, 409);
        }
        return { request_id: body.request_id, status: 'completed', evidence: { ok: true, lease_expires_at: Date.now() / 1000 + 180 } };
      }
      throw new Error('unexpected url: ' + url);
    },
  });
  manager.start();
  await manager.drain();
  await manager.stop();
  const rel = actions.find(a => a.action === 'release');
  assert.ok(rel, 'expected a release action');
  assert.equal(rel.input.busy_retry, true);
  assert.equal(rel.input.reason_code, 'worker_busy');
  assert.equal(rel.input.state, 'worker_busy');
});

test('adaptive polling uses activeIntervalMs when work exists and wake() triggers immediate tick', async () => {
  const { createOperationsAgentManager } = await loadAgentModule();
  let polls = 0;
  let active = false;
  const ctx = {
    agents: {
      create: async () => ({ dispose: async () => {} }),
      resume: async () => ({ dispose: async () => {} }),
    },
  };
  const manager = createOperationsAgentManager(ctx, {
    cwd: '/tmp',
    intervalMs: 5000,
    activeIntervalMs: 30,
    resolveRoute: () => ({ provider: 'p', model: 'm' }),
    hasWork: () => {
      polls += 1;
      return { has_work: active, batches: active ? ['paper-a'] : [] };
    },
    backendRequest: async (url) => {
      if (url === '/api/operations/incidents') return { incidents: [] };
      return {};
    },
  });
  manager.start();
  await manager.drain();
  assert.equal(polls, 1);
  assert.equal(manager.status().loopAlive, true);

  // Even though idle intervalMs is 5000ms, wake() triggers an immediate tick.
  active = true;
  await manager.wake();
  assert.equal(polls, 2);

  // Now that active=true, activeIntervalMs (30ms) should drive additional ticks quickly.
  await new Promise(resolve => setTimeout(resolve, 110));
  assert.ok(polls >= 4, `expected >= 4 polls under activeIntervalMs, got ${polls}`);
  await manager.stop();
});

test('wake() called during an in-flight poll queues an immediate follow-up tick', async () => {
  const { createOperationsAgentManager } = await loadAgentModule();
  let polls = 0;
  let releaseFirst;
  const firstEntered = new Promise(resolve => {
    const gate = () => resolve();
    releaseFirst = gate;
  });
  let finishFirst;
  const firstBlocker = new Promise(resolve => { finishFirst = resolve; });
  const ctx = {
    agents: {
      create: async () => ({ dispose: async () => {} }),
      resume: async () => ({ dispose: async () => {} }),
    },
  };
  const manager = createOperationsAgentManager(ctx, {
    cwd: '/tmp',
    intervalMs: 10000,
    activeIntervalMs: 10000,
    resolveRoute: () => ({ provider: 'p', model: 'm' }),
    hasWork: async () => {
      polls += 1;
      if (polls === 1) {
        releaseFirst();
        await firstBlocker;
      }
      return { has_work: false, batches: [] };
    },
    backendRequest: async () => ({ incidents: [] }),
  });
  manager.start();
  await firstEntered;
  // Call wake() while the first poll is still in flight.
  manager.wake();
  finishFirst();
  await new Promise(resolve => setTimeout(resolve, 40));
  assert.ok(polls >= 2, `expected follow-up poll queued by wake(), got ${polls}`);
  await manager.stop();
});
