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
test('二审标签逐条呈现抽查题号，保留多条结果和核对说明', () => {
 const d=detail([{number:'77',rationale:'依据第一段',assessment:{knowledge:['主题/新标签'],source_comparison:'与答案一致',difficulty:[{number:'77',value:'适中'}]}},
                 {number:'79',assessment:{knowledge:['主题/另一标签']}}]);
 const rendered=text(labelAuditPanels(d));
 assert.match(rendered,/抽查第 77 题/);
 assert.match(rendered,/抽查第 79 题/);
 assert.match(rendered,/新标签/);
 assert.match(rendered,/另一标签/);
 const notes=auditNotes(d).join(' ');
 assert.match(notes,/二审（第 77 题）/);
 assert.match(notes,/依据第一段/);
 assert.match(notes,/与答案一致/);
});
test('未抽中的单元显示未抽中，不伪装为待审核或已通过', () => {
 const rendered=text(labelAuditPanels(detail([], '未覆盖')));
 assert.match(rendered,/未抽中，未做二审/);
 assert.doesNotMatch(rendered,/待审核/);
});

test('分类显示题源分类（source_type），不把占位未提供当作分类', () => {
  const d = { unit_kind: 'standalone', labels: { knowledge: ['主题/原标签'], source_type: '中考真题', categories: ['未提供'] },
    comparison: { original: { labels: { knowledge: ['主题/原标签'] } }, rounds: [] } };
  const rendered = text(labelAuditPanels(d));
  assert.match(rendered, /分类\s*中考真题/);
  assert.doesNotMatch(rendered, /分类\s*未提供/);
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
  assert.match(rendered, /社区生活/);              // 一审标签行直接展示不一致
  assert.match(rendered, /不一致/);
  // 难度不在审核校验范围：只显示第一次打标签的结果，不出现一审的「容易」。
  assert.match(rendered, /难度\s*适中/);
  assert.doesNotMatch(rendered, /容易/);
  // 审核说明不再重复罗列知识点差异，也不再出现难度比对。
  const notes = auditNotes(d).join(' ');
  assert.doesNotMatch(notes, /难度/);
  assert.doesNotMatch(notes, /知识点与原标签不同/);
  assert.equal(notes.length, 0);
  assert.equal(hasKnowledgeDiff(d), true);
  // 有差异时审核说明的空态文案指向知识点标签，而不是错误地说“无差异”。
  assert.match(source, /kpDiff \? '差异已在上方「知识点标签」中标出。'/);
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
  assert.match(rendered, /难度\s*适中/);
  assert.doesNotMatch(rendered, /困难/);
  assert.doesNotMatch(auditNotes(d).join(' '), /难度/);
});
