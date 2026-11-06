const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const client = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');

function harness(get) {
  const start = client.indexOf('      async function checkUpload(name) {');
  const end = client.indexOf('\n      const [menuFor,', start);
  assert.ok(start !== -1 && end !== -1);
  const code = client.slice(start, end);
  const context = vm.createContext({
    jget: get,
    uploadGeneration: { current: {} },
    uploadChecks: { current: {} },
    uploadStatesRef: { current: {} },
    setUploadState(name, value) { context.uploadStatesRef.current[name] = value; },
  });
  vm.runInContext(code, context);
  return context;
}

test('upload status reports progress, receipt and a subsequent failure independently', async () => {
  let status = { state: 'running', step: '准备上传' };
  const h = harness(async () => status);
  await h.checkUpload('one');
  assert.equal(h.uploadStatesRef.current.one.message, '准备上传');
  status = { state: 'accepted', receipt: { id: 'r1' } };
  await h.checkUpload('one');
  assert.match(h.uploadStatesRef.current.one.message, /等待远端确认/);
  assert.match(h.uploadStatesRef.current.one.message, /r1/);
  status = { state: 'failed', error: '闸门未通过' };
  await h.checkUpload('two');
  assert.match(h.uploadStatesRef.current.two.message, /闸门未通过/);
  assert.equal(h.uploadStatesRef.current.one.state, 'accepted');
  status = { state: 'idle' };
  await h.checkUpload('two');
  assert.equal(h.uploadStatesRef.current.two.message, '');
});

test('network loss and empty status after submission never imply success or enable retry', async () => {
  let status = { state: 'idle' };
  const h = harness(async () => { if (status instanceof Error) throw status; return status; });
  h.uploadStatesRef.current.one = { state: 'running', attempted: true };
  await h.checkUpload('one');
  assert.equal(h.uploadStatesRef.current.one.state, 'unknown');
  status = new Error('offline');
  await h.checkUpload('one');
  assert.equal(h.uploadStatesRef.current.one.state, 'unknown');
  assert.match(h.uploadStatesRef.current.one.message, /重连/);
  status = { state: 'accepted', receipt: 'receipt' };
  await h.checkUpload('one');
  assert.equal(h.uploadStatesRef.current.one.state, 'accepted');
});

test('stale checks cannot overwrite a new upload attempt', async () => {
  let resolve;
  const h = harness(() => new Promise((r) => { resolve = r; }));
  const pending = h.checkUpload('one');
  h.uploadGeneration.current.one = 1;
  h.uploadStatesRef.current.one = { state: 'running', attempted: true };
  resolve({ state: 'idle' });
  await pending;
  assert.equal(h.uploadStatesRef.current.one.state, 'running');
});

test('review renders per-batch feedback and gates submission until status is known', () => {
  assert.match(client, /role: upload\.state === 'failed'/);
  assert.match(client, /!\['idle', 'failed'\]\.includes\(upload\.state\)/);
  assert.match(client, /current\.state !== 'idle' && current\.state !== 'failed'/);
  assert.match(client, /setInterval\(\(\) => checkUpload\(name\), 3000\)/);
});
