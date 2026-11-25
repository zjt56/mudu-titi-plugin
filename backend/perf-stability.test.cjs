'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { Readable } = require('node:stream');
const { EventEmitter } = require('node:events');

const root = path.resolve(__dirname, '..');
const client = fs.readFileSync(path.join(root, 'lib/client.js'), 'utf8');
const host = fs.readFileSync(path.join(root, 'lib/index.js'), 'utf8');
const ops = fs.readFileSync(path.join(root, 'lib/operations-agent.js'), 'utf8');

test('proxyToBackend does not mark backend unready or retry when client aborts mid-request', async () => {
  const code = host.slice(host.indexOf('async function proxyToBackend('), host.indexOf('// 写入 router 时'));
  let ensured = 0;
  let fetchAborted = false;
  const backend = { port: 8791, ready: true };
  const proxy = vm.runInNewContext(code + '\nproxyToBackend', {
    Buffer, AbortController, setTimeout, clearTimeout,
    backend,
    backendAuthHeaders: () => ({}),
    ensureBackend: async () => { ensured += 1; return { ok: true }; },
    fetch: async (_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => {
        fetchAborted = true;
        reject(new DOMException('aborted', 'AbortError'));
      });
    }),
  });
  const req = Readable.from([]);
  req.method = 'GET';
  req.url = '/wf-plugin/api/batches/p/units/u1';
  req.headers = {};
  req.resume();
  await new Promise((r) => req.once('end', r));

  const res = new EventEmitter();
  res.writableEnded = false;
  res.destroyed = false;
  res.writeHead = () => { throw new Error('must not write response after client abort'); };
  res.end = () => { throw new Error('must not end response after client abort'); };

  const task = proxy(req, res, '/api/batches/p/units/u1');
  setImmediate(() => { res.emit('close'); });
  await task;

  assert.equal(fetchAborted, true);
  assert.equal(backend.ready, true, 'client abort must not flip backend.ready=false');
  assert.equal(ensured, 0, 'client abort must not trigger ensureBackend');
});

test('proxyToBackend refreshes x-workbench-token on retry after backend restart or 401 rotation', async () => {
  const code = host.slice(host.indexOf('async function proxyToBackend('), host.indexOf('// 写入 router 时'));
  const seenTokens = [];
  const backend = { port: 8791, ready: true, token: 'old-tok' };
  let callCount = 0;
  const proxy = vm.runInNewContext(code + '\nproxyToBackend', {
    Buffer, AbortController, setTimeout, clearTimeout,
    backend,
    backendAuthHeaders: () => (backend.token ? { 'x-workbench-token': backend.token } : {}),
    ensureBackend: async () => {
      backend.token = 'new-tok';
      backend.ready = true;
      return { ok: true, token: backend.token };
    },
    fetch: async (_url, init) => {
      callCount += 1;
      seenTokens.push(init.headers['x-workbench-token']);
      if (callCount === 1) {
        return {
          status: 401,
          headers: new Map([['content-type', 'application/json']]),
          arrayBuffer: async () => Buffer.from('{"error":"缺少或无效的访问令牌"}'),
        };
      }
      return {
        status: 200,
        headers: new Map([['content-type', 'application/json']]),
        arrayBuffer: async () => Buffer.from('{"batches":[]}'),
      };
    },
  });

  const req = Readable.from([]);
  req.method = 'GET';
  req.url = '/wf-plugin/api/batches';
  req.headers = {};
  req.resume();
  await new Promise((r) => req.once('end', r));

  let statusCode = 0;
  let bodyOut = '';
  const res = new EventEmitter();
  res.writableEnded = false;
  res.destroyed = false;
  res.writeHead = (s) => { statusCode = s; };
  res.end = (b) => { bodyOut = String(b || ''); res.writableEnded = true; };

  await proxy(req, res, '/api/batches');

  assert.deepEqual(seenTokens, ['old-tok', 'new-tok']);
  assert.equal(statusCode, 200);
  assert.equal(bodyOut, '{"batches":[]}');
});

