const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');

// 纯函数：占位识别、逐题解析、无号题与去重（不依赖 React）。
const from = source.indexOf('    const CONTENT_PLACEHOLDERS');
const to = source.indexOf('    function ContentPreviewCard(');
assert.ok(from > 0 && to > from, '内容预览纯函数应可单独提取');
const context = {};
vm.createContext(context);
vm.runInContext(source.slice(from, to) + '\nthis.api = { contentText, contentPreviewModel };', context);
const { contentText, contentPreviewModel } = context.api;

function groupDetail(overrides = {}) {
  return {
    unit_kind: 'group', type: '完形填空', unit_id: 'group-31-40',
    item: {
      explanation: '无',
      articleSummary: '本文是一篇记叙文。文章主要叙述“我”学习汉语的经历。',
      subquestions: [
        { number: 31, answer: 'A', explanation: '根据空后一句可知，A项意为“讨厌”，符合语境。' },
        { number: 32, answer: 'C', explanation: '无' },
      ],
    },
    subquestions: [{ number: 31, display_number: 31 }, { number: 32, display_number: 32 }],
    ...overrides,
  };
}

test('占位不是正文：空、无、未提供、未填写按缺失处理，正文原样保留', () => {
  for (const value of ['', '   ', '无', '未提供', '未填写', 'N/A', null, undefined]) {
    assert.equal(contentText(value), '', '应判为缺失：' + String(value));
  }
  assert.equal(contentText(' 听力录音材料：\nM: Hello. '), '听力录音材料：\nM: Hello.');
  // 只认整串占位，正文里出现“无”字不能误判。
  assert.equal(contentText('无可奈何'), '无可奈何');
});

test('组题：文章大意、逐题解析、答案与缺失统计各自独立', () => {
  const model = contentPreviewModel(groupDetail());
  assert.equal(model.summary, '本文是一篇记叙文。文章主要叙述“我”学习汉语的经历。');
  assert.equal(model.unit, '');
  assert.equal(model.group, true);
  assert.equal(model.written, false);
  assert.equal(model.rows.length, 2);
  assert.deepEqual(model.rows.map((r) => r.display), ['31', '32']);
  assert.deepEqual(model.rows.map((r) => r.answer), ['A', 'C']);
  assert.equal(model.rows[0].text, '根据空后一句可知，A项意为“讨厌”，符合语境。');
  assert.equal(model.rows[1].text, '');
  assert.equal(model.withText, 1);
  assert.equal(model.missing, 1);
  assert.equal(model.rows[0].unnumbered, false);
});

test('整组共用解析（听力稿）单独展示，不被逐题行吞掉', () => {
  const detail = groupDetail({
    item: {
      explanation: '听力录音材料：\nM: It’s raining again.', articleSummary: '无',
      subquestions: [{ number: 16, answer: 'B', explanation: '无' }],
    },
    subquestions: [{ number: 16, display_number: 16 }],
  });
  const model = contentPreviewModel(detail);
  assert.equal(model.unit, '听力录音材料：\nM: It’s raining again.');
  assert.equal(model.rows.length, 1);
  assert.equal(model.withText, 0);
  assert.equal(model.missing, 1);
});

test('整题解析与唯一小题解析逐字相同只显示一次', () => {
  const same = {
    unit_kind: 'standalone', type: '单项填空',
    item: { explanation: '同一段解析', subquestions: [{ number: 7, answer: 'B', explanation: '同一段解析' }] },
    subquestions: [{ number: 7, display_number: 7 }],
  };
  assert.equal(contentPreviewModel(same).unit, '');
  const diff = { ...same, item: { explanation: '整题解析', subquestions: [{ number: 7, answer: 'B', explanation: '小题解析' }] } };
  assert.equal(contentPreviewModel(diff).unit, '整题解析');
});

test('独立题没有逐题行时只展示整题解析，不凭空造小题', () => {
  const model = contentPreviewModel({
    unit_kind: 'standalone', type: '听力短对话理解',
    item: { explanation: '听力录音材料：\nW: Hi, Bill.', articleSummary: '无', subquestions: [] },
    subquestions: [],
  });
  assert.equal(model.rows.length, 0);
  assert.equal(model.unit, '听力录音材料：\nW: Hi, Bill.');
  assert.equal(model.group, false);
});

test('无印刷题号显示「—」并用内部身份定位，不伪装成印刷题号', () => {
  const model = contentPreviewModel({
    unit_kind: 'group', printed_numbers: [], type: '任务型阅读',
    item: { articleSummary: '无', subquestions: [{ number: 1, answer: 'A', explanation: '原文可见。' }] },
    subquestions: [{ number: 1, display_number: null }],
  });
  assert.equal(model.rows[0].display, '—');
  assert.equal(model.rows[0].unnumbered, true);
});

test('书面表达按规则不要求解析与大意，不显示成内容缺失', () => {
  const model = contentPreviewModel({
    unit_kind: 'standalone', type: '书面表达',
    item: { explanation: '无', articleSummary: '无', subquestions: [] },
    subquestions: [],
  });
  assert.equal(model.written, true);
  assert.equal(model.summary, '');
  assert.equal(model.unit, '');
  assert.equal(model.rows.length, 0);
});

