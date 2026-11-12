const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const client = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');
function helpers() {
  const ctx = vm.createContext({ setTimeout, clearTimeout });
  vm.runInContext(client.slice(client.indexOf('    async function requestAdvance('), client.indexOf('    function recoveryProgressText(')), ctx);
  return ctx;
}
function handler(post, get = async () => ({}), refresh = async () => {}) {
  const ctx = helpers();
  let feedback = {}, batches = [{ name: '甲卷', advance: {} }], advancing = '';
  const update = (old, value) => typeof value === 'function' ? value(old) : value;
  Object.assign(ctx, { advanceRequests: { current: new Set() }, aliveRef: { current: true },
    setAdvancing(value) { advancing = update(advancing, value); },
    setAdvanceFeedback(value) { feedback = update(feedback, value); },
    setBatches(value) { batches = update(batches, value); }, jpost: post, jget: get, refresh });
  const start = client.indexOf('      async function advance(name)');
  vm.runInContext(client.slice(start, client.indexOf('      // 删除（继续处理区）', start)), ctx);
  return { ctx, state: () => ({ feedback, batches, advancing }) };
}

test('waiting and queued receipts explain why work has not started', () => {
  const ctx = helpers();
  assert.match(ctx.advanceReceiptFeedback({ started: false, waiting: true, waiting_for: '前卷' }).message, /前卷/);
  assert.match(ctx.advanceReceiptFeedback({ started: true, queued: true, running: false }).message, /队列/);
  assert.match(ctx.advanceReceiptFeedback({ external: true, running: true }).message, /另一执行器/);
  assert.match(ctx.advanceReceiptFeedback({ running: true }).message, /已在处理/);
});
test('HTTP 200 rejection is an error, never assumed started', () => {
  const ctx = helpers();
  assert.equal(ctx.advanceReceiptFeedback({ started: false, error: 'stale_operations_identity' }).error, true);
  assert.equal(ctx.advanceReceiptFeedback({ started: false, reason: '拒绝启动' }).message, '拒绝启动');
  assert.equal(ctx.advanceReceiptFeedback({}).error, true);
});
test('queued click immediately updates batch and refreshes, without running-only polling', async () => {
  let posts = 0, reads = 0, refreshes = 0;
  const h = handler(async () => { posts++; return { started: true, queued: true, running: false }; },
    async () => { reads++; }, async () => { refreshes++; });
  await h.ctx.advance('甲卷');
  assert.equal(posts, 1); assert.equal(reads, 0); assert.equal(refreshes, 1);
  assert.equal(h.state().batches[0].advance.queued, true);
  assert.match(h.state().feedback['甲卷'].message, /队列/);
  assert.equal(h.state().advancing, '');
});
test('rapid double click sends one request and submission has immediate visible feedback', async () => {
  let release, posts = 0;
  const h = handler(() => { posts++; return new Promise(resolve => { release = resolve; }); });
  const first = h.ctx.advance('甲卷');
  assert.match(h.state().feedback['甲卷'].message, /提交/);
  await h.ctx.advance('甲卷');
  assert.equal(posts, 1);
  release({ started: false, waiting: true, waiting_for: '前卷' }); await first;
  assert.match(h.state().feedback['甲卷'].message, /前卷/);
});
test('request and worker errors appear in batch feedback beside flow', async () => {
  const h = handler(async () => { throw Object.assign(new Error('服务不可用'), { httpStatus: 503 }); });
  await h.ctx.advance('甲卷');
  assert.match(h.state().feedback['甲卷'].message, /服务不可用/);
  assert.equal(h.state().feedback['甲卷'].error, true);
  const ctx = helpers();
  assert.match(ctx.advanceProgressFeedback({ result: { error: '执行锁失败' } }).message, /执行锁失败/);
  assert.equal(ctx.advanceProgressFeedback({ result: { status: 'blocked', stopped_at: 'answers', reason: '来源不符' } }).error, true);
  assert.match(ctx.advanceProgressFeedback({ result: { reason: '审核差异', status: 'needs_review' } }).message, /审核差异/);
  assert.match(client, /role: flowFeedback.error \? 'alert' : 'status'/);
});
test('live queue state wins over stale previous worker error', () => {
  const ctx = helpers();
  const feedback = ctx.advanceProgressFeedback({ queued: true, result: { error: '旧错误' } });
  assert.match(feedback.message, /队列/); assert.notEqual(feedback.error, true);
});
test('lost response reconciles read-only state without duplicate POST', async () => {
  const ctx = helpers(); let posts = 0, reads = 0;
  const result = await ctx.requestAdvance('甲卷', async () => { posts++; throw new Error('连接中断'); },
    async path => { reads++; assert.match(path, /pipeline$/); return { queued: true }; });
  assert.equal(result.queued, true); assert.equal(posts, 1); assert.equal(reads, 1);
});
test('hanging POST has bounded wait and reads pipeline before reporting unknown', async () => {
  const ctx = helpers(); let reads = 0;
  const result = await ctx.requestAdvance('甲卷', () => new Promise(() => {}),
    async () => { reads++; return { running: true }; }, 5);
  assert.equal(result.running, true); assert.equal(reads, 1);
});
test('unknown submission is reported honestly without automatic retry', async () => {
  const ctx = helpers(); let posts = 0;
  await assert.rejects(ctx.requestAdvance('甲卷', async () => { posts++; throw new Error('断线'); }, async () => ({ running: false })), /结果未确认.*未自动重复提交/);
  assert.equal(posts, 1);
});
test('unmount prevents delayed request from writing view state', async () => {
  let release;
  const h = handler(() => new Promise(resolve => { release = resolve; }));
  const task = h.ctx.advance('甲卷'); h.ctx.aliveRef.current = false;
  const original = h.state().feedback;
  release({ queued: true }); await task;
  assert.equal(h.state().feedback, original);
  assert.equal(h.ctx.advanceRequests.current.size, 0);
});
