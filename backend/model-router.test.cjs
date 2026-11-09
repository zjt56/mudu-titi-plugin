const { test } = require('node:test');
const assert = require('node:assert/strict');
const {
  chmodSync, copyFileSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync,
} = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { spawnSync } = require('node:child_process');

const script = join(__dirname, 'wf-router.sh');

function fixture(t, router, promptText = 'Stage: types\n\nReply with exactly: OK\n') {
  const root = mkdtempSync(join(tmpdir(), 'wf-router-test-'));
  const plugin = join(root, 'plugin');
  mkdirSync(join(plugin, 'backend'), { recursive: true });
  mkdirSync(join(plugin, 'config'), { recursive: true });
  copyFileSync(script, join(plugin, 'backend', 'wf-router.sh'));
  // 路由脚本依赖同目录的 python 助手（route_pick / provider merge），测试要一起带上。
  for (const helper of readdirSync(__dirname)) {
    if (helper.endsWith('.py')) copyFileSync(join(__dirname, helper), join(plugin, 'backend', helper));
  }
  writeFileSync(join(plugin, 'config', 'model-router.json'),
    typeof router === 'string' ? router : JSON.stringify(router));
  const home = join(root, 'dsh');
  mkdirSync(join(home, 'profiles', 'fake'), { recursive: true });
  const fakePatch = '- id: llm-pi-ai\n  config:\n    providers:\n      magpie-responses:\n        models:\n          - id: "imported/gpt-5.6-sol"\n'
    + '- id: agent-default-model\n  config:\n    provider: magpie-responses\n    model: "imported/gpt-5.6-sol"\n';
  writeFileSync(join(home, 'profiles', 'fake', 'cordis.patch.yml'), fakePatch);
  // 每台设备自己的 Harness 默认模型（仓库不固定模型时跟随它）。
  mkdirSync(join(home, 'profiles', 'desktop'), { recursive: true });
  writeFileSync(join(home, 'profiles', 'desktop', 'cordis.patch.yml'), fakePatch);
  writeFileSync(join(home, '.credentials.yaml'),
    'DEEPSEEK_API_KEY: synthetic-test\nMAGPIE_GATEWAY_KEY: synthetic-test\n');
  const fake = join(root, 'fake-dsh');
  writeFileSync(fake, '#!/bin/sh\ncat >/dev/null\necho OK\n');
  chmodSync(fake, 0o755);
  const prompt = join(root, 'prompt.txt');
  writeFileSync(prompt, promptText);
  const response = join(root, 'response.txt');
  const result = spawnSync('sh', [join(plugin, 'backend', 'wf-router.sh'), prompt, response], {
    encoding: 'utf8',
    env: { ...process.env, WF_PLUGIN_DIR: plugin, DSH_HOME: home, DSH_BIN: fake },
  });
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return { result, response, home, plugin };
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

test('Chinese self-heal prompt retains its explicit selected route at a multibyte truncation boundary', t => {
  const { result, response } = fixture(t, {
    default: { provider: 'magpie-responses', model: 'default-only', profile: 'fake' },
    stages: { self_heal: { provider: 'deepseek-official', model: 'deepseek-flash', profile: 'fake' } },
    review: { rounds: {} },
  }, 'Stage: self_heal\n' + '根据原文判断自动修复动作。'.repeat(1000));
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /stage=self_heal.*provider=deepseek-official model=deepseek-flash/);
  assert.doesNotMatch(result.stderr, /illegal byte sequence/);
  assert.equal(readFileSync(response, 'utf8'), 'OK\n');
});

test('router script has no hardcoded substitute model', () => {
  const text = readFileSync(script, 'utf8');
  assert.equal(text.includes('deepseek-v4-pro'), false);
});

test('plugin agent config 按本地资源保守设置并发，不按 API 限流', () => {
  const config = JSON.parse(readFileSync(join(__dirname, 'local-agent.plugin.json'), 'utf8'));
  // 每个分片都是独立 dsh 进程；16 路在 16GB 机器上曾把整机压到内存紧张并导致宿主重启，
  // 因此改为 4。这是本地资源保护，不是按 provider 速率上限做 API 限流。
  assert.equal(config.part_concurrency, 4);
  assert.equal(config.review_part_parallel, 4);
  assert.match(config._concurrency_note || '', /内存/);
});

test('router follows the device Harness model when no plugin default is pinned', t => {
  // 仓库不固定模型：default 为空时跟随本机 Harness 的 agent-default-model。
  const { result, response } = fixture(t, {
    default: {}, stages: {}, review: { rounds: {} },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /provider=magpie-responses model=imported\/gpt-5\.6-sol/);
  assert.equal(readFileSync(response, 'utf8'), 'OK\n');
});
