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

test('选项配图使用固定 A-D 网格位置和统一尺寸容器', () => {
  assert.match(client, /\.wf-opt-row\{display:grid;grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/);
  assert.match(client, /gridColumn: String\(\(optionIndex % 4\) \+ 1\)/);
  assert.match(client, /\.wf-opt-row\{[^}]*max-width:504px/);
  assert.match(client, /\.wf-opt-frame\{[^}]*height:72px/);
  const optionLabel = new Function('return (' + client.match(/function optionLabel\(text\) \{[\s\S]*?\n      \}/)[0] + ')')();
  assert.equal(optionLabel('**A****.****'), 'A.');
  assert.equal(optionLabel('****\t****B****.****'), 'B.');
  assert.equal(optionLabel('C.'), 'C.');
  assert.equal(optionLabel('正文内容'), null);
  assert.match(client, /\.wf-opt-frame \.wf-img\{[^}]*object-fit:contain/);
});

test('题干图片居中并限制在响应式矩形内', () => {
  assert.match(client, /\.wf-body-image-frame\{[^}]*width:100%;max-width:200px;aspect-ratio:4\/3;margin:8px auto/);
  assert.match(client, /\.wf-body-image-frame \.wf-img\{[^}]*object-fit:contain/);
  assert.match(client, /className: 'wf-body-image-frame'/);
});

test('上下键切题不重建审阅视图，列表滚动跟随当前题', () => {
  // 按 unitId 作 key 会每次切题重挂载，把所有滚动条重置到顶端。
  assert.doesNotMatch(client, /key: active\.unitId/);
  assert.match(client, /key: 'review:' \+ active\.name/);
  // 切题时用 detailPair 键校验防旧题闪烁，并显式收起上一题的标签编辑弹窗。
  assert.match(client, /const detail = \(detailPair && detailPair\.key === cacheKey\) \? detailPair\.data : getCachedUnit\(batchName, unitId\);/);
  assert.match(client, /setLabelDlg\(false\);/);
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
  // 浅色主题的 bg-layer-2 就是纯白，选中态必须按主题色混合出更深的底色。
  assert.match(client, /--wf-sel-bg:color-mix\(in srgb, var\(--dsw-alias-label-primary\) 10%, var\(--dsw-alias-bg-base\)\)/);
  assert.match(client, /--wf-sel-border:var\(--dsw-alias-label-secondary\)/);
  // 主按钮字重与普通按钮一致（不再加粗）。
  assert.doesNotMatch(client, /btnPrimary: \{[^}]*fontWeight: 600/);
  // 主题色与常规样式不使用绿色成功色（仅 AI 批量生成完成进度条例外）。
  const greenMatches = client.match(/--dsw-alias-state-success-primary/g) || [];
  assert.ok(greenMatches.length <= 1);
  if (greenMatches.length === 1) {
    assert.match(client, /aiModal\.status === 'done' \? 'var\(--dsw-alias-state-success-primary\)'/);
  }
});

test('左侧题目列表：每题有独立边框分隔，选中态加深并带强调条', () => {
  assert.match(client, /margin: '0 2px 6px', padding: '7px 9px'/);
  assert.match(client, /border: '1px solid ' \+ \(active \? 'var\(--wf-sel-border\)' : 'var\(--dsw-alias-border-l2\)'\)/);
  assert.match(client, /background: active \? 'var\(--wf-sel-bg\)' : 'var\(--dsw-alias-bg-base\)'/);
  assert.match(client, /boxShadow: active \? 'inset 2px 0 0 var\(--dsw-alias-label-primary\)' : 'none'/);
  // hover 不再把选中行刷成纯白，也不影响原卷正文。
  assert.match(client, /\.wf-paper\[data-unit\]:not\(\.wf-active\):hover/);
  assert.doesNotMatch(client, /\.wf-paper\.wf-active:hover\{filter:brightness/);
});

test('左侧题号列表使用橙色问题圆点，正常题使用默认文字色且不展示待审徽标', () => {
  const from = client.indexOf('filteredRows.map((r) => {');
  const to = client.indexOf('\n          })),', from);
  assert.ok(from >= 0 && to > from);
  const render = new Function('h', 'T', 'unitId', 'onSelectUnit', 'r',
    client.slice(from + 'filteredRows.map((r) => {'.length, to));
  const h = (tag, props, ...children) => ({ tag, props, children });
  const row = (data) => render(h, { paperRow: () => ({}), badge: () => ({}) }, '', () => {},
    { unit_id: 'q1', number: 1, type: '单项填空', ...data });
  for (const data of [{ needs_human: true, open_count: 2, open_summary: '知识点差异' },
                      { state: 'attention' }, { state: 'blocked' }]) {
    const result = row(data);
    const dot = result.children.at(-1);
    assert.equal(dot.props.className, 'wf-review-row-dot');
    assert.equal(dot.props.style.background, 'var(--dsw-alias-state-warn-primary)');
    assert.equal(dot.props['aria-label'], '有审核问题');
    assert.equal(dot.children.length, 0);
  }
  assert.equal(row({ needs_human: true, open_summary: '知识点差异' }).children.at(-1).props.title, '知识点差异');
  assert.equal(row({ state: 'ok' }).children.at(-1).props.style.background, 'var(--dsw-alias-label-primary)');
  assert.equal(row({ state: 'unverified' }).children.at(-1).props['aria-label'], '尚未核对');
  assert.equal(row({ excluded: true }).children.at(-1).children[0], '听力跳过');
});

test('左侧题号列表只显示主题型，不显示子类/子题型/题干', () => {
  // 题号列只放原卷印刷题号；无号题组显示「—」，不把大题标题（像题干）塞进题号列。
  assert.match(client, /Array\.isArray\(r\.printed_numbers\) && r\.printed_numbers\.length === 0\s*\? '—'/);
  // 题型列只用主题型；子类、子题型与题干一律不进列表。
  assert.match(client, /String\(r\.type \|\| ''\)\.trim\(\) \|\| '未判定'/);
  assert.doesNotMatch(client, /\[String\(r\.type \|\| ''\), \(Array\.isArray\(r\.subtypes\)/);
  const listCol = client.slice(client.indexOf('const listCol = h('),
    client.indexOf('// ---- 右栏：工作区 ----'));
  assert.ok(listCol.length > 0, '找不到左侧题目列表代码');
  assert.doesNotMatch(listCol, /r\.stem/);
  assert.doesNotMatch(listCol, /subtypes/);
});

test('插件图标同步改为中性灰', () => {
  const icon = fs.readFileSync(path.resolve(__dirname, '..', 'icon.svg'), 'utf8');
  assert.doesNotMatch(icon, /#4f46e5/);
  assert.match(icon, /#8a94a6/);
});

test('预览对照按钮使用独立样式类与显式 hover/active 状态，点击不再发白看不见', () => {
  const idx = client.indexOf("docIcon(14), '预览对照'");
  assert.ok(idx >= 0, '找不到预览对照按钮');
  const btnChunk = client.slice(Math.max(0, idx - 260), idx + 40);
  assert.match(btnChunk, /className: 'wf-paper-preview-btn'/);
  assert.doesNotMatch(btnChunk, /wf-btn-primary/);
  assert.doesNotMatch(btnChunk, /color: '#ffffff'/);
  assert.match(client, /\.wf-paper-preview-btn\{[^}]*display:inline-flex/);
  assert.match(client, /\.wf-paper-preview-btn:not\(:disabled\):hover\{[^}]*background:color-mix/);
  assert.match(client, /\.wf-paper-preview-btn:not\(:disabled\):active\{[^}]*background:color-mix/);
});

test('预览对照弹窗支持左右双栏 Word 原文件对照、离线 vendor 静态库与按题目同步滚动', () => {
  assert.ok(fs.existsSync(path.resolve(__dirname, '..', 'lib', 'vendor', 'jszip.min.js')), '缺少离线 jszip.min.js');
  assert.ok(fs.existsSync(path.resolve(__dirname, '..', 'lib', 'vendor', 'docx-preview.min.js')), '缺少离线 docx-preview.min.js');
  const host = fs.readFileSync(path.resolve(__dirname, '..', 'lib', 'index.js'), 'utf8');
  assert.match(host, /sub\.startsWith\('\/vendor\/'\)/, 'Host 必须暴露 /wf-plugin/vendor/* 白名单静态路由');
  assert.match(client, /title: '预览对照'/);
  assert.match(client, /Word 原文件对照/);
  assert.match(client, /wf-compare-split/);
  assert.match(client, /同步滚动：已开启/);
  assert.match(client, /定位原文/);
  assert.match(client, /\/source-docx/);

  // 抽出纯函数验证按题目锚点匹配（避开卷末参考答案）与分段线性插值
  const start = client.indexOf('function normalizeCompareText(');
  const end = client.indexOf('function PaperPreviewModal(');
  assert.ok(start > 0 && end > start, '找不到预览对照同步滚动纯函数区');
  const helperCode = client.slice(start, end) + '\nreturn { normalizeCompareText, buildCompareSearchProfile, matchCompareAnchors, interpolateSyncedScroll };';
  const helpers = new Function(helperCode)();

  const items = [
    { source_section: { title: '一、单项选择' }, number: 1, stem: '— Good morning, Eric!' },
    { source_section: { title: '二、完形填空' }, unit_kind: 'group', numbers: [11, 12], material: 'Last Sunday my family went to the zoo together.' },
    { source_section: { title: '三、看图写话' }, number: 21, stem: '[IMG q21.png]' },
    { source_section: { title: '四、阅读理解' }, unit_kind: 'group', numbers: [22, 23], material: 'Pandas are one of the most popular animals in China.' },
  ];
  const profiles = items.map((it) => helpers.buildCompareSearchProfile(it, { secChanged: true, secTitle: it.source_section.title }));
  const rightRaw = [
    '七年级英语单元测试',
    '一、单项选择',
    '1. — Good morning, Eric!  A. Hello  B. Thanks',
    '二、完形填空',
    'Last Sunday my family went to the zoo together. We saw many animals.',
    '11. A. big  B. small',
    '三、看图写话',
    '四、阅读理解',
    'Pandas are one of the most popular animals in China. They love bamboo.',
    '参考答案',
    '1. A  11. B  21. Look at the picture.  22. C',
    '一、单项选择 答案解析',
  ];
  const rightBlocks = rightRaw.map((raw) => ({ raw, norm: helpers.normalizeCompareText(raw) }));
  const anchors = helpers.matchCompareAnchors(profiles, rightBlocks);
  assert.deepEqual(
    anchors.map((a) => [a.idx, a.blockIdx]),
    [[0, 1], [1, 3], [2, 6], [3, 7]],
    '应按题顺序精准锚定到右侧正文区且不落入卷末参考答案',
  );

  // 左侧展开答案后高度变大（比如 0..2000），右侧仍为 0..1000，按锚点插值应精准停在对应题段
  const pairs = [
    { src: 100, dst: 80 },
    { src: 900, dst: 480 },
    { src: 1600, dst: 800 },
  ];
  assert.equal(helpers.interpolateSyncedScroll(100, pairs, 2000, 1000), 80);
  assert.equal(helpers.interpolateSyncedScroll(500, pairs, 2000, 1000), 280);
  assert.equal(helpers.interpolateSyncedScroll(900, pairs, 2000, 1000), 480);
});
