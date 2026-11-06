const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
  chmodSync, copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync,
} = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { spawnSync } = require('node:child_process');

const script = join(__dirname, 'wf-router.sh');

function fixture(t, router) {
  const root = mkdtempSync(join(tmpdir(), 'wf-router-test-'));
  const plugin = join(root, 'plugin');
  mkdirSync(join(plugin, 'backend'), { recursive: true });
  mkdirSync(join(plugin, 'config'), { recursive: true });
  copyFileSync(script, join(plugin, 'backend', 'wf-router.sh'));
  writeFileSync(join(plugin, 'config', 'model-router.json'),
    typeof router === 'string' ? router : JSON.stringify(router));
  const home = join(root, 'dsh');
  mkdirSync(join(home, 'profiles', 'fake'), { recursive: true });
  writeFileSync(join(home, 'profiles', 'fake', 'cordis.patch.yml'),
    '- id: agent-default-model\n  config:\n    provider: magpie-responses\n    model: "imported/gpt-5.6-sol"\n');
  writeFileSync(join(home, '.credentials.yaml'),
    'DEEPSEEK_API_KEY: synthetic-test\nMAGPIE_GATEWAY_KEY: synthetic-test\n');
  const fake = join(root, 'fake-dsh');
  writeFileSync(fake, '#!/bin/sh\ncat >/dev/null\necho OK\n');
  chmodSync(fake, 0o755);
  const prompt = join(root, 'prompt.txt');
  writeFileSync(prompt, 'Stage: types\n\nReply with exactly: OK\n');
  const response = join(root, 'response.txt');
  const result = spawnSync('sh', [join(plugin, 'backend', 'wf-router.sh'), prompt, response], {
    encoding: 'utf8',
    env: { ...process.env, WF_PLUGIN_DIR: plugin, DSH_HOME: home, DSH_BIN: fake },
  });
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return { result, response };
}

test('router uses exactly the selected default model', t => {
  const { result, response } = fixture(t, {
    default: { provider: 'deepseek-official', model: 'deepseek-flash', profile: 'fake' },
    stages: {}, review: { rounds: {} },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /provider=deepseek-official model=deepseek-flash/);
  assert.equal(readFileSync(response, 'utf8'), 'OK\n');
});

test('router honours a selected third-party model without substituting', t => {
  const { result, response } = fixture(t, {
    default: { provider: 'magpie-responses', model: 'imported/gpt-6.1-sol', profile: 'fake' },
    stages: {}, review: { rounds: {} },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /provider=magpie-responses model=imported\/gpt-6\.1-sol/);
  assert.equal(readFileSync(response, 'utf8'), 'OK\n');
});

test('router fails instead of falling back when the default model is missing', t => {
  const { result, response } = fixture(t, {
    default: { provider: 'deepseek-official' },
    stages: {}, review: { rounds: {} },
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /no valid default provider\/model/);
  assert.throws(() => readFileSync(response, 'utf8'), { code: 'ENOENT' });
});

test('router fails instead of falling back when the config is unreadable', t => {
  const { result, response } = fixture(t, '{');
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /cannot read model-router.json/);
  assert.throws(() => readFileSync(response, 'utf8'), { code: 'ENOENT' });
});

test('router script has no hardcoded substitute model', () => {
  const text = readFileSync(script, 'utf8');
  assert.equal(text.includes('deepseek-v4-pro'), false);
});

test('plugin agent config requests high part concurrency without API-level throttling', () => {
  const config = JSON.parse(readFileSync(join(__dirname, 'local-agent.plugin.json'), 'utf8'));
  assert.equal(config.part_concurrency, 16);
  assert.equal(config.review_part_parallel, 16);
});
