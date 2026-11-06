import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, rmSync, readFileSync, statSync, existsSync, writeFileSync, symlinkSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { __plugin } from '../lib/index.js';
import { createOCRSettings } from '../lib/ocr-settings.js';

async function fixture(t) {
  const directory = mkdtempSync(join(tmpdir(), 'wf-ocr-test-'));
  const calls = [];
  let poll = 0;
  const remote = createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = Buffer.concat(chunks);
    calls.push({ path: req.url, method: req.method, headers: req.headers, body });
    if (req.url === '/error/jobs') {
      res.statusCode = 403;
      res.end(JSON.stringify({ errorCode: 'QUOTA_EXCEEDED', errorMsg: 'Quota exceeded: synthetic-test-token https://service.example/jobs?token=synthetic-test-token', internal_debug: 'never expose this field' }));
    } else if (req.url === '/jobs' && req.method === 'POST') {
      res.end(JSON.stringify({ data: { jobId: 'synthetic-job' } }));
    } else if (req.url === '/jobs/synthetic-job') {
      poll++;
      res.end(JSON.stringify({ data: { state: poll % 2 ? 'running' : 'done', resultUrl: { jsonUrl: `http://127.0.0.1:${remote.address().port}/result.jsonl` } } }));
    } else {
      res.end(JSON.stringify({ result: { layoutParsingResults: [{ markdown: { text: '# Synthetic\nHello 123', images: {} }, outputImages: {} }] } }) + '\n');
    }
  });
  await new Promise(resolve => remote.listen(0, '127.0.0.1', resolve));
  const ocr = createOCRSettings({ stateDir: directory, env: {}, python: () => process.env.WF_PYTHON || 'python3' });
  let route;
  __plugin.registerRoutes({ effect: effect => effect(), webServer: { register: value => { route = value; return () => {}; } } }, async () => [], undefined, ocr);
  const server = createServer(route.handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => {
    for (const instance of [server, remote]) { instance.closeAllConnections(); await new Promise(resolve => instance.close(resolve)); }
    rmSync(directory, { recursive: true, force: true });
  });
  const origin = `http://127.0.0.1:${server.address().port}/wf-plugin/ocr`;
  const endpoint = `http://127.0.0.1:${remote.address().port}/jobs`;
  const post = (path, value) => fetch(origin + path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(value) });
  return { directory, calls, ocr, origin, endpoint, post };
}

test('OCR public config saves privately, clears, and uses live private path', async t => {
  const { directory, ocr, origin, endpoint, post } = await fixture(t);
  const initial = await fetch(origin);
  assert.equal(initial.headers.get('cache-control'), 'no-store');
  const initialConfig = (await initial.json()).config;
  assert.deepEqual(Object.keys(initialConfig).sort(), ['mode', 'enabled', 'endpoint', 'model', 'has_token', 'useDocOrientationClassify', 'useDocUnwarping', 'useChartRecognition', 'timeout_seconds', 'poll_interval_seconds'].sort());
  assert.equal(initialConfig.has_token, false);
  assert.equal(initialConfig.mode, 'auto');
  assert.equal(initialConfig.enabled, true);
  const runtimeBefore = ocr.runtimeEnv();
  assert.equal(existsSync(runtimeBefore.MUDU_PADDLE_OCR_CONFIG), false);
  const assistBefore = JSON.parse(readFileSync(runtimeBefore.MUDU_OCR_ASSIST_STATE, 'utf8'));
  assert.deepEqual(assistBefore, { mode: 'auto', enabled: true });
  const saved = await post('', { endpoint, token: 'synthetic-test-token', useChartRecognition: true, mode: 'on' });
  assert.equal(saved.status, 200);
  const body = await saved.text();
  assert.equal(body.includes('synthetic-test-token'), false);
  assert.equal(JSON.parse(body).config.has_token, true);
  assert.equal(JSON.parse(body).config.mode, 'on');
  assert.equal(JSON.parse(body).config.enabled, true);
  assert.deepEqual(JSON.parse(readFileSync(join(directory, 'ocr', 'assist.json'), 'utf8')), { mode: 'on', enabled: true });
  assert.equal(statSync(join(directory, 'ocr')).mode & 0o777, 0o700);
  for (const file of ['ocr-config.json', 'token']) assert.equal(statSync(join(directory, 'ocr', file)).mode & 0o777, 0o600);
  assert.equal(readFileSync(join(directory, 'ocr', 'ocr-config.json'), 'utf8').includes('synthetic-test-token'), false);
  assert.deepEqual(ocr.runtimeEnv(), runtimeBefore);
  const disabled = await post('', { mode: 'off' });
  assert.equal(disabled.status, 200);
  const disabledConfig = (await disabled.json()).config;
  assert.equal(disabledConfig.mode, 'off');
  assert.equal(disabledConfig.enabled, false);
  assert.deepEqual(JSON.parse(readFileSync(join(directory, 'ocr', 'assist.json'), 'utf8')), { mode: 'off', enabled: false });
  const cleared = await post('', { clear_token: true });
  assert.equal((await cleared.json()).config.has_token, false);
  const tested = await post('/test', { file_url: 'https://example.org/synthetic.png' });
  assert.equal((await tested.json()).code, 'missing_token');
});

