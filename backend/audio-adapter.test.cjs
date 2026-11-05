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
  // 默认多语种 small：中文卷听力音频含中文播报，纯英语 small.en 会把题号播报
  // 转成拼音乱码、锚点几乎全丢。
  assert.equal(audioConfig.asr_model, 'large-v3-turbo');
  assert.equal(audioConfig.max_parallel_transcriptions, 1);
  assert.equal(audioConfig.anchor_language, 'zh');
  assert.equal(audioConfig.align_model, 'facebook/wav2vec2-base-960h');
  assert.equal(audioConfig.vad_method, 'pyannote');
  assert.equal(audioConfig.skeleton_silence, 3.0);
  assert.equal(audioConfig.hf_endpoint, '');
  assert.equal(audioConfig.cpu_torch, true);
});

test('设置页有听力切段卡片并挂在 SettingsTab 里', () => {
  assert.match(client, /function AudioSettings\(\)/);
  assert.match(client, /h\(AudioSettings\)/);
  assert.match(client, /await post\('bootstrap'/);
  assert.match(client, /await post\('segment'/);
  assert.match(client, /安装运行时与模型/);
  // 未就绪时不允许切段
  assert.match(client, /disabled: !!busy \|\| running \|\| !info\.ready, onClick: segment/);
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
