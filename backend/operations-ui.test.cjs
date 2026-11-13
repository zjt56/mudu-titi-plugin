const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const client = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');
const start = client.indexOf('    function operationsText(');
const end = client.indexOf('    function recoveryProgressText(', start);
function harness(fetch = async () => { throw new Error('unexpected request'); }) {
  const ctx = vm.createContext({ window: { fetch }, setTimeout, clearTimeout, AbortController });
  vm.runInContext(client.slice(start, end), ctx);
  return ctx;
}

test('all operation states are distinct from workflow quality and model claims', () => {
  const ctx = harness();
  for (const [status, label] of Object.entries({ unclaimed: '已触发', queued: '已触发', investigating: '调查中', repairing: '修复中', verifying: '复验中', waiting_external: '待外部条件', cancelled: '已取消' })) {
    assert.equal(ctx.operationsView({ status }).label, label);
  }
  assert.equal(ctx.operationsView(null).label, '未接手');
  assert.equal(ctx.operationsView({ status: 'success', outcome: '已修复' }).label, '状态待确认');
  for (const verification of [null, 'passed', { passed: 'true' }, { status: 'success' }, { passed: false }]) {
    assert.equal(ctx.operationsView({ status: 'recovered', verification }).label, '复验待确认');
  }
  assert.equal(ctx.operationsView({ status: 'recovered', verification: { passed: true, evidence_id: 'gate-receipt' } }).label, '已恢复');
  assert.equal(ctx.operationsView({ status: 'repairing', verification: { passed: true } }).label, '修复中');
});

test('backend incident shapes require recorded evidence before resolved means recovered', () => {
  const ctx = harness();
  const incident = { incident_id: 'i1', batch_name: 'fixture', session_id: 'sess-1', status: 'resolved',
    verification: { verified: true, evidence_id: 'receipt-1', snapshot: 'fingerprint', stage: 'validate' },
    events: [{ timestamp: '2026-11-08T10:00:00Z', action: 'finish', outcome: 'completed', detail: '真实闸门已通过', verification: null }] };
  assert.equal(ctx.operationsView(incident).label, '已恢复');
  assert.match(ctx.operationsView(incident).outcome, /真实闸门已通过/);
  assert.equal(ctx.operationsView({ ...incident, verification: { verified: true } }).label, '复验待确认');
  assert.equal(ctx.operationsView({ ...incident, verification: { verified: 'true', evidence_id: 'r' } }).label, '复验待确认');
  assert.equal(ctx.operationsView({ ...incident, status: 'needs_deep_repair' }).label, '自动升级修复');
  assert.equal(ctx.operationsView({ status: 'waiting_evidence' }).label, '等待新证据');
  assert.equal(ctx.operationsView({ ...incident, status: 'claimed', events: [{ action: 'apply_repair' }] }).label, '修复中');
  assert.equal(ctx.operationsView({ ...incident, status: 'claimed', events: [{ action: 'verify' }] }).label, '复验中');
});

test('unavailable host preserves confirmed enabled state and its readable reason', async () => {
  const ctx = harness(async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ enabled: false, available: false, error: 'Harness不可用' }) }));
  const result = await ctx.operationsRequest('status');
  assert.equal(result.enabled, false);
  assert.equal(result.error, 'Harness不可用');
});

test('event aliases tolerate missing fields and choose the latest timestamp', () => {
  const ctx = harness();
  const result = ctx.operationsView({ session_id: 'sess-1', status: 'investigating', events: [null,
    { timestamp: '2026-11-08T10:00:00Z', action: 'inspect', outcome: { message: 'missing input' }, verification: { passed: false } },
    { at: '2026-11-07T10:00:00Z', type: 'older', message: 'old' },
  ] });
  assert.equal(result.action, 'inspect');
  assert.match(result.outcome, /missing input/);
  assert.match(result.verification, /false/);
  assert.equal(result.sessionId, 'sess-1');
  assert.equal(ctx.operationsView({ events: 'unexpected', session_id: {} }).sessionId, '');
  assert.equal(ctx.operationsView({ events: [{ kind: 'read', result: 'pending', time: 'unparsed' }] }).action, 'read');
});

test('host reads stay GET and config sends only explicitly requested enabled value', async () => {
  const calls = [];
  const ctx = harness(async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, text: async () => JSON.stringify({ available: true, enabled: false }) };
  });
  await ctx.operationsRequest('status');
  await ctx.operationsRequest('config', { enabled: false });
  assert.equal(calls[0].url, '/wf-plugin/operations/status');
  assert.equal(calls[0].options.method, 'GET');
  assert.equal(calls[0].options.body, undefined);
  assert.equal(calls[1].url, '/wf-plugin/operations/config');
  assert.equal(calls[1].options.method, 'POST');
  assert.deepEqual(JSON.parse(calls[1].options.body), { enabled: false });
});

