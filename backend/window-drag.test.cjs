const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = readFileSync(path.join(__dirname, '../lib/client.js'), 'utf8').replace(/\r\n/g, '\n');

test('Harness window drag is limited to the recording panel header', () => {
  const component = source.match(/    function WfPanel\(props\) \{[\s\S]*?\n    \}/)[0];
  const tabbar = source.match(/      tabbar: (\{[\s\S]*?\n      \}),/)[1];
  const render = vm.runInNewContext(component + '\nWfPanel', {
    require: () => ({
      useState: value => [value, () => {}],
      createElement: (tag, props, ...children) => ({ tag, props, children }),
    }),
    T: { tab: () => ({}), tabbar: vm.runInNewContext('(' + tabbar + ')') },
    TEXT: vm.runInNewContext('(' + source.match(/var TEXT = (\{[\s\S]*?\n    \});/)[1] + ')'),
    BRAND_LOGO: source.match(/var BRAND_LOGO = '([^']+)'/)[1],
    closeIcon() {},
    UploadView() {},
    WorkbenchTab() {},
    TypeReviewTab() {},
    SettingsTab() {},
  });
  const closePanel = () => {};
  const panel = render({ closePanel });
  const [header, body] = panel.children;
  assert.equal(header.props['data-window-drag'], '');
  assert.equal(header.props.style.paddingInlineStart, 'max(18px, var(--dsh-frame-leading-clearance, 0px))');
  assert.equal(header.props.style.paddingInlineEnd, 18);
  const [logo, title] = header.children[0].children;
  assert.equal(title.children[0], '木牍题库系统');
  assert.equal(logo.tag, 'img');
  assert.match(logo.props.src, /^data:image\/webp;base64,UklGR/);
  assert.equal(logo.props.draggable, false);
  assert.ok(!('data-window-drag' in panel.props));
  assert.ok(!('data-window-drag' in body.props));
  const tabList = header.children.find(child => child.props.role === 'tablist');
  assert.equal(tabList.children.length, 4);
  assert.deepEqual(tabList.children.map(tab => tab.children[0]), ['上传试卷', '试卷审核', '题型审核', '模型与设置']);
  assert.ok(tabList.children.every(tab => tab.tag === 'button' && typeof tab.props.onClick === 'function'));
  assert.ok(header.children.some(child => child.tag === 'button' && child.props.onClick === closePanel));
});
