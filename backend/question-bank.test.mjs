import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createQuestionBankSettings } from '../lib/question-bank.js';

const SYNTHETIC_KEY = 'synthetic-test-only-token.123';
const repo = fileURLToPath(new URL('../', import.meta.url));
function fixture(t, options = {}) {
  const stateDir = fs.mkdtempSync(path.join(os.tmpdir(), 'question-bank-test-'));
  t.after(() => fs.rmSync(stateDir, { recursive: true, force: true }));
  return { stateDir, settings: createQuestionBankSettings({ stateDir, env: {}, ...options }) };
}
function noLeak(value, key = SYNTHETIC_KEY) {
  assert.equal(JSON.stringify(value).includes(key), false);
  assert.equal(Object.hasOwn(value, 'api_key'), false);
}
function invalid(settings, payload) {
  assert.throws(() => settings.save(payload), error => error.code === 'invalid_config' && !error.message.includes(SYNTHETIC_KEY));
}

test('defaults, persistence, private atomic files, public read and runtime env', t => {
  const { settings, stateDir } = fixture(t);
  assert.deepEqual(settings.read(), { base_url: 'http://127.0.0.1:8089/api', subject_id: 'english', timeout_seconds: 30, knowledge_version_id: '', has_api_key: false });
  const saved = settings.save({ base_url: 'https://question-bank.example/api', subject_id: 'math', timeout_seconds: 12.5, api_key: SYNTHETIC_KEY });
  noLeak(saved);
  assert.equal(saved.has_api_key, true);
  const again = createQuestionBankSettings({ stateDir, env: {} });
  assert.deepEqual(again.read(), saved);
  const runtime = again.runtimeEnv();
  assert.equal(runtime.MUDU_API_KEY, SYNTHETIC_KEY);
  assert.equal(path.isAbsolute(runtime.MUDU_UPLOAD_CONFIG), true);
  assert.equal(runtime instanceof Promise, false);
  const config = JSON.parse(fs.readFileSync(runtime.MUDU_UPLOAD_CONFIG, 'utf8'));
  assert.deepEqual(config, { base_url: 'https://question-bank.example/api', subject_id: 'math', timeout_seconds: 12.5, api_key_env: 'MUDU_API_KEY' });
  noLeak(config);
  const directory = path.join(stateDir, 'question-bank');
  assert.equal(fs.statSync(directory).mode & 0o777, 0o700);
  assert.deepEqual(fs.readdirSync(directory).sort(), ['api-key', 'upload-config.json']);
  for (const name of fs.readdirSync(directory)) assert.equal(fs.statSync(path.join(directory, name)).mode & 0o777, 0o600);
});

test('runtime config loads through the actual Python upload_client without a request', t => {
  const { settings } = fixture(t);
  const initial = settings.runtimeEnv();
  assert.equal(fs.existsSync(initial.MUDU_UPLOAD_CONFIG), true);
  settings.save({ knowledge_version_id: 'version-1', api_key: SYNTHETIC_KEY });
  const runtime = settings.runtimeEnv();
  const result = spawnSync('python3', ['-c', 'import json; from workflow.upload_client import load_config; print(json.dumps(load_config()))'], {
    cwd: path.join(repo, 'app'), encoding: 'utf8', env: { ...process.env, ...runtime },
  });
  assert.equal(result.status, 0, 'Python load_config must accept persisted config');
  const config = JSON.parse(result.stdout);
  assert.equal(config.api_key_env, 'MUDU_API_KEY');
  assert.equal(config.knowledge_version_id, 'version-1');
  noLeak(config);
  settings.save({ knowledge_version_id: '' });
  assert.equal(Object.hasOwn(JSON.parse(fs.readFileSync(runtime.MUDU_UPLOAD_CONFIG, 'utf8')), 'knowledge_version_id'), false);
});

test('blank key preserves; clear removes and persists; replacement never echoes', t => {
  const { settings, stateDir } = fixture(t, { env: { MUDU_API_KEY: SYNTHETIC_KEY, OTHER_SECRET: 'must-not-read' } });
  assert.equal(settings.read().has_api_key, true);
  settings.save({ api_key: 'replacement-synthetic-token' });
  const blank = settings.save({ api_key: '', subject_id: 'english-2' });
  noLeak(blank, 'replacement-synthetic-token');
  assert.equal(settings.runtimeEnv().MUDU_API_KEY, 'replacement-synthetic-token');
  settings.save({ clear_api_key: true, api_key: '' });
  assert.equal(settings.read().has_api_key, false);
  assert.equal(settings.runtimeEnv().MUDU_API_KEY, '');
  assert.equal(createQuestionBankSettings({ stateDir, env: { MUDU_API_KEY: SYNTHETIC_KEY } }).read().has_api_key, false);
  settings.save({ api_key: '' });
  assert.equal(settings.read().has_api_key, false);
  invalid(settings, { clear_api_key: true, api_key: SYNTHETIC_KEY });
});

