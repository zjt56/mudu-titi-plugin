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

test('parsing a profile exposes every llm-pi-ai provider', () => {
  const models = plugin.parseProfileModels('desktop');
  const providers = new Set(models.map((m) => m.provider));
  assert.ok(providers.has('opencode-go'), [...providers].join(','));
  assert.ok(models.some((m) => m.provider === 'opencode-go' && m.model === 'deepseek-v4.1-flash'));
});
