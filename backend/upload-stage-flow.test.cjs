const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const client = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');

// Render only the existing upload component: no timers, requests, models or real batches.
function renderFlow(items, current = items[0], options = {}) {
  let stateIndex = 0;
  const batch = { name: 'fixture', paper: { title: '测试试卷' },
    flow_done: !!options.finished, advance: options.advance || {}, stage: { items, current, done: 0, total: items.length } };
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity).filter((x) => x != null && x !== false) }),
    useState(initial) { return [stateIndex++ === 5 ? [batch] : initial, () => {}]; },
    useRef: (current) => ({ current }),
    useEffect() {},
    useCallback: (callback) => callback,
  };
  const context = vm.createContext({
    require: (name) => { assert.equal(name, 'react'); return React; },
    formField: (label, control) => React.createElement('label', null, label, control),
    EmptyState() {}, uploadIcon() {}, docIcon() {}, diagBanner: () => null,
    jget: () => { throw new Error('No network in flow tests'); },
    jpost: () => { throw new Error('No network in flow tests'); },
  });
  vm.runInContext(client.slice(client.indexOf('    var TEXT ='), client.indexOf('    // 统一的线性图标')), context);
  const start = client.indexOf('    function UploadView(');
  // Keep the component plus its adjacent pure label helpers, excluding other views.
  const helpersEnd = client.indexOf('    function diagBanner(', start);
  assert.ok(start >= 0 && helpersEnd > start);
  vm.runInContext(client.slice(start, helpersEnd), context);
  return { tree: context.UploadView({}), context };
}

function walk(node, predicate, found = []) {
  if (!node || typeof node !== 'object') return found;
  if (predicate(node)) found.push(node);
  for (const child of node.children || []) walk(child, predicate, found);
  return found;
}
function text(node) {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  return (node && node.children || []).map(text).join(' ');
}

const ids = ['extract', 'structure_gate', 'preassemble', 'answers', 'audio', 'types',
  'explanations', 'knowledge', 'assemble', 'meta', 'underlines', 'validate', 'review',
  'review_gate', 'format_gate', 'upload_gate', 'upload', 'publish_gate'];
const stages = () => ids.map((id) => ({ id, status: 'pending' }));

test('main flow stays under ten nodes while retaining every actual substage', () => {
  const items = stages().concat([{ id: 'future_contract_stage', title: '契约扩展阶段', status: 'pending' }]);
  const { tree } = renderFlow(items);
  const svg = walk(tree, (node) => node.type === 'svg' && node.props.role === 'img')[0];
  const nodes = walk(svg, (node) => node.type === 'g');
  assert.deepEqual(nodes.map((node) => node.props.key), ['extract', 'preassemble', 'audio', 'answers', 'knowledge', 'assemble', 'review', 'format_gate', 'upload']);
  assert.equal(nodes.length, 9);
  assert.deepEqual(nodes.flatMap((node) => node.props['data-stage-ids'].split(' ')).sort(), items.map((item) => item.id).sort());
  const details = walk(tree, (node) => node.type === 'ul' && node.props['aria-label'] === '实际子步骤状态')[0];
  assert.deepEqual(details.children.map((node) => node.props.key), items.map((item) => item.id));
  assert.match(text(svg), /前置批次组装/);
  assert.match(text(svg), /填充汇总/);
  assert.match(text(svg), /上传格式检查/);
  assert.match(text(svg), /上传人工确认/);
  assert.match(text(svg), /契约扩展阶段/);
  assert.doesNotMatch(text(svg), /front_assemble/);
});

test('preassembly completion remains independent from fill summary and final format check', () => {
  const items = stages();
  items.find((item) => item.id === 'preassemble').status = 'completed';
  items.find((item) => item.id === 'answers').status = 'running';
  items.find((item) => item.id === 'audio').status = 'running';
  const { tree } = renderFlow(items, items.find((item) => item.id === 'answers'));
  const nodes = walk(tree, (node) => node.type === 'g');
  const node = (id) => nodes.find((entry) => entry.props.key === id);
  assert.match(text(node('preassemble')), /已完成/);
  assert.match(text(node('preassemble')), /✓/);
  for (const id of ['assemble', 'format_gate', 'upload']) {
    assert.match(text(node(id)), /待处理/);
    assert.doesNotMatch(text(node(id)), /✓/);
  }
  for (const id of ['answers', 'audio']) {
    assert.match(text(node(id)), /处理中/);
    assert.ok(walk(node(id), (entry) => entry.type === 'circle' && entry.props.r === 4).length);
  }
});

