'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const client = fs.readFileSync(path.resolve(__dirname, '..', 'lib/client.js'), 'utf8').replace(/\r\n/g, '\n');

test('导航栏包含「题型审核」页签，并接入 TypeReviewTab', () => {
  assert.match(client, /typeReview:\s*'题型审核'/);
  const wfPanel = client.match(/    function WfPanel\(props\) \{[\s\S]*?\n    \}/)[0];
  assert.match(wfPanel, /tabBtn\('typeReview',\s*TEXT\.typeReview\)/);
  assert.match(wfPanel, /visited\.typeReview[\s\S]*?h\(TypeReviewTab/);
  assert.match(client, /exports\.__panel = \{[^}]*TypeReviewTab/);
});

test('题型审核默认全部题型、每页最多20题、支持快速搜索与弹窗选择题型，且禁用整卷预拉取', () => {
  assert.match(client, /var TYPE_REVIEW_PAGE_SIZE = 20;/);
  const idx = client.indexOf('function TypeReviewTab(props) {');
  const endIdx = client.indexOf('function OnboardingSection(', idx);
  assert.ok(idx > 0 && endIdx > idx, '找不到 TypeReviewTab');
  const body = client.slice(idx, endIdx);

  // 默认全部题型
  assert.match(body, /const \[selectedType, setSelectedType\] = React\.useState\(''\)/);
  assert.match(body, /if \(!selectedType\) return '全部题型';/);
  // 分页请求（每页最多20题，绝不整批缓存/拉取）
  assert.match(body, /page_size:\s*String\(TYPE_REVIEW_PAGE_SIZE\)/);
  assert.match(body, /jget\('\/type-review\/units\?' \+ qs\.toString\(\)/);
  assert.doesNotMatch(body, /loadBatchUnitsBundle/);
  assert.doesNotMatch(body, /SHARED_BATCHES_CACHE/);
  // 底下快速搜索框 + 选择题型弹窗按钮 + 上一页/下一页
  assert.match(body, /placeholder:\s*'快速搜索题型 \/ 题号 \/ 试卷 \/ 知识点…'/);
  assert.match(body, /'aria-haspopup':\s*'dialog'/);
  assert.match(body, /'选择题型：' \+ selectedTypeLabel/);
  assert.doesNotMatch(body, /title:\s*'恢复为默认全部题型'/);
  assert.match(body, /h\(ModalShell,[\s\S]*?title:\s*'选择题型'/);
  assert.match(body, /chooseType\(''\)/);
  assert.match(body, /chooseType\(t\.value\)/);
  assert.match(body, /'上一页'/);
  assert.match(body, /'下一页'/);
  // 右侧 ReviewView 禁用整卷预加载、内置左侧列表与整卷预览按钮
  assert.match(body, /hideListCol:\s*true/);
  assert.match(body, /disableBatchBundle:\s*true/);
  assert.match(body, /disableKeyNav:\s*true/);
  assert.match(body, /hidePaperPreview:\s*true/);
  assert.match(body, /hideAiSupplement:\s*true/);
});

test('TypeReviewTab 渲染态：展示全部题型筛选栏、分页器、快速搜索及题型选择弹窗', () => {
  const fnBody = client.match(/    function TypeReviewTab\(props\) \{[\s\S]*?\n    \}/)[0];
  let setTypeModalCalls = 0;
  let stateSlot = 0;
  const states = [
    '',       // selectedType = '' (全部题型)
    'all',    // statusFilter
    '',       // searchInput
    '',       // appliedQuery
    1,        // page
    {         // data
      items: [
        { batch_name: 'b1', paper_title: '2025中考', unit_id: 'u1', label: '1', number: '1', printed_numbers: [1], type: '单项填空', state: 'attention', needs_human: true, uploaded: false },
      ],
      total: 45,
      page: 1,
      page_size: 20,
      total_pages: 3,
      all_total: 45,
      all_pending: 10,
      types: [
        { name: '单项填空', value: '单项填空', count: 25, pending: 6 },
        { name: '阅读理解', value: '阅读理解', count: 20, pending: 4 },
      ],
    },
    false,    // loading
    '',       // err
    'b1\0u1', // selectedKey
    true,     // typeModalOpen (opened!)
    '',       // typeModalFilter
    [],       // taxonomyTypes
  ];
  const fakeReact = {
    Fragment: 'Fragment',
    createElement: (tag, props, ...children) => ({ tag, props, children }),
    useState: () => {
      const idx = stateSlot++;
      const setter = idx === 9 ? () => { setTypeModalCalls += 1; } : () => {};
      return [states[idx], setter];
    },
    useRef: (v) => ({ current: v }),
    useMemo: (fn) => fn(),
    useCallback: (fn) => fn,
    useEffect: () => {},
  };
  const TypeReviewTab = vm.runInNewContext(fnBody + '\nTypeReviewTab', {
    TYPE_REVIEW_PAGE_SIZE: 20,
    UNTYPED_TYPE_VALUE: '__untyped__',
    require: () => fakeReact,
    T: { btn: {}, miniBtn: {}, input: {}, hint: {}, statusErr: {}, badge: () => ({}), paperRow: () => ({}) },
    TEXT: { reload: '刷新', loading: '加载中…', close: '关闭' },
    EmptyState: () => null,
    ModalShell: (props, ...children) => ({ tag: 'ModalShell', props, children }),
    ReviewView: (props) => ({ tag: 'ReviewView', props, children: [] }),
    jget: async () => ({}),
  });

  const tree = TypeReviewTab({ isActive: true, uploadedTick: 0 });
  const json = JSON.stringify(tree, (_k, v) => (typeof v === 'function' ? '[fn]' : v));
  assert.match(json, /选择题型：全部题型/);
  assert.match(json, /快速搜索题型 \/ 题号 \/ 试卷 \/ 知识点/);
  assert.doesNotMatch(json, /"全部批次"/);
  assert.doesNotMatch(json, /"未上传"/);
  assert.doesNotMatch(json, /"已上传"/);
  const unitRows = [];
  (function walk(n) {
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) return n.forEach(walk);
    if (n.props && n.props['data-type-unit']) unitRows.push(n);
    if (n.children) n.children.forEach(walk);
  })(tree);
  assert.equal(unitRows.length, 1);
  assert.equal(unitRows[0].children.length, 3, '左侧列表行与试卷审核一致：题号 + 主题型 + 状态点');
  assert.doesNotMatch(JSON.stringify(unitRows[0]), /2025中考|已上传/, '左侧列表行不展示来源试卷标题或已上传徽标');
  assert.match(json, /"1 \/ 3 页"/);
  assert.match(json, /"单项填空"/);
  assert.match(json, /"阅读理解"/);
  assert.match(json, /"disableBatchBundle":true/);
  assert.match(json, /"hideListCol":true/);
  assert.match(json, /"hidePaperPreview":true/);
  assert.match(json, /"hideAiSupplement":true/);
  assert.match(json, /"isActive":true/);
  assert.equal(setTypeModalCalls, 0);
});

test('整包执行 WfPanel -> 切到「题型审核」-> 加载分页列表并挂载 ReviewView，全链路无运行时异常', async () => {
  let exported = null;
  const fetchCalls = [];
  const createHarness = () => {
    const compStates = new WeakMap();
    let activeRec = null;
    const React = {
      Fragment: Symbol.for('react.fragment'),
      Component: class Component { constructor(p) { this.props = p; } },
      createElement(type, props, ...children) {
        return { type, props: props || {}, children };
      },
      useState(init) {
        const i = activeRec.idx++;
        if (!(i in activeRec.slots)) activeRec.slots[i] = typeof init === 'function' ? init() : init;
        const rec = activeRec;
        return [rec.slots[i], (v) => { rec.slots[i] = typeof v === 'function' ? v(rec.slots[i]) : v; }];
      },
      useRef(init) {
        const i = activeRec.idx++;
        if (!(i in activeRec.slots)) activeRec.slots[i] = { current: init };
        return activeRec.slots[i];
      },
      useMemo(fn) {
        const i = activeRec.idx++;
        activeRec.slots[i] = fn();
        return activeRec.slots[i];
      },
      useCallback(fn) { return fn; },
      useEffect(fn) { activeRec.effects.push(fn); },
    };
    function mountOrUpdate(fn, props, keyId = 'root') {
      if (!compStates.has(fn)) compStates.set(fn, new Map());
      const byKey = compStates.get(fn);
      if (!byKey.has(keyId)) byKey.set(keyId, { slots: [], idx: 0, effects: [] });
      const prev = activeRec;
      activeRec = byKey.get(keyId);
      activeRec.idx = 0;
      activeRec.effects = [];
      const tree = fn(props);
      const effects = activeRec.effects.slice();
      activeRec = prev;
      for (const ef of effects) ef();
      return tree;
    }
    return { React, mountOrUpdate };
  };

  const { React, mountOrUpdate } = createHarness();
  const context = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    URLSearchParams, Map, Set, AbortController, DOMException,
    encodeURIComponent, decodeURIComponent,
    window: {
      __ModuleLoader__: { load(mod) { exported = mod.factory(() => React); } },
      fetch: async (url) => {
        fetchCalls.push(url);
        if (url.includes('/api/taxonomy')) return { ok: true, json: async () => ({ types: ['单项填空', '阅读理解'] }) };
        if (url.includes('/api/type-review/units')) {
          return {
            ok: true,
            json: async () => ({
              items: [{
                batch_name: 'b1', paper_title: '2025中考', uploaded: false,
                unit_id: 'u1', number: '1', numbers: [1], printed_numbers: [1],
                label: '1', unit_kind: 'standalone', type: '单项填空', subtypes: ['语境'],
                state: 'attention', needs_human: true, excluded: false,
                open_summary: '差异', open_findings: 1,
                source_section: { title: 'I.', context: 'A', occurrence: 1 }, knowledge: ['语法/动词'],
              }],
              total: 1, page: 1, page_size: 20, total_pages: 1, all_total: 1, all_pending: 1,
              types: [{ name: '单项填空', value: '单项填空', count: 1, pending: 1 }],
            }),
          };
        }
        if (url.includes('/batches/b1/units/u1')) {
          return {
            ok: true,
            json: async () => ({
              unit_id: 'u1', label: '1', printed_numbers: [1], numbers: [1], unit_kind: 'standalone',
              preview_blocks: [{ type: 'text', text: '1. He ____ to school.' }],
              paper_bulk_bold: false,
              labels: { type: '单项填空', subtypes: ['语境'], difficulty: '适中', categories: [], knowledge: [] },
              comparison: { rounds: [], merged: { knowledge: [], has_round1: false }, stale: false },
              subquestions: [{ number: 1, stem: '1. He ____ to school.', options: [], answer: 'A' }],
              label_findings: [], format_findings: [], taxonomy: { types: ['单项填空'], subtypes: { '单项填空': ['语境'] } },
            }),
          };
        }
        return { ok: true, json: async () => ({}) };
      },
      addEventListener() {}, removeEventListener() {},
    },
    document: { getElementById: () => null, head: { appendChild() {} }, createElement: () => ({}), visibilityState: 'visible', addEventListener() {}, removeEventListener() {} },
  };
  context.window.window = context.window;
  vm.runInNewContext(client, context);

  const { WfPanel, TypeReviewTab } = exported.__panel;
  const panelTree = mountOrUpdate(WfPanel, { closePanel: () => {} });
  // 模拟点击导航栏「题型审核」按钮
  const tabButtons = [];
  (function findTabs(n) {
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) return n.forEach(findTabs);
    if (n.type === 'button' && n.props && String(n.props.className || '').includes('wf-nav-tab')) tabButtons.push(n);
    if (n.children) n.children.forEach(findTabs);
  })(panelTree);
  const typeReviewBtn = tabButtons.find((b) => b.children.includes('题型审核'));
  assert.ok(typeReviewBtn, '导航栏应存在「题型审核」按钮');
  typeReviewBtn.props.onClick();
  mountOrUpdate(WfPanel, { closePanel: () => {} });

  // 挂载 TypeReviewTab 并等待首屏分页数据返回后渲染子组件 ReviewView（含单题加载后）
  mountOrUpdate(TypeReviewTab, { isActive: true, uploadedTick: 0 });
  await new Promise((r) => setTimeout(r, 30));
  const afterListTree = mountOrUpdate(TypeReviewTab, { isActive: true, uploadedTick: 0 });
  const reviewNodes = [];
  (function findChildren(n) {
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) return n.forEach(findChildren);
    if (typeof n.type === 'function') reviewNodes.push(n);
    if (n.children) n.children.forEach(findChildren);
  })(afterListTree);
  const rv = reviewNodes.find((n) => n.type.name === 'ReviewView');
  assert.ok(rv, '加载分页题目后应渲染 ReviewView');
  mountOrUpdate(rv.type, rv.props, 'rv-b1');
  await new Promise((r) => setTimeout(r, 30));
  const detailTree = mountOrUpdate(rv.type, rv.props, 'rv-b1');
  assert.ok(detailTree, 'ReviewView 单题加载后应正常渲染');
  const detailJson = JSON.stringify(detailTree, (_k, v) => (typeof v === 'function' ? '[fn]' : v));
  assert.doesNotMatch(detailJson, /整卷预览/, '题型审核视图不渲染整卷预览按钮');
  assert.doesNotMatch(detailJson, /AI解析|AI文章大意|听力材料补全/, '题型审核视图不渲染 AI解析/AI文章大意/听力材料补全 按钮');
  assert.doesNotMatch(detailJson, /"标签审核"/, '右侧不再展示「标签审核」标题');
  assert.match(detailJson, /"知识点标签"/, '右侧直接以「知识点标签」作为小标题');
  assert.doesNotMatch(client, /h\('div',\s*\{\s*style:\s*T\.cardTitle\s*\},\s*'标签审核'\)/, '试卷审核与题型审核右侧共用 ReviewView 均已移除「标签审核」标题');
  assert.ok(fetchCalls.some((u) => u.includes('/api/type-review/units?page=1&page_size=20')), '应发起每页 20 题分页请求');
  assert.ok(fetchCalls.some((u) => u.includes('/api/batches/b1/units/u1')), '应仅请求当前选中单题');
  assert.ok(!fetchCalls.some((u) => u.endsWith('/api/batches/b1/units')), '不得整卷预拉取 /units');
});

