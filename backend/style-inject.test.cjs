const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const client = fs.readFileSync(path.resolve(__dirname, '..', 'lib/client.js'), 'utf8');

function loadInject() {
  const start = client.indexOf('function injectStyles()');
  const end = client.indexOf('\n    }\n', start) + 7;
  const code = 'var STYLE_ID="wf-plugin-styles";\n' + client.slice(start, end) + '\ninjectStyles';
  return { code };
}

class Doc { constructor(){ this.head=new Head(this); this.els={}; }
  createElement(){ return new El(this); }
  getElementById(id){ return this.els[id]||null; } }
class Head { constructor(d){ this.d=d; this.kids=[]; }
  appendChild(e){ this.kids.push(e); if(e.id) this.d.els[e.id]=e; } }
class El { constructor(d){ this.d=d; this._id=''; this.textContent=''; }
  set id(v){ this._id=v; if(this.d) this.d.els[v]=this; } get id(){ return this._id; } }

test('injectStyles 热加载时覆盖旧 <style> 内容，而不是因已存在而跳过', () => {
  const doc = new Doc();
  const ctx = vm.createContext({ document: doc });
  const fn = vm.runInContext(loadInject().code, ctx);
  // 第一次注入
  fn();
  const el = doc.getElementById('wf-plugin-styles');
  assert.ok(el, '应创建 style');
  // 模拟旧内容残留（HMR 后 DOM 里还是旧 CSS）
  el.textContent = 'STALE-OLD-CSS';
  // 再次注入（等价于 HMR 重跑 apply）
  fn();
  assert.notEqual(doc.getElementById('wf-plugin-styles').textContent, 'STALE-OLD-CSS', '必须覆盖为最新 CSS');
  assert.match(doc.getElementById('wf-plugin-styles').textContent, /minmax\(300px,1fr\)/, '注入的是新布局 CSS');
  assert.equal(doc.head.kids.length, 1, '不应重复插入 style 元素');
});