test('rejects unsafe URLs before URL normalization and accepts literal loopback/HTTPS', t => {
  const { settings } = fixture(t);
  for (const base_url of [
    'http://example.com/api', 'http://127.1/api', 'http://2130706433/api', 'http://0x7f000001/api',
    'http://0177.0.0.1/api', 'http://127.0.0.01/api', 'http://LOCALHOST/api', 'http://localhost./api',
    'https://user:pass@example.com/api', 'https://example.com/api?', 'https://example.com/api#',
    'https://example.com/a/../api', 'https://example.com/./api', 'https://example.com/%2e%2e/api',
    'https://example.com/%61pi', 'https://example.com/a//api', 'https://example.com/api/',
    'https://example.com\\evil/api', 'https://example.com/api\n', ' https://example.com/api',
    'https://example.com:0/api', 'https://example.com:65536/api', 'ftp://example.com/api',
    'https:////example.com/api', 'https://example.com/api?token=' + SYNTHETIC_KEY,
    'http://[0:0:0:0:0:0:0:1]/api', 'http://[::ffff:127.0.0.1]/api',
  ]) invalid(settings, { base_url });
  for (const base_url of ['http://127.0.0.1:8089/api', 'http://localhost/api', 'http://[::1]:8089/api', 'https://example.com/prefix/api', 'https://EXAMPLE.com:443/api']) {
    assert.equal(settings.save({ base_url }).base_url, base_url);
  }
});

test('strict payload, identifiers, timeout and visible ASCII token validation', t => {
  const { settings } = fixture(t);
  for (const payload of [null, [], '', { extra: true }, { api_key_env: 'OTHER_SECRET' }, { has_api_key: true },
    { subject_id: '' }, { subject_id: '  ' }, { subject_id: 'x'.repeat(257) }, { subject_id: 'a\nb' },
    { knowledge_version_id: ' ' }, { timeout_seconds: 0 }, { timeout_seconds: 0.5 }, { timeout_seconds: 121 },
    { timeout_seconds: Infinity }, { timeout_seconds: NaN }, { timeout_seconds: '30' }, { timeout_seconds: true },
    { api_key: ' ' }, { api_key: 'a b' }, { api_key: 'a\nb' }, { api_key: 'é' }, { api_key: 'a'.repeat(4097) },
    { api_key: null }, { clear_api_key: 'true' }, { [Symbol('secret')]: true }]) invalid(settings, payload);
  for (const timeout_seconds of [1, 120]) assert.equal(settings.save({ timeout_seconds }).timeout_seconds, timeout_seconds);
  const value = settings.save({ api_key: 'a'.repeat(4096) });
  assert.equal(value.has_api_key, true);
  assert.equal(settings.runtimeEnv().MUDU_API_KEY.length, 4096);
});

test('only fixed private files are read; symlinks and repository state are refused', t => {
  const { settings, stateDir } = fixture(t, { env: { OTHER_SECRET: SYNTHETIC_KEY, MUDU_UPLOAD_CONFIG: '/must-not-be-read' } });
  assert.equal(settings.read().has_api_key, false);
  assert.throws(() => createQuestionBankSettings({ stateDir: path.join(repo, 'state') }), { code: 'invalid_config' });
  settings.save({});
  const keyPath = path.join(stateDir, 'question-bank', 'api-key');
  fs.symlinkSync('/must-not-be-read', keyPath);
  assert.throws(() => settings.read(), { code: 'storage_error' });
  fs.unlinkSync(keyPath);
  const configPath = settings.runtimeEnv().MUDU_UPLOAD_CONFIG;
  fs.writeFileSync(configPath, JSON.stringify({ api_key_env: 'OTHER_SECRET' }));
  assert.throws(() => settings.read(), { code: 'invalid_config' });
});