test('题型审核键盘上下键跨页：底部再按向下跳下一页第一题，长按不连翻，顶部向上跳上一页最后一题', async () => {
  let exported = null;
  const keyListeners = [];
  const makePageItems = (pageNum) => Array.from({ length: 20 }, (_, i) => {
    const n = (pageNum - 1) * 20 + i + 1;
    return {
      batch_name: 'b1', paper_title: '2025中考', uploaded: false,
      unit_id: 'u' + n, number: String(n), numbers: [n], printed_numbers: [n],
      label: String(n), unit_kind: 'standalone', type: '单项填空', subtypes: ['语境'],
      state: 'attention', needs_human: true, excluded: false,
      open_summary: '差异', open_findings: 1,
      source_section: { title: 'I.', context: 'A', occurrence: 1 }, knowledge: ['语法/动词'],
    };
  });
  const createHarness = () => {
    const compStates = new WeakMap();
    let activeRec = null;
    const depsChanged = (prevDeps, nextDeps) => {
      if (!prevDeps || !nextDeps || prevDeps.length !== nextDeps.length) return true;
      for (let i = 0; i < prevDeps.length; i++) if (!Object.is(prevDeps[i], nextDeps[i])) return true;
      return false;
    };
    const React = {
      Fragment: Symbol.for('react.fragment'),
      Component: class Component { constructor(p) { this.props = p; } },
      createElement(type, props, ...children) {
        return { type, props: props || {}, children };
      },
      useState(init) {
        const i = activeRec.idx++;
        if (!(i in activeRec.slots)) activeRec.slots[i] = typeof init === 'function' ? init() : init;
        const rec = activeRec;
        return [rec.slots[i], (v) => { rec.slots[i] = typeof v === 'function' ? v(rec.slots[i]) : v; }];
      },
      useRef(init) {
        const i = activeRec.idx++;
        if (!(i in activeRec.slots)) activeRec.slots[i] = { current: init };
        return activeRec.slots[i];
      },
      useMemo(fn, deps) {
        const i = activeRec.idx++;
        const prev = activeRec.slots[i];
        if (!prev || depsChanged(prev.deps, deps)) {
          activeRec.slots[i] = { value: fn(), deps };
        }
        return activeRec.slots[i].value;
      },
      useCallback(fn, deps) {
        const i = activeRec.idx++;
        const prev = activeRec.slots[i];
        if (!prev || depsChanged(prev.deps, deps)) {
          activeRec.slots[i] = { value: fn, deps };
        }
        return activeRec.slots[i].value;
      },
      useEffect(fn, deps) {
        const i = activeRec.idx++;
        const prev = activeRec.slots[i];
        if (!prev || depsChanged(prev.deps, deps)) {
          activeRec.slots[i] = { deps, cleanup: prev && prev.cleanup };
          activeRec.effects.push({ idx: i, fn });
        }
      },
    };
    function mountOrUpdate(fn, props, keyId = 'root') {
      if (!compStates.has(fn)) compStates.set(fn, new Map());
      const byKey = compStates.get(fn);
      if (!byKey.has(keyId)) byKey.set(keyId, { slots: [], idx: 0, effects: [] });
      const rec = byKey.get(keyId);
      const prev = activeRec;
      activeRec = rec;
      activeRec.idx = 0;
      activeRec.effects = [];
      const tree = fn(props);
      const effects = activeRec.effects.slice();
      activeRec = prev;
      for (const ef of effects) {
        const slot = rec.slots[ef.idx];
        if (slot && typeof slot.cleanup === 'function') slot.cleanup();
        const res = ef.fn();
        if (slot) slot.cleanup = typeof res === 'function' ? res : undefined;
      }
      return tree;
    }
    return { React, mountOrUpdate };
  };

  const { React, mountOrUpdate } = createHarness();
  const context = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    URLSearchParams, Map, Set, AbortController, DOMException,
    encodeURIComponent, decodeURIComponent,
    window: {
      __ModuleLoader__: { load(mod) { exported = mod.factory(() => React); } },
      fetch: async (url) => {
        if (url.includes('/api/taxonomy')) return { ok: true, json: async () => ({ types: ['单项填空'] }) };
        if (url.includes('/api/type-review/units')) {
          const m = url.match(/page=(\d+)/);
          const p = m ? Number(m[1]) : 1;
          return {
            ok: true,
            json: async () => ({
              items: makePageItems(p),
              total: 40, page: p, page_size: 20, total_pages: 2, all_total: 40, all_pending: 40,
              types: [{ name: '单项填空', value: '单项填空', count: 40, pending: 40 }],
            }),
          };
        }
        return {
          ok: true,
          json: async () => ({
            unit_id: 'u1', label: '1', printed_numbers: [1], numbers: [1], unit_kind: 'standalone',
            preview_blocks: [], labels: { type: '单项填空', subtypes: [], knowledge: [] },
            comparison: { rounds: [] }, subquestions: [], label_findings: [], format_findings: [],
          }),
        };
      },
      addEventListener(ev, fn) { if (ev === 'keydown') keyListeners.push(fn); },
      removeEventListener(ev, fn) {
        if (ev === 'keydown') {
          const i = keyListeners.indexOf(fn);
          if (i >= 0) keyListeners.splice(i, 1);
        }
      },
    },
    document: { getElementById: () => null, head: { appendChild() {} }, createElement: () => ({}), visibilityState: 'visible', addEventListener() {}, removeEventListener() {}, querySelector: () => null },
  };
  context.window.window = context.window;
  vm.runInNewContext(client, context);

  const { TypeReviewTab } = exported.__panel;
  const activeUnitId = (tree) => {
    let found = null;
    (function walk(n) {
      if (!n || typeof n !== 'object' || found) return;
      if (Array.isArray(n)) return n.forEach(walk);
      if (n.type && n.type.name === 'ReviewView') found = n.props.unitId;
      if (n.children) n.children.forEach(walk);
    })(tree);
    return found;
  };
  const pageText = (tree) => JSON.stringify(tree, (_k, v) => (typeof v === 'function' ? '[fn]' : v));
  const fireKey = (key, extra) => {
    for (const fn of keyListeners.slice()) fn({ key, preventDefault() {}, ...(extra || {}) });
  };

  mountOrUpdate(TypeReviewTab, { isActive: true, uploadedTick: 0 });
  await new Promise((r) => setTimeout(r, 20));
  let tree = mountOrUpdate(TypeReviewTab, { isActive: true, uploadedTick: 0 });
  assert.equal(activeUnitId(tree), 'u1');

  // 连续按到第 1 页最后一题（第 20 题）
  for (let i = 0; i < 19; i++) {
    fireKey('ArrowDown');
    tree = mountOrUpdate(TypeReviewTab, { isActive: true, uploadedTick: 0 });
  }
  assert.equal(activeUnitId(tree), 'u20');

  // 长按 repeat 到第 1 页底部时不得连翻到下一页
  fireKey('ArrowDown', { repeat: true });
  tree = mountOrUpdate(TypeReviewTab, { isActive: true, uploadedTick: 0 });
  assert.match(pageText(tree), /"1 \/ 2 页"/);
  assert.equal(activeUnitId(tree), 'u20');

  // 在底部再次单独按 ArrowDown：翻到第 2 页，并自动选中第 2 页第 1 题（u21），绝不落在第 20 项（u40）
  fireKey('ArrowDown');
  tree = mountOrUpdate(TypeReviewTab, { isActive: true, uploadedTick: 0 });
  await new Promise((r) => setTimeout(r, 20));
  tree = mountOrUpdate(TypeReviewTab, { isActive: true, uploadedTick: 0 });
  assert.match(pageText(tree), /"2 \/ 2 页"/);
  assert.equal(activeUnitId(tree), 'u21', '跨到第 2 页后应自动跳到该页第一题 u21');

  // 在第 2 页第 1 题按 ArrowUp：回到第 1 页，并选中第 1 页最后一题（u20）
  fireKey('ArrowUp');
  tree = mountOrUpdate(TypeReviewTab, { isActive: true, uploadedTick: 0 });
  await new Promise((r) => setTimeout(r, 20));
  tree = mountOrUpdate(TypeReviewTab, { isActive: true, uploadedTick: 0 });
  assert.match(pageText(tree), /"1 \/ 2 页"/);
  assert.equal(activeUnitId(tree), 'u20', '向上跨回第 1 页应选中该页最后一题 u20');
});
