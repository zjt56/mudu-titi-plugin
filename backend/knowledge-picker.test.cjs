const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../lib/client.js'), 'utf8');
const start = source.indexOf('    function KnowledgePicker(');
const end = source.indexOf('    // 修改标签弹窗', start);

test('搜索结果是可点击按钮，添加时保留原标签', async () => {
  const states = ['', [], false, false, null];
  let index = 0;
  const React = { createElement: (tag, props, ...children) => ({ tag, props: props || {}, children }),
    useState: () => { const i = index++; return [states[i], value => { states[i] = value; }]; } };
  const context = { require: () => React, T: { input: {} }, window: { fetch: async () => ({ json: async () => ({ items: [{ path: '主题/新标签' }] }) }) },
    setTimeout: fn => { context.pending = fn; return 1; }, clearTimeout() {} };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end) + '\nthis.Picker=KnowledgePicker;', context);
  let selected = ['主题/原标签'];
  const render = () => { index = 0; return context.Picker({ paths: selected, onChange: value => { selected = Array.from(value); } }); };
  const walk = (node, predicate) => {
    if (!node || typeof node !== 'object') return null;
    if (Array.isArray(node)) { for (const child of node) { const match = walk(child, predicate); if (match) return match; } return null; }
    if (predicate(node)) return node;
    return walk(node.children, predicate);
  };
  walk(render(), n => n.tag === 'input').props.onChange({ target: { value: '新标签' } });
  await context.pending();
  const button = walk(render(), n => n.tag === 'button' && n.children.includes('主题/新标签'));
  assert.equal(button.props.type, 'button');
  button.props.onClick();
  assert.deepEqual(selected, ['主题/原标签', '主题/新标签']);
  assert.match(source.slice(end, source.indexOf('    // 原卷修改弹窗', end)), /role: 'group'.*aria-label.*TEXT\.knowledge/);
});
