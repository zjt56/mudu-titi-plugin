const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const client = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');

// Render only the existing upload component: no timers, requests, models or real batches.
function renderFlow(items, current = items[0], options = {}) {
  let stateIndex = 0;
  const callbacks = [], updates = [];
  const batch = { name: 'fixture', paper: { title: '测试试卷' },
    flow_done: !!options.finished, advance: options.advance || {}, stage: { items, current, done: 0, total: items.length } };
  const React = {
    createElement: (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity).filter((x) => x != null && x !== false) }),
    useState(initial) { const index = stateIndex++; return [index === 6 ? (options.batches || [batch]) : index === 11 ? (options.refreshError || initial) : initial, value => updates.push({ index, value })]; },
    useRef: (current) => ({ current }), useEffect() {},
    useCallback: (callback) => { callbacks.push(callback); return callback; },
  };
  const context = vm.createContext({
    require: (name) => { assert.equal(name, 'react'); return React; },
    formField: (label, control) => React.createElement('label', null, label, control),
    EmptyState() {}, uploadIcon() {}, docIcon() {}, diagBanner: () => null,
    jget: options.jget || (() => { throw new Error('No network in flow tests'); }),
    jpost: () => { throw new Error('No network in flow tests'); },
  });
  vm.runInContext(client.slice(client.indexOf('    var TEXT ='), client.indexOf('    // 统一的线性图标')), context);
  const start = client.indexOf('    function UploadView(');
  const helpersEnd = client.indexOf('    function diagBanner(', start);
  assert.ok(start >= 0 && helpersEnd > start);
  vm.runInContext(client.slice(start, helpersEnd), context);
  return { tree: context.UploadView({}), context, refresh: callbacks[0], updates };
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
function visibleText(node) {
  if (node && node.type === 'title') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  return (node && node.children || []).map(visibleText).join(' ');
}
const nodesOf = (tree) => walk(tree, node => node.type === 'g');
const nodeOf = (tree, id) => nodesOf(tree).find(node => node.props.key === id);
const tipOf = (node) => text(walk(node, entry => entry.type === 'title')[0]);
const ids = ['extract', 'structure_gate', 'preassemble', 'answers', 'audio', 'types',
  'explanations', 'knowledge', 'assemble', 'meta', 'underlines', 'validate', 'review',
  'review_gate', 'format_gate', 'upload_gate', 'upload', 'publish_gate'];
const stages = () => ids.map(id => ({ id, status: 'pending' }));
const classIds = ['extract', 'structure_gate', 'preassemble', 'audio', 'answers', 'explanations',
  'knowledge', 'assemble', 'validate', 'review', 'format_gate', 'upload'];
const classNames = ['内容提取', '结构确认', '组装判型', '听力处理', '答案填充', '解析填充',
  '知识标注', '汇总整理', '本地体检', '内容审核', '格式检查', '审核上传'];

test('main flow uses the approved twelve classes and retains every configured stage', () => {
  const items = stages().concat([{ id: 'future_contract_stage', title: '契约扩展阶段', status: 'pending' }]);
  const { tree } = renderFlow(items);
  const nodes = nodesOf(tree);
  assert.deepEqual(nodes.map(node => node.props.key), classIds);
  assert.equal(nodes.length, 12);
  assert.deepEqual(nodes.flatMap(node => node.props['data-stage-ids'].split(' ')).sort(), items.map(item => item.id).sort());
  assert.deepEqual(nodes.map(node => visibleText(node).trim()), classNames);
  assert.equal(walk(tree, node => node.type === 'details').length, 0);
  assert.doesNotMatch(text(tree), /查看分项处理与状态|06_batch\/batch\.json/);
});

test('chart connects the twelve nodes as one sequential snake line, 3 per row', () => {
  const { tree } = renderFlow(stages());
  const edges = walk(tree, node => node.props['data-flow-from']).map(node => [node.props['data-flow-from'], node.props['data-flow-to']]);
  // 首尾相接：相邻节点两两相连，共 11 段，恰为一条从头到尾的线。
  assert.deepEqual(edges, [
    ['extract', 'structure_gate'], ['structure_gate', 'preassemble'], ['preassemble', 'audio'],
    ['audio', 'answers'], ['answers', 'explanations'], ['explanations', 'knowledge'],
    ['knowledge', 'assemble'], ['assemble', 'validate'], ['validate', 'review'],
    ['review', 'format_gate'], ['format_gate', 'upload'],
  ]);
  assert.equal(edges.length, 11);
  const centers = classIds.map(id => walk(nodeOf(tree, id), node => node.type === 'circle' && node.props.fill !== 'none')[0].props);
  // 每行 3 个、共 4 行
  assert.equal(new Set(centers.map(c => c.cy)).size, 4);
  for (let r = 0; r < 4; r += 1) {
    assert.equal(centers.slice(r * 3, r * 3 + 3).filter(c => c.cy === centers[r * 3].cy).length, 3);
  }
  // 蛇形：奇偶行左右交替
  assert.ok(centers[0].cx < centers[2].cx);
  assert.ok(centers[3].cx > centers[5].cx);
});

