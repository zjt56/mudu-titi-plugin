const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8');
const from = source.indexOf('      function roundAssessment(');
const to = source.indexOf('      // 两轮审核（一审全量', from);
const context = { T: { hint: {} }, h: (tag, props, ...children) => ({ tag, props, children }) };
vm.createContext(context);
vm.runInContext(source.slice(from, to) + '\nthis.api={roundAssessment,labelAuditPanels,auditNotes,hasKnowledgeDiff};', context);
const { roundAssessment, labelAuditPanels, auditNotes, hasKnowledgeDiff } = context.api;
function text(node) {
  if (node == null) return '';
  if (typeof node !== 'object') return String(node);
  if (Array.isArray(node)) return node.map(text).join(' ');
  return text(node.children);
}
function hasErrorColor(node) {
  if (node == null || typeof node !== 'object') return false;
  if (Array.isArray(node)) return node.some(hasErrorColor);
  if (node.props && node.props.style && node.props.style.color === 'var(--dsw-alias-state-error-primary)') return true;
  return hasErrorColor(node.children);
}
function detail(checks, status='已提供') {
  return { unit_kind: 'group', labels: { knowledge: ['主题/原标签'] },
    comparison: { original: { labels: { knowledge: ['主题/原标签'] }, subquestions: { '77': { difficulty: '容易' } } },
      rounds: [{round_id:'labels',status:'已提供',checks:[{assessment:{knowledge:['主题/原标签']}}]},
               {round_id:'sample',status,checks}] } };
}
test('二审小题记录保留，不能当作整组assessment', () => {
 const d=detail([{number:'77',assessment:{knowledge:['主题/新标签']}}]);
 const round=roundAssessment(d.comparison,'sample');
 assert.equal(round.assessment,null);
 assert.equal(round.checks.length,1);
 assert.equal(round.checks[0].number,'77');
});
test('标签合并将原标签与一审知识点去重合并，且不再展示二审标签', () => {
 const d = detail([], '未覆盖');
 d.comparison.rounds[0].checks[0].rationale = '依据第一段补充社区生活知识点';
 d.comparison.rounds[0].checks[0].assessment.knowledge = ['主题/原标签', '主题/新标签'];
 const rendered = text(labelAuditPanels(d));
 assert.match(rendered, /原标签\s+原标签/);
 assert.match(rendered, /一审标签\s+原标签[\s、]+新标签/);
 assert.match(rendered, /标签合并.*原标签[\s、]+新标签/);
 assert.doesNotMatch(rendered, /二审标签|未抽中/);
 assert.equal(hasErrorColor(labelAuditPanels(d)), true);
 const notes = auditNotes(d).join(' ');
 assert.match(notes, /差异原因：依据第一段补充社区生活知识点/);
});
test('一审未完成时标签合并显示待审核占位，不显示二审', () => {
 const d = { unit_kind: 'standalone', labels: { knowledge: ['主题/原标签'] },
   comparison: { original: { labels: { knowledge: ['主题/原标签'] } }, rounds: [] } };
 const rendered = text(labelAuditPanels(d));
 assert.match(rendered, /标签合并/);
 assert.doesNotMatch(rendered, /二审/);
});

test('答案词显示在原标签与标签合并，已绑定和待绑定的同一路径只显示一次', () => {
  const d = detail([], '未覆盖');
  d.labels.knowledge.push('词汇/答案考点/pencil');
  d.labels.answer_point_tags = ['pencil', 'each other'];
  d.comparison.stale = true;
  const rendered = text(labelAuditPanels(d));
  assert.match(rendered, /原标签\s+原标签[\s、]+pencil[\s、]+each other/);
  assert.match(rendered, /一审标签/);
  assert.match(rendered, /标签合并.*原标签[\s、]+pencil[\s、]+each other/);
  assert.doesNotMatch(rendered, /答案考点|历史一审|历史二审|二审标签/);
  assert.doesNotMatch(rendered, /不一致/);
  assert.equal(hasKnowledgeDiff(d), false);
});

test('答案考点按一审实际判断显示，漏标和不同答案参与比较', () => {
  const d = detail([], '未覆盖');
  const first = d.comparison.rounds[0].checks[0];
  first.expected_answer_points = ['have seen'];
  first.assessment.answer_points = ['have seen'];
  assert.match(text(labelAuditPanels(d)), /一审标签\s+原标签[\s、]+have seen.*标签合并/);
  assert.equal(hasKnowledgeDiff(d), false);
  first.assessment.answer_points = [];
  assert.equal(hasKnowledgeDiff(d), true);
  const panels = labelAuditPanels(d);
  assert.equal(hasErrorColor(panels), true);
  assert.doesNotMatch(text(panels), /不一致/);
});

