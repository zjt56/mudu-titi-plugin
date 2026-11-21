const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync(require('node:path').join(__dirname, '../lib/client.js'), 'utf8');
const helpersStart = source.indexOf('    const KNOWLEDGE_TREE_CACHE = new Map();');
const start = source.indexOf('    function KnowledgePicker(');
const end = source.indexOf('    // 修改标签弹窗', start);

test('目录树节点可点击添加并保留原标签，且修改标签弹窗保留分组语义', async () => {
  assert.ok(helpersStart > 0 && start > helpersStart && end > start);
  const states = [];
  const refs = [];
  let stateIdx = 0;
  let refIdx = 0;
  let pendingEffects = [];

  const React = {
    createElement: (tag, props, ...children) => ({ tag, props: props || {}, children }),
    useState: (initial) => {
      const i = stateIdx++;
      if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial;
      return [states[i], (val) => {
        states[i] = typeof val === 'function' ? val(states[i]) : val;
      }];
    },
    useMemo: (fn) => fn(),
    useRef: (initial) => {
      const i = refIdx++;
      if (!(i in refs)) refs[i] = { current: initial };
      return refs[i];
    },
    useEffect: (fn) => { pendingEffects.push(fn); },
  };

  const treeResponse = {
    items: [
      {
        name: '主题',
        path: '主题',
        selectable: false,
        children: [
          { name: '原标签', path: '主题/原标签', selectable: true, children: [] },
          { name: '新标签', path: '主题/新标签', selectable: true, children: [] },
        ],
      },
    ],
    scoped: false,
    type: '',
    selectable_total: 2,
  };

  const context = {
    require: () => React,
    T: { input: {}, hint: {} },
    AbortController,
    jget: async () => treeResponse,
    setTimeout: (fn) => { fn(); return 1; },
    clearTimeout() {},
  };
  vm.createContext(context);
  vm.runInContext(source.slice(helpersStart, end) + '\nthis.Picker=KnowledgePicker;', context);

  let selected = ['主题/原标签'];
  const render = () => {
    stateIdx = 0;
    refIdx = 0;
    pendingEffects = [];
    const tree = context.Picker({
      paths: selected,
      onChange: (value) => { selected = Array.from(value); },
      fetchTree: async () => treeResponse,
    });
    return tree;
  };
  const flushEffects = async () => {
    const effects = pendingEffects.slice();
    pendingEffects = [];
    for (const fn of effects) fn();
    await Promise.resolve();
    await Promise.resolve();
  };
  const walk = (node, predicate) => {
    if (!node || typeof node !== 'object') return null;
    if (Array.isArray(node)) {
      for (const child of node) {
        const match = walk(child, predicate);
        if (match) return match;
      }
      return null;
    }
    if (predicate(node)) return node;
    return walk(node.children, predicate);
  };

  render();
  await flushEffects();
  render();
  await flushEffects();
  const tree = render();

  // 找到「新标签」对应的目录树叶子按钮并点击添加
  const leafButton = walk(tree, (n) => n.tag === 'button' && n.props && n.props['data-path'] === '主题/新标签');
  assert.ok(leafButton, '目录树应渲染可选叶子按钮「主题/新标签」');
  assert.equal(leafButton.props.type, 'button');
  leafButton.props.onClick();
  assert.deepEqual(selected, ['主题/原标签', '主题/新标签']);

  assert.match(
    source.slice(end, source.indexOf('    // 原卷修改弹窗', end)),
    /role: 'group'.*aria-label.*TEXT\.knowledge/,
  );
});