test('tooltip expands backend substeps and preserves exact child statuses without permanent labels', () => {
  const items = [
    { id: 'extract', title: '原卷提取', status: 'completed', substeps: [
      { id: 'word', title: 'Word文字提取', status: 'completed' },
      { id: 'ocr', title: 'PDF识别', status: 'pending' },
      { id: 'media', title: '媒体索引', status: 'skipped' },
    ] },
    { id: 'preassemble', title: '前置组装', status: 'running', substeps: [{ id: 'split', title: '原卷切题', status: 'running' }] },
    { id: 'types', title: '题型交接', status: 'pending' },
    { id: 'review', title: '两轮审核', status: 'failed', description: '长描述不出现', notes: '长备注不出现', substeps: [
      { id: 'first', title: '首轮审核', status: 'failed' }, { id: 'second', title: '次轮审核', status: 'pending' },
    ] },
  ];
  const { tree, context } = renderFlow(items, items[1]);
  const extract = context.compactFlowStages(items).find(group => group.id === 'extract');
  assert.deepEqual(Array.from(extract.substeps, step => [step.id, step.title, step.status]), [
    ['word', 'Word文字提取', 'completed'], ['ocr', 'PDF识别', 'pending'], ['media', '媒体索引', 'skipped'],
  ]);
  assert.equal(extract.status, 'completed'); // UI does not invent a different backend stage verdict.
  assert.match(tipOf(nodeOf(tree, 'extract')), /Word文字提取 · 已完成\nPDF识别 · 待处理\n媒体索引 · 已跳过/);
  assert.doesNotMatch(tipOf(nodeOf(tree, 'extract')), /原卷提取/); // substeps replace the stage fallback.
  assert.match(tipOf(nodeOf(tree, 'preassemble')), /原卷切题 · 处理中\n题型交接 · 待处理/);
  assert.match(tipOf(nodeOf(tree, 'review')), /首轮审核 · 失败\n次轮审核 · 待处理/);
  assert.doesNotMatch(visibleText(tree), /Word文字提取|PDF识别|媒体索引|原卷切题|题型交接|首轮审核|次轮审核/);
  assert.doesNotMatch(text(tree), /长描述不出现|长备注不出现/);
});

test('nodes expose keyboard focus and accessible tooltip labels with authoritative substep titles', () => {
  const items = stages();
  items[0].substeps = [{ id: 'answers', title: '原卷答案段识别', status: 'pending' }];
  const { tree } = renderFlow(items);
  const svg = walk(tree, node => node.type === 'svg' && node.props.role === 'group')[0];
  assert.ok(svg); // A parent image role would flatten the individually named children.
  for (const node of nodesOf(tree)) {
    assert.equal(node.props.tabIndex, 0);
    assert.equal(node.props.role, 'img');
    assert.equal(node.props['aria-label'], tipOf(node));
  }
  const extract = nodeOf(tree, 'extract');
  assert.match(extract.props['aria-label'], /原卷答案段识别 · 待处理/);
  assert.doesNotMatch(extract.props['aria-label'], /答案填充/);
  assert.equal(visibleText(extract).trim(), '内容提取');
});

test('nonblocking warning substeps use localized tooltip text without changing stage completion', () => {
  const items = stages();
  Object.assign(items.find(item => item.id === 'validate'), { status: 'completed', substeps: [
    { id: 'format_hints', title: '格式表达检查', status: 'warning' },
  ] });
  const { tree, context } = renderFlow(items);
  const validate = nodeOf(tree, 'validate');
  assert.match(tipOf(validate), /格式表达检查 · 提示/);
  assert.doesNotMatch(tipOf(validate), /warning/);
  assert.equal(validate.props['aria-label'], tipOf(validate));
  assert.doesNotMatch(visibleText(validate), /格式表达检查|提示/);
  assert.equal(context.compactFlowStages(items).find(item => item.id === 'validate').status, 'completed');
});