test('后端过滤掉未启用答案考点的answer_points后前端无差异且不标红', () => {
  const d = detail([], '未覆盖');
  const first = d.comparison.rounds[0].checks[0];
  first.expected_answer_points = [];
  const panels = labelAuditPanels(d);
  assert.equal(hasErrorColor(panels), false);
  assert.equal(hasKnowledgeDiff(d), false);
  assert.doesNotMatch(text(panels), /不一致/);
});

test('审核面板不展示难度/来源/分类等基本标签（不在审核范畴）', () => {
  const d = { unit_kind: 'standalone', labels: { knowledge: ['主题/原标签'], source_type: '中考真题', categories: ['未提供'], difficulty: '适中' },
    comparison: { original: { labels: { knowledge: ['主题/原标签'] } }, rounds: [] } };
  const all = text(labelAuditPanels(d)) + ' ' + auditNotes(d).join(' ');
  assert.doesNotMatch(all, /分类/);
  assert.doesNotMatch(all, /难度/);
  assert.doesNotMatch(all, /来源/);
  assert.equal(auditNotes(d).length, 0);
});

test('不再有单独的红色差异块：不一致直接在知识点标签里标出', () => {
  const d = {
    unit_kind: 'standalone', numbers: [6],
    labels: { knowledge: ['主题/日常活动', '细节理解'], difficulty: '适中' },
    comparison: {
      original: { labels: { knowledge: ['主题/日常活动', '细节理解'] }, subquestions: { '6': { difficulty: '适中' } } },
      rounds: [
        { round_id: 'labels', status: '已提供', checks: [{ assessment: {
          knowledge: ['主题/社区生活', '细节理解'], difficulty: [{ number: '6', value: '容易' }] } }] },
        { round_id: 'sample', status: '未覆盖', checks: [] },
      ],
    },
  };
  const rendered = text(labelAuditPanels(d));
  assert.doesNotMatch(rendered, /审核差异/);        // 没有单独的红色差异区
  assert.match(rendered, /知识点标签/);
  assert.match(rendered, /社区生活/);              // 一审标签行直接标红展示差异，不再显示“不一致”三字
  assert.equal(hasErrorColor(labelAuditPanels(d)), true);
  assert.doesNotMatch(rendered, /不一致/);
  // 难度不在审核范畴，任何位置都不出现。
  assert.doesNotMatch(rendered, /难度/);
  const notes = auditNotes(d).join(' ');
  // 有知识点差异时给一句简短原因，不做原文核对罗列。
  assert.match(notes, /差异原因：/);
  assert.equal(hasKnowledgeDiff(d), true);
  // 有差异时审核说明的空态文案指向知识点标签，而不是错误地说“无差异”。
  assert.match(source, /kpDiff \? '差异已在上方「知识点标签」中标出。'/);
});

test('同名叶子不同完整路径：只显示末级，完整路径留在 title', () => {
  const d = detail([],'未覆盖');
  d.comparison.original.labels.knowledge = ['设题角度/阅读/细节理解'];
  d.comparison.rounds[0].checks[0].assessment.knowledge = ['主题/人与社会/细节理解'];
  assert.equal(hasKnowledgeDiff(d), true);
  const nodes = labelAuditPanels(d);
  const rendered = text(nodes);
  assert.equal(hasErrorColor(nodes), true);
  assert.doesNotMatch(rendered, /不一致/);
  // 用户要求：知识点标签只展示到子级，上级不展示。
  assert.doesNotMatch(rendered, /设题角度\/阅读\/细节理解/);
  assert.doesNotMatch(rendered, /主题\/人与社会\/细节理解/);
  assert.match(rendered, /细节理解/);
  // 完整路径仍在 title 里，可悬浮查看分支。
  const titles = [];
  (function walk(node) {
    if (node == null || typeof node !== 'object') return;
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (node.props && node.props.title) titles.push(node.props.title);
    (node.children || []).forEach(walk);
  })(nodes);
  assert.ok(titles.some((t) => t.includes('设题角度/阅读/细节理解')), titles.join(' | '));
  assert.ok(titles.some((t) => t.includes('主题/人与社会/细节理解')), titles.join(' | '));
});

test('完整路径集合相同，顺序和重复项不产生差异', () => {
  const d = detail([],'未覆盖');
  d.comparison.original.labels.knowledge = ['语法/be动词','词汇/动词辨析'];
  d.comparison.rounds[0].checks[0].assessment.knowledge = ['词汇/动词辨析','语法/be动词','语法/be动词'];
  assert.equal(hasKnowledgeDiff(d), false);
  assert.equal(hasErrorColor(labelAuditPanels(d)), false);
  assert.doesNotMatch(text(labelAuditPanels(d)), /不一致/);
  assert.deepEqual(Array.from(auditNotes(d)), []);
});

