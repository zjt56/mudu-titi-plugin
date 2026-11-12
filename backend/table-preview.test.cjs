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

test('普通下划线文字不被当成空档', () => {
  const ctx = harness();
  assert.equal(ctx.markLine('<u>farmers</u>'), '<u>farmers</u>');
  assert.equal(ctx.markLine('to be __careful__ here'), 'to be <u>careful</u> here');
});

test('无题号的空白下划线保留为普通填空线', () => {
  const ctx = harness();
  assert.match(ctx.markLine('a <u> </u> b'), /<u> <\/u>/);
});

function renderTable(block) {
  const from = client.indexOf('      function renderBlocks(blocks) {');
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
