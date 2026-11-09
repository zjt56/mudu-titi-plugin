const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const read = (rel) => fs.readFileSync(path.join(root, rel), 'utf8');
const STAGES = ['preassemble', 'answers', 'audio', 'types', 'explanations', 'knowledge', 'assemble', 'review'];
const ARTIFACTS = {
  preassemble: '03_types/types.json',
  answers: '01_source_ans/answers.json',
  audio: '02_audio/audio_map.json',
  types: '03_types/types.json',
  explanations: '04_knowledge/explanations.json',
  knowledge: '04_knowledge/knowledge.json',
  assemble: '06_batch/batch.json',
  review: '07_review/agent-review.json',
};

test('every agent stage ships a role file with matching stage and artifact', () => {
  for (const stage of STAGES) {
    const text = read(`app/prompts/roles/${stage}.md`);
    assert.match(text, /^# 角色：\S+/m, stage);
    assert.match(text, new RegExp(`^stage: ${stage}$`, 'm'), stage);
    assert.match(text, new RegExp(`^artifact: ${ARTIFACTS[stage]}$`, 'm'), stage);
    assert.match(text, /脑子检查/, stage);
  }
  const files = fs.readdirSync(path.join(root, 'app/prompts/roles')).sort();
  assert.deepEqual(files, STAGES.map((s) => `${s}.md`).sort());
});

test('router prefers the sub-agent role over the stage and keeps the default fallback', () => {
  const script = read('backend/wf-router.sh');
  const resolver = read('backend/route_pick.py');
  assert.match(script, /detect_agent\(\)/);
  assert.match(script, /read_route "\$STAGE" "\$ROUND" "\$AGENT"/);
  assert.match(script, /route_pick\.py/);
  assert.match(resolver, /roles\.get\(agent\)/);
  assert.match(resolver, /pick\(entry, default\)/);
  // 不能因为缺角色配置就退到别的模型：没有默认模型必须失败。
  assert.match(resolver, /no valid default provider\/model/);
});

test('settings select a model per sub-agent and older stage configs still show', () => {
  const client = read('lib/client.js');
  assert.match(client, /router\?\.roles\?\.\[s\] \|\| router\?\.stages\?\.\[s\]/);
  assert.match(client, /persist\(\{ \.\.\.router, roles \}\)/);
  assert.match(client, /各子 Agent 模型/);
  assert.match(client, /内置 8 个子 Agent/);
});

test('router writes profiles for role entries too', () => {
  const index = read('lib/index.js');
  assert.match(index, /if \(router\.roles\) for \(const k of Object\.keys\(router\.roles\)\) fill\(router\.roles\[k\]\)/);
});
