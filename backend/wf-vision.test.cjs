const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const bridge = import(pathToFileURL(path.join(__dirname, 'wf-vision.mjs')).href);

function fixture() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'wf-vision-adapter-'));
  const root = path.join(home, 'pi-ai');
  for (const dir of ['dist/providers', 'node_modules/yaml']) fs.mkdirSync(path.join(root, dir), { recursive: true });
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ type: 'module', version: 'test' }));
  fs.writeFileSync(path.join(root, 'node_modules/yaml/index.js'), 'exports.parse=JSON.parse');
  fs.writeFileSync(path.join(root, 'dist/providers/all.js'), `export const builtinProviders=()=>[{id:'chosen',getModels:()=>[{id:'vision',api:'openai-completions',baseUrl:'https://authoritative.invalid/v1',contextWindow:10000,maxTokens:2000,input:['text','image']}]}];`);
  fs.writeFileSync(path.join(root, 'dist/index.js'), `export const createModels=()=>({setProvider(){},async completeSimple(model,context,options){return {stopReason:'stop',content:[{type:'text',text:JSON.stringify({imageCount:context.messages[0].content.filter(c=>c.type==='image').length,bytes:context.messages[0].content.filter(c=>c.type==='image').map(c=>c.data),session:!!options.sessionId,keyCorrect:options.apiKey==='secret-test-key',url:model.baseUrl})}],usage:{input:1,output:1}}}});`);
  fs.mkdirSync(path.join(home, 'plugin-state/md-upload-tiku'), { recursive: true });
  const router = path.join(home, 'plugin-state/md-upload-tiku/model-router.json');
  fs.writeFileSync(router, JSON.stringify({ default: { provider: 'chosen', model: 'vision', profile: 'custom' } }));
  fs.mkdirSync(path.join(home, 'profiles/custom'), { recursive: true });
  fs.writeFileSync(path.join(home, 'profiles/custom/cordis.patch.yml'), JSON.stringify([{ id: 'llm-pi-ai', config: { providers: { chosen: { apiKeyEnv: 'TEST_KEY' } } } }]));
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
