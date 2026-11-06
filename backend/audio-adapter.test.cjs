const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const client = fs.readFileSync(path.join(root, 'lib/client.js'), 'utf8');
const host = fs.readFileSync(path.join(root, 'lib/index.js'), 'utf8');
const server = fs.readFileSync(path.join(root, 'app/workbench/server.py'), 'utf8');
const audioConfig = JSON.parse(fs.readFileSync(path.join(root, 'config/audio.json'), 'utf8'));

test('听力切段默认不安装、不启用', () => {
  assert.equal(audioConfig.enabled, false);
  // 当前默认多语种 small：资源优先；纯英语 small.en 会把中文题号播报转成拼音，不能用。
  assert.equal(audioConfig.asr_model, 'small');
  assert.equal(audioConfig.max_parallel_transcriptions, 1);
  assert.equal(audioConfig.anchor_language, 'zh');
  assert.equal(audioConfig.align_model, 'facebook/wav2vec2-base-960h');
  assert.equal(audioConfig.vad_method, 'pyannote');
  assert.equal(audioConfig.skeleton_silence, 3.0);
  assert.equal(audioConfig.hf_endpoint, '');
  assert.equal(audioConfig.cpu_torch, true);
});

test('设置页有听力切段卡片并挂在 SettingsTab 里', () => {
  assert.match(client, /function AudioSettings\(/);
  const settings = client.slice(client.indexOf('function SettingsTab()'));
  assert.match(settings, /\['audio', '听力切分'\]/);
  assert.match(settings, /\['audio', AudioSettings\]/);
  assert.match(settings, /visited\[id\] \? h\(Component, /);
  assert.match(client, /visible: activeSection === id/);
  assert.match(client, /await post\('bootstrap'/);
  assert.match(client, /await post\('segment'/);
  assert.match(client, /安装运行时与模型/);
  // 未就绪时不允许切段
  assert.match(client, /disabled: !!busy \|\| running \|\| !info\.ready \|\| !batch, onClick: segment/);
});

test('宿主把听力数据根传给后端，与 resolveDataRoot 一致', () => {
  assert.match(host, /WF_AUDIO_DATA_DIR: join\(dataRoot, 'audio'\)/);
});

test('工作台后端暴露五个 /api/audio 路由与后台任务闸', () => {
  for (const route of ['/api/audio/config', '/api/audio/status',
                       '["api", "audio", "bootstrap"]', '["api", "audio", "segment"]']) {
    assert.ok(server.includes(route), 'missing ' + route);
  }
  assert.match(server, /def start_audio_job\(/);
  assert.match(server, /_audio_job = \{"running": False/);
  // 批次列表暴露 has_audio，设置页只列有整卷录音的批次
  assert.match(server, /"has_audio": has_audio/);
  assert.match(client, /filter\(\(b\) => b\.has_audio\)/);
});

test('设置页暴露镜像与 CPU torch 开关', () => {
  assert.match(client, /field\('hf_endpoint'/);
  assert.match(client, /change\('cpu_torch'/);
  assert.match(client, /模型镜像：/);
});

test('文档列明分发到别的机器的前置条件', () => {
  const doc = fs.readFileSync(path.join(root, 'app/docs/audio.md'), 'utf8');
  assert.match(doc, /vendor/);
  assert.match(doc, /ffmpeg \/ ffprobe/);
  assert.match(doc, /hf-mirror\.com/);
  assert.match(doc, /fetch_vendor\.py/);
});

test('文档说明切点来自静音骨架而不是转写时间戳', () => {
  const doc = fs.readFileSync(path.join(root, 'app/docs/audio.md'), 'utf8');
  assert.match(doc, /转写时间戳绝不直接当切点/);
  assert.match(doc, /segment-evidence\.json/);
  assert.match(doc, /不需要 HuggingFace token/);
});

// 每个可选模型都必须带真实下载体积，且体积/语言标签与后端允许列表一一对应。
function readAsrModels() {
  const start = client.indexOf('const ASR_MODELS = [');
  assert.ok(start > 0, 'client.js 缺少 ASR_MODELS');
  const end = client.indexOf('\n    ];', start);
  assert.ok(end > start, 'ASR_MODELS 未闭合');
  const body = client.slice(start, end + '\n    ];'.length);
  // 纯字面量数组，直接求值即可（不执行任何插件代码）。
  return new Function(body + '\nreturn ASR_MODELS;')();
}

test('设置页给每个 ASR 模型标注下载体积', () => {
  const models = readAsrModels();
  assert.ok(models.length >= 13, '可选模型不应少于后端允许列表');
  for (const m of models) {
    assert.match(m.id, /^(tiny|base|small|medium|large)(\.en)?$|^large-v[123](-turbo)?$/);
    assert.equal(typeof m.mb, 'number', m.id + ' 缺体积');
    assert.ok(Number.isInteger(m.mb) && m.mb > 0, m.id + ' 体积必须是正整数 MB');
    assert.ok(m.note && m.note.length > 4, m.id + ' 缺特点说明');
    assert.equal(typeof m.en, 'boolean', m.id + ' 缺语言分组');
  }
  // 体积排序必须与真实模型一致：tiny < base < small < medium < large，turbo 在 large 与 medium 之间。
  const by = Object.fromEntries(models.map((m) => [m.id, m.mb]));
  assert.ok(by.tiny < by.base && by.base < by.small && by.small < by.medium && by.medium < by['large-v3']);
  assert.ok(by['large-v3-turbo'] > by.medium && by['large-v3-turbo'] < by['large-v3']);
  assert.equal(by.large, by['large-v3'], 'large 是 large-v3 的别名');
  assert.equal(by['small.en'], by.small, '英语版与多语种版体积相同');
  assert.equal(by['medium.en'], by.medium);
  // 下拉项必须显示 MB，默认项带“默认”标记。
  assert.match(client, /const asrLabel = \(m\) => m\.id \+ '（' \+ \[m\.tag, m\.lang, '≈' \+ m\.mb \+ ' MB'\]/);
  assert.match(client, /\{ id: 'large-v3-turbo', mb: 1618, lang: '多语种大模型', tag: '默认'/);
  assert.match(client, /h\('optgroup'/);
  assert.match(client, /'各模型体积与特点'/);
});

test('可选模型与后端允许列表完全一致', () => {
  const models = readAsrModels().map((m) => m.id);
  const cfg = fs.readFileSync(path.join(root, 'app/audio/config.py'), 'utf8');
  const block = cfg.slice(cfg.indexOf('"asr_model": ('));
  const tuple = block.slice(block.indexOf('(') + 1, block.indexOf(')'));
  const allowed = tuple.split(',').map((s) => s.trim().replace(/^"|"$/g, '')).filter(Boolean);
  assert.deepEqual([...models].sort(), [...allowed].sort(),
    '设置页下拉必须覆盖后端允许的每个模型，不能多也不能少');
});

test('文档列出每个模型的体积', () => {
  const doc = fs.readFileSync(path.join(root, 'app/docs/audio.md'), 'utf8');
  for (const [id, mb] of [['large-v3-turbo', 1618], ['large-v3', 3087], ['medium', 1528],
                          ['small', 484], ['base', 145], ['tiny', 75]]) {
    assert.ok(doc.includes('`' + id + '`') && doc.includes(String(mb)), '文档缺 ' + id + ' 的体积');
  }
});
