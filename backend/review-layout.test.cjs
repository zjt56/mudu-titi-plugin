const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const client = fs.readFileSync(path.resolve(__dirname, '..', 'lib/client.js'), 'utf8');

test('审核布局：右栏“待人工处理”始终固定右侧，绝不换行到下方', () => {
  // 两栏常驻 grid：左栏 minmax(300px,1fr)、右栏 minmax(240px,285px)（较原先收窄约 1/4）
  assert.match(client, /\.wf-review-layout\{display:grid;grid-template-columns:minmax\(300px,1fr\) minmax\(240px,285px\)/);
  // 整块有最小宽度，面板更窄时靠外层容器横向滚动（overflow:auto），而不是把右栏挤下去
  assert.match(client, /\.wf-review-layout\{[^}]*min-width:564px/);
  assert.match(client, /\.wf-review-container\{flex:1;min-height:0;overflow:auto\}/);
  // 不再有任何“切单列/换行”规则
  assert.doesNotMatch(client, /@container wfrev/);
  assert.doesNotMatch(client, /wf-review-layout\{flex-direction:column\}/);
  assert.doesNotMatch(client, /grid-template-columns:minmax\(0,1fr\)\}$/m);
});

test('逐题列表列已压缩，给工作区让宽', () => {
  assert.match(client, /flex: '0 1 220px', minWidth: 140/);
});

test('DOM 分层：container 内含 layout，左右两栏为 grid 子项', () => {
  assert.match(client, /className: 'wf-scroll wf-review-container'/);
  assert.match(client, /className: 'wf-review-layout'/);
  assert.match(client, /className: 'wf-review-main'/);
  assert.match(client, /className: 'wf-review-notes'/);
});