test('format gate failures remain visible after content review completes, with contract detail', () => {
  const items = stages();
  for (const item of items.slice(0, ids.indexOf('format_gate'))) item.status = 'completed';
  const format = items.find((item) => item.id === 'format_gate');
  Object.assign(format, { title: '最终本地上传格式检查', description: '核验冻结前的最终上传形状', status: 'failed', notes: 'knowledgeIds 类型错误' });
  const { tree } = renderFlow(items, format);
  const node = walk(tree, (entry) => entry.type === 'g' && entry.props.key === 'format_gate')[0];
  assert.match(text(node), /上传格式检查 · 失败/);
  assert.match(text(node), /最终本地上传格式检查：失败/);
  assert.match(text(node), /核验冻结前的最终上传形状/);
  assert.match(text(node), /knowledgeIds 类型错误/);
  assert.doesNotMatch(text(tree), /本轮流程已结束/);
});

test('stage labels honor authoritative backend titles and fallback labels cover new semantics', () => {
  const { context } = renderFlow([]);
  assert.equal(context.shortStageName({ id: 'assemble', title: '契约中的汇总名称' }), '契约中的汇总名称');
  assert.equal(context.shortStageName({ id: 'preassemble' }), '前置批次组装');
  assert.equal(context.shortStageName({ id: 'assemble' }), '填充汇总');
  assert.equal(context.shortStageName({ id: 'format_gate' }), '上传格式检查');
  assert.equal(context.shortStageName({ id: 'future' }), 'future');
});

test('workflow explains shared batch, parallel fill, placeholder preservation and final upload format check', () => {
  const { tree } = renderFlow(stages());
  const content = text(tree);
  assert.match(content, /识别与结构 → 前置组装 → 分项处理 → 内容审核 → 上传格式检查 → 进入人工审核/);
  assert.match(content, /主图合并为最多 9 个环节/);
  assert.match(content, /查看分项处理与状态/);
  assert.match(content, /06_batch\/batch\.json/);
  assert.match(content, /后续只填充同一个批次/);
  assert.match(content, /答案、音频、题型并行/);
  assert.match(content, /题型完成后，解析与文章大意、知识点并行/);
  assert.match(content, /确认缺失时沿用「无解析 \/ 未填写 \/ 未提供」/);
  assert.match(content, /不重新组题/);
  assert.match(content, /通过即打包成可上传批次并进入人工审核区/);
  assert.ok(walk(tree, (node) => node.type === 'details').length);
});

test('grouped status never completes while a real child is pending or failed', () => {
  const { context } = renderFlow([]);
  for (const [first, second, expected] of [
    ['completed', 'pending', 'pending'], ['running', 'pending', 'running'],
    ['completed', 'failed', 'failed'], ['running', 'blocked', 'blocked'],
    ['completed', 'needs_review', 'needs_review'], ['completed', 'skipped', 'completed'],
    ['skipped', 'skipped', 'skipped'],
  ]) {
    const grouped = context.compactFlowStages([{ id: 'types', status: first }, { id: 'knowledge', status: second }]);
    assert.equal(grouped.length, 1);
    assert.equal(grouped[0].status, expected);
  }
});

