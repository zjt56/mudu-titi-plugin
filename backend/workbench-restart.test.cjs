const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const client = readFileSync(path.join(root, 'lib/client.js'), 'utf8').replace(/\r\n/g, '\n');
const host = readFileSync(path.join(root, 'lib/index.js'), 'utf8').replace(/\r\n/g, '\n');

test('宿主保留空闲自动重启并支持查看状态与强制重启参数', () => {
  assert.match(host, /const RESTART_DEFER_MS = 5000;/);
  assert.match(host, /if \(req\.method === 'GET' && sub === '\/workbench\/status'\)/);
  assert.match(host, /deferred:\s*Boolean\(deferredRestart\)/);
  assert.match(host, /restarting:\s*backendRestartInFlight\(\)/);
  assert.match(host, /const force = payload\?\.force === true;/);
  assert.match(host, /if \(!force && await backendBusy\(\)\)/);
});

test('前端只在最前台顶栏和模型设置页提供重启后端入口，排队区与试卷审核工具栏不再重复放置', () => {
  const topTabbar = client.slice(client.indexOf("style: T.tabbar, 'data-window-drag': ''"), client.indexOf("style: T.tabbar, 'data-window-drag': ''") + 650);
  const queueHeader = client.slice(client.indexOf("'排队区'"), client.indexOf("'排队区'") + 360);
  const reviewIdx = client.indexOf('const reviewFilterBar = h(');
  const reviewToolbar = client.slice(reviewIdx, reviewIdx + 1200);
  const settingsIdx = client.indexOf('onClick: writeEffective');
  const settingsFooter = client.slice(settingsIdx, settingsIdx + 260);
  assert.match(topTabbar, /BackendRestartControl/);
  assert.doesNotMatch(queueHeader, /BackendRestartControl/);
  assert.doesNotMatch(reviewToolbar, /BackendRestartControl/);
  assert.match(settingsFooter, /BackendRestartControl/);
});

test('BackendRestartControl 点击直接触发立即重启，不展示「空闲后自动重启」状态', async () => {
  const match = client.match(/    function BackendRestartControl\(props\) \{[\s\S]*?\n    \}/);
  assert.ok(match, 'BackendRestartControl should exist in lib/client.js');
  const fetchCalls = [];
  let stateIndex = 0;
  const states = [];

  function renderOnce(controlFn) {
    stateIndex = 0;
    return controlFn({ onRestarted: () => {} });
  }

  const fakeReact = {
    createElement: (tag, props, ...children) => ({ tag, props, children }),
    useState: (initial) => {
      const idx = stateIndex++;
      if (!(idx in states)) states[idx] = initial;
      const set = (updater) => {
        states[idx] = typeof updater === 'function' ? updater(states[idx]) : updater;
      };
      return [states[idx], set];
    },
    useRef: (initial) => ({ current: initial }),
    useCallback: (fn) => fn,
    useEffect: () => {},
  };

  const BackendRestartControl = vm.runInNewContext(match[0] + '\nBackendRestartControl', {
    require: () => fakeReact,
    T: { pill: {}, miniBtn: {} },
    window: {
      fetch: async (url, init = {}) => {
        fetchCalls.push({ url, ...init });
        return {
          ok: true,
          json: async () => ({ ok: true, restarted: true }),
        };
      },
    },
  });

  const tree1 = renderOnce(BackendRestartControl);
  const restartBtn = tree1.children.filter(Boolean).find((c) => c.tag === 'button' && c.children.includes('重启后端'));
  assert.ok(restartBtn, '应当渲染「重启后端」按钮');
  await restartBtn.props.onClick();

  assert.equal(fetchCalls[0].url, '/wf-plugin/workbench/restart');
  assert.deepEqual(JSON.parse(fetchCalls[0].body), { force: true });

  const tree2 = renderOnce(BackendRestartControl);
  const texts = tree2.children.filter(Boolean).map((c) => c.children.join(''));
  assert.ok(!texts.includes('空闲后自动重启'), '不应再显示「空闲后自动重启」');
  assert.ok(!texts.includes('立即重启'), '不应再额外渲染「立即重启」按钮');
  assert.ok(texts.includes('✓ 后端已重启'), '重启完成后应当显示「✓ 后端已重启」');
});
