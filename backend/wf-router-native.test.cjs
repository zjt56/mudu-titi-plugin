const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.resolve(__dirname, '..');
const router = path.join(__dirname, 'wf-router.mjs');

function fixture(t, promptText = 'Stage: answers\nAgent: answer\n') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-router-native-'));
  const plugin = path.join(dir, 'plugin');
  const home = path.join(dir, 'dsh');
  fs.mkdirSync(path.join(plugin, 'config'), { recursive: true });
  fs.mkdirSync(path.join(home, 'profiles', 'fake'), { recursive: true });
  fs.writeFileSync(path.join(plugin, 'config', 'model-router.json'), JSON.stringify({
    default: { provider: 'deepseek-official', model: 'deepseek-flash', profile: 'fake' },
    stages: {}, roles: {}, review: { rounds: {} },
  }));
  fs.writeFileSync(path.join(home, 'profiles', 'fake', 'cordis.patch.yml'),
    '- id: agent-default-model\n  config:\n    provider: old\n    model: old\n    reasoningEffort: medium\n');
  fs.mkdirSync(path.join(home, 'profiles', 'headless'), { recursive: true });
  fs.writeFileSync(path.join(home, 'profiles', 'headless', 'cordis.patch.yml'), '[]\n');
  // Use a platform-native fake entry so the routing logic is testable everywhere.
  const isWin = process.platform === 'win32';
  const dsh = path.join(dir, isWin ? 'fake-dsh.cmd' : 'fake-dsh');
  if (isWin) fs.writeFileSync(dsh, '@echo off\r\necho OK\r\n');
  else { fs.writeFileSync(dsh, '#!/bin/sh\ncat >/dev/null\necho OK\n'); fs.chmodSync(dsh, 0o755); }
  const prompt = path.join(dir, 'prompt.txt');
  const response = path.join(dir, 'response.txt');
  fs.writeFileSync(prompt, promptText, 'utf8');
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return { dir, plugin, home, dsh, prompt, response };
}

