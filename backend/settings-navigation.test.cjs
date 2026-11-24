const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const client = fs.readFileSync(path.join(root, 'lib/client.js'), 'utf8');
const host = fs.readFileSync(path.join(root, 'lib/index.js'), 'utf8');
const settings = client.slice(client.indexOf('function SettingsTab(props'));

test('设置页用左侧五项导航（含初始化引导），顺序固定', () => {
  assert.match(settings, /const sections = \[\['onboarding', '初始化引导'\], \['models', '模型设置'\], \['api', '上传设置'\], \['ocr', 'OCR 识别'\], \['audio', '听力切分'\]\]/);
  // 新装用户默认落在引导页，第一眼看到要装/要配什么。
  assert.match(settings, /React\.useState\('onboarding'\)/);
  assert.match(settings, /id: 'wf-settings-panel-onboarding'/);
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
  assert.doesNotMatch(client, /OCR_MODE_TEXT/);
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

test('SettingsTab 带 props 或无参均可正常挂载，不报 props is not defined', () => {
  const vm = require('node:vm');
  let exported = null;
  let slot = 0;
  const states = ['onboarding', { onboarding: true }, [], null, 'idle', ''];
  const fakeReact = {
    Fragment: 'Fragment',
    Component: class Component { constructor(p) { this.props = p; } },
    createElement: (tag, props, ...children) => ({ tag, props, children }),
    useState: (init) => [slot < states.length ? states[slot++] : (typeof init === 'function' ? init() : init), () => {}],
    useRef: (v) => ({ current: v }),
    useMemo: (fn) => fn(),
    useCallback: (fn) => fn,
    useEffect: () => {},
  };
  const ctx = {
    console,
    setTimeout: () => 1, clearTimeout: () => {},
    setInterval: () => 1, clearInterval: () => {},
    URLSearchParams, Map, Set, AbortController, DOMException, encodeURIComponent, decodeURIComponent,
    window: {
      __ModuleLoader__: { load(mod) { exported = mod.factory(() => fakeReact); } },
      fetch: async () => ({ ok: true, json: async () => ({}) }),
      addEventListener() {}, removeEventListener() {},
    },
    document: { getElementById: () => null, head: { appendChild() {} }, createElement: () => ({}), visibilityState: 'visible', addEventListener() {}, removeEventListener() {}, querySelector: () => null },
  };
  ctx.window.window = ctx.window;
  vm.runInNewContext(client, ctx);
  const { SettingsTab, TabBoundary } = exported.__panel;
  assert.doesNotThrow(() => { slot = 0; SettingsTab({ isActive: true }); });
  assert.doesNotThrow(() => { slot = 0; SettingsTab(); });
  // 即使接口异常返回非数组 models / null router，SettingsTab 也不得抛异常导致白屏。
  const badStates = ['models', { onboarding: true, models: true }, null, null, 'idle', ''];
  assert.doesNotThrow(() => {
    slot = 0;
    const orig = states.slice();
    states.splice(0, states.length, ...badStates);
    try { SettingsTab({ isActive: true }); } finally { states.splice(0, states.length, ...orig); }
  });
  // TabBoundary 捕获渲染错误时展示错误提示与重试按钮，不整页白屏。
  assert.equal(typeof TabBoundary.getDerivedStateFromError, 'function');
  const errState = TabBoundary.getDerivedStateFromError(new Error('boom'));
  assert.equal(errState.error, 'boom');
  const boundary = new TabBoundary({ name: 'settings', label: '模型与设置', children: 'ok' });
  boundary.state = { error: 'boom', retry: 0 };
  const fallback = boundary.render();
  assert.ok(fallback && fallback.props && String(fallback.props.title || '').includes('boom'));
  assert.match(client, /wrapTab\('settings', TEXT\.settings,/);
});
