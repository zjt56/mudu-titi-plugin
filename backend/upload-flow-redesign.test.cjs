// 上传页流程重设计的回归约束（静态断言，避免改动悄悄回退）。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const client = fs.readFileSync(path.resolve(__dirname, '..', 'lib/client.js'), 'utf8');
const server = fs.readFileSync(path.resolve(__dirname, '..', 'app/workbench/server.py'), 'utf8');

test('后端阶段卡复用契约顺序与 flow_done 口径，客户端无需维护第二份阶段顺序', () => {
  assert.match(server, /"flow_done":\s*flow_done/);
  assert.match(server, /STAGE_ORDER = \[s\.id for s in wf_contracts\.STAGES\]/);
  assert.match(server, /for sid in \(STAGE_ORDER or list\(stages\)\)/);
});

test('排队区只列处理中的试卷；内容审核与上传格式检查结束的从队列消失', () => {
  assert.match(client, /const pendingBatches = batches\.filter\(\(b\) => !b\.flow_done && !b\.uploaded(?: && !b\.queue_removed)?\)/);
  assert.match(client, /const finishedBatches = batches\.filter\(\(b\) => b\.flow_done && !b\.uploaded\)/);
  // 队列行遍历的是 pendingBatches，不是全部 batches。
  assert.match(client, /pendingBatches\.map\(\(b, i\)/);
});

test('上传后不再自动跳到审核页，改由醒目入口按需切换', () => {
  // onUploaded 只刷新计数，不再 setTab('review')。
  assert.match(client, /onUploaded: \(\) => \{ setUploadedTick\(\(x\) => x \+ 1\); \}/);
  assert.match(client, /onGoReview: \(\) => \{ setUploadedTick\(\(x\) => x \+ 1\); setTab\('review'\); \}/);
  // 醒目入口：已完成 N 份 + 前往人工确认按钮。
  assert.match(client, /已完成 ' \+ finishedBatches\.length/);
  assert.match(client, /前往人工确认 →/);
  assert.match(client, /const goReview = \(\) => \{ if \(props\.onGoReview\) props\.onGoReview\(\); \}/);
});

test('当前试卷内容审核与上传格式检查结束后自动切到下一份未结束的，流程图开始新流程', () => {
  assert.match(client, /pending\.some\(\(b\) => b\.name === cur\) \? cur : pending\[0\]\.name/);
  // 流程图只跟踪处理中的试卷。
  assert.match(client, /const selBatch = pendingBatches\.find\(\(b\) => b\.name === sel\) \|\| pendingBatches\[0\]/);
  // 无任务时仍显示完整中性流程图，已完成的确认入口保留在左侧。
  assert.match(client, /const isFlowOverview = !selBatch/);
  assert.match(client, /compactFlowStages\(sourceItems, isFlowOverview\)/);
  assert.doesNotMatch(client, /const flowDone =|!selBatch \? flowDone/);
  // 排队区常驻显示：即使没有处理中的试卷也保留这块区域和空状态，
  // 不因队列为空而整块收起；内容审核与上传格式检查结束的试卷仍不占用队列。
  assert.match(client, /排队区常驻显示/);
  assert.match(client, /const queue = h\('div', \{ style: \{ flex: 1, minHeight: 190/);
  assert.match(client, /pendingBatches\.length === 0\s*\n?\s*\? h\(EmptyState/);
  assert.doesNotMatch(client, /没有任何处理中试卷时整块排队区收起/);
  // 无阶段时不显示「0 / 0 阶段」进度条。
  assert.match(client, /items\.length \? h\('div', \{ style: \{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 7/);
});

test('流程图精修：紧凑两行节点名、方向箭头、已完成段用中性色、阶段进度', () => {
  assert.match(client, /const DONE = \{ completed: true, approved: true, skipped: true, completed_with_findings: true \}/);
  assert.match(client, /doneN \+ ' \/ ' \+ items\.length\)/);
  assert.match(client, /arrows\.map/);   // 方向箭头消除蛇形回绕的阅读歧义
  assert.match(client, /s\.line/); // 连线按状态取色：已走过/运行中/未走
  assert.match(client, /--wf-flow-line-live/); // 运行中连线流动虚线
  assert.match(client, /className: s\.live \? 'wf-flow-live'/);
  assert.match(client, /@keyframes wf-flow-dash/);
  // 十二类使用四行依赖布局，四路并行明确分叉，不再强制蛇形串行。
  // 行距与半径加大，避免纵向拥挤；仍保持同一四行依赖布局。
  assert.match(client, /const R = 11, CW = 84, ROW_H = 86/);
  // 单条蛇形连线：相邻节点两两相连，首尾相接成一条线。
  assert.match(client, /for \(let k = 0; k < grid\.length - 1; k \+= 1\)/);
  assert.match(client, /const lineFor = \(a, b, reached\)/);
  // 整图是带 viewBox 的自适应 SVG：随容器等比缩放，节点名在 SVG 内两行居中。
  assert.match(client, /viewBox: '0 0 ' \+ svgW/);
  // 自适应：不设固定宽高，随列宽拉伸，高度按比例自动。
  assert.match(client, /preserveAspectRatio: 'xMidYMid meet'/);
  assert.match(client, /style: \{ display: 'block', width: '100%', height: 'auto', aspectRatio: svgW \+ ' \/ ' \+ svgH \}/);
  assert.doesNotMatch(client, /maxWidth: svgW/);
  assert.match(client, /function flowLabelLines\(name\)/);
  assert.match(client, /flowLabelLines\(shortStageName\(it\)\)/);
  assert.match(client, /textAnchor: 'middle'/);
  // 上传页不再出现绿色完成态（与全局中性色板一致）。
  const uploadView = client.slice(client.indexOf('    function UploadView('), client.indexOf('    function statusText('));
  assert.doesNotMatch(uploadView, /state-success-primary/);
  // 线条/描边统一用浅色：已完成段是浅灰，不是黑色实线；节点描边也不再用黑色。
  assert.match(client, /--wf-flow-line:var\(--dsw-alias-border-l2\)/);
  assert.match(client, /--wf-flow-line-done:color-mix/);
  assert.match(client, /--wf-flow-ring-done:color-mix/);
  assert.match(client, /const doneLine = 'var\(--wf-flow-line-done\)'/);
  // 排队区选中行只留加深底色，不再套一圈黑色内描边。
  assert.doesNotMatch(client, /boxShadow: active \? 'inset 0 0 0 1px var\(--wf-sel-border\)'/);
  // 节点状态覆盖 blocked/failed 与 needs_review/invalidated，不只 completed。
  assert.match(client, /it\.status === 'blocked' \|\| it\.status === 'failed'/);
  assert.match(client, /it\.status === 'needs_review' \|\| it\.status === 'invalidated'/);
});

test('试卷审核列表：按最新时间在最上面，待人工确认仍标出', () => {
  assert.match(client, /function batchLatestTime\(b\)/);
  assert.match(client, /sort\(\(a, b\) => batchLatestTime\(b\) - batchLatestTime\(a\)\)/);
  assert.match(client, /待人工确认/);
  assert.match(server, /"created_at": manifest\.get\("created_at"\)/);
  assert.match(server, /"updated_at": manifest\.get\("updated_at"\)/);
});

test('减文案：上传页冗余小字说明与冗长按钮文字已移除', () => {
  // 页头说明、sectionNote、上传提示、拖放说明、流程说明、图例都已删。
  assert.doesNotMatch(client, /选填 · 留空将自动识别/);
  assert.doesNotMatch(client, /点击选择，或将文件拖放到此处/);
  assert.doesNotMatch(client, /文件名含“答案\/解析”的会自动识别为答案卷/);
  assert.doesNotMatch(client, /内容审核与上传格式检查结束后自动离开队列/);
  assert.doesNotMatch(client, /上传试卷后，这里显示从提取到内容审核与上传格式检查结束的流程/);
  assert.doesNotMatch(client, /选择一份试卷进入审核；也可重新标签/);
  assert.doesNotMatch(client, /上传并开始处理/);
  assert.doesNotMatch(client, /const legend = /);
  // 分区标题仍保留（只去掉附注）。
  assert.match(client, /sectionHead\('试卷信息'\)/);
  // 主按钮缩短为「上传」，但功能不变。
  assert.match(client, /busy \? TEXT\.uploading : '上传'\)/);
});

test('收次要按钮：审核卡片把重新标签/重新审核/删除批次收进「⋯」菜单，功能不丢', () => {
  assert.match(client, /const \[menuFor, setMenuFor\] = React\.useState\(''\)/);
  assert.match(client, /'⋯'/);
  // 三个动作仍在菜单里可点，且卡片外层只直接渲染「进入审核」一个按钮。
  assert.match(client, /\['重新标签', \(\) => doRerun\(b\.name, 'tag'\), false\]/);
  assert.match(client, /\['重新审核', \(\) => doRerun\(b\.name, 'review'\), false\]/);
  assert.match(client, /\[["']彻底删除["'], \(\) => openDelete\(b\.name\), true\]/);
  // 点遮罩收起菜单、不触发进入审核。
  assert.match(client, /position: 'fixed', inset: 0, zIndex: 40/);
  // 卡片上不再平铺三个次要按钮（不再有独立的「重新标签」按钮节点）。
  assert.doesNotMatch(client, /}, '重新标签'\)/);
});
