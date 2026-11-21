const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8').replace(/\r\n/g, '\n');

test('Harness window drag is limited to the recording panel header', () => {
  const component = source.match(/    function WfPanel\(props\) \{[\s\S]*?\n    \}/)[0];
  const render = vm.runInNewContext(component + '\nWfPanel', {
    require: () => ({
      useState: value => [value, () => {}],
      createElement: (tag, props, ...children) => ({ tag, props, children }),
    }),
    T: { tab: () => ({}) },
    TEXT: {},
    docIcon() {},
    closeIcon() {},
    UploadView() {},
    WorkbenchTab() {},
    SettingsTab() {},
  });
  const closePanel = () => {};
  const panel = render({ closePanel });
  const [header, body] = panel.children;
  assert.equal(header.props['data-window-drag'], '');
  assert.ok(!('data-window-drag' in panel.props));
  assert.ok(!('data-window-drag' in body.props));
  const tabList = header.children.find(child => child.props.role === 'tablist');
  assert.equal(tabList.children.length, 3);
  assert.ok(tabList.children.every(tab => tab.tag === 'button' && typeof tab.props.onClick === 'function'));
  assert.ok(header.children.some(child => child.tag === 'button' && child.props.onClick === closePanel));
});
