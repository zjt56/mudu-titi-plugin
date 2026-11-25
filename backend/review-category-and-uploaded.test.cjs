const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const client = fs.readFileSync(path.resolve(__dirname, '..', 'lib/client.js'), 'utf8');

function extractFn(name) {
  const re = new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n      \\}`);
  const match = client.match(re);
  assert.ok(match, `找不到函数 ${name}`);
  return vm.runInNewContext(match[0] + `\n${name}`);
}

test('batchCategory 准确区分试卷类与课时类（优先 source_type，缺失时按标题兜底）', () => {
  const batchCategory = extractFn('batchCategory');
  assert.equal(batchCategory({ paper: { source_type: '同步练习', title: '八上人教版课时A计划 Unit 3 课时1' } }), 'lesson');
  assert.equal(batchCategory({ paper: { source_type: '中考真题', title: '2025年安徽省初中学业水平考试英语' } }), 'exam');
  assert.equal(batchCategory({ paper: { source_type: '模拟卷', title: '合肥一模英语试卷' } }), 'exam');
  assert.equal(batchCategory({ paper: { source_type: '未提供', title: 'Unit 3 单元话题写作' } }), 'lesson');
  assert.equal(batchCategory({ name: '1726秋八上人教版课时A计划Unit3-第一课时', paper: { source_type: '' } }), 'lesson');
  assert.equal(batchCategory({ name: '2025期中英语测试卷', paper: { source_type: '' } }), 'exam');
});

test('isBatchUploaded 识别已上传与远端暂存待确认批次，未上传返回 false', () => {
  const isBatchUploaded = extractFn('isBatchUploaded');
  assert.equal(isBatchUploaded({ uploaded: true }), true);
  assert.equal(isBatchUploaded({ uploaded: false, upload_pending_confirmation: true }), true);
  assert.equal(isBatchUploaded({ stages: [{ id: 'upload', status: 'completed' }] }), true);
  assert.equal(isBatchUploaded({ upload_status: { receipt: { state: 'staged' } } }), true);
  assert.equal(isBatchUploaded({ upload_status: { receipt: { submitted_at: '2026-11-23T11:51:33+08:00' } } }), true);
  assert.equal(isBatchUploaded({ uploaded: false, upload_pending_confirmation: false, flow_done: true, stages: [{ id: 'upload', status: 'pending' }] }), false);
  assert.equal(isBatchUploaded(null), false);
});

test('matchReviewFilter 按全部、试卷类、课时类、已上传四个平铺分类准确筛选', () => {
  const batchCategory = extractFn('batchCategory');
  const isBatchUploaded = extractFn('isBatchUploaded');
  const re = /function matchReviewFilter\([^)]*\) \{[\s\S]*?\n      \}/;
  const match = client.match(re);
  assert.ok(match, '找不到函数 matchReviewFilter');
  const matchReviewFilter = vm.runInNewContext(match[0] + '\nmatchReviewFilter', { batchCategory, isBatchUploaded });

  const examUn = { name: '2025中考', paper: { source_type: '中考真题' }, uploaded: false };
  const examUp = { name: '2024中考', paper: { source_type: '中考真题' }, uploaded: true };
  const lessonUn = { name: '课时1', paper: { source_type: '同步练习' }, uploaded: false };
  const lessonUp = { name: '课时2', paper: { source_type: '同步练习' }, uploaded: false, upload_pending_confirmation: true };

  assert.equal(matchReviewFilter(examUn, 'all'), true);
  assert.equal(matchReviewFilter(lessonUp, 'all'), true);
  assert.equal(matchReviewFilter(examUn, 'exam'), true);
  assert.equal(matchReviewFilter(examUp, 'exam'), true);
  assert.equal(matchReviewFilter(lessonUn, 'exam'), false);
  assert.equal(matchReviewFilter(lessonUn, 'lesson'), true);
  assert.equal(matchReviewFilter(lessonUp, 'lesson'), true);
  assert.equal(matchReviewFilter(examUn, 'lesson'), false);
  assert.equal(matchReviewFilter(examUp, 'uploaded'), true);
  assert.equal(matchReviewFilter(lessonUp, 'uploaded'), true);
  assert.equal(matchReviewFilter(examUn, 'uploaded'), false);
  assert.equal(matchReviewFilter(lessonUn, 'uploaded'), false);
});

test('试卷审核采用简洁平铺筛选栏（全部 / 试卷类 / 课时类 / 已上传），移除顶部统计条与重启后端，卡片标绿已上传', () => {
  const filterIdx = client.indexOf('const reviewFilterBar = h(');
  assert.ok(filterIdx >= 0);
  const toolbarChunk = client.slice(filterIdx, filterIdx + 1200);
  assert.match(toolbarChunk, /TEXT\.reload/);
  assert.doesNotMatch(toolbarChunk, /BackendRestartControl/);
  assert.doesNotMatch(client, /'共 ' \+ baseList\.length \+ ' 份 · 未上传 '/);
  assert.match(client, /jget\('\/batches\?review_list=1'\)/);
  assert.match(toolbarChunk, /renderFilterChip\('all', '全部', baseList\.length\)/);
  assert.match(toolbarChunk, /renderFilterChip\('exam', '试卷类', examCount\)/);
  assert.match(toolbarChunk, /renderFilterChip\('lesson', '课时类', lessonCount\)/);
  assert.match(toolbarChunk, /renderFilterChip\('uploaded', '已上传', uploadedCount/);
  assert.doesNotMatch(client, /renderFilterGroup/);
  assert.match(client, /const uploadedCard = isBatchUploaded\(b\);/);
  assert.match(client, /uploadedCard \? \{ background: 'rgba\(34, 197, 94, 0\.06\)', borderColor: 'rgba\(34, 197, 94, 0\.24\)' \}/);
  assert.match(client, /uploadedCard[\s\S]*?background: '#16a34a'[\s\S]*?'已上传'[\s\S]*?: null,/);
  const cardChunk = client.slice(client.indexOf('const uploadedCard = isBatchUploaded(b);'), client.indexOf('const uploadedCard = isBatchUploaded(b);') + 1200);
  assert.doesNotMatch(cardChunk, /待人工确认/);
});

test('isBatchPendingInQueue 准确识别上传排队区未完成批次，且试卷审核 baseList 将其排除', () => {
  const isBatchUploaded = extractFn('isBatchUploaded');
  const re = /function isBatchPendingInQueue\([^)]*\) \{[\s\S]*?\n      \}/;
  const match = client.match(re);
  assert.ok(match, '找不到函数 isBatchPendingInQueue');
  const isBatchPendingInQueue = vm.runInNewContext(match[0] + '\nisBatchPendingInQueue', { isBatchUploaded });

  // 上传排队区处理中/排队中/卡住且未完成：应判定为 pending（不出现在试卷审核）
  assert.equal(isBatchPendingInQueue({ name: 'q1', flow_done: false, uploaded: false, queue_removed: false }), true);
  assert.equal(isBatchPendingInQueue({ name: 'q2', flow_done: false }), true);
  // 流程已完成：应出现在试卷审核
  assert.equal(isBatchPendingInQueue({ name: 'd1', flow_done: true, uploaded: false, queue_removed: false }), false);
  // 已上传或远端暂存待确认：应出现在试卷审核
  assert.equal(isBatchPendingInQueue({ name: 'u1', flow_done: false, uploaded: true }), false);
  assert.equal(isBatchPendingInQueue({ name: 'u2', flow_done: false, upload_pending_confirmation: true }), false);
  assert.equal(isBatchPendingInQueue({ name: 'u3', flow_done: false, upload_status: { receipt: { state: 'staged' } } }), false);
  // 显式移出排队区：不再处于排队区，不隐藏
  assert.equal(isBatchPendingInQueue({ name: 'r1', flow_done: false, uploaded: false, queue_removed: true }), false);
  // 阶段异常停滞且无活跃运行：透出到试卷卡片页标红展示，不隐藏在后台；若正在续跑则仍留在排队区
  assert.equal(isBatchPendingInQueue({ name: 'b1', flow_done: false, stage_blocked: true }), false);
  assert.equal(isBatchPendingInQueue({ name: 'b2', flow_done: false, stage_blocked: true, advance: { running: true } }), true);
  assert.equal(isBatchPendingInQueue(null), false);

  assert.match(client, /const baseList = \(batches \|\| \[\]\)\.filter\(\(b\) => !isBatchPendingInQueue\(b\)\)/);
  assert.match(client, /wf-card-anomaly/);
  assert.match(client, /setServerActiveWork\(Boolean\(d && d\.has_active_work\)\)/);
});
