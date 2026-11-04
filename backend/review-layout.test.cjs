const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const client = fs.readFileSync(path.resolve(__dirname, '..', 'lib/client.js'), 'utf8');

test('review 布局用 Grid + 分层容器查询（容器不能改自身）', () => {
  // 外层滚动区是尺寸容器
  assert.match(client, /\.wf-review-container\{[^}]*container-type:inline-size[^}]*container-name:wfrev\}/);
  // 内层是 grid
  assert.match(client, /\.wf-review-layout\{display:grid;grid-template-columns:minmax\(0,1\.4fr\) minmax\(280px,1fr\)/);
  // 窄容器查询改的是后代 .wf-review-layout（不是容器自身）
  assert.match(client, /@container wfrev \(max-width:720px\)\{\.wf-review-layout\{grid-template-columns:minmax\(0,1fr\)\}\}/);
  // DOM 分层：container 内含 layout
  assert.match(client, /className: 'wf-scroll wf-review-container'/);
  assert.match(client, /className: 'wf-review-layout'/);
  // 已移除会互相打架的 flex-basis 旧方案
  assert.doesNotMatch(client, /wf-review-main\{flex:1 1 420px/);
});
