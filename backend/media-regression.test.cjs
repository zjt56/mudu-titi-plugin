const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { Readable } = require('node:stream');
const root = path.resolve(__dirname, '..');
const client = fs.readFileSync(path.join(root, 'lib/client.js'), 'utf8');
const host = fs.readFileSync(path.join(root, 'lib/index.js'), 'utf8');
function mediaContext(fetch, timer = setTimeout) {
  const code = client.slice(client.indexOf('    function imageRequestUrl('), client.indexOf('    function PreviewImage('));
  return vm.runInNewContext(code + '\n({imageRequestUrl, fetchPreviewImage})', {
    window: { fetch }, AbortController, DOMException, URL, setTimeout: timer, clearTimeout,
  });
}
test('local images use lossless JSON transport instead of a binary bridge', async () => {
  let requested;
  const src = 'data:image/jpeg;base64,/9j/AA==';
  const media = mediaContext(async (url) => {
    requested = url;
    return { ok: true, json: async () => ({ src }), blob: () => { throw Error('binary must not be read'); } };
  });
  assert.equal(await media.fetchPreviewImage('/api/batches/p/media/a.jpeg', new AbortController().signal), src);
  assert.equal(requested, '/wf-plugin/api/batches/p/media/a.jpeg?format=data-url');
});
test('cancellation settles even when the desktop bridge ignores AbortSignal', async () => {
  const media = mediaContext(() => new Promise(() => {}));
  const controller = new AbortController();
  const pending = media.fetchPreviewImage('/api/batches/p/media/a.jpeg', controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
});
test('stalled image times out without blocking another image request', async () => {
  const media = mediaContext((url) => url.includes('stalled') ? new Promise(() => {}) :
    Promise.resolve({ ok: true, json: async () => ({ src: 'data:image/png;base64,AA==' }) }),
    (fn, ms) => setTimeout(fn, Math.min(ms, 20)));
  const pending = media.fetchPreviewImage('/api/batches/p/media/stalled.png', new AbortController().signal);
  assert.equal(await media.fetchPreviewImage('/api/batches/p/media/next.png', new AbortController().signal), 'data:image/png;base64,AA==');
  await assert.rejects(pending, /超时/);
});
test('invalid image payload fails locally', async () => {
  const media = mediaContext(async () => ({ ok: true, json: async () => ({ error: 'not found' }) }));
  await assert.rejects(media.fetchPreviewImage('/api/batches/p/media/a.jpeg', new AbortController().signal), /有效图片/);
});
test('health probe uses constant-time health route and releases its response', async () => {
  const code = host.slice(host.indexOf('async function probeBackend('), host.indexOf('function spawnBackend('));
  let released = false;
  const probe = vm.runInNewContext(code + '\nprobeBackend', {
    AbortController, setTimeout, clearTimeout, backendUrl: () => 'http://backend/',
    fetch: async (url) => { assert.equal(url, 'http://backend/api/health'); return { ok: true, body: { cancel: async () => { released = true; } } }; },
  });
  assert.equal(await probe(), true);
  assert.equal(released, true);
});
test('ready proxy skips full batch probes and handles an already-ended GET', async () => {
  const code = host.slice(host.indexOf('async function proxyToBackend('), host.indexOf('// 写入 router 时'));
  const proxy = vm.runInNewContext(code + '\nproxyToBackend', {
    Buffer, AbortController, setTimeout, clearTimeout, backend: { port: 8791, ready: true },
    probeBackend: async () => { await new Promise((resolve) => setImmediate(resolve)); return true; },
    fetch: async () => new Response(Buffer.from([255, 216, 255]), { headers: { 'content-type': 'image/jpeg', 'content-length': '3' } }),
  });
  const req = Readable.from([]);
  req.method = 'GET'; req.url = '/wf-plugin/api/batches/p/media/a.jpeg'; req.headers = {};
  req.resume();
  await new Promise((resolve) => req.once('end', resolve));
  let result;
  const res = { once() {}, removeListener() {}, writeHead(status, headers) { assert.equal(status, 200); assert.equal(headers['content-type'], 'image/jpeg'); }, end(data) { result = data; } };
  let timeout;
  try {
    await Promise.race([proxy(req, res, '/api/batches/p/media/a.jpeg'), new Promise((_, reject) => { timeout = setTimeout(() => reject(Error('proxy remained blocked')), 300); })]);
  } finally { clearTimeout(timeout); }
  assert.deepEqual([...result], [255, 216, 255]);
});
