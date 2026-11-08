const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../lib/index.js'), 'utf8');
const routes = source.slice(source.indexOf('function registerRoutes('), source.indexOf('// 把请求原样代理'));

async function request(method, url, payload, operations) {
  let route;
  const ctx = { webServer: { register(value) { route = value; return () => {}; } }, effect(fn) { fn(); } };
  const env = { URL, Buffer, console, questionBankSettings: {}, ocrSettings: {}, readBody: async () => JSON.stringify(payload) };
  vm.createContext(env);
  vm.runInContext(routes + '\nthis.register = registerRoutes;', env);
  env.register(ctx, async () => [], {}, {}, operations);
  const response = { headers: {}, setHeader(k, v) { this.headers[k] = v; }, writeHead(status) { this.status = status; }, end(value) { this.body = JSON.parse(value); } };
  await route.handler({ method, url, headers: { 'content-type': 'application/json' } }, response);
  return response;
}

test('operations status remains explicit when optional Harness services are absent', async () => {
  const result = await request('GET', '/wf-plugin/operations/status', null, null);
  assert.equal(result.status, 200);
  assert.equal(result.body.available, false);
  assert.match(result.body.error, /原有录题/);
});

test('operations configuration goes through shared owner and reports actual failures', async () => {
  let value = true;
  const operations = { status: () => ({ available: true, enabled: value }), configure: async input => {
    if (typeof input.enabled !== 'boolean') throw new Error('invalid enabled');
    value = input.enabled;
    return { available: true, enabled: value };
  } };
  let response = await request('POST', '/wf-plugin/operations/config', { enabled: false }, operations);
  assert.equal(response.status, 200);
  assert.equal(response.body.enabled, false);
  response = await request('GET', '/wf-plugin/operations/status', null, operations);
  assert.equal(response.body.enabled, false);
  response = await request('POST', '/wf-plugin/operations/config', { enabled: 'yes' }, operations);
  assert.equal(response.status, 400);
  assert.match(response.body.error, /invalid/);
});