test('内容预览卡挂在原卷预览卡片之后、右栏之前，并按题重挂载', () => {
  const previewButton = source.indexOf("}, '原卷修改'),");
  const mount = source.indexOf('h(ContentPreviewCard, {');
  const rightColumn = source.indexOf('// 右：悬浮等宽三按钮 + 标签审核');
  assert.ok(previewButton > 0 && mount > previewButton && rightColumn > mount,
    '卡片必须紧跟原卷预览，且不进入右侧标签审核栏');
  assert.ok(source.includes("key: 'content:' + String(detail.unit_id || detail.label || '')"));
  // 「原卷修改」与下方「解析修改」采用一致的紧凑按钮尺寸
  const compactStyle = "...T.miniBtnPrimary, padding: '3px 10px', fontSize: 11.5, lineHeight: 1.4, whiteSpace: 'nowrap'";
  assert.ok(source.includes(compactStyle + ", marginLeft: 'auto'"), '原卷修改按钮应使用紧凑尺寸');
  assert.ok(source.includes(compactStyle + " },\n        onClick: onEdit,\n      }, '解析修改')"), '下方解析与大意卡片按钮应为「解析修改」且与原卷修改保持同尺寸');
});

test('内容预览只读：不调用写接口、不出现可编辑控件', () => {
  const start = source.indexOf('    function ContentPreviewCard(');
  const end = source.indexOf('    function ContentEditModal(', start);
  assert.ok(start > 0 && end > start, '内容预览组件应可定位');
  const block = source.slice(start, end);
  assert.doesNotMatch(block, /jpost\(|jget\(|onChange|contentEditable/);
  assert.doesNotMatch(block, /h\('(input|textarea|select)'/);
  assert.match(block, /aria-expanded/);
  assert.match(block, /文章大意/);
  assert.match(block, /答案解析/);
});

test('占位与缺失口径写死在预览卡：整串「无/未提供/未填写」不当正文', () => {
  assert.ok(source.includes("const CONTENT_PLACEHOLDERS = new Set(['无', '未提供', '未填写', 'n/a', 'none']);"));
  assert.ok(source.includes("'已填 ' + model.withText + '/' + model.rows.length"));
  assert.ok(source.includes("'暂无文章大意'"));
  assert.ok(source.includes("'暂无答案解析'"));
});

test('内容预览样式走既有 token，长文按行数截断', () => {
  for (const cls of ['.wf-content-clamp{', '.wf-content-clamp-3{', '.wf-content-num{', '.wf-content-ans{', '.wf-content-row{']) {
    assert.ok(source.includes(cls), '缺少样式 ' + cls);
  }
  assert.ok(source.includes('.wf-content-clamp{display:-webkit-box;'));
  assert.ok(source.includes('background:var(--wf-brand-soft)'));
});

test('任务型阅读长答案换行，逐题解析和缺失提示都独占正文列', () => {
  const React = {
    createElement: (tag, props, ...children) => ({ tag, props, children: children.flat().filter(Boolean) }),
    Fragment: 'fragment',
    useState: (initial) => [typeof initial === 'boolean' ? true : initial, () => {}],
  };
  const block = source.slice(from, source.indexOf('    function ContentEditModal(', to));
  const { ContentPreviewCard } = vm.runInNewContext(block + '\n({ ContentPreviewCard })', {
    require: () => React,
    T: { badge: () => ({}), hint: {} }, TEXT: {}, markLine: (text) => text,
  });
  const answer = 'Never give up./Face difficulties bravely./' + 'LongUnbrokenAnswer'.repeat(12);
  const explanation = '根据原文末段可知。'.repeat(20);
  const detail = groupDetail({ type: '任务型阅读' });
  detail.item.subquestions = [
    { number: 73, answer, explanation },
    { number: 75, answer, explanation: '无' },
  ];
  const card = ContentPreviewCard({ detail });
  const nodes = (node) => !node?.children ? [] : [node, ...node.children.flatMap(nodes)];
  const rows = nodes(card).filter((node) => node.props?.className?.startsWith('wf-content-row') &&
    node.children[0]?.props?.className === 'wf-content-num');
  assert.equal(rows.length, 2);
  for (const row of rows) {
    assert.equal(row.children.length, 2);
    const body = row.children[1];
    assert.equal(body.props.className, 'wf-content-row-body');
    assert.equal(body.children[0].children[0].children[0], '答案：' + answer);
  }
  assert.equal(rows[0].children[1].children[1].props.dangerouslySetInnerHTML.__html, explanation);
  assert.equal(rows[1].children[1].children[1].children[0], '暂无解析');
  assert.match(source, /\.wf-content-row\{display:grid;grid-template-columns:max-content minmax\(0,1fr\)/);
  assert.match(source, /\.wf-content-ans\{[^}]*max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere/);
});