test('draft URL and local multipart tests execute actual Python HTTP submit/poll/result without saving', async t => {
  const { directory, calls, ocr, endpoint, post } = await fixture(t);
  const draft = { endpoint, token: 'synthetic-test-token', poll_interval_seconds: 0.01, timeout_seconds: 10 };
  const urlResult = await post('/test', { config: draft, file_url: 'https://example.org/synthetic.png' });
  assert.equal(urlResult.status, 200);
  const result = await urlResult.json();
  assert.equal(result.ok, true);
  assert.equal(result.pages[0].text, '# Synthetic\nHello 123');
  assert.equal(result.job_id, 'synthetic-job');
  assert.deepEqual(JSON.parse(calls[0].body).optionalPayload, { useDocOrientationClassify: false, useDocUnwarping: false, useChartRecognition: false });
  assert.equal(calls[0].headers.authorization, 'Bearer synthetic-test-token');
  assert.equal(calls.at(-1).headers.authorization, undefined);
  const image = join(directory, 'synthetic.png');
  writeFileSync(image, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aRXYAAAAASUVORK5CYII=', 'base64'));
  const local = await post('/test', { ...draft, file_path: image });
  assert.equal(local.status, 200);
  assert.equal((await local.json()).ok, true);
  const upload = calls.findLast(call => call.method === 'POST');
  assert.match(upload.headers['content-type'], /multipart\/form-data/);
  assert.ok(upload.body.includes(readFileSync(image)));
  assert.equal(ocr.read().has_token, false);
  assert.equal(existsSync(join(directory, 'ocr', 'ocr-config.json')), false);
});

test('malformed unsafe and ambiguous inputs do not echo credentials or submit', async t => {
  const { calls, origin, post } = await fixture(t);
  for (const payload of [{ endpoint: 'http://public.example/jobs', token: 'synthetic-never-echo' }, { token: 'a\nb' }, { clear_token: true, token: 'synthetic-never-echo' }, { useChartRecognition: 'true' }, { enabled: 'yes' }, { mode: 'weird' }, { timeout_seconds: true }, { has_token: true }]) {
    const response = await post('', payload);
    assert.equal(response.status, 400);
    assert.equal((await response.text()).includes('synthetic-never-echo'), false);
  }
  for (const payload of [{ token: 'synthetic-never-echo' }, { token: 'synthetic-never-echo', file_path: '/missing.png', file_url: 'https://example.org/a.png' }, { config: {}, token: 'synthetic-never-echo', file_url: 'https://example.org/a.png' }]) {
    const response = await post('/test', payload);
    assert.equal(response.status, 400);
    assert.equal((await response.text()).includes('synthetic-never-echo'), false);
  }
  assert.equal((await fetch(origin + '/test')).status, 405);
  assert.equal((await fetch(origin, { method: 'POST', headers: { 'content-type': 'text/plain' }, body: '{}' })).status, 415);
  assert.equal((await fetch(origin, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{' })).status, 400);
  assert.equal(calls.length, 0);
});

test('HTTP failures retain bounded service details with token and URLs redacted', async t => {
  const { endpoint, post } = await fixture(t);
  const response = await post('/test', { endpoint: endpoint.replace('/jobs', '/error/jobs'), token: 'synthetic-test-token', file_url: 'https://example.org/synthetic.png' });
  assert.equal(response.status, 400);
  const raw = await response.text();
  assert.equal(raw.includes('synthetic-test-token'), false);
  assert.equal(raw.includes('https://'), false);
  assert.equal(raw.includes('never expose this field'), false);
  const result = JSON.parse(raw);
  assert.equal(result.http_status, 403);
  assert.equal(result.error_code, 'QUOTA_EXCEEDED');
  assert.match(result.error_message, /Quota exceeded/);
  assert.match(result.message, /Quota exceeded/);
});

test('private OCR state refuses repository paths and symlinked key files', async t => {
  const { directory } = await fixture(t);
  assert.throws(() => createOCRSettings({ stateDir: process.cwd() }), { code: 'invalid_config' });
  mkdirSync(join(directory, 'ocr'));
  const target = join(directory, 'elsewhere');
  writeFileSync(target, 'synthetic-never-read');
  symlinkSync(target, join(directory, 'ocr', 'token'));
  const ocr = createOCRSettings({ stateDir: directory, env: {} });
  assert.throws(() => ocr.read(), { code: 'storage_error' });
});

test('legacy boolean enabled maps to the matching on/off mode', async t => {
  const { directory, origin } = await fixture(t);
  mkdirSync(join(directory, 'ocr'), { recursive: true });
  const tokenFile = join(directory, 'ocr', 'token');
  writeFileSync(tokenFile, 'synthetic-legacy-token');
  writeFileSync(join(directory, 'ocr', 'ocr-config.json'), JSON.stringify({
    backend: 'paddle_jobs', token_file: tokenFile,
    endpoint: 'https://paddleocr.aistudio-app.com/api/v2/ocr/jobs', model: 'PaddleOCR-VL-1.6',
    enabled: false,
  }));
  const config = (await (await fetch(origin)).json()).config;
  assert.equal(config.mode, 'off');
  assert.equal(config.enabled, false);
  assert.equal(config.has_token, true);
});
