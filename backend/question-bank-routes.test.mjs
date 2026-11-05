import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { __plugin } from '../lib/index.js';
import { createQuestionBankSettings } from '../lib/question-bank.js';

async function fixture(t) {
  const directory = mkdtempSync(join(tmpdir(), 'wf-bank-routes-'));
  const calls = [];
  const bank = createQuestionBankSettings({ stateDir: directory, env: {}, fetchImpl: async (url, options) => {
    calls.push({ url, method: options.method, redirect: options.redirect });
    assert.equal(options.headers.Authorization, 'Bearer synthetic-test-only');
    return { status: 200, redirected: false, body: { cancel() {} } };
  } });
  let route;
  __plugin.registerRoutes({ effect: (effect) => effect(), webServer: { register: (value) => { route = value; return () => {}; } } }, async () => [], bank);
  const server = createServer(route.handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); rmSync(directory, { recursive: true, force: true }); });
  const origin = `http://127.0.0.1:${server.address().port}/wf-plugin/question-bank`;
  return { origin, calls };
}

test('Host routes save and read public settings, and test only a catalog GET', async t => {
  const { origin, calls } = await fixture(t);
  const read = await fetch(origin);
  assert.equal(read.status, 200);
  assert.equal(read.headers.get('cache-control'), 'no-store');
  assert.equal((await read.json()).config.has_api_key, false);
  const response = await fetch(origin, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ base_url: 'https://bank.example/api', api_key: 'synthetic-test-only', subject_id: 'english' }) });
  assert.equal(response.status, 200);
  const body = await response.text();
  assert.equal(body.includes('synthetic-test-only'), false);
  assert.equal(JSON.parse(body).config.has_api_key, true);
  const tested = await fetch(origin + '/test', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
  assert.equal(tested.status, 200);
  assert.equal((await tested.json()).ok, true);
  assert.deepEqual(calls, [{ url: 'https://bank.example/api/catalog/knowledge-versions', method: 'GET', redirect: 'error' }]);
  const cleared = await fetch(origin, { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{"clear_api_key":true}' });
  assert.equal((await cleared.json()).config.has_api_key, false);
});

test('Host routes reject unsafe and malformed submissions without reflecting input', async t => {
  const { origin, calls } = await fixture(t);
  for (const body of ['{"api_key":"synthetic-never-echo"', JSON.stringify({ base_url: 'http://public.example/api', api_key: 'synthetic-never-echo' })]) {
    const response = await fetch(origin, { method: 'POST', headers: { 'content-type': 'application/json' }, body });
    assert.equal(response.status, 400);
    const raw = await response.text();
    assert.equal(raw.includes('synthetic-never-echo'), false);
    assert.equal(JSON.parse(raw).code, 'invalid_config');
  }
  const wrongType = await fetch(origin, { method: 'POST', headers: { 'content-type': 'text/plain' }, body: '{}' });
  assert.equal(wrongType.status, 415);
  const wrongMethod = await fetch(origin + '/test');
  assert.equal(wrongMethod.status, 405);
  assert.equal(calls.length, 0);
});