test('missing empty or invalid substeps fall back to the original stage without fabricated completion', () => {
  const items = [
    { id: 'assemble', title: '填充汇总', status: 'completed' },
    { id: 'meta', title: '元数据', status: 'pending', substeps: [] },
    { id: 'underlines', title: '画线', status: 'blocked', substeps: [null] },
  ];
  const { tree, context } = renderFlow(items);
  assert.equal(context.compactFlowStages(items)[0].status, 'blocked');
  assert.match(tipOf(nodeOf(tree, 'assemble')), /填充汇总 · 已完成\n元数据 · 待处理\n画线 · 受阻/);
});

test('preassembly completion remains independent from parallel fills summary and local checks', () => {
  const items = stages();
  for (const id of ['preassemble', 'types']) items.find(item => item.id === id).status = 'completed';
  for (const id of ['answers', 'audio', 'explanations', 'knowledge']) items.find(item => item.id === id).status = 'running';
  const { tree, context } = renderFlow(items, items.find(item => item.id === 'answers'));
  assert.match(tipOf(nodeOf(tree, 'preassemble')), /组装判型 · 已完成/);
  assert.equal(context.compactProgress({ items }).done, 1);
  for (const id of ['assemble', 'validate', 'format_gate', 'upload']) {
    assert.match(tipOf(nodeOf(tree, id)), /待处理/);
    assert.doesNotMatch(visibleText(nodeOf(tree, id)), /✓/);
  }
  for (const id of ['answers', 'audio', 'explanations', 'knowledge']) {
    assert.ok(walk(nodeOf(tree, id), entry => entry.props.className === 'wf-node-pulse').length);
  }
});

test('grouped status never completes while a real stage is pending or failed', () => {
  const { context } = renderFlow([]);
  for (const [first, second, expected] of [
    ['completed', 'pending', 'pending'], ['running', 'pending', 'running'],
    ['completed', 'failed', 'failed'], ['running', 'blocked', 'blocked'],
    ['completed', 'needs_review', 'needs_review'], ['completed', 'skipped', 'completed'], ['skipped', 'skipped', 'skipped'],
  ]) {
    const group = context.compactFlowStages([{ id: 'preassemble', status: first }, { id: 'types', status: second }])[0];
    assert.equal(group.status, expected);
  }
});

test('format failures stay separate from completed content review', () => {
  const items = stages();
  for (const item of items.slice(0, ids.indexOf('format_gate'))) item.status = 'completed';
  const format = items.find(item => item.id === 'format_gate');
  Object.assign(format, { status: 'failed', notes: 'knowledgeIds 类型错误' });
  const { tree } = renderFlow(items, format);
  assert.match(tipOf(nodeOf(tree, 'format_gate')), /格式检查 · 失败/);
  assert.match(tipOf(nodeOf(tree, 'review')), /内容审核 · 已完成/);
  assert.doesNotMatch(text(tree), /knowledgeIds 类型错误|本轮流程已结束/);
});

test('executed findings finish execution without manufacturing quality approval', () => {
  const items = stages();
  items.find(item => item.id === 'review').status = 'completed';
  const gate = items.find(item => item.id === 'review_gate');
  Object.assign(gate, { status: 'needs_review', execution_completed: true, continue_unqualified: true, quality_status: 'failed', release_ready: false });
  const { tree, context } = renderFlow(items);
  const group = () => context.compactFlowStages(items).find(item => item.id === 'review');
  assert.equal(group().status, 'completed_with_findings');
  assert.match(tipOf(nodeOf(tree, 'review')), /内容审核 · 已执行，有差异/);
  assert.match(visibleText(nodeOf(tree, 'review')), /!/);
  assert.doesNotMatch(visibleText(nodeOf(tree, 'review')), /✓/);
  gate.continue_unqualified = false;
  assert.equal(group().status, 'needs_review');
  Object.assign(gate, { status: 'blocked', continue_unqualified: true });
  assert.equal(group().status, 'blocked');
});

test('active overrides inProgress including false and sibling failures preserve live activity', () => {
  const items = stages();
  Object.assign(items.find(item => item.id === 'review'), { status: 'running', active: true });
  items.find(item => item.id === 'review_gate').status = 'failed';
  Object.assign(items.find(item => item.id === 'extract'), { status: 'completed', active: true });
  Object.assign(items.find(item => item.id === 'answers'), { status: 'running', active: false, inProgress: true });
  Object.assign(items.find(item => item.id === 'explanations'), { status: 'pending', inProgress: true });
  Object.assign(items.find(item => item.id === 'knowledge'), { status: 'pending', active: false, inProgress: true });
  const { tree, context } = renderFlow(items, items.find(item => item.id === 'answers'));
  const groups = context.compactFlowStages(items);
  assert.equal(groups.find(item => item.id === 'review').status, 'failed');
  for (const id of ['review', 'extract', 'explanations']) {
    assert.equal(groups.find(item => item.id === id).inProgress, true);
    assert.ok(walk(nodeOf(tree, id), node => node.props.className === 'wf-node-pulse').length);
  }
  for (const id of ['answers', 'knowledge']) {
    assert.equal(groups.find(item => item.id === id).inProgress, false);
    assert.equal(walk(nodeOf(tree, id), node => node.props.className === 'wf-node-pulse').length, 0);
  }
  assert.equal(context.compactFlowStages(stages()).some(item => item.inProgress), false);
});