test('malformed and failed host responses are readable errors, never success', async () => {
  for (const response of [
    { ok: false, status: 503, text: async () => '{"error":"服务离线"}' },
    { ok: true, status: 200, text: async () => '<html>offline</html>' },
    { ok: true, status: 200, text: async () => '{"error":"配置不可写"}' },
  ]) {
    await assert.rejects(harness(async () => response).operationsRequest('config', { enabled: true }), /服务离线|无法读取|配置不可写/);
  }
});

test('heartbeat does not replace repair phase and unknown receipts are explicit', () => {
  const ctx = harness();
  const item = { status: 'claimed', events: [
    { action: 'code_repair', timestamp: '2026-11-10T01:00:00Z', outcome: 'completed' },
    { action: 'heartbeat', timestamp: '2026-11-10T01:00:15Z', outcome: 'completed' },
  ] };
  const view = ctx.operationsView(item);
  assert.equal(view.label, '修复中');
  assert.equal(view.action, 'code_repair');
  assert.equal(view.heartbeat, '2026-11-10T01:00:15Z');
  const quarantined = ctx.operationsView({ ...item, status: 'queued' }, { receipts: [{ status: 'unknown' }] });
  assert.equal(quarantined.label, '待核对动作回执');
  assert.equal(quarantined.unknown, 1);
  assert.equal(ctx.operationsView({ status: 'queued', not_before: 200 }, { now: 100000 }).label, '冷却等待');
  assert.equal(ctx.operationsView({ status: 'claimed', lease_expires_at: 50 }, { now: 100000 }).label, '接手已过期');
});

test('host attempt error and real recovery evidence remain separate', () => {
  const ctx = harness();
  const view = ctx.operationsView({ incident_id: 'i', status: 'claimed' }, {
    batchName: 'b', host: { attempts: [{ batch_name: 'b', incident_id: 'i', active: true, state: 'session_failed', error: 'quota' }] },
  });
  assert.equal(view.label, '会话失败');
  assert.equal(view.error, 'quota');
  assert.equal(view.verified, false);
});


function renderPanel({ host = {}, incident = null, batchName = 'fixture', hostError = '', recordError = '' } = {}) {
  const states = [host, { batchName, incident, receipts: [] }, hostError, recordError, false, 0];
  let index = 0;
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat().filter(v => v !== null && v !== undefined) }),
    useState: () => [states[index++], () => {}], useRef: () => ({ current: null }), useEffect: () => {},
  };
  const ctx = harness();
  ctx.require = () => React;
  ctx.T = { hint: { color: 'var(--dsw-alias-label-secondary)' }, statusErr: { color: 'var(--dsw-alias-state-error-primary)' }, sectionTitle: {}, miniBtn: {} };
  return ctx.OperationsPanel({ batchName });
}
function nodes(tree) { return [tree, ...(tree.children || []).flatMap(v => typeof v === 'object' ? nodes(v) : [])]; }
function text(tree) { return (tree.children || []).map(v => typeof v === 'object' ? text(v) : String(v)).join(' '); }
function healthy(overrides = {}) { return { loaded: true, enabled: true, available: true, running: true, loopAlive: true, lastPollAt: new Date().toISOString(), ...overrides }; }

test('compact panel renders one status with necessary controls and no event log or session details', () => {
  const tree = renderPanel({ host: healthy(), incident: { status: 'investigating', detail: 'validate: QUESTION_NOT_FOUND，缺少第3题', session_id: 'private-session' } });
  const all = nodes(tree), content = text(tree);
  assert.match(content, /AI运维 调查中 关闭运维 刷新/);
  assert.match(content, /批次「fixture」（workflows\/fixture）：validate: QUESTION_NOT_FOUND，缺少第3题/);
  assert.equal(all.filter(n => n.props.className === 'wf-operations-status').length, 1);
  assert.equal(all.filter(n => n.type === 'button').length, 2);
  assert.equal(tree.props.style.flexShrink, 0);
  assert.equal(tree.props.style.paddingTop, 8);
  assert.equal(all.find(n => n.props.className === 'wf-operations-hint').props.style.WebkitLineClamp, 2);
  assert.ok(!all.some(n => ['details', 'summary', 'input'].includes(n.type) || n.props.role === 'log'));
  assert.doesNotMatch(content, /private-session|最近扫描|暂无运维消息|最近运维事件|verification/);
  assert.ok(all.every(n => n.props.style?.height !== 132));
});