test('unsaved test makes exactly one catalog GET with bearer and redirect:error', async t => {
  const calls = [];
  const { settings } = fixture(t, { fetchImpl: async (...args) => { calls.push(args); return { status: 200 }; } });
  settings.save({ api_key: SYNTHETIC_KEY });
  const result = await settings.test({ base_url: 'https://synthetic.example/api', subject_id: 'new-subject', api_key: 'replacement-synthetic-token' });
  assert.deepEqual(result, { ok: true, message: 'Question-bank connection succeeded.' });
  noLeak(result, 'replacement-synthetic-token');
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], 'https://synthetic.example/api/catalog/knowledge-versions');
  const options = calls[0][1];
  assert.equal(options.method, 'GET');
  assert.equal(options.headers.Authorization, 'Bearer replacement-synthetic-token');
  assert.equal(options.redirect, 'error');
  assert.equal(options.signal instanceof AbortSignal, true);
  assert.equal(Object.hasOwn(options, 'body'), false);
  assert.equal(settings.read().subject_id, 'english');
  assert.equal(settings.runtimeEnv().MUDU_API_KEY, SYNTHETIC_KEY);
  await settings.test({ api_key: '' });
  assert.equal(calls[1][1].headers.Authorization, 'Bearer ' + SYNTHETIC_KEY);
});

test('HTTP/redirect/network failures are fixed bounded summaries; response text is never read', async t => {
  for (const status of [301, 302, 400, 401, 403, 429, 500]) {
    const { settings } = fixture(t, { fetchImpl: async () => ({ status, statusText: SYNTHETIC_KEY,
      text() { assert.fail('must not read response'); }, json() { assert.fail('must not read response'); } }) });
    settings.save({ api_key: SYNTHETIC_KEY });
    const result = await settings.test();
    assert.equal(result.ok, false);
    assert.equal(result.code, status < 400 ? 'unsafe_redirect' : 'http_error');
    assert.ok(result.message.length < 100);
    noLeak(result);
  }
  const { settings } = fixture(t, { fetchImpl: async () => { throw new Error('Bearer ' + SYNTHETIC_KEY); } });
  settings.save({ api_key: SYNTHETIC_KEY });
  const result = await settings.test();
  assert.equal(result.code, 'network_error');
  noLeak(result);
});

test('invalid or keyless test performs no network call; clear is unsaved', async t => {
  let calls = 0;
  const { settings } = fixture(t, { fetchImpl: async () => { calls++; return { status: 200 }; } });
  assert.equal((await settings.test()).code, 'missing_api_key');
  settings.save({ api_key: SYNTHETIC_KEY });
  assert.equal((await settings.test({ clear_api_key: true })).code, 'missing_api_key');
  assert.equal(settings.read().has_api_key, true);
  assert.equal((await settings.test({ base_url: 'http://remote.example/api' })).code, 'invalid_config');
  assert.equal(calls, 0);
});

test('storage rejects symlink repository paths and hard-linked key files', t => {
  const { stateDir, settings } = fixture(t);
  const alias = path.join(stateDir, 'repo-alias');
  fs.symlinkSync(repo, alias);
  const aliased = createQuestionBankSettings({ stateDir: path.join(alias, 'must-not-create') });
  assert.throws(() => aliased.save({ api_key: SYNTHETIC_KEY }), { code: 'storage_error' });
  assert.equal(fs.existsSync(path.join(repo, 'must-not-create')), false);
  settings.save({});
  const foreign = path.join(stateDir, 'unrelated-file');
  fs.writeFileSync(foreign, SYNTHETIC_KEY);
  fs.linkSync(foreign, path.join(stateDir, 'question-bank', 'api-key'));
  assert.throws(() => settings.read(), { code: 'storage_error' });
});

test('connection result never waits for a response body or returns reflected redirect data', async t => {
  const { settings } = fixture(t, { fetchImpl: async () => ({ status: 200, body: { cancel() { return new Promise(() => {}); } } }) });
  settings.save({ api_key: SYNTHETIC_KEY });
  assert.equal((await settings.test()).ok, true);
  const other = fixture(t, { fetchImpl: async () => ({ status: 200, redirected: true, url: SYNTHETIC_KEY }) }).settings;
  other.save({ api_key: SYNTHETIC_KEY });
  const result = await other.test();
  assert.equal(result.code, 'unsafe_redirect');
  noLeak(result);
});

test('timeout aborts the GET and does not expose transport errors', async t => {
  let signal;
  const { settings } = fixture(t, { fetchImpl: async (_url, options) => { signal = options.signal; return new Promise(() => {}); } });
  settings.save({ api_key: SYNTHETIC_KEY });
  const start = Date.now();
  const result = await settings.test({ timeout_seconds: 1 });
  assert.equal(result.code, 'timeout');
  assert.equal(signal.aborted, true);
  assert.ok(Date.now() - start < 3000);
  noLeak(result);
});
