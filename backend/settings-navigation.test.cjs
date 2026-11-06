const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const client = fs.readFileSync(path.join(root, 'lib/client.js'), 'utf8');
const host = fs.readFileSync(path.join(root, 'lib/index.js'), 'utf8');
const settings = client.slice(client.indexOf('function SettingsTab()'));

test('设置页用左侧四项导航，顺序固定', () => {
  assert.match(settings, /const sections = \[\['models', '大模型配置'\], \['api', '题库 API'\], \['ocr', 'OCR 识别'\], \['audio', '听力切分'\]\]/);
  assert.match(settings, /role: 'tablist'/);
  assert.match(settings, /role: 'tab'/);
  assert.match(settings, /role: 'tabpanel'/);
});

test('大模型/题库/OCR/听力各自成板块并保留已访问状态', () => {
  assert.match(settings, /\[\[\'api', QuestionBankSettings\], \['ocr', OCRSettings\], \['audio', AudioSettings\]\]/);
  assert.match(settings, /visited\[id\] \? h\(Component, /);
  // 切换板块只改 aria-selected / hidden，不卸载已访问组件，未保存输入不丢。
  assert.match(settings, /hidden: activeSection !== id/);
});

test('OCR 板块读写宿主接口并使用草稿测试', () => {
  assert.match(client, /function OCRSettings\(\)/);
  assert.match(client, /'\/wf-plugin\/ocr' \+ \(test \? '\/test' : ''\)/);
  assert.match(client, /useDocOrientationClassify/);
  assert.match(client, /useDocUnwarping/);
  assert.match(client, /useChartRecognition/);
  assert.match(client, /file_url/);
  assert.match(client, /file_path/);
  assert.match(client, /clear_token/);
});

test('OCR Token 只在密码框提交，不落浏览器存储', () => {
  assert.match(client, /type: 'password', autoComplete: 'new-password'/);
  assert.equal(/localStorage[\s\S]{0,80}(token|Token)/.test(client), false);
});

test('OCR 板块为三态策略（自动/开启/关闭），默认自动、可即时切换', () => {
  assert.match(client, /'OCR 复核策略'/);
  assert.match(client, /OCR_MODE_TEXT/);
  assert.match(client, /\[\['auto', '自动（AI 判断）'\], \['on', '始终开启'\], \['off', '关闭'\]\]/);
  assert.match(client, /setMode\(value\)/);
  assert.match(client, /await request\('POST', \{ mode: next \}\)/);
  assert.match(client, /mode: config\.mode \|\| 'auto'/);
  // 默认自动由后端/设置模块保证
  const ocrSettings = fs.readFileSync(path.join(root, 'lib/ocr-settings.js'), 'utf8');
  assert.match(ocrSettings, /mode: 'auto'/);
  assert.match(ocrSettings, /result\.enabled = result\.mode !== 'off'/);
});

test('宿主注册 OCR 配置与完整测试路由', () => {
  assert.match(host, /sub === '\/ocr' \|\| sub === '\/ocr\/test'/);
  assert.match(host, /ocr\.read\(\)/);
  assert.match(host, /ocr\.save\(payload\)/);
  assert.match(host, /await ocr\.test\(payload\)/);
  assert.match(host, /ocrSettings\.runtimeEnv\(\)/);
  const ocrSettings = fs.readFileSync(path.join(root, 'lib/ocr-settings.js'), 'utf8');
  assert.match(ocrSettings, /MUDU_PADDLE_OCR_CONFIG/);
  assert.match(ocrSettings, /has_token/);
});

test('旧左右两栏设置样式已移除', () => {
  assert.equal(client.includes('wf-settings-columns'), false);
});