test('closed, disconnected and stale services never render continuous monitoring', () => {
  for (const [host, hostError, label] of [
    [healthy({ enabled: false }), '', '已关闭'],
    [healthy({ loaded: false, available: false }), 'Failed to fetch', '服务失联'],
    [healthy({ available: false }), '', '服务不可用'],
    [healthy({ running: false }), '', '监测已停止'],
    [healthy({ loopAlive: false }), '', '监测已停止'],
    [healthy({ pollStale: true }), '', '服务失联'],
    [healthy({ lastPollAt: new Date(Date.now() - 30000).toISOString() }), '', '服务失联'],
    [healthy({ lastPollError: '后端连接失败' }), '', '扫描异常'],
  ]) {
    const tree = renderPanel({ host, hostError });
    assert.match(text(tree), new RegExp(label));
    assert.doesNotMatch(text(tree), /持续监测中/);
  }
});

test('normal monitoring shows no invented anomaly, incident or historical attempts', () => {
  const tree = renderPanel({ host: healthy({ healingCount: 2, attempts: [{ active: false, batch_name: 'old', state: 'session_failed', error: 'old error' }] }) });
  assert.match(text(tree), /持续监测中/);
  assert.doesNotMatch(text(tree), /异常|故障|未接手|old error|推进中|待命/);
  assert.equal(nodes(tree).filter(n => n.props.className === 'wf-operations-hint').length, 0);
  const pending = renderPanel({ host: healthy({ lastPollAt: '' }) });
  assert.match(text(pending), /监测待确认/);
  assert.equal(harness().operationsSummary(healthy(), null, { batchName: 'fixture' }).label, '记录读取中');
});

test('investigation and repair mode render their real phase rather than old action prose', () => {
  for (const [mode, state, label] of [['investigation', 'investigating', '调查中'], ['repair', 'repairing', '修复中'], ['repair', 'waiting_repair', '等待修复锁']]) {
    const host = healthy({ attempts: [{ batch_name: 'fixture', incident_id: 'i', active: true, mode, state }] });
    const incident = { incident_id: 'i', status: 'claimed', mode, detail: 'answers：第2题来源绑定失败', events: [{ action: 'apply_repair' }] };
    assert.match(text(renderPanel({ host, incident })), new RegExp(label));
  }
  const ctx = harness();
  assert.equal(ctx.operationsView({ status: 'claimed', mode: 'investigation', events: [{ action: 'verify' }] }).label, '调查中');
  assert.equal(ctx.operationsView({ status: 'claimed', mode: 'repair' }).label, '准备修复');
  for (const [phase, label] of [['evidence_read', '定位代码'], ['code_write', '修改中'], ['code_test', '测试中'], ['no_substantive_progress', '修复无进展']]) {
    assert.equal(ctx.operationsView({ incident_id: 'i', status: 'claimed', mode: 'repair', repair_progress: { phase } }).label, label);
    const host = healthy({ attempts: [{ batch_name: 'fixture', incident_id: 'i', active: true, mode: 'repair', state: 'repairing', phase }] });
    assert.match(text(renderPanel({ host, incident: { incident_id: 'i', status: 'claimed', mode: 'repair' } })), new RegExp(label));
  }
  assert.equal(ctx.operationsView({ status: 'claimed', mode: 'repair', lease_expires_at: 1, repair_progress: { phase: 'code_write' } }).label, '接手已过期');
  assert.match(text(renderPanel({ host: healthy(), incident: { status: 'verifying' } })), /复验中/);
  assert.match(text(renderPanel({ host: healthy(), incident: { status: 'recovered', verification: { passed: true, evidence_id: 'gate-1' } } })), /已恢复/);
});

test('record failures preserve exact batch and error without showing stale recovered status', () => {
  const tree = renderPanel({ host: healthy(), incident: { status: 'recovered', verification: { passed: true, evidence_id: 'old' } }, recordError: 'HTTP 503' });
  assert.match(text(tree), /记录不可用/);
  assert.match(text(tree), /批次「fixture」（workflows\/fixture）：运维记录读取失败：HTTP 503/);
  assert.doesNotMatch(text(tree), /已恢复|持续监测中/);
  assert.equal(nodes(tree).find(n => n.props.className === 'wf-operations-hint').props.role, 'alert');
});

test('flow removes redundant progress copy while preserving true errors and native hover graph', () => {
  assert.doesNotMatch(client, /进度更新：|正在读取进度…|pctAll|setLastRefresh/);
  assert.ok(client.includes("refreshError ? h('p', { role: 'alert'"));
  assert.ok(client.includes('flowFeedback.error && flowFeedback.message'));
  assert.ok(client.includes("h('title', null, nodeLabel)"));
  assert.match(client, /diagBanner\(selBatch, stage\),\s*h\(OperationsPanel/);
  assert.ok(client.includes("jget('/batches/' + encodeURIComponent(batchName) + '/operations'"));
  assert.ok(client.includes('generation.current++; controller.abort(); clearTimeout(timer)'));
});
