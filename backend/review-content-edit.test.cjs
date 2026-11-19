const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');

test('原卷修改弹窗含文章大意输入框，次序为 材料 → 文章大意 → 解析', () => {
  const material = source.indexOf("formField('正文 / 材料");
  const summary = source.indexOf("formField('文章大意");
  const explanation = source.indexOf("formField('解析'");
  assert.ok(material > 0 && summary > material && explanation > summary,
    '文章大意应排在同一弹窗的材料与解析之间');
  const field = source.slice(summary, explanation);
  assert.match(field, /value: content\.articleSummary \|\| ''/);
  assert.match(field, /onUnit\('articleSummary', e\.target\.value\)/);
});

test('打开弹窗时读取文章大意，保存时只提交改动过的字段', () => {
  assert.ok(source.includes("articleSummary: item.articleSummary != null ? String(item.articleSummary) : ''"),
    'initContent 必须从 detail.item 读入文章大意');
  const start = source.indexOf('      function buildContentPatch(');
  const end = source.indexOf('      function setSubField(', start);
  assert.ok(start > 0 && end > start);
  const patch = source.slice(start, end);
  assert.match(patch, /\['material', 'articleSummary', 'explanation'\]/);
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