test('proxyToBackend uses extended timeout for uploads and delete/purge endpoints', () => {
  assert.match(host, /subPath === '\/api\/uploads'/);
  assert.match(host, /delete\|purge/);
  assert.match(host, /const upstreamTimeoutMs = isLongOp \? 120000 : 30000;/);
});

test('/workbench/status combines readiness and busy check into one /api/health request', () => {
  const chunk = host.slice(host.indexOf("sub === '/workbench/status'"), host.indexOf("sub === '/workbench/start'"));
  assert.match(chunk, /fetch\(backendUrl\(\) \+ 'api\/health'/);
  assert.doesNotMatch(chunk, /await backendBusy\(\)/);
});

test('IMG_CACHE uses true LRU eviction and revokes evicted blob URLs', async () => {
  const code = client.slice(client.indexOf('    function imageRequestUrl('), client.indexOf('    function PreviewImage('));
  const revoked = [];
  let seq = 0;
  const ctx = vm.runInNewContext(
    code + '\nIMG_CACHE_MAX = 2;\n({ fetchPreviewImage, IMG_CACHE, IMG_RESOLVED })',
    {
      window: {
        fetch: async () => ({
          ok: true,
          blob: async () => ({ type: 'image/png', id: ++seq }),
        }),
      },
      AbortController,
      DOMException,
      URL: {
        createObjectURL: (b) => 'blob:mock/' + b.id,
        revokeObjectURL: (u) => revoked.push(u),
      },
      setTimeout,
      clearTimeout,
    },
  );

  const b1 = await ctx.fetchPreviewImage('https://example.com/1.png');
  const b2 = await ctx.fetchPreviewImage('https://example.com/2.png');
  // Touch 1.png so 2.png becomes the least recently used.
  assert.equal(await ctx.fetchPreviewImage('https://example.com/1.png'), b1);
  await ctx.fetchPreviewImage('https://example.com/3.png');

  assert.equal(ctx.IMG_CACHE.has('https://example.com/1.png'), true, 'recently hit entry must stay cached');
  assert.equal(ctx.IMG_CACHE.has('https://example.com/2.png'), false, 'LRU entry must be evicted');
  assert.deepEqual(revoked, [b2], 'evicted blob URL must be revoked to prevent memory leaks');
});

test('jpost enforces timeout via AbortController so stalled writes do not hang UI buttons', async () => {
  const start = client.indexOf('    async function jpost(');
  const end = client.indexOf('    // 页面 origin', start);
  const code = client.slice(start, end);
  let aborted = false;
  const jpost = vm.runInNewContext(code + '\njpost', {
    API: '/wf-plugin/api',
    AbortController,
    setTimeout,
    clearTimeout,
    window: {
      fetch: (_url, init) => new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => {
          aborted = true;
          reject(new DOMException('aborted', 'AbortError'));
        });
      }),
    },
  });
  await assert.rejects(() => jpost('/batches/b1/advance', {}, { timeoutMs: 25 }), /超时/);
  assert.equal(aborted, true);
});

test('hidden/inactive polling guards are wired for OperationsPanel, UploadView, and BackendRestartControl', () => {
  assert.match(client, /function OperationsPanel\(\{ batchName, isActive = true \}\)/);
  assert.match(client, /if \(isActive === false\) return undefined;/);
  assert.match(client, /h\(OperationsPanel, \{ key: 'operations:' \+ \(selBatch \? selBatch\.name : ''\), batchName: selBatch \? selBatch\.name : '', isActive \}\)/);
  assert.match(client, /if \(typeof document === 'undefined' \|\| !document\.hidden\) await refresh\(\);/);
  assert.match(client, /h\(BackendRestartControl, \{ isActive: \(!props \|\| props\.isActive !== false\) && activeSection === 'models' \}\)/);
  assert.match(ops, /const MAX_SETTLED_RECORDS = 100;/);
});
