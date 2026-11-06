const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const client = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');

test('prepare feedback shows blocking summary without dumping output', async () => {
  const start = client.indexOf('      async function checkUpload(name) {');
  const end = client.indexOf('\n      const [menuFor,', start);
  assert.ok(start >= 0 && end > start);
  const error = '上传准备未通过：3 项阻断错误\n- 缺少答案（2 项，示例单元：Q1、Q2）\n- 媒体无效（1 项）\n另有 200 项非阻断警告。';
  const context = vm.createContext({
    jget: async () => ({ state: 'failed', error, output: 'raw warning noise' }),
    uploadGeneration: { current: {} }, uploadChecks: { current: {} },
    uploadStatesRef: { current: {} },
    setUploadState(name, value) { context.uploadStatesRef.current[name] = value; },
  });
  vm.runInContext(client.slice(start, end), context);
  await context.checkUpload('batch');
  const message = context.uploadStatesRef.current.batch.message;
  assert.match(message, /缺少答案/);
  assert.match(message, /媒体无效/);
  assert.match(message, /200 项非阻断警告/);
  assert.doesNotMatch(message, /raw warning noise/);
});
