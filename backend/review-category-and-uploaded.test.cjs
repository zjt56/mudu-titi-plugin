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

test('试卷审核工具栏在重启后端同行展示全部分类按钮（全部/试卷类/课时类），卡片仅对已上传标绿提示且不再展示待人工确认', () => {
  const toolbarIdx = client.indexOf('{ style: T.toolbarTitle }, TEXT.review');
  assert.ok(toolbarIdx >= 0);
  const toolbarChunk = client.slice(toolbarIdx, toolbarIdx + 360);
  assert.match(toolbarChunk, /categoryBar/);
  assert.match(toolbarChunk, /BackendRestartControl/);
  assert.match(client, /\['all', '全部', baseList\.length\]/);
  assert.match(client, /\['exam', '试卷类', examCount\]/);
  assert.match(client, /\['lesson', '课时类', lessonCount\]/);
  assert.match(client, /const uploadedCard = isBatchUploaded\(b\);/);
  assert.match(client, /uploadedCard \? \{ background: 'rgba\(34, 197, 94, 0\.06\)', borderColor: 'rgba\(34, 197, 94, 0\.24\)' \}/);
  assert.match(client, /uploadedCard[\s\S]*?background: '#16a34a'[\s\S]*?'已上传'[\s\S]*?: null,/);
  const cardChunk = client.slice(client.indexOf('const uploadedCard = isBatchUploaded(b);'), client.indexOf('const uploadedCard = isBatchUploaded(b);') + 1200);
  assert.doesNotMatch(cardChunk, /待人工确认/);
});
