const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const bridge = import(pathToFileURL(path.join(__dirname, 'wf-vision.mjs')).href);

function fixture({ answer, reasoning = false, thinkingLevelMap, configuredReasoning } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-vision-adapter-'));
  const root = path.join(home, 'pi-ai');
  for (const dir of ['dist/providers', 'node_modules/yaml']) fs.mkdirSync(path.join(root, dir), { recursive: true });
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ type: 'module', version: 'test' }));
  fs.writeFileSync(path.join(root, 'node_modules/yaml/index.js'), 'exports.parse=JSON.parse');
  fs.writeFileSync(path.join(root, 'dist/providers/all.js'), `export const builtinProviders=()=>[{id:'chosen',getModels:()=>[{id:'vision',api:'openai-completions',baseUrl:'https://authoritative.invalid/v1',contextWindow:10000,maxTokens:2000,input:['text','image'],reasoning:${JSON.stringify(reasoning)},thinkingLevelMap:${JSON.stringify(thinkingLevelMap)}}]}];`);
  fs.writeFileSync(path.join(root, 'dist/index.js'), `export const createModels=()=>({setProvider(){},async completeSimple(model,context,options){${answer ? `return ${JSON.stringify(answer)};` : ''}return {stopReason:'stop',content:[{type:'text',text:JSON.stringify({imageCount:context.messages[0].content.filter(c=>c.type==='image').length,bytes:context.messages[0].content.filter(c=>c.type==='image').map(c=>c.data),session:!!options.sessionId,keyCorrect:options.apiKey==='secret-test-key',url:model.baseUrl,reasoning:options.reasoning})}],usage:{input:1,output:1}}}});`);
  fs.mkdirSync(path.join(home, 'plugin-state/md-upload-tiku'), { recursive: true });
  const router = path.join(home, 'plugin-state/md-upload-tiku/model-router.json');
  fs.writeFileSync(router, JSON.stringify({ default: { provider: 'chosen', model: 'vision', profile: 'custom' } }));
  fs.mkdirSync(path.join(home, 'profiles/custom'), { recursive: true });
  fs.writeFileSync(path.join(home, 'profiles/custom/cordis.patch.yml'), JSON.stringify([{ id: 'llm-pi-ai', config: { providers: { chosen: { apiKeyEnv: 'TEST_KEY', ...(configuredReasoning !== undefined ? { reasoning: configuredReasoning } : {}) } } } }]));
  fs.writeFileSync(path.join(home, '.credentials.yaml'), JSON.stringify({ refs: { TEST_KEY: 'secret-test-key' } }));
  const image = path.join(home, 'image.png'); fs.writeFileSync(image, Buffer.from('89504e470d0a1a0a00000000', 'hex'));
  return { home, root, router, image, env: { DSH_HOME: home } };
}

