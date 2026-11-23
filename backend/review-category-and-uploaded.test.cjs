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

test('matchReviewFilter 按全部、试卷类（全部/未上传/已上传）、课时类（全部/未上传/已上传）准确筛选', () => {
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
  assert.equal(matchReviewFilter(examUn, 'exam:all'), true);
  assert.equal(matchReviewFilter(examUp, 'exam:all'), true);
  assert.equal(matchReviewFilter(lessonUn, 'exam:all'), false);
  assert.equal(matchReviewFilter(examUn, 'exam:unuploaded'), true);
  assert.equal(matchReviewFilter(examUp, 'exam:unuploaded'), false);
  assert.equal(matchReviewFilter(examUp, 'exam:uploaded'), true);
  assert.equal(matchReviewFilter(examUn, 'exam:uploaded'), false);
  assert.equal(matchReviewFilter(lessonUn, 'lesson:all'), true);
  assert.equal(matchReviewFilter(lessonUp, 'lesson:all'), true);
  assert.equal(matchReviewFilter(examUn, 'lesson:all'), false);
  assert.equal(matchReviewFilter(lessonUn, 'lesson:unuploaded'), true);
  assert.equal(matchReviewFilter(lessonUp, 'lesson:unuploaded'), false);
  assert.equal(matchReviewFilter(lessonUp, 'lesson:uploaded'), true);
  assert.equal(matchReviewFilter(lessonUn, 'lesson:uploaded'), false);
});

test('试卷审核采用分组筛选栏（全部 + 试卷类/课时类各含全部/未上传/已上传），移除顶部统计条与重启后端，卡片标绿已上传', () => {
  const filterIdx = client.indexOf('const reviewFilterBar = h(');
  assert.ok(filterIdx >= 0);
  const toolbarChunk = client.slice(filterIdx, filterIdx + 1200);
  assert.match(toolbarChunk, /TEXT\.reload/);
  assert.doesNotMatch(toolbarChunk, /BackendRestartControl/);
  assert.doesNotMatch(client, /'共 ' \+ baseList\.length \+ ' 份 · 未上传 '/);
  assert.match(client, /jget\('\/batches\?review_list=1'\)/);
  assert.match(client, /renderFilterChip\('all', '全部', baseList\.length\)/);
  assert.match(client, /renderFilterGroup\('exam', '试卷类'/);
  assert.match(client, /renderFilterGroup\('lesson', '课时类'/);
  assert.match(client, /renderFilterChip\(groupKey \+ ':all', '全部'/);
  assert.match(client, /renderFilterChip\(groupKey \+ ':unuploaded', '未上传'/);
  assert.match(client, /renderFilterChip\(groupKey \+ ':uploaded', '已上传'/);
  assert.match(client, /const uploadedCard = isBatchUploaded\(b\);/);
  assert.match(client, /uploadedCard \? \{ background: 'rgba\(34, 197, 94, 0\.06\)', borderColor: 'rgba\(34, 197, 94, 0\.24\)' \}/);
  assert.match(client, /uploadedCard[\s\S]*?background: '#16a34a'[\s\S]*?'已上传'[\s\S]*?: null,/);
  const cardChunk = client.slice(client.indexOf('const uploadedCard = isBatchUploaded(b);'), client.indexOf('const uploadedCard = isBatchUploaded(b);') + 1200);
  assert.doesNotMatch(cardChunk, /待人工确认/);
});
