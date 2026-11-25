'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function loadOperationsAgent() {
  const file = path.join(__dirname, '..', 'lib', 'operations-agent.js');
  return import(require('node:url').pathToFileURL(file).href);
}

test('OPERATIONS_PROMPT retains core redlines and 3-install budget', async () => {
  const mod = await loadOperationsAgent();
  assert.match(mod.OPERATIONS_PROMPT, /最多安装 3 次/);
  assert.match(mod.OPERATIONS_PROMPT, /绝不自动上传发布/);
  assert.match(mod.OPERATIONS_PROMPT, /REVIEW_SAMPLE_SOURCE_UNVERIFIED/);
  assert.match(mod.OPERATIONS_PROMPT, /patch_lines/);
  assert.match(mod.OPERATIONS_PROMPT, /multi_patch/);
});

test('dual-bucket read budget separates evidence reads from code reads', async () => {
  const { createOperationsAgentManager } = await loadOperationsAgent();
  const calls = [];
  const tools = new Map();
  const agent = {
    model: { select() {} },
    inbox: {},
    followup() {},
    cancel() {},
    whenIdle: async () => {
      const exec = (action, input = {}) => tools.get(`operations_${action}`).execute(input, {});
      await exec('read_evidence', { path: 'a.json' });
      await exec('read_evidence', { path: 'b.json' });
      await exec('read_evidence', { path: 'c.json' });
      const r4 = await exec('read_evidence', { path: 'd.json' });
      calls.push({ kind: 'ev4', deferred: Boolean(r4.deferred) });
      await exec('request_code_repair', { summary: 'bug' });
      await exec('code_repair', { operation: 'search', query: 'foo' });
      await exec('code_repair', { operation: 'read_lines', path: 'app/a.py', start_line: 1 });
      await exec('code_repair', { operation: 'read_lines', path: 'app/a.py', start_line: 20 });
      await exec('code_repair', { operation: 'read_lines', path: 'app/a.py', start_line: 40 });
      const c5 = await exec('code_repair', { operation: 'read_lines', path: 'app/a.py', start_line: 60 });
      const c6 = await exec('code_repair', { operation: 'read_lines', path: 'app/a.py', start_line: 80 });
      calls.push({ kind: 'code5', deferred: Boolean(c5.deferred) });
      calls.push({ kind: 'code6', deferred: Boolean(c6.deferred) });
      const schema = tools.get('operations_code_repair').parameters.properties;
      calls.push({
        kind: 'schema',
        hasPatchLines: schema.operation.enum.includes('patch_lines'),
        hasMultiPatch: schema.operation.enum.includes('multi_patch'),
        hasFast: Boolean(schema.fast),
      });
      await exec('finish', { outcome: 'recovery_queued', summary: 'done' });
    },
  };
  const create = async (init) => {
    init.setup({
      tools: {
        restrict: () => () => {},
        guard: () => () => {},
        register: (def) => { tools.set(def.name, def); return () => tools.delete(def.name); },
      },
      systemPrompt: { section: () => () => {} },
      on: () => () => {},
    }, agent);
    return { dispose: async () => {} };
  };
  let claimed = false;
  const manager = createOperationsAgentManager({ agents: { create, resume: create } }, {
    cwd: '/tmp',
    intervalMs: 60000,
    heartbeatMs: 50,
    maxReadOnlyActions: 3,
    maxCodeReadActions: 5,
    maxIdleContinuations: 0,
    resolveRoute: () => ({ provider: 'mock', model: 'mock' }),
    backendRequest: async (url, init) => {
      if (url === '/api/operations/incidents') {
        if (claimed) return { incidents: [] };
        return {
          incidents: [{
            incident_id: 'inc-dual',
            batch_name: 'b1',
            batch_dir: '/tmp/b1',
            status: 'queued',
            eligible: true,
            queue_generation: 0,
          }],
        };
      }
      if (url.endsWith('/claim')) {
        claimed = true;
        return {
          mode: 'repair',
          attempt_id: 'att-1',
          session_id: init.body.session_id,
          lease_expires_at: Math.floor(Date.now() / 1000) + 180,
        };
      }
      return { request_id: init.body?.request_id, status: 'completed', evidence: { ok: true } };
    },
  });
  manager.start();
  await manager.tick();
  await manager.drain();
  await manager.dispose();

  assert.deepEqual(calls.find(c => c.kind === 'ev4'), { kind: 'ev4', deferred: true });
  assert.deepEqual(calls.find(c => c.kind === 'code5'), { kind: 'code5', deferred: false });
  assert.deepEqual(calls.find(c => c.kind === 'code6'), { kind: 'code6', deferred: true });
  assert.deepEqual(calls.find(c => c.kind === 'schema'), {
    kind: 'schema',
    hasPatchLines: true,
    hasMultiPatch: true,
    hasFast: true,
  });
});