test('runtime discovery supports packaged Windows layout and explicit installed package', async () => {
  const { discoverRuntime } = await bridge;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-vision-win-'));
  try {
    const app = path.join(dir, 'Programs/DeepSeek Harness');
    fs.mkdirSync(path.join(app, 'resources'), {recursive:true});
    fs.writeFileSync(path.join(app, 'resources/app.asar'), 'archive');
    fs.writeFileSync(path.join(app, 'DeepSeek Harness.exe'), 'executable');
    const runtime = discoverRuntime({ DSH_HOME: dir, LOCALAPPDATA: dir, PATH: '' }, 'win32');
    assert.equal(runtime.root, path.join(app, 'resources/app.asar/dsh/node_modules/@earendil-works/pi-ai'));
    assert.equal(runtime.executable, path.join(app, 'DeepSeek Harness.exe'));
    assert.equal(discoverRuntime({WF_PI_AI_ROOT:dir,WF_VISION_NODE:'exact-node'}).executable,'exact-node');
  } finally { fs.rmSync(dir,{recursive:true}); }
});
test('route selection mirrors role/stage/review order', async () => {
  const { chooseRoute } = await bridge;
  const d = { provider: 'default', model: 'd' };
  const role = { provider: 'role', model: 'r' };
  const stage = { provider: 'stage', model: 's' };
  const round = { provider: 'round', model: 'v' };
  const data = { default: d, roles: { analyst: role }, stages: { extract: stage, review: stage }, review: { rounds: { two: round } } };
  assert.deepEqual(chooseRoute(data, 'Stage: extract\nAgent: analyst', () => null), role);
  assert.deepEqual(chooseRoute(data, 'Stage: extract', () => null), stage);
  assert.deepEqual(chooseRoute(data, 'Stage: review\nROUND: two\nAgent: analyst', () => null), round);
  assert.deepEqual(chooseRoute({ default: {} }, '', () => d), d);
});
test('corrupt explicit route never falls back', async () => {
  const { chooseRoute } = await bridge;
  assert.throws(() => chooseRoute({ default: { provider: 'broken' } }, '', () => ({ provider: 'other', model: 'other' })), /refusing fallback/);
  assert.throws(() => chooseRoute({ default: { provider: 'ok', model: 'ok' }, stages: { extract: { model: 'broken' } } }, 'Stage: extract', () => null), /refusing fallback/);
  assert.throws(() => chooseRoute({ default: {} }, '', () => null), /no selected/);
});
test('multi-image input contains bytes and IDs, not only paths', async () => {
  const { imageContent } = await bridge;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-vision-test-'));
  try {
    const one = path.join(dir, 'a.png');
    fs.writeFileSync(one, Buffer.from('89504e470d0a1a0a00000000', 'hex'));
    const result = imageContent([{ id: 'a', path: one }, { id: 'b', path: one }]);
    assert.equal(result.content.filter(x => x.type === 'image').length, 2);
    assert.equal(result.content[1].data, fs.readFileSync(one).toString('base64'));
    assert.equal(result.content[1].mimeType, 'image/png');
    assert.deepEqual(result.metadata.map(x => x.id), ['a', 'b']);
    assert.throws(() => imageContent([{ id: 'a', path: one }, { id: 'a', path: one }]), /unique/);
    assert.throws(() => imageContent([]), /non-empty/);
    fs.writeFileSync(one, 'not an image');
    assert.throws(() => imageContent([{ id: 'a', path: one }]), /PNG, JPEG/);
  } finally { fs.rmSync(dir, { recursive: true }); }
});
test('native adapter receives both images, exact catalog endpoint, in-memory credential and session', async () => {
  const { runVision } = await bridge; const f = fixture();
  try {
    const result = await runVision({ prompt: 'Stage: extract\nread', images: [{id:'a',path:f.image},{id:'b',path:f.image}] }, f);
    const text = JSON.parse(result.text);
    assert.equal(result.provider, 'chosen'); assert.equal(result.model, 'vision');
    assert.equal(text.imageCount, 2); assert.equal(text.session, true); assert.equal(text.keyCorrect, true);
    assert.equal(text.bytes[0], fs.readFileSync(f.image).toString('base64'));
    assert.equal(text.url, 'https://authoritative.invalid/v1');
    assert.ok(!JSON.stringify(result).includes('secret-test-key'));
  } finally { fs.rmSync(f.home, { recursive: true }); }
});
test('unknown exact model and missing credential fail without alternate dispatch', async () => {
  const { runVision } = await bridge; const f = fixture();
  try {
    const input = { prompt: 'read', images: [{id:'a',path:f.image}] };
    fs.writeFileSync(f.router, JSON.stringify({ default: { provider: 'chosen', model: 'unknown', profile: 'custom' } }));
    await assert.rejects(runVision(input, f), error => error.code === 'UNKNOWN_MODEL');
    fs.writeFileSync(f.router, JSON.stringify({ default: { provider: 'chosen', model: 'vision', profile: 'custom' } }));
    fs.writeFileSync(path.join(f.home, '.credentials.yaml'), '{}');
    await assert.rejects(runVision(input, f), error => error.code === 'MISSING_CREDENTIAL');
  } finally { fs.rmSync(f.home, { recursive: true }); }
});
test('truncated thinking is classified before empty with safe diagnostics only', async () => {
  const { runVision } = await bridge;
  const f = fixture({ answer: { stopReason: 'length', rawStopReason: 'length', content: [{ type: 'thinking', thinking: 'PRIVATE_THINKING' }], usage: { input: 1, output: 10000, reasoning: 10000, secret: 'PRIVATE_USAGE' }, errorMessage: 'PRIVATE_ERROR', durationMs: 12 } });
  try {
    await assert.rejects(runVision({ prompt: 'read', images: [{ id: 'a', path: f.image }] }, f), error => {
      assert.equal(error.code, 'TRUNCATED_RESPONSE');
      assert.deepEqual(error.diagnostics, { stop_reason: 'length', raw_stop_reason: 'length', content_types: ['thinking'], usage: { input: 1, output: 10000, reasoning: 10000 }, duration_ms: 12 });
      assert.ok(!JSON.stringify(error).includes('PRIVATE_'));
      return true;
    });
  } finally { fs.rmSync(f.home, { recursive: true }); }
});
test('empty tool response retains block type without arguments or raw errors', async () => {
  const { runVision } = await bridge;
  const f = fixture({ answer: { stopReason: 'toolUse', content: [{ type: 'toolCall', arguments: { secret: 'PRIVATE_TOOL' } }], usage: { output: 4 } } });
  try {
    await assert.rejects(runVision({ prompt: 'read', images: [{ id: 'a', path: f.image }] }, f), error => {
      assert.equal(error.code, 'EMPTY_RESPONSE'); assert.deepEqual(error.diagnostics.content_types, ['toolCall']);
      assert.ok(!JSON.stringify(error).includes('PRIVATE_')); return true;
    });
  } finally { fs.rmSync(f.home, { recursive: true }); }
});
test('reasoning input supports catalog levels and preserves explicit provider reasoning', async () => {
  const { runVision, requestReasoning } = await bridge;
  const model = { reasoning: true, thinkingLevelMap: { off: null, minimal: null, low: 'low', medium: null, high: 'high', xhigh: null, max: 'max' } };
  assert.equal(requestReasoning(model, 'low'), 'low');
  assert.equal(requestReasoning(model, undefined, 'high'), 'high');
  assert.equal(requestReasoning(model, 'high', 'high'), 'high');
  for (const level of ['off', 'minimal', 'medium', 'bogus', null, {}]) assert.throws(() => requestReasoning(model, level), error => error.code === 'INVALID_REQUEST');
  assert.throws(() => requestReasoning(model, 'low', 'high'), /conflicts/);
  assert.throws(() => requestReasoning({ reasoning: false }, 'low'), /supported catalog/);
  const f = fixture({ reasoning: true, thinkingLevelMap: model.thinkingLevelMap });
  try {
    const result = await runVision({ prompt: 'read', images: [{ id: 'a', path: f.image }], reasoning: 'low' }, f);
    assert.equal(JSON.parse(result.text).reasoning, 'low');
    await assert.rejects(runVision({ prompt: 'read', images: [{ id: 'a', path: f.image }], reasoning: 'off' }, f), error => error.code === 'INVALID_REQUEST');
  } finally { fs.rmSync(f.home, { recursive: true }); }
});
test('configured provider reasoning wins and conflicting input is rejected before dispatch', async () => {
  const { runVision } = await bridge;
  const f = fixture({ reasoning: true, thinkingLevelMap: { off: null, low: 'low', high: 'high' }, configuredReasoning: 'high' });
  const input = { prompt: 'read', images: [{ id: 'a', path: f.image }] };
  try {
    const result = await runVision(input, f);
    assert.equal(JSON.parse(result.text).reasoning, 'high');
    await assert.rejects(runVision({ ...input, reasoning: 'low' }, f), error => error.code === 'INVALID_REQUEST' && /conflicts/.test(error.message));
  } finally { fs.rmSync(f.home, { recursive: true }); }
});
test('response diagnostics whitelist provider-controlled strings and numeric usage', async () => {
  const { responseDiagnostics } = await bridge;
  const result = responseDiagnostics({ stopReason: 'SECRET', rawStopReason: 'PRIVATE', content: [{ type: 'UNKNOWN_SECRET', text: 'SECRET' }], usage: { output: -1, input: Infinity, reasoning: 2, token: 'SECRET' } });
  assert.deepEqual(result, { stop_reason: 'unknown', raw_stop_reason: 'unknown', content_types: ['unknown'], usage: { reasoning: 2 } });
});
test('route-only reads exact plugin state with no model call', async () => {
  const { runVision } = await bridge;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-vision-route-'));
  try {
    fs.mkdirSync(path.join(dir, 'plugin-state/md-upload-tiku'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'plugin-state/md-upload-tiku/model-router.json'), JSON.stringify({ default: { provider: 'current', model: 'precise', profile: 'custom' } }));
    const root = path.join(dir, 'pi-ai');
    fs.mkdirSync(path.join(root, 'node_modules/yaml'), { recursive: true });
    fs.writeFileSync(path.join(root, 'node_modules/yaml/index.js'), 'exports.parse=JSON.parse');
    const result = await runVision({ prompt: 'Stage: extract', route_only: true }, { root, env: { DSH_HOME: dir } });
    assert.deepEqual(result, { provider: 'current', model: 'precise', profile: 'custom' });
  } finally { fs.rmSync(dir, { recursive: true }); }
});
