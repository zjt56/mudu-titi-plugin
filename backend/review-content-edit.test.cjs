const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');

test('原卷修改弹窗编辑材料与小题，文章大意与解析在独立弹窗中编辑', () => {
  const modalStart = source.indexOf('    function ContentModal(');
  const modalEnd = source.indexOf('    function ContentEditModal(', modalStart);
  assert.ok(modalStart > 0 && modalEnd > modalStart);
  const contentModal = source.slice(modalStart, modalEnd);
  assert.match(contentModal, /'正文 \/ 材料'/);
  assert.match(contentModal, /onUnit\('material', e\.target\.value\)/);
  const editStart = modalEnd;
  const editEnd = source.indexOf('    function PaperPreviewModal(', editStart);
  assert.ok(editEnd > editStart);
  const contentEditModal = source.slice(editStart, editEnd);
  assert.match(contentEditModal, /文章大意/);
  assert.match(contentEditModal, /解析/);
});

test('打开原卷修改弹窗时仅对改动过的字段生成 patch 并携带 expected 原值', () => {
  const start = source.indexOf('      function buildContentPatch(');
  const end = source.indexOf('      function setSubField(', start);
  assert.ok(start > 0 && end > start);
  const patch = source.slice(start, end);
  assert.match(patch, /for \(const key of \['material'\]\)/);
  assert.match(patch, /edited\[key\] !== original\[key\]/, '只提交与打开时不同的字段');
  assert.match(patch, /expected\[key\] = original\[key\]/, '提交改动前的正式值用于防覆盖');
});

test('保存内容复用 /units 保存接口，且失败留在弹窗内提示', () => {
  const start = source.indexOf('      async function saveContent()');
  const end = source.indexOf('      async function decide(', start);
  assert.ok(start > 0 && end > start);
  const save = source.slice(start, end);
  assert.equal((save.match(/jpost\(/g) || []).length, 1, '保存内容应只有一次 POST');
  assert.match(save, /\/units\//);
  assert.match(save, /setContentError\(String\(e\.message \|\| e\)\)/, '失败不能换成整页错误');
});

test('ContentEditModal 仅提交实际改动的字段并携带 expected 原值防覆盖', () => {
  const start = source.indexOf('    function ContentEditModal(props)');
  const end = source.indexOf('    function PaperPreviewModal(props)', start);
  assert.ok(start > 0 && end > start);
  const modal = source.slice(start, end);
  assert.match(modal, /summary !== initial\.summary/);
  assert.match(modal, /expected\.articleSummary = initial\.summary/);
  assert.match(modal, /subPatch\.expected = subExpected/);
  assert.match(modal, /if \(Object\.keys\(patch\)\.length <= 1\)/);
});
