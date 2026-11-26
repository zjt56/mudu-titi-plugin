'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'client.js'), 'utf8');

function extractFn(name) {
  const re = new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n    \\}`);
  const match = src.match(re);
  assert.ok(match, `找不到函数 ${name}`);
  return vm.runInNewContext(match[0] + `\n${name}`);
}

test('isCachedUnitStale detects pre-review cached snapshot when row already shows needs_human or verified', () => {
  const isCachedUnitStale = extractFn('isCachedUnitStale');

  // 审核前预取的旧快照：无一审记录、无标签合并、无 label_findings
  const stalePreReview = {
    unit_id: 'u-62',
    label_findings: [],
    comparison: {
      rounds: [],
      merged: { knowledge: [], has_round1: false },
      stale: false,
    },
  };
  assert.equal(isCachedUnitStale(stalePreReview, { unit_id: 'u-62', needs_human: true, state: 'needs_review' }), true);
  assert.equal(isCachedUnitStale(stalePreReview, { unit_id: 'u-62', needs_human: false, state: 'ok' }), true);
  assert.equal(isCachedUnitStale(stalePreReview, { unit_id: 'u-62', needs_human: false, state: 'unverified' }), false);

  // 审核完成后的新鲜快照：有一审与标签合并、且包含 label_findings
  const freshPostReview = {
    unit_id: 'u-62',
    label_findings: [{ finding_id: 'WF-0004', field: 'knowledge' }],
    comparison: {
      rounds: [{ round_id: 'labels', checks: [{ unit_id: 'u-62', assessment: { type: '补全对话' } }] }],
      merged: { knowledge: ['主题/人与自我/生活与学习/语言学习'], has_round1: true },
      stale: false,
    },
  };
  assert.equal(isCachedUnitStale(freshPostReview, { unit_id: 'u-62', needs_human: true, state: 'needs_review' }), false);
  assert.equal(isCachedUnitStale({ ...freshPostReview, comparison: { ...freshPostReview.comparison, stale: true } }, null), true);
});

test('batchCacheSignature changes on flow_done / open_findings / stage transitions and topReviewNamesKey only prefetches flow_done', () => {
  const batchCacheSignature = extractFn('batchCacheSignature');

  const beforeReview = {
    name: '1726',
    updated_at: '2026-11-23T22:13:26+08:00',
    flow_done: false,
    uploaded: false,
    unit_total: 19,
    open_findings: 0,
    needs_human: 0,
    stage: { running: true, current: { id: 'review', status: 'running' } },
  };
  const afterReview = {
    ...beforeReview,
    updated_at: '2026-11-23T22:18:04+08:00',
    flow_done: true,
    open_findings: 5,
    needs_human: 5,
    stage: { running: false, current: { id: 'format_gate', status: 'completed' } },
  };
  assert.notEqual(batchCacheSignature(beforeReview), batchCacheSignature(afterReview));

  // 按需加载 + 微预取下 1 题：彻底移除列表页自动整卷预取、悬停整卷预取与进卷整卷 bundle 拉取
  assert.doesNotMatch(src, /topReviewNamesKey/);
  assert.doesNotMatch(src, /scheduleHoverPrefetch/);
  assert.match(src, /var UNIT_CACHE_MAX = 1;/);
  assert.match(src, /var TYPE_UNIT_CACHE_MAX = 3;/);
  assert.match(src, /var IMG_CACHE_MAX = 24;/);
  assert.match(src, /nextPrefetchTarget/);
});