test('人工修改后标签合并行显示修改结果及已修改，原标签保留原始快照', () => {
  const d = detail([], '未覆盖');
  d.labels = { knowledge: ['主题/修改后'], knowledge_manually_saved: true };
  const rendered = text(labelAuditPanels(d));
  assert.match(rendered, /原标签\s+原标签/);
  assert.match(rendered, /标签合并\s+已修改\s+修改后/);
  assert.doesNotMatch(rendered, /当前标签/);
  assert.equal(hasKnowledgeDiff(d), false);
  d.labels = { knowledge: ['主题/原标签'], knowledge_manually_saved: false };
  assert.doesNotMatch(text(labelAuditPanels(d)), /已修改/);
});

test('本轮已提供空知识点是差异，不伪装成待审核', () => {
  const d = detail([],'未覆盖');
  d.comparison.rounds[0].checks[0].assessment.knowledge = [];
  assert.equal(hasKnowledgeDiff(d), true);
  const nodes = labelAuditPanels(d);
  assert.equal(hasErrorColor(nodes), true);
  assert.doesNotMatch(text(nodes), /不一致/);
});

test('子类按集合比较且一审理由从实际check读取', () => {
  const d = detail([],'未覆盖');
  d.comparison.original.labels.subtypes = ['a','b'];
  d.comparison.rounds[0].checks[0].assessment.subtypes = ['b','a'];
  assert.equal(auditNotes(d).length,0);
  d.comparison.rounds[0].checks[0].assessment.knowledge = ['主题/新标签'];
  d.comparison.rounds[0].checks[0].rationale = '一审依据原文明确判断该知识点';
  assert.match(auditNotes(d).join(' '), /一审依据原文/);
});

test('verdict ok 加结构化差异不能显示全通过，未抽中也不伪装完成', () => {
  const a = source.indexOf('      const ROUND_SHORT =');
  const b = source.indexOf('      // 知识点：多选', a);
  vm.runInContext(source.slice(a,b) + '\nthis.roundApi={nodeState,renderRounds};', context);
  const { nodeState, renderRounds } = context.roundApi;
  const round = {round_id:'labels', status:'已提供', checks:[{verdict:'ok', differences:[{
    field:'knowledge',original:['语法/be动词'],reviewed:['词汇/动词辨析']}]}]};
  assert.equal(nodeState(round).kind,'diff');
  assert.doesNotMatch(text(renderRounds({rounds:[round]})), /全通过/);
  assert.match(text(renderRounds({rounds:[round]})), /差异/);
  assert.equal(nodeState({status:'未覆盖',checks:[]}).kind,'na');
});

test('难度即使被审核改动，也只按第一次打标签结果显示', () => {
  const d = {
    unit_kind: 'standalone', numbers: [9],
    labels: { knowledge: ['主题/原标签'], difficulty: '适中' },
    comparison: {
      original: { labels: { knowledge: ['主题/原标签'] }, subquestions: { '9': { difficulty: '适中' } } },
      rounds: [
        { round_id: 'labels', status: '已提供', checks: [{ assessment: {
          knowledge: ['主题/原标签'], difficulty: [{ number: '9', value: '困难' }] } }] },
        { round_id: 'sample', status: '未提供', checks: [] },
      ],
    },
  };
  const rendered = text(labelAuditPanels(d));
  assert.doesNotMatch(rendered, /难度/);
  assert.doesNotMatch(rendered, /容易|困难/);
  assert.equal(auditNotes(d).length, 0);
});

test('题型/子类始终展示到最末级叶子，仅把存在差异的末级子类标红', () => {
  const collectRed = (node, out = []) => {
    if (node == null || typeof node !== 'object') return out;
    if (Array.isArray(node)) { node.forEach((n) => collectRed(n, out)); return out; }
    if (node.props && node.props.style && node.props.style.color === 'var(--dsw-alias-state-error-primary)') {
      out.push(text(node).trim());
    }
    collectRed(node.children, out);
    return out;
  };
  const d = {
    unit_kind: 'standalone',
    labels: { type: '单词拼写', subtypes: ['句意'], knowledge: ['词汇/拼写与词形', '语法/数词/基数词'] },
    comparison: {
      original: { labels: { type: '单词拼写', subtypes: ['句意'], knowledge: ['词汇/拼写与词形', '语法/数词/基数词'] } },
      rounds: [{
        round_id: 'labels', status: '已提供',
        checks: [{ assessment: { type: '单词拼写', subtypes: ['汉语提示'], knowledge: ['语法/数词/基数词', '词汇/拼写与词形'] } }],
      }],
    },
  };
  const panels = labelAuditPanels(d);
  const rendered = text(panels);
  assert.match(rendered, /题型 \/ 子类/);
  assert.match(rendered, /原标签\s+单词拼写\s*\/\s*句意/);
  assert.match(rendered, /一审标签\s+单词拼写\s*\/\s*汉语提示/);
  assert.deepEqual(collectRed(panels), ['句意', '汉语提示']);

  // 无差异时仍展示到最末级叶子，但不标红。
  d.comparison.rounds[0].checks[0].assessment.subtypes = ['句意'];
  const samePanels = labelAuditPanels(d);
  assert.match(text(samePanels), /原标签\s+单词拼写\s*\/\s*句意/);
  assert.deepEqual(collectRed(samePanels), []);
});

