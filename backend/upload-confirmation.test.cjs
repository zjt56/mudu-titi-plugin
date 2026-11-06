const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const client = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');
function harness(review, confirm, postError) {
  const start = client.indexOf('      async function doUpload(name) {');
  const end = client.indexOf('\n      // 重新执行', start);
  assert.ok(start >= 0 && end > start);
  const requests = [], prompts = [], alerts = [];
  const context = vm.createContext({
    window: { confirm: (text) => { prompts.push(text); return confirm; }, alert: (text) => alerts.push(text) },
    uploadStatesRef: { current: { batch: { state: 'idle', requiresUnreviewedConfirmation: review, unreviewedRevision: 'review-revision' } } },
    uploadGeneration: { current: {} },
    setUploadState(name, state) { context.uploadStatesRef.current[name] = state; },
    async jpost(url, body) { requests.push({ url, body }); if (postError) throw postError; },
    async checkUpload() {},
  });
  vm.runInContext(client.slice(start, end), context);
  return { context, requests, prompts, alerts };
}

test('unreviewed upload requires explicit confirmation and sends override only after consent', async () => {
  const cancelled = harness(true, false);
  await cancelled.context.doUpload('batch');
  assert.match(cancelled.prompts[0], /尚未完成审核/);
  assert.equal(cancelled.requests.length, 0);
  const accepted = harness(true, true);
  await accepted.context.doUpload('batch');
  assert.equal(accepted.requests.length, 1);
  assert.equal(accepted.requests[0].body.allow_unreviewed, true);
  assert.equal(accepted.requests[0].body.unreviewed_revision, 'review-revision');
});

test('reviewed upload omits override and unknown review state blocks submission', async () => {
  const reviewed = harness(false, true);
  await reviewed.context.doUpload('batch');
  assert.equal(reviewed.requests.length, 1);
  assert.equal(Object.hasOwn(reviewed.requests[0].body, 'allow_unreviewed'), false);
  const unknown = harness(undefined, true);
  await unknown.context.doUpload('batch');
  assert.equal(unknown.requests.length, 0);
  assert.equal(unknown.prompts.length, 0);
});

test('definitive rejected request permits status refresh instead of unknown submission', async () => {
  const error = Object.assign(new Error('审核状态已变化，请重新确认'), { httpStatus: 400 });
  const rejected = harness(true, true, error);
  await rejected.context.doUpload('batch');
  assert.match(rejected.alerts[0], /审核状态已变化/);
  assert.equal(rejected.context.uploadStatesRef.current.batch.state, 'checking');
});

test('upload notification exposes accessible live feedback', () => {
  assert.match(client, /role: 'dialog', 'aria-label': '上传通知'/);
  assert.match(client, /'aria-live': upload\.state/);
  assert.match(client, /查看上传通知/);
});