test('queue progress counts twelve visible classes rather than the eighteen-stage ledger', () => {
  const items = stages();
  for (const item of items) item.status = item.id === 'review' ? 'pending' : 'completed';
  const { context } = renderFlow(items);
  assert.deepEqual(JSON.parse(JSON.stringify(context.compactProgress({ items }))), { done: 11, total: 12 });
  assert.match(client, /const progress = compactProgress\(st\)/);
  assert.doesNotMatch(client, /const done = st\.done \|\| 0, total = st\.total \|\| 0/);
});

test('backend stage titles remain authoritative in tooltip fallbacks', () => {
  const { context } = renderFlow([]);
  assert.equal(context.shortStageName({ id: 'assemble', title: '契约中的汇总名称' }), '契约中的汇总名称');
  assert.equal(context.shortStageName({ id: 'preassemble' }), '前置批次组装');
  assert.equal(context.shortStageName({ id: 'format_gate' }), '上传格式检查');
  assert.equal(context.shortStageName({ id: 'future' }), 'future');
});

test('unknown configured stages affect their preceding visible class without adding nodes', () => {
  const items = stages().concat(Array.from({ length: 12 }, (_, i) => ({ id: 'future-' + i, status: i === 5 ? 'failed' : 'completed' })));
  const { tree } = renderFlow(items, items[items.length - 1]);
  assert.equal(nodesOf(tree).length, 12);
  assert.match(tipOf(nodeOf(tree, 'upload')), /失败/);
});

test('late polling responses cannot overwrite newer progress and errors remain visible', async () => {
  const replies = [];
  const f = renderFlow(stages(), undefined, { jget: () => new Promise((resolve, reject) => replies.push({ resolve, reject })) });
  const older = f.refresh(), newer = f.refresh();
  replies[1].resolve({ batches: [{ name: 'fresh' }] }); await newer;
  replies[0].resolve({ batches: [{ name: 'old' }] }); await older;
  assert.deepEqual(f.updates.filter(x => x.index === 6).map(x => x.value[0].name), ['fresh']);
  const failing = f.refresh(); replies[2].reject(new Error('offline')); await failing;
  assert.ok(f.updates.some(x => typeof x.value === 'string' && x.value.includes('进度刷新失败')));
});

test('flow omits redundant repair copy while preserving twelve classes and verification semantics', () => {
  const { tree, context } = renderFlow(stages(), null, { advance: { running: true, self_heal: { status: 'diagnosing', stage: 'format_gate' } } });
  assert.doesNotMatch(text(tree), /AI 自动修复：正在判断卡点原因|进度更新：/);
  assert.equal(nodesOf(tree).length, 12);
  assert.match(context.recoveryProgressText({ self_heal: { status: 'repaired' } }), /局部修复，继续校验/);
  assert.match(context.recoveryProgressText({ self_heal: { status: 'failed' } }), /尚未修复/);
  assert.match(context.recoveryProgressText({ self_heal: { status: 'verified' } }), /校验已通过/);
});

test('idle upload view retains all twelve neutral nodes and the same dependency arrows', () => {
  const { tree } = renderFlow([], null, { batches: [] });
  assert.deepEqual(nodesOf(tree).map(node => node.props.key), classIds);
  // 悬浮层显示分类名 + 子流程；首行是分类名，其余行以 · 列出子流程。
  assert.deepEqual(nodesOf(tree).map(node => tipOf(node).split('\n')[0]), classNames);
  assert.ok(nodesOf(tree).every(node => tipOf(node).split('\n').length >= 2));
  const svg = walk(tree, node => node.type === 'svg' && node.props.role === 'group')[0];
  assert.equal(svg.props['aria-label'], '试卷处理流程总览：12 个环节');
  assert.equal(walk(tree, node => node.props['data-flow-from']).length, 11);
  for (const node of nodesOf(tree)) {
    assert.equal(walk(node, entry => /wf-node-(pulse|dot)/.test(entry.props.className || '')).length, 0);
    assert.doesNotMatch(text(node), /✓|!|已完成|待处理|处理中|失败|受阻/);
    assert.equal(walk(node, entry => entry.type === 'circle' && entry.props.fill !== 'none')[0].props.stroke, 'var(--wf-flow-node-ring)');
  }
  assert.doesNotMatch(text(tree), /0 \/ 12|正在读取进度|继续处理|暂无进行中的流程|本轮流程已结束/);
});

