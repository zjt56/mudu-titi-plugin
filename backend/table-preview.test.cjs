// 预览的填空线：Word 多段下划线属于同一处空档，不能渲染成数字 + 孤立横线。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const client = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');
const start = client.indexOf('    function escHtml(');
const end = client.indexOf('    function isHeaderRow(', start);

function harness() {
  const ctx = vm.createContext({});
  vm.runInContext(client.slice(start, end), ctx);
  return ctx;
}

test('一处空档的多段下划线合并成一条填空线', () => {
  const ctx = harness();
  const html = ctx.markLine('how to <u>　</u><u>16</u><u>　</u><u> </u> and');
  assert.match(html, /<span class="wf-blank">16<\/span>/);
  assert.doesNotMatch(html, /<u>[\s\u3000]*<\/u>/);
});

test('库侧 __N__ 空档同样渲染成填空线', () => {
  const ctx = harness();
  assert.match(ctx.markLine('to be __17__ and friendly'), /<span class="wf-blank">17<\/span>/);
});
test('编号空档只绘制一条线，不与 Word 下划线叠加', () => {
  const ctx = harness();
  for (const source of [
    '<u>　1　</u>',
    '<u>__1__</u>',
    '<u>　__1__　</u>',
    '<u>　_1_　</u>',
    '____1____',
    '____<u>1</u>____',
  ]) {
    assert.equal(ctx.markLine(source), '<span class="wf-blank">1</span>', source);
  }
  assert.equal(ctx.markLine('<u>3</u>'), '<span class="wf-blank">3</span>');
  assert.equal(ctx.markLine('<u>farmers</u>'), '<u>farmers</u>');
});

test('普通下划线文字不被当成空档', () => {
  const ctx = harness();
  assert.equal(ctx.markLine('<u>farmers</u>'), '<u>farmers</u>');
  // 库侧空档格式只有 __数字__；__文字__ 不是后端产物，不得被当标记（保持原样）。
  assert.equal(ctx.markLine('to be __careful__ here'), 'to be __careful__ here');
  // 多段纯空下划线夹文字：不得把中间文字误包成下划线，且横线统一走同一 CSS 类。
  assert.equal(
    ctx.markLine('______ are ______?'),
    '<span class="wf-fill-blank wf-rule-sm"></span> are <span class="wf-fill-blank wf-rule-sm"></span>?',
  );
});

test('正文保留上下标，外部字体标签及属性按文本处理', () => {
  const { markLine } = harness();
  assert.equal(markLine('x<sup>2</sup> H<sub>2</sub>O **重点**'),
    'x<sup>2</sup> H<sub>2</sub>O <b>重点</b>');
  assert.match(markLine('<sup style="font-family:Arial">2</sup>'), /^&lt;sup style=/);
  assert.match(markLine('<font face="Arial">Text</font>'), /^&lt;font /);
});

test('无题号的空白下划线统一渲染为全局一致的填空横线', () => {
  const ctx = harness();
  assert.equal(ctx.markLine('a <u> </u> b'), 'a <span class="wf-fill-blank wf-rule-sm"></span> b');
});

test('通篇加粗只在该试卷的渲染中压平，保留局部粗体、画线和上下标', () => {
  const { markLine } = harness();
  const paragraph = '**Most**** of the ****<u>words</u><sup>2</sup>.**';
  assert.equal(markLine(paragraph, true), 'Most of the <u>words</u><sup>2</sup>.');
  assert.equal(markLine('****What class?**', true), 'What class?');
  assert.equal(markLine('**', true), '');
  assert.equal(markLine('See **this word** here.', true), 'See <b>this word</b> here.');
  assert.equal(markLine('**word**'), '<b>word</b>');
});

function renderTable(block) {
  const from = client.indexOf('      function renderBlocks(');
  const to = client.indexOf('      // 预览小字标签', from);
  const ctx = vm.createContext({
    h: (type, props, ...children) => ({ type, props, children }),
    optionLabel: () => null, isHeaderRow: () => false,
    markLine: (text) => text, PreviewImage: 'image',
  });
  vm.runInContext(client.slice(from, to), ctx);
  return ctx.renderBlocks([block]);
}

function tableCells(nodes) {
  const result = [];
  function walk(value) {
    if (Array.isArray(value)) return value.forEach(walk);
    if (!value || typeof value !== 'object') return;
    if (value.type === 'td' || value.type === 'th') result.push(value);
    walk(value.children);
  }
  walk(nodes);
  return result;
}

test('完整来源对象的 cellSpans 与单元格图片共同渲染', () => {
  const cells = tableCells(renderTable({ type: 'table', rows: [['原文']],
    cellSpans: [[{ rowspan: 2, colspan: 3 }]],
    cellImages: [[[{ name: 'a.png', url: '/a.png' }]]] }));
  assert.equal(cells.length, 1);
  assert.equal(cells[0].props.rowSpan, 2);
  assert.equal(cells[0].props.colSpan, 3);
  assert.equal(cells[0].props.className, 'wf-table-imgcell');
});

test('旧 structured 表格保留合并跨度与显式表头', () => {
  const cells = tableCells(renderTable({ type: 'table', rows: [['平面副本']],
    structured: [{ cells: [{ text: '原文', rowspan: 3, colspan: 2, header: true }] }] }));
  assert.equal(cells.length, 1);
  assert.equal(cells[0].type, 'th');
  assert.equal(cells[0].props.rowSpan, 3);
  assert.equal(cells[0].props.colSpan, 2);
});

