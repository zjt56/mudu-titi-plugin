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
  for (const [status, label] of Object.entries({ unclaimed: '未接手', queued: '排队', investigating: '调查', repairing: '修复', verifying: '复验', waiting_external: '待外部条件', cancelled: '已取消' })) {
    assert.equal(ctx.operationsView({ status }).label, label);
  }
  assert.equal(ctx.operationsView(null).label, '未接手');
  assert.equal(ctx.operationsView({ status: 'success', outcome: '已修复' }).label, '状态待确认');
  for (const verification of [null, 'passed', { passed: 'true' }, { status: 'success' }, { passed: false }]) {
    assert.equal(ctx.operationsView({ status: 'recovered', verification }).label, '复验待确认');
  }
  assert.equal(ctx.operationsView({ status: 'recovered', verification: { passed: true, evidence_id: 'gate-receipt' } }).label, '已恢复');
  assert.equal(ctx.operationsView({ status: 'repairing', verification: { passed: true } }).label, '修复');
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
  assert.equal(ctx.operationsView({ ...incident, status: 'claimed', events: [{ action: 'apply_repair' }] }).label, '修复');
  assert.equal(ctx.operationsView({ ...incident, status: 'claimed', events: [{ action: 'verify' }] }).label, '复验');
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
  assert.equal(view.label, '修复');
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

test('operations toolbar keeps title and controls together with status on the next row', () => {
  const toolbarStart = client.indexOf("className: 'wf-operations-toolbar'");
  const statusStart = client.indexOf("className: 'wf-operations-status'", toolbarStart);
  assert.ok(toolbarStart > 0 && statusStart > toolbarStart);
  const toolbar = client.slice(toolbarStart, statusStart);
  assert.match(toolbar, /flexWrap: 'nowrap'/);
  assert.match(toolbar, /'AI运维'/);
  assert.match(toolbar, /host\.enabled \? '关闭运维' : '启动运维'/);
  assert.match(toolbar, /setRefresh\(\(n\) => n \+ 1\)/);
  assert.match(toolbar, /'刷新'/);
  assert.doesNotMatch(toolbar, /OperationsStatus|T\.hint \}, service/);
  assert.match(client.slice(statusStart, statusStart + 750), /T\.hint \}, service/);
  assert.match(client.slice(statusStart, statusStart + 750), /OperationsStatus/);
});

test('panel omits analysis advice and shows automatic repair progress', () => {
  const panel = client.slice(start, end);
  assert.doesNotMatch(panel, /分析与建议|current\.incident\.findings/);
  assert.match(panel, /首个阻断异常在安全检查点接手/);
  assert.match(panel, /卡点自动接管、修复、复验并续跑/);
  assert.doesNotMatch(panel, /仅在普通自愈失败后接手|个批次，自愈进行中/);
});

test('panel is isolated beside diagnostics and has no repair or invented session navigation', () => {
  const panel = client.slice(start, end);
  assert.match(client, /diagBanner\(selBatch, stage\),\s*h\(OperationsPanel/);
  assert.match(panel, /jget\('\/batches\/' \+ encodeURIComponent\(batchName\) \+ '\/operations'/);
  assert.doesNotMatch(panel, /\/repair|jpost\(|window\.open|href:|location\./);
  assert.match(panel, /enabled: true, available: false, loaded: false/);
  assert.match(panel, /原有自愈不受影响/);
  assert.match(panel, /clipboard\.writeText\(view\.sessionId\)/);
  assert.match(panel, /generation\.current\+\+; controller\.abort\(\); clearTimeout\(timer\)/);
});
