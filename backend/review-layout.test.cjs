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
  assert.match(client, /\.wf-review-container\{[^}]*overflow-x:auto/);
  // 不再有任何“切单列/换行”规则
  assert.doesNotMatch(client, /@container wfrev/);
  assert.doesNotMatch(client, /wf-review-layout\{flex-direction:column\}/);
  assert.doesNotMatch(client, /grid-template-columns:minmax\(0,1fr\)\}$/m);
});

test('原卷预览和右侧审核区有独立的纵向滚动条', () => {
  assert.match(client, /\.wf-review-layout \.wf-review-main\{[^}]*overflow-y:auto/);
  assert.match(client, /\.wf-review-layout \.wf-review-notes\{[^}]*overflow-y:auto/);
  assert.match(client, /\.wf-review-container\{[^}]*overflow-y:hidden/);
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

test('选项配图使用固定 A-D 网格位置和等比例容器', () => {
  assert.match(client, /\.wf-opt-row\{display:grid;grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/);
  assert.match(client, /gridColumn: String\(\(optionIndex % 4\) \+ 1\)/);
  assert.match(client, /\.wf-opt-frame\{[^}]*aspect-ratio:4\/3/);
  assert.match(client, /\.wf-opt-frame \.wf-img\{[^}]*object-fit:contain/);
});

test('题干图片居中并限制在响应式矩形内', () => {
  assert.match(client, /\.wf-body-image-frame\{[^}]*width:min\(100%,560px\);aspect-ratio:4\/3;margin:12px auto/);
  assert.match(client, /\.wf-body-image-frame \.wf-img\{[^}]*object-fit:contain/);
  assert.match(client, /className: 'wf-body-image-frame'/);
});

test('上下键切题不重建审阅视图，列表滚动跟随当前题', () => {
  // 按 unitId 作 key 会每次切题重挂载，把所有滚动条重置到顶端。
  assert.doesNotMatch(client, /key: active\.unitId/);
  assert.match(client, /key: 'review:' \+ active\.name/);
  // 切题时显式清掉上一题状态，避免新题短暂显示旧题数据。
  assert.match(client, /setDetail\(null\);[\s\S]{0,400}setLabelDlg\(false\)/);
  // 列表容器挂 ref、行挂 data-unit，选中行滚进可视区而不是整体跳回顶部。
  assert.match(client, /const listScrollRef = React\.useRef\(null\)/);
  assert.match(client, /ref: listScrollRef/);
  assert.match(client, /'data-unit': uid/);
  assert.match(client, /box\.querySelectorAll\('\[data-unit\]'\)/);
  assert.match(client, /box\.scrollTop -= \(bc\.top - br\.top\) \+ 8/);
  assert.match(client, /box\.scrollTop \+= \(br\.bottom - bc\.bottom\) \+ 8/);
});

test('全局中性色板：无蓝色，按钮浅白底 + 中性灰文字', () => {
  assert.doesNotMatch(client, /#2f5bea|#4176e6|rgba\(65,118,230|rgba\(47,91,234/);
  assert.match(client, /--wf-brand:var\(--dsw-alias-label-primary\)/);
  assert.match(client, /--wf-brand-soft:var\(--dsw-alias-bg-layer-2\)/);
  assert.match(client, /--wf-brand-border:var\(--dsw-alias-border-l2\)/);
  assert.match(client, /--wf-sel-bg:var\(--dsw-alias-bg-layer-2\)/);
  // 主按钮字重与普通按钮一致（不再加粗）。
  assert.doesNotMatch(client, /btnPrimary: \{[^}]*fontWeight: 600/);
});

test('插件图标同步改为中性灰', () => {
  const icon = fs.readFileSync(path.resolve(__dirname, '..', 'icon.svg'), 'utf8');
  assert.doesNotMatch(icon, /#4f46e5/);
  assert.match(icon, /#8a94a6/);
});