test('单词后接纯空下划线不产生嵌套 <u> 双线（用户反馈很短一粗一细两条线）', () => {
  const ctx = harness();
  // 单词填空：<u>i______</u>、<u>i</u>______ 曾被 __(.+?)__ 误拆，渲染出嵌套 <u>_</u>。
  for (const source of ['<u>i______</u>', '<u>i</u>______', '<u>r______</u>', '<u>o</u>____']) {
    const html = ctx.markLine(source);
    // 不得出现嵌套 <u>（内部又套 <u>），否则就是双线
    assert.doesNotMatch(html, /<u>[^<]*<u>/, source + ' -> ' + html);
  }
  // 真正的库侧题号空 __17__ 仍还原为填空线
  assert.match(ctx.markLine('to be __17__ and'), /<span class="wf-blank">17<\/span>/);
});

test('整行超长下划线与长空白 <u> 答题线归一为单行自适应横线，不折成两行', () => {
  const ctx = harness();
  // 83 个下划线的整行答题线
  const rawUnderscores = '_'.repeat(83);
  assert.equal(ctx.markLine(rawUnderscores), '<span class="wf-answer-line"></span>');
  // Word 用 <u>\xa0</u><u>   ...   </u> 拼出的整行答题线（中考任务型阅读 / 书面表达常见）
  const rawSpaced = '<u>\u00a0</u><u>' + ' '.repeat(78) + '</u>';
  assert.equal(ctx.markLine(rawSpaced), '<span class="wf-answer-line"></span>');
  // 表格/作文框内由 <br> 分隔的多条答题横线
  const inCell = 'Dear David,<br>' + rawSpaced + '<br>' + rawSpaced + '<br>Yours,';
  assert.equal(
    ctx.markLine(inCell),
    'Dear David,<br><span class="wf-answer-line"></span><br><span class="wf-answer-line"></span><br>Yours,',
  );
  // 样式表必须将 .wf-answer-line 限制在单行 100% 宽度内
  assert.match(client, /\.wf-fill-blank,\.wf-answer-line,\.wf-fill-line\{[^}]*display:inline-block[^}]*max-width:100%/);
  assert.match(client, /\.wf-answer-line,\.wf-fill-line\{width:100%\}/);
});

test('全局填空横线统一使用同一套 1px 自适应细线，同一题内短/中/长横线粗细完全一致', () => {
  const ctx = harness();
  // 同一道题内同时含 8 个和 16 个下划线：全部走 .wf-fill-blank，杜绝字体 '_' 与 CSS 混用导致粗细不一
  assert.equal(
    ctx.markLine('Jack________ ________________ his parents ________ friendly to me.'),
    'Jack<span class="wf-fill-blank wf-rule-sm"></span> <span class="wf-fill-blank wf-rule-md"></span> his parents <span class="wf-fill-blank wf-rule-sm"></span> friendly to me.',
  );
  // 中等行内空（12~19 字符）
  assert.equal(
    ctx.markLine('/ʊ/:' + '_'.repeat(12) + ' '),
    '/ʊ/:<span class="wf-fill-blank wf-rule-md"></span> ',
  );
  // 长行内空（20~35 字符）
  assert.equal(
    ctx.markLine('the hillside ' + '_'.repeat(24) + ' colourful wildflowers.'),
    'the hillside <span class="wf-fill-blank wf-rule-lg"></span> colourful wildflowers.',
  );
  // 超长行内空（36+ 字符）
  assert.equal(
    ctx.markLine('73. ' + '_'.repeat(48)),
    '73. <span class="wf-fill-blank wf-rule-xl"></span>',
  );
  // 行内超长空白 <u>
  assert.equal(
    ctx.markLine('Answer: <u>' + ' '.repeat(24) + '</u>.'),
    'Answer: <span class="wf-fill-blank wf-rule-lg"></span>.',
  );
  // 样式表所有横线共享同一 1px 规则且按百分比 clamp 自适应宽度
  assert.match(client, /\.wf-fill-blank,\.wf-answer-line,\.wf-fill-line\{[^}]*border-bottom:1px solid currentColor/);
  assert.match(client, /\.wf-fill-blank\.wf-rule-sm\{width:clamp\([^}]*%\s*,[^}]*\)\}/);
  assert.match(client, /\.wf-fill-blank\.wf-rule-md\{width:clamp\([^}]*%\s*,[^}]*\)\}/);
  assert.match(client, /\.wf-fill-blank\.wf-rule-lg\{width:clamp\([^}]*%\s*,[^}]*\)\}/);
  assert.match(client, /\.wf-fill-blank\.wf-rule-xl\{width:clamp\([^}]*%\s*,[^}]*\)\}/);
  assert.match(client, /\.wf-paper u,\.wf-table u\{[^}]*border-bottom:1px solid currentColor/);
});

test('Word 底部着重号 <em> 原样放行并用底部实心圆点（非斜体）渲染', () => {
  const ctx = harness();
  assert.equal(
    ctx.markLine('两个<em>黄</em>鹂鸣翠柳,一行<em>白</em>鹭上青天。'),
    '两个<em>黄</em>鹂鸣翠柳,一行<em>白</em>鹭上青天。',
  );
  // 通篇加粗压平时保留 <em>
  assert.equal(
    ctx.markLine('**两个<em>黄</em>鹂**', true),
    '两个<em>黄</em>鹂',
  );
  // 样式表用底部实心圆点渲染 <em>，不斜体
  assert.match(client, /\.wf-paper em,\.wf-table em,\.wf-content-text em\{[^}]*font-style:normal[^}]*text-emphasis:filled dot[^}]*text-emphasis-position:under right/);
});
