'use strict';
// Model list must expose providers the router can actually serve.
const test = require('node:test');
const assert = require('node:assert');

let plugin;
test.before(async () => { plugin = (await import('../lib/index.js')).__plugin; });

test('desktop-only providers such as opencode-go are offered', () => {
  const base = [{ provider: 'magpie', model: 'auto/deepseek-v4.1-flash' }];
  const desktop = [
    { provider: 'opencode-go', model: 'deepseek-v4.1-flash' },
    { provider: 'magpie', model: 'auto/deepseek-v4.1-flash' },
    { provider: 'magpie', model: 'desktop-only-model' },
  ];
  const merged = plugin.mergeDetectedModels(base, desktop);
  assert.deepEqual(merged, [
    { provider: 'magpie', model: 'auto/deepseek-v4.1-flash' },
    { provider: 'opencode-go', model: 'deepseek-v4.1-flash' },
  ]);
});

test('a provider declared by the base profile keeps only base models', () => {
  const base = [{ provider: 'shared', model: 'base-model' }];
  const desktop = [{ provider: 'shared', model: 'extra-model' }, { provider: 'new', model: 'm' }];
  assert.deepEqual(plugin.mergeDetectedModels(base, desktop), [
    { provider: 'shared', model: 'base-model' },
    { provider: 'new', model: 'm' },
  ]);
});

test('duplicate entries are never repeated', () => {
  const base = [{ provider: 'a', model: 'm' }];
  assert.deepEqual(plugin.mergeDetectedModels(base, [{ provider: 'a', model: 'm' }]), base);
  assert.deepEqual(plugin.mergeDetectedModels([], [{ provider: 'a', model: 'm' }]), [
    { provider: 'a', model: 'm' },
  ]);
});

test('host-only providers are not offered because the route profile cannot serve them', () => {
  const detected = [{ provider: 'opencode-go', model: 'deepseek-v4.1-flash' }];
  const host = [
    { provider: 'opencode-go', model: 'deepseek-v4.1-flash' },
    { provider: 'magpie', model: 'auto/deepseek-v4.1-flash' },
  ];
  assert.deepEqual(plugin.servableHostModels(detected, host), [
    { provider: 'opencode-go', model: 'deepseek-v4.1-flash' },
  ]);
});

test('parsing a profile exposes every llm-pi-ai provider', () => {
  const models = plugin.parseProfileModels('desktop');
  const providers = new Set(models.map((m) => m.provider));
  assert.ok(providers.has('opencode-go'), [...providers].join(','));
  assert.ok(models.some((m) => m.provider === 'opencode-go' && m.model === 'deepseek-v4.1-flash'));
});

test('harness default resolves to a servable model without entering any API key', () => {
  const found = plugin.harnessDefaultModel();
  assert.ok(found && found.provider && found.model, 'expected a Harness default model');
  const declared = plugin.parseProfileModels(found.profile || 'desktop');
  assert.ok(declared.some((m) => m.provider === found.provider && m.model === found.model),
    `${found.provider}/${found.model} must be declared by its profile`);
});

test('AI operations route prefers explicit role, then Harness model, then plugin default', () => {
  const harness = { provider: 'h', model: 'hm' };
  assert.deepEqual(plugin.operationsRouteFor({ roles: { operations: { provider: 'q', model: 'ops' } } }, harness), { provider: 'q', model: 'ops' });
  // 用户设置的默认模型优先；没有默认才跟随 Harness。
  assert.deepEqual(plugin.operationsRouteFor({ default: { provider: 'p', model: 'd' } }, harness), { provider: 'p', model: 'd' });
  assert.deepEqual(plugin.operationsRouteFor({ default: {} }, harness), { provider: 'h', model: 'hm' });
  assert.deepEqual(plugin.operationsRouteFor({ default: {} }, harness), { provider: 'h', model: 'hm' });
  assert.throws(() => plugin.operationsRouteFor({ default: {} }, null));
});

test('model settings expose a dedicated AI operations row', () => {
  const client = require('node:fs').readFileSync(require('node:path').join(__dirname, '..', 'lib', 'client.js'), 'utf8');
  assert.match(client, /\['operations', 'AI运维'\]/);
});