test('知识点标签采用左对齐主次分层与胶囊标签展示，头部右侧放紧凑修改按钮且不再显示一致/有差异', () => {
  const d = detail(['主题/新标签'], '已提供');
  d.comparison.rounds[0].checks[0].assessment.knowledge = ['主题/原标签', '主题/扩展点'];
  let edited = false;
  const panels = labelAuditPanels(d, () => { edited = true; });
  const kpSection = panels[0].children[0];
  // children[0] 为「知识点标签 + 修改按钮」头部，children[1..3] 分别为 原标签 / 一审标签 / 标签合并 块
  const titleHead = kpSection.children[0];
  assert.equal(titleHead.props.style.justifyContent, 'space-between');
  assert.match(text(titleHead), /知识点标签\s+修改/);
  assert.doesNotMatch(text(titleHead), /有差异|一致|待审核/);
  const editBtn = titleHead.children[1];
  assert.equal(editBtn.tag, 'button');
  assert.equal(editBtn.props.style.fontSize, 11);
  editBtn.props.onClick();
  assert.equal(edited, true);
  // 顶部工具栏已移除「通过」按钮（decideAll）
  assert.doesNotMatch(source, /const decideAll = async \(status\)/);
  const cmpRows = kpSection.children.slice(1);
  assert.equal(cmpRows.length, 3);
  const [origRow, r1Row, mergedRow] = cmpRows;
  for (const row of [origRow, r1Row]) {
    assert.equal(row.props.style.flexDirection, 'column');
    const [headNode, bodyNode] = row.children;
    assert.equal(headNode.props.style.border, undefined);
    assert.equal(headNode.props.style.borderBottom, undefined);
    assert.equal(headNode.props.style.boxShadow, undefined);
    assert.equal(bodyNode.props.style.flexWrap, 'wrap');
    assert.ok(bodyNode.children.length >= 1);
  }
  assert.equal(mergedRow.props.style.flexDirection, 'column');
  // 2026-11-25 起「标签合并」与上方各行样式一致：不加底色、不显示「自动合并」，仅人工修改时显示「已修改」。
  assert.equal(mergedRow.props.style.background, undefined, '标签合并块不应再加底色');
  assert.doesNotMatch(text(mergedRow), /自动合并/);
});

test('审核说明完整展示超长差异原因（不截断）并采用可滑动区域', () => {
  const longReason = '本题考查固定搭配与上下文语境推理：第一段明确交代人物在社区志愿服务中的分工，原标签仅标了日常活动，遗漏了社区生活与人际沟通两个核心主题维度；同时第 2 小题需要结合第二段转折句判断作者态度，因此一审补充对应知识点并保留完整说明。';
  assert.ok(longReason.length > 100);
  const d = detail([], '未覆盖');
  d.comparison.original.labels.type = '阅读理解';
  d.comparison.rounds[0].checks = [
    {
      assessment: { type: '阅读理解', subtypes: ['细节理解'], knowledge: ['主题/原标签', '主题/社区生活'] },
      rationale: longReason,
    },
    {
      assessment: { knowledge: ['主题/原标签', '主题/社区生活'] },
      rationale: '补充核对：第二条去重后的独立说明也应完整保留。',
    },
  ];
  const notes = auditNotes(d);
  assert.ok(notes.some((n) => n.startsWith('子类差异：')));
  assert.ok(notes.includes('差异原因：' + longReason), '超长差异原因不应被 60 字截断');
  assert.ok(notes.includes('差异原因：补充核对：第二条去重后的独立说明也应完整保留。'));
  // 审核说明卡片内容区使用独立可纵向滑动容器
  assert.match(source, /\.wf-audit-notes-body\{[^}]*max-height:200px[^}]*overflow-y:auto/);
  assert.match(source, /className:\s*'wf-audit-notes-body'/);
});