test('executed quality findings finish execution without implying quality approval', () => {
  const items = stages();
  for (const id of ['validate', 'review']) items.find((item) => item.id === id).status = 'completed';
  Object.assign(items.find((item) => item.id === 'review_gate'), {
    status: 'blocked', execution_completed: true, continue_unqualified: true,
    quality_status: 'failed', release_ready: false, notes: '18 项真实差异保留',
  });
  const { tree, context } = renderFlow(items, items.find((item) => item.id === 'format_gate'));
  const grouped = context.compactFlowStages(items).find((item) => item.id === 'review');
  assert.equal(grouped.status, 'completed_with_findings');
  const node = walk(tree, (entry) => entry.type === 'g' && entry.props.key === 'review')[0];
  assert.match(text(node), /已执行，有差异/);
  assert.match(text(node), /有质量差异（未放行）/);
  assert.match(text(node), /18 项真实差异保留/);
  assert.ok(node.children.some((entry) => entry.type === 'text' && text(entry) === '!'));
  assert.ok(!node.children.some((entry) => entry.type === 'text' && text(entry) === '✓'));
  items.find((item) => item.id === 'review_gate').continue_unqualified = false;
  assert.equal(context.compactFlowStages(items).find((item) => item.id === 'review').status, 'blocked');
});

test('queue progress counts the visible flow nodes, never the internal stage ledger', () => {
  // The diagram has at most nine nodes; a queue row must not print e.g. "16/18".
  const items = [];
  const ids = ['extract', 'structure_gate', 'preassemble', 'audio', 'answers', 'explanations',
    'types', 'knowledge', 'assemble', 'meta', 'underlines', 'validate', 'review', 'review_gate',
    'format_gate', 'upload_gate', 'upload', 'publish_gate'];
  for (const id of ids) items.push({ id, title: id, status: id === 'review' ? 'pending' : 'completed', kind: 'command' });
  items.push({ id: 'review', title: '内容审核', status: 'pending', kind: 'agent' });
  const html = renderFlow(items.slice(0, items.length - 1), items[0], { finished: false });
  const text = JSON.stringify(html);
  assert.doesNotMatch(text, /\b(1[0-9])\/18\b/);
  const queueText = client.slice(client.indexOf('function compactProgress'), client.indexOf('function shortStageName'));
  assert.match(queueText, /compactFlowStages/);
  assert.match(client, /const progress = compactProgress\(st\)/);
  assert.doesNotMatch(client, /const done = st\.done \|\| 0, total = st\.total \|\| 0/);
});

test('unknown configured stages affect a visible group without adding nodes', () => {
  const items = stages().concat(Array.from({ length: 12 }, (_, i) => ({ id: 'future-' + i, status: i === 5 ? 'failed' : 'completed' })));
  const { tree } = renderFlow(items, items[items.length - 1]);
  const svg = walk(tree, (node) => node.type === 'svg' && node.props.role === 'img')[0];
  const nodes = walk(svg, (node) => node.type === 'g');
  assert.equal(nodes.length, 9);
  assert.match(text(nodes.find((node) => node.props.key === 'upload')), /失败/);
  assert.match(text(svg), /future-5：失败/);
});

test('automatic repair is visible without adding workflow nodes or claiming success early', () => {
  const { tree, context } = renderFlow(stages(), null, { advance: { running: true, self_heal: { status: 'diagnosing', stage: 'format_gate' } } });
  const status = walk(tree, (node) => node.props.role === 'status')[0];
  assert.match(text(status), /AI 自动修复：正在判断卡点原因/);
  assert.equal(status.props['aria-live'], 'polite');
  const svg = walk(tree, (node) => node.type === 'svg' && node.props.role === 'img')[0];
  assert.equal(walk(svg, (node) => node.type === 'g').length, 9);
  assert.match(context.recoveryProgressText({ self_heal: { status: 'repaired' } }), /局部修复，继续校验/);
  assert.match(context.recoveryProgressText({ self_heal: { status: 'requeued', has_feedback: true } }), /受影响题目带反馈重试/);
  assert.match(context.recoveryProgressText({ self_heal: { status: 'failed' } }), /尚未修复/);
  assert.match(context.recoveryProgressText({ supervisor: { status: 'exhausted' } }), /稍后重试/);
  assert.match(context.recoveryProgressText({ self_heal: { status: 'verified' } }), /校验已通过/);
});

test('handoff completion describes content review plus upload format check', () => {
  const { tree } = renderFlow(stages(), null, { finished: true });
  assert.match(text(tree), /已完成内容审核与上传格式检查，等待人工确认/);
});
