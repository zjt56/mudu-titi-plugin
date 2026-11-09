const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.join(__dirname, '..');

// The global default must not leave stage/round overrides that silently still
// use the previous default model. Explicitly chosen other models survive.
test('changing the global default clears entries still following the old default', () => {
  const client = fs.readFileSync(path.join(root, 'lib/client.js'), 'utf8');
  const start = client.indexOf('      const inferProfile = () =>');
  const end = client.indexOf('      function ModelSelect(', start);
  assert.ok(start > 0 && end > start, 'setDefault block present');
  let saved = null;
  const context = vm.createContext({
    router: {
      default: { provider: 'magpie', model: 'auto/deepseek-v4.1-flash' },
      stages: { assemble: { provider: 'magpie', model: 'auto/deepseek-v4.1-flash' },
                answers: { provider: 'other', model: 'custom-1' } },
      roles: { answers: { provider: 'magpie', model: 'auto/deepseek-v4.1-flash' } },
      review: { rounds: { labels: { provider: 'magpie', model: 'auto/deepseek-v4.1-flash' },
                          sample: { provider: 'other', model: 'custom-2' } } },
    },
    persist: (next) => { saved = next; },
  });
  vm.runInContext(client.slice(start, end) + '\nthis.setDefault = setDefault;', context);
  context.setDefault({ provider: 'deepseek-official', model: 'deepseek-v4.1-flash' });
  assert.equal(saved.default.model, 'deepseek-v4.1-flash');
  assert.equal(saved.default.provider, 'deepseek-official');
  // Entries that only mirrored the old default are gone (they follow the new one).
  assert.equal(saved.stages.assemble, undefined);
  assert.equal(saved.roles.answers, undefined);
  assert.equal(saved.review.rounds.labels, undefined);
  // Explicitly different models are preserved.
  assert.equal(saved.stages.answers.model, 'custom-1');
  assert.equal(saved.review.rounds.sample.model, 'custom-2');
});

test('selecting the same default again keeps existing overrides untouched', () => {
  const client = fs.readFileSync(path.join(root, 'lib/client.js'), 'utf8');
  const start = client.indexOf('      const inferProfile = () =>');
  const end = client.indexOf('      function ModelSelect(', start);
  let saved = null;
  const context = vm.createContext({
    router: { default: { provider: 'magpie', model: 'auto/deepseek-v4.1-flash' },
              stages: { answers: { provider: 'magpie', model: 'auto/deepseek-v4.1-flash' } },
              roles: {}, review: { rounds: {} } },
    persist: (next) => { saved = next; },
  });
  vm.runInContext(client.slice(start, end) + '\nthis.setDefault = setDefault;', context);
  context.setDefault({ provider: 'magpie', model: 'auto/deepseek-v4.1-flash' });
  assert.equal(saved.stages.answers.model, 'auto/deepseek-v4.1-flash');
});

// Malformed (non-object) maps must be left untouched so downstream `|| {}` guards
// handle them; the setter must not "clean" a broken config into a different shape.
test('non-object stage/role/review maps stay unchanged when the default changes', () => {
  const client = fs.readFileSync(path.join(root, 'lib/client.js'), 'utf8');
  const start = client.indexOf('      const inferProfile = () =>');
  const end = client.indexOf('      function ModelSelect(', start);
  const body = client.slice(start, end) + '\nthis.setDefault = setDefault;';
  function run(router) {
    let saved = null;
    const context = vm.createContext({ router, persist: (next) => { saved = next; } });
    vm.runInContext(body, context);
    context.setDefault({ provider: 'deepseek-official', model: 'deepseek-v4.1-flash' });
    return saved;
  }
  const A = { provider: 'magpie', model: 'auto/deepseek-v4.1-flash' };
  const stagesArr = run({ default: A, stages: [] });
  assert.ok(Array.isArray(stagesArr.stages), 'array stages preserved');
  const rolesStr = run({ default: A, roles: 'x' });
  assert.equal(rolesStr.roles, 'x');
  const reviewStr = run({ default: A, review: 'bad' });
  assert.equal(reviewStr.review, 'bad');
  const roundsArr = run({ default: A, review: { rounds: [] } });
  assert.ok(Array.isArray(roundsArr.review.rounds), 'array rounds preserved');
});