test('native router runs a Windows cmd dsh entry and writes the response file', t => {
  const f = fixture(t);
  const result = spawnSync(process.execPath, [router, f.prompt, f.response], {
    encoding: 'utf8', env: { ...process.env, WF_PLUGIN_DIR: f.plugin, DSH_HOME: f.home, DSH_BIN: f.dsh },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(f.response, 'utf8').replace(/\r\n/g, '\n'), 'OK\n');
  const routeProfile = fs.readdirSync(path.join(f.home, 'profiles')).find(name => name.startsWith('wf-plugin-route-'));
  assert.ok(routeProfile);
  const patch = fs.readFileSync(path.join(f.home, 'profiles', routeProfile, 'cordis.patch.yml'), 'utf8');
  assert.match(patch, /provider: "deepseek-official"/);
  assert.match(patch, /model: "deepseek-flash"/);
  assert.match(patch, /reasoningEffort: null/);
  assert.doesNotMatch(patch, /reasoningEffort: medium/);
});

test('native router honours stages.review when no round or role override exists', t => {
  const f = fixture(t, 'Stage: review\nROUND: labels\nAgent: review\n');
  const cfg = JSON.parse(fs.readFileSync(path.join(f.plugin, 'config', 'model-router.json'), 'utf8'));
  cfg.stages = { review: { provider: 'deepseek-official', model: 'deepseek-flash', profile: 'fake' } };
  fs.writeFileSync(path.join(f.plugin, 'config', 'model-router.json'), JSON.stringify(cfg));
  const result = spawnSync(process.execPath, [router, f.prompt, f.response], {
    encoding: 'utf8', env: { ...process.env, WF_PLUGIN_DIR: f.plugin, DSH_HOME: f.home, DSH_BIN: f.dsh },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stderr, /stage=review.*provider=deepseek-official model=deepseek-flash/);
});

test('native router quotes a Harness path containing spaces', t => {
  const f = fixture(t);
  const install = path.join(f.dir, 'DeepSeek Harness', 'resources', 'runtime', 'cli', 'bin');
  const installed = path.join(install, 'dsh.cmd');
  fs.mkdirSync(install, { recursive: true });
  fs.copyFileSync(f.dsh, installed);
  const result = spawnSync(process.execPath, [router, f.prompt, f.response], {
    encoding: 'utf8', env: {
      ...process.env,
      WF_PLUGIN_DIR: f.plugin,
      DSH_HOME: f.home,
      DSH_BIN: installed,
    },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(f.response, 'utf8').replace(/\r\n/g, '\n'), 'OK\n');
});

test('headless overlay preserves custom provider config and excludes desktop startup', t => {
  const f = fixture(t);
  const base = path.join(f.home, 'profiles', 'fake');
  const providers = '- id: llm-pi-ai\n  name: "@deepseek-ai/dsh-llm-pi-ai"\n  config:\n    providers:\n      my-company:\n        api: openai-completions\n        baseURL: https://example.invalid/v1\n        apiKeyEnv: COMPANY_API_KEY\n        models:\n          - id: custom-model\n            contextWindow: 123456\n';
  fs.writeFileSync(path.join(base, 'package.json'), JSON.stringify({ dsh: { profile: { bundles: ['desktop-app', 'md-upload-tiku'] } } }));
  fs.writeFileSync(path.join(base, 'cordis.patch.yml'),
    '- id: agent-default-model\n  config:\n    provider: old\n    model: old\n' + providers
    + '- insert:\n    - id: desktop-startup\n      name: fake-desktop-app\n'
    + '- id: ui-chat\n  config:\n    enabled: true\n');
  const config = JSON.parse(fs.readFileSync(path.join(f.plugin, 'config', 'model-router.json')));
  config.roles.answer = { provider: 'my-company', model: 'custom-model', profile: 'fake' };
  fs.writeFileSync(path.join(f.plugin, 'config', 'model-router.json'), JSON.stringify(config));
  const before = fs.readFileSync(path.join(base, 'cordis.patch.yml'), 'utf8');
  const result = spawnSync(process.execPath, [router, f.prompt, f.response], {
    encoding: 'utf8', env: { ...process.env, WF_PLUGIN_DIR: f.plugin, DSH_HOME: f.home, DSH_BIN: f.dsh },
  });
  assert.equal(result.status, 0, result.stderr);
  const routeProfile = fs.readdirSync(path.join(f.home, 'profiles')).find(name => name.startsWith('wf-plugin-route-'));
  const routeDir = path.join(f.home, 'profiles', routeProfile);
  const patch = fs.readFileSync(path.join(routeDir, 'cordis.patch.yml'), 'utf8');
  assert.ok(patch.startsWith(providers));
  assert.match(patch, /provider: "my-company"/);
  assert.match(patch, /model: "custom-model"/);
  assert.doesNotMatch(patch, /desktop-startup|ui-chat|insert:/);
  assert.deepEqual(fs.readdirSync(routeDir), ['cordis.patch.yml']);
  assert.equal(fs.readFileSync(path.join(base, 'cordis.patch.yml'), 'utf8'), before);
  assert.match(patch, /reasoningEffort: null/);
  // Provider endpoint/catalog changes must invalidate the overlay cache even
  // though the selected provider/model identity is unchanged.
  fs.writeFileSync(path.join(base, 'cordis.patch.yml'), before.replace('example.invalid', 'changed.invalid'));
  const rerun = spawnSync(process.execPath, [router, f.prompt, f.response], {
    encoding: 'utf8', env: { ...process.env, WF_PLUGIN_DIR: f.plugin, DSH_HOME: f.home, DSH_BIN: f.dsh },
  });
  assert.equal(rerun.status, 0, rerun.stderr);
  const profiles = fs.readdirSync(path.join(f.home, 'profiles')).filter(name => name.startsWith('wf-plugin-route-'));
  assert.equal(profiles.length, 2);
  const changed = profiles.find(name => name !== routeProfile);
  assert.match(fs.readFileSync(path.join(f.home, 'profiles', changed, 'cordis.patch.yml'), 'utf8'), /changed.invalid/);
});

test('Windows cmd forwards headless argv intact with spaces in both launcher and patch paths', { skip: process.platform !== 'win32' }, t => {
  const f = fixture(t);
  const install = path.join(f.dir, 'DeepSeek Harness');
  fs.mkdirSync(install);
  const capture = path.join(install, 'capture.cjs');
  fs.writeFileSync(capture, `process.stdin.resume(); let input = ''; process.stdin.setEncoding('utf8'); process.stdin.on('data', x => input += x); process.stdin.on('end', () => console.log(JSON.stringify({args: process.argv.slice(2), input})));`);
  const installed = path.join(install, 'dsh.cmd');
  fs.writeFileSync(installed, `@echo off\r\n"${process.execPath}" "${capture}" %*\r\n`);
  const home = path.join(f.dir, 'DSH home with spaces');
  fs.renameSync(f.home, home);
  const result = spawnSync(process.execPath, [router, f.prompt, f.response], {
    encoding: 'utf8', env: { ...process.env, WF_PLUGIN_DIR: f.plugin, DSH_HOME: home, DSH_BIN: installed },
  });
  assert.equal(result.status, 0, result.stderr);
  const actual = JSON.parse(fs.readFileSync(f.response, 'utf8'));
  assert.deepEqual(actual.args.slice(0, 3), ['--profile', 'headless', '--patch']);
  assert.equal(actual.args.length, 5);
  assert.equal(actual.args[4], '-');
  assert.ok(actual.args[3].startsWith(home + path.sep));
  assert.ok(fs.existsSync(actual.args[3]));
  assert.equal(actual.input, fs.readFileSync(f.prompt, 'utf8'));
});

test('real installed CLI accepts the generated overlay and stdin argument without a model call', { skip: !process.env.WF_ROUTER_REAL_DSH }, t => {
  const f = fixture(t, '');
  fs.writeFileSync(path.join(f.home, 'profiles', 'headless', 'package.json'), JSON.stringify({
    name: 'dsh-profile-headless', private: true, dependencies: {},
    dsh: { profile: { bundles: ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-headless'] } },
  }));
  fs.writeFileSync(path.join(f.home, 'profiles', 'headless', 'cordis.yml'), '[]\n');
  const result = spawnSync(process.execPath, [router, f.prompt, f.response], {
    encoding: 'utf8', timeout: 20000,
    env: { ...process.env, WF_PLUGIN_DIR: f.plugin, DSH_HOME: f.home, DSH_BIN: process.env.WF_ROUTER_REAL_DSH },
  });
  assert.equal(result.status, 1, result.stderr);
  assert.match(result.stderr, /a task is required/);
  assert.doesNotMatch(result.stderr, /too many arguments|startup canceled|Cannot find/);
  assert.equal(fs.existsSync(f.response), false);
});

function providerFixture(t, provider, model) {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.home, 'profiles', 'fake', 'cordis.patch.yml'),
    '- id: llm-pi-ai\n  name: "@deepseek-ai/dsh-llm-pi-ai"\n  config:\n    providers:\n      opencode-go:\n        api: openai-completions\n        baseURL: https://example.invalid/v1\n        apiKeyEnv: KEY\n        models:\n          - id: deepseek-v4.1-flash\n');
  fs.writeFileSync(path.join(f.plugin, 'config', 'model-router.json'), JSON.stringify({
    default: { provider, model, profile: 'fake' }, stages: {}, roles: {}, review: { rounds: {} },
  }));
  return f;
}

test('native router refuses a provider the route profile cannot serve', t => {
  const f = providerFixture(t, 'magpie', 'auto/deepseek-v4.1-flash');
  const result = spawnSync(process.execPath, [router, f.prompt, f.response], {
    encoding: 'utf8', env: { ...process.env, WF_PLUGIN_DIR: f.plugin, DSH_HOME: f.home, DSH_BIN: f.dsh },
  });
  assert.equal(result.status, 1);
  assert.match(result.stderr, /provider "magpie"/);
  assert.match(result.stderr, /opencode-go/);
  assert.equal(fs.existsSync(f.response), false);
});

test('native router accepts a provider declared by the route profile', t => {
  const f = providerFixture(t, 'opencode-go', 'deepseek-v4.1-flash');
  const result = spawnSync(process.execPath, [router, f.prompt, f.response], {
    encoding: 'utf8', env: { ...process.env, WF_PLUGIN_DIR: f.plugin, DSH_HOME: f.home, DSH_BIN: f.dsh },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(f.response, 'utf8').replace(/\r\n/g, '\n'), 'OK\n');
});

test('plugin config uses node native router and does not require /bin/sh', () => {
  const config = JSON.parse(fs.readFileSync(path.join(__dirname, 'local-agent.plugin.json'), 'utf8'));
  assert.deepEqual(config.command.slice(0, 2), ['node', '__PLUGIN_BACKEND__/wf-router.mjs']);
});

test('native router follows the device Harness model when no plugin default is pinned', t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.plugin, 'config', 'model-router.json'), JSON.stringify({
    default: {}, stages: {}, roles: {}, review: { rounds: {} },
  }));
  fs.mkdirSync(path.join(f.home, 'profiles', 'desktop'), { recursive: true });
  fs.writeFileSync(path.join(f.home, 'profiles', 'desktop', 'cordis.patch.yml'),
    '- id: llm-pi-ai\n  config:\n    providers:\n      opencode-go:\n        models:\n          - id: deepseek-v4.1-flash\n'
    + '- id: agent-default-model # device menu\n  config:\n    provider: opencode-go\n    model: deepseek-v4.1-flash\n');
  const result = spawnSync(process.execPath, [router, f.prompt, f.response], {
    encoding: 'utf8', env: { ...process.env, WF_PLUGIN_DIR: f.plugin, DSH_HOME: f.home, DSH_BIN: f.dsh },
  });
  // The fake dsh entry cannot execute on this host; the routing decision and the
  // generated overlay are what this test proves.
  assert.match(result.stderr, /provider=opencode-go model=deepseek-v4\.1-flash/);
  const routeProfile = fs.readdirSync(path.join(f.home, 'profiles')).find(name => name.startsWith('wf-plugin-route-'));
  const patch = fs.readFileSync(path.join(f.home, 'profiles', routeProfile, 'cordis.patch.yml'), 'utf8');
  assert.match(patch, /provider: "opencode-go"/);
  assert.match(patch, /model: "deepseek-v4.1-flash"/);
});