test('OperationsPanel renders unfreeze button and detail modal only when applicable', () => {
  const clientSrc = fs.readFileSync(path.join(__dirname, '..', 'lib', 'client.js'), 'utf8');
  const start = clientSrc.indexOf('function operationsStatusTone(');
  const end = clientSrc.indexOf('async function requestAdvance(');
  assert.ok(start > 0 && end > start, 'OperationsPanel helpers found in lib/client.js');
  const fnCode = clientSrc.slice(start, end);

  function renderWithState(states, summaryOverride) {
    let idx = 0;
    const React = {
      createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat().filter(Boolean) }),
      useState: (init) => {
        const val = idx < states.length ? states[idx] : init;
        idx++;
        return [val, () => {}];
      },
      useRef: (init) => ({ current: init }),
      useEffect: () => {},
    };
    const sandbox = {
      require: (name) => (name === 'react' ? React : {}),
      operationsSummary: () => summaryOverride || { label: '等待新证据', hint: '已冻结', error: false },
      operationsText: (v) => String(v || ''),
      operationsRequest: async () => ({}),
      jget: async () => ({}),
      jpost: async () => ({}),
      T: { sectionTitle: {}, hint: {}, miniBtn: {}, statusErr: {} },
    };
    vm.runInNewContext(fnCode, sandbox);
    return sandbox.OperationsPanel({ batchName: 'batch-1' });
  }

  function collect(node, pred, out = []) {
    if (!node || typeof node !== 'object') return out;
    if (pred(node)) out.push(node);
    for (const c of node.children || []) collect(c, pred, out);
    return out;
  }

  const frozenTree = renderWithState([
    { enabled: true, available: true, loaded: true },
    {
      batchName: 'batch-1',
      incident: {
        status: 'waiting_evidence',
        stage: 'review_gate',
        recovery: { reason: '相同证据下未产生新修改' },
        findings: [{ summary: '发现源码映射错误' }],
      },
      receipts: [],
    },
    '', '', false, 0, true, { perf: { budgets: { attempts: 3, attempts_limit: 24, installs: 1, installs_limit: 3, regenerations: 0, regenerations_limit: 6 } }, diff: { patch: '+fix' } },
  ]);

  const buttons = collect(frozenTree, n => n.type === 'button').map(b => b.children.join(''));
  assert.ok(buttons.includes('解除冻结'), `Expected 解除冻结 button, got: ${buttons}`);
  assert.ok(buttons.includes('收起详情'), `Expected 收起详情 button, got: ${buttons}`);
  assert.equal(collect(frozenTree, n => n.type === 'details').length, 0, 'Must never use <details>');
  const dialogs = collect(frozenTree, n => n.props?.role === 'dialog');
  assert.equal(dialogs.length, 1, 'Expected div detail modal');
  assert.equal(dialogs[0].props.style.position, 'fixed', 'Detail modal must float outside fixed-height card');

  // Verify fixed-height card, status pill tone, and scrollable real-time feed
  const activeTree = renderWithState([
    {
      enabled: true, available: true, loaded: true,
      attempts: [{ batch_name: 'batch-1', incident_id: 'inc-1', active: true, mode: 'repair', state: 'repairing', phase: 'code_write', detail: '修改 self_heal.py 补丁' }],
    },
    {
      batchName: 'batch-1',
      incident: {
        incident_id: 'inc-1',
        status: 'claimed',
        mode: 'repair',
        stage: 'validate',
        repair_progress: { phase: 'code_test', operation: 'pytest test_operations.py', at: '2026-11-19T10:02:00Z' },
        events: [
          { action: 'heartbeat', timestamp: '2026-11-19T10:00:10Z', outcome: 'completed' },
          { action: 'diagnose', timestamp: '2026-11-19T10:00:30Z', detail: '定位第3题题干为空' },
          { action: 'apply_repair', timestamp: '2026-11-19T10:01:00Z', detail: '已回填缺失题干并复验' },
        ],
        findings: [{ summary: '提取阶段漏掉第3题首行' }],
      },
      receipts: [],
    },
    '', '', false, 0, false, null,
  ], { label: '修改中', hint: '批次「batch-1」：validate 缺少第3题', error: false });

  assert.equal(activeTree.props.style.height, 176);
  assert.equal(activeTree.props.style.flexShrink, 0);
  assert.equal(activeTree.props.style.overflow, 'hidden');
  const statusPill = collect(activeTree, n => n.props?.className === 'wf-operations-status')[0];
  assert.equal(statusPill.props['data-tone'], 'active');
  const feed = collect(activeTree, n => String(n.props?.className || '').includes('wf-operations-feed'))[0];
  assert.equal(feed.props.style.overflowY, 'auto');
  assert.equal(feed.props.style.flex, 1);
  assert.equal(feed.props.style.minHeight, 0);
  const feedItems = collect(activeTree, n => n.props?.className === 'wf-operations-feed-item');
  assert.ok(feedItems.length >= 4, `Expected >=4 real-time feed items, got ${feedItems.length}`);
  const feedText = JSON.stringify(feedItems);
  assert.match(feedText, /修改 self_heal\.py 补丁/);
  assert.match(feedText, /定位第3题题干为空/);
  assert.match(feedText, /已回填缺失题干并复验/);
  assert.doesNotMatch(feedText, /heartbeat/);
  assert.match(clientSrc, /className:\s*'wf-upload-right'[\s\S]*?className:\s*'wf-flow-card'[\s\S]*?diagBanner\(selBatch, stage, refresh, flowFeedback\),[\s\S]*?\),\s*h\(OperationsPanel/);
});
