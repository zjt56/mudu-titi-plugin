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

test('前端在排队区、审核列表和模型设置页提供重启后端与立即强启入口', () => {
  const queueHeader = client.slice(client.indexOf("'排队区'"), client.indexOf("'排队区'") + 360);
  const reviewToolbar = client.slice(client.indexOf('{ style: T.toolbarTitle }, TEXT.review'), client.indexOf('{ style: T.toolbarTitle }, TEXT.review') + 360);
  const settingsIdx = client.indexOf('onClick: writeEffective');
  const settingsFooter = client.slice(settingsIdx, settingsIdx + 260);
  assert.match(queueHeader, /BackendRestartControl/);
  assert.match(reviewToolbar, /BackendRestartControl/);
  assert.match(settingsFooter, /BackendRestartControl/);
});

test('BackendRestartControl 支持安全延后与强制立即重启交互', async () => {
  const match = client.match(/    function BackendRestartControl\(props\) \{[\s\S]*?\n    \}/);
  assert.ok(match, 'BackendRestartControl should exist in lib/client.js');
  const fetchCalls = [];
  let nextRestartPayload = { ok: true, deferred: true, busy: true };
  let stateIndex = 0;
  const states = [];
  const setters = [];
  let currentSetters = [];

  function renderOnce(controlFn) {
    stateIndex = 0;
    currentSetters = [];
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
      setters[idx] = set;
      currentSetters[idx] = set;
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
          json: async () => url.endsWith('/restart') ? nextRestartPayload : { running: true, deferred: false, restarting: false },
        };
      },
    },
  });

  const tree1 = renderOnce(BackendRestartControl);
  const restartBtn = tree1.children.filter(Boolean).find((c) => c.tag === 'button' && c.children.includes('重启后端'));
  assert.ok(restartBtn, '应当渲染「重启后端」按钮');
  await restartBtn.props.onClick();

  assert.equal(fetchCalls[0].url, '/wf-plugin/workbench/restart');
  assert.deepEqual(JSON.parse(fetchCalls[0].body), {});

  const tree2 = renderOnce(BackendRestartControl);
  const texts = tree2.children.filter(Boolean).map((c) => c.children.join(''));
  assert.ok(texts.includes('空闲后自动重启'), '忙时应当显示「空闲后自动重启」状态');
  const forceBtn = tree2.children.filter(Boolean).find((c) => c.tag === 'button' && c.children.includes('立即重启'));
  assert.ok(forceBtn, '等待空闲时应当提供「立即重启」强制按钮');

  nextRestartPayload = { ok: true, restarted: true };
  await forceBtn.props.onClick();
  assert.equal(fetchCalls[1].url, '/wf-plugin/workbench/restart');
  assert.deepEqual(JSON.parse(fetchCalls[1].body), { force: true });
});