test('finished batches leave a neutral demonstration chart and retain the review handoff', () => {
  const items = stages().map(item => ({ ...item, status: 'completed', active: true }));
  const { tree } = renderFlow(items, items[0], { finished: true, advance: { running: true } });
  assert.deepEqual(nodesOf(tree).map(node => tipOf(node).split('\n')[0]), classNames);
  assert.match(text(tree), /已完成 1 份 · 待人工确认/);
  assert.match(text(tree), /前往人工确认 →/);
  assert.doesNotMatch(text(tree), /12 \/ 12|继续处理|进度更新/);
  assert.equal(walk(tree, node => node.props.className === 'wf-node-pulse').length, 0);
});

test('overview tooltip lists each class substeps, not only the class name', () => {
  const { tree } = renderFlow([], null, { batches: [] });
  const tip = id => tipOf(nodeOf(tree, id));
  // 首行分类名，后续以 · 列出该分类包含的子流程。
  assert.match(tip('extract'), /^内容提取\n· 原卷提取$/);
  assert.match(tip('preassemble'), /组装判型\n· 前置批次组装\n· 题型填充/);
  assert.match(tip('upload'), /审核上传\n· .+\n· .+\n· /);
});

test('chart is width-adaptive: no fixed pixel size, svg stretches with its container', () => {
  const { tree } = renderFlow(stages());
  const svg = walk(tree, node => node.type === 'svg')[0];
  // 不允许出现固定像素宽高：宽度100%、高度按 viewBox 比例自动。
  assert.equal(svg.props.width, undefined);
  assert.equal(svg.props.height, undefined);
  assert.equal(svg.props.preserveAspectRatio, 'xMidYMid meet');
  assert.equal(svg.props.style.width, '100%');
  assert.equal(svg.props.style.height, 'auto');
  assert.match(svg.props.style.aspectRatio, /^\d+ \/ \d+$/);
  assert.equal(svg.props.style.maxWidth, undefined);
  // 节点与连线都留在同一 viewBox 坐标系内，缩放时整体等比，不会错位。
  assert.match(svg.props.viewBox, /^0 0 \d+ \d+$/);
  assert.equal(walk(tree, node => node.props['data-flow-from']).length, 11);
});

test('each node carries the three-state style tokens for completion, idle and running', () => {
  const items = stages();
  Object.assign(items.find(item => item.id === 'extract'), { status: 'completed', active: true });
  Object.assign(items.find(item => item.id === 'answers'), { status: 'running', active: true });
  const { tree } = renderFlow(items, items.find(item => item.id === 'answers'));
  const node = id => nodeOf(tree, id);
  const ring = id => walk(node(id), n => n.type === 'circle' && n.props.r === 11 && n.props.fill !== 'none')[0].props;
  // 已完成：实心浅底 + 灰勾
  assert.equal(ring('extract').fill, 'var(--wf-flow-node-done)');
  assert.match(visibleText(node('extract')), /✓/);
  // 运行中：呼吸圆点 + 脉冲外圈
  assert.equal(walk(node('answers'), n => n.props.className === 'wf-node-dot').length, 1);
  assert.equal(walk(node('answers'), n => n.props.className === 'wf-node-pulse').length, 1);
  // 未运行：空心浅底、无状态标记
  assert.equal(ring('structure_gate').fill, 'var(--wf-flow-node-idle)');
  assert.doesNotMatch(visibleText(node('structure_gate')), /✓|!/);
});

test('an idle read failure keeps the complete chart and displays the real error', () => {
  const { tree } = renderFlow([], null, { batches: [], refreshError: '进度刷新失败：offline' });
  assert.equal(nodesOf(tree).length, 12);
  assert.match(text(tree), /进度刷新失败：offline/);
});

test('active batches restore real progress and statuses after the neutral overview', () => {
  const items = stages();
  Object.assign(items.find(item => item.id === 'answers'), { status: 'running', active: true });
  const { tree } = renderFlow(items);
  assert.match(tipOf(nodeOf(tree, 'answers')), /答案填充 · 处理中/);
  assert.equal(walk(nodeOf(tree, 'answers'), node => node.props.className === 'wf-node-pulse').length, 1);
  assert.match(text(tree), /0 \/ 12|继续处理/);
});
