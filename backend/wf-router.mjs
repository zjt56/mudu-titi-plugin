#!/usr/bin/env node
/** Windows/POSIX-native model router for the workflow command provider. */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { homedir } from 'node:os';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const [promptFile, responseFile] = process.argv.slice(2);
if (!promptFile || !responseFile) fail('usage: wf-router.mjs <prompt_file> <response_file>');
const scriptDir = dirname(fileURLToPath(import.meta.url));
const pluginDir = process.env.WF_PLUGIN_DIR || resolve(scriptDir, '..');
const dshHome = process.env.DSH_HOME || join(homedir(), '.dsh');
// Per-device model selection lives in the plugin state dir; the repository
// config is only the default template and must never override a saved choice.
const stateRouter = join(dshHome, 'plugin-state', 'md-upload-tiku', 'model-router.json');
const routerFile = process.env.WF_ROUTER_FILE
  || (existsSync(stateRouter) ? stateRouter : join(pluginDir, 'config', 'model-router.json'));
const prompt = readFileSync(promptFile, 'utf8');

function fail(message, code = 1) { console.error(`wf-router: ${message}`); process.exit(code); }
function firstHeader(name, limit) {
  const match = prompt.slice(0, limit).match(new RegExp(`^${name}:[\\t ]*([A-Za-z0-9_-]+)`, 'm'));
  return match ? match[1] : '';
}
function valid(entry) { return entry && typeof entry === 'object' && entry.provider && entry.model; }
/** Read `- id: agent-default-model` provider/model from one profile patch. */
function defaultModelBlock(file) {
  if (!existsSync(file)) return null;
  let inside = false; let provider = ''; let model = '';
  for (const line of readFileSync(file, 'utf8').split(/\r?\n/)) {
    if (/^-\s*id:\s*['"]?agent-default-model['"]?\s*(?:#.*)?$/.test(line)) { inside = true; continue; }
    if (inside) {
      if (/^-\s/.test(line)) break;
      const p = line.match(/^\s+provider:\s*['"]?([^'"#\s]+)/);
      if (p) provider = p[1];
      const m = line.match(/^\s+model:\s*['"]?([^'"#]+?)['"]?\s*(?:#.*)?$/);
      if (m) model = m[1].trim();
    }
  }
  return provider && model ? { provider, model } : null;
}
/** The model this device currently has selected in its Harness profile. */
/** First declared servable model in a profile patch (bootstrap fallback). */
function firstModel(file) {
  if (!existsSync(file)) return null;
  let block = ''; let provider = '';
  for (const line of readFileSync(file, 'utf8').split(/\r?\n/)) {
    const id = line.match(/^-\s*id:\s*['"]?([A-Za-z0-9_-]+)/);
    if (id) { block = id[1]; provider = ''; continue; }
    if (block === 'llm-pi-ai') {
      const p = line.match(/^ {6}([A-Za-z0-9._-]+):\s*$/);
      if (p) { provider = p[1]; continue; }
      const m = line.match(/^ {10}-\s*id:\s*(.+?)\s*$/);
      if (m && provider) return { provider, model: m[1].trim().replace(/^['"]|['"]$/g, '') };
    } else if (block === 'llm-deepseek' || block === 'llm-deepseek-account') {
      const m = line.match(/^ {6}-\s*id:\s*(.+?)\s*$/);
      if (m) return { provider: 'deepseek-official', model: m[1].trim().replace(/^['"]|['"]$/g, '') };
    }
  }
  return null;
}
function harnessDefault() {
  const names = [process.env.DSH_PROFILE, 'desktop'].filter(Boolean);
  for (const name of names) {
    const file = join(dshHome, 'profiles', name, 'cordis.patch.yml');
    const found = defaultModelBlock(file);
    if (!found) continue;
    // Only providers declared in this profile survive the headless overlay.
    // Host-only relays (WorkBuddy magpie etc.) would fail with NO_ADAPTER.
    const declared = overlayProviders(readFileSync(file, 'utf8'));
    if (!declared.size || !declared.has(found.provider)) continue;
    return { ...found, profile: name };
  }
  // No explicit agent-default-model: fall back to the first servable model of the
  // Harness profile so a fresh device works without entering any API key.
  for (const name of ['desktop', 'wf-deepseek-fast', 'wf-deepseek']) {
    const first = firstModel(join(dshHome, 'profiles', name, 'cordis.patch.yml'));
    if (first) return { ...first, profile: name };
  }
  return null;
}
function choose(data, stage, round, agent) {
  const raw = data.default;
  let def;
  if (raw === undefined || raw === null || (typeof raw === 'object' && !Array.isArray(raw) && Object.keys(raw).length === 0)) {
    // Repository config pins no model; follow this device's Harness selection.
    def = harnessDefault();
  } else {
    // Present but corrupt stays an error: never a silent fallback.
    def = valid(raw) ? raw : null;
  }
  if (!valid(def)) fail('model-router.json has no valid default provider/model and no Harness agent-default-model was found; select a model in the plugin settings');
  let entry;
  if (stage === 'review') {
    entry = round && data.review?.rounds?.[round];
    if (!valid(entry)) entry = agent && data.roles?.[agent];
    if (!valid(entry)) entry = data.roles?.review;
    if (!valid(entry)) entry = data.stages?.review;
    if (!valid(entry)) entry = data.review?.default;
  } else {
    entry = agent && data.roles?.[agent];
    if (!valid(entry)) entry = data.stages?.[stage];
  }
  return valid(entry) ? entry : def;
}
function credential(name) {
  const file = join(dshHome, '.credentials.yaml');
  if (!existsSync(file)) return '';
  const line = readFileSync(file, 'utf8').split(/\r?\n/).find((x) => new RegExp(`^\\s*${name}:`).test(x));
  return line ? line.replace(new RegExp(`^\\s*${name}:\\s*`), '').trim().replace(/^['"]|['"]$/g, '') : '';
}
function findDsh() {
  if (process.env.DSH_BIN) return process.env.DSH_BIN;
  const localAppData = process.env.LOCALAPPDATA
    || (process.platform === 'win32' ? join(homedir(), 'AppData', 'Local') : '');
  const candidates = [
    join(dshHome, '.local', 'bin', process.platform === 'win32' ? 'dsh.cmd' : 'dsh'),
    join(localAppData, 'Programs', 'DeepSeek Harness', 'resources', 'runtime', 'cli', 'bin', 'dsh.cmd'),
    'dsh',
  ];
  return candidates.find((x) => x === 'dsh' || existsSync(x));
}
/** Preserve provider configuration verbatim, without desktop apps, tools or bundles.
 * These are the provider rows shipped in the base/headless bundle; custom API
 * providers are entries in llm-pi-ai.config.providers, not extra applications.
 */
function providerOverlay(file) {
  const lines = readFileSync(file, 'utf8').split(/\r?\n/);
  const out = [];
  for (let index = 0; index < lines.length;) {
    const start = index++;
    while (index < lines.length && !/^- /.test(lines[index])) index += 1;
    const match = lines[start].match(/^- id:\s*['"]?(llm-pi-ai|llm-deepseek|llm-deepseek-account)['"]?\s*(?:#.*)?$/);
    if (match) out.push(...lines.slice(start, index));
  }
  return out.join('\n').trimEnd();
}
/** Provider ids the route profile can actually serve, derived from the overlay
 * itself (llm-pi-ai provider map and llm-deepseek adapters). A provider that the
 * device profile declares but the base bundle does not ship is merged in by
 * makeProfile; anything still absent makes dsh fail with NO_ADAPTER, so the
 * router refuses such a selection up front. */
/** Provider sub-blocks (6-space keys under llm-pi-ai.providers) of a patch file. */
function providerBlocks(file) {
  const blocks = new Map();
  if (!existsSync(file)) return blocks;
  const lines = readFileSync(file, 'utf8').split(/\r?\n/);
  let inRow = false;
  let inProviders = false;
  let current = null;
  for (const line of lines) {
    if (/^- /.test(line)) {
      inRow = /^- id:\s*['"]?llm-pi-ai['"]?\s*(?:#.*)?$/.test(line);
      inProviders = false;
      current = null;
      continue;
    }
    if (!inRow) continue;
    if (/^\s{4}providers:\s*$/.test(line)) { inProviders = true; continue; }
    if (!inProviders) continue;
    const start = line.match(/^ {6}([A-Za-z0-9._-]+):\s*$/);
    if (start) { current = start[1]; blocks.set(current, [line]); continue; }
    if (current) {
      if (line.trim() === '' || /^ {7,}\S/.test(line)) blocks.get(current).push(line);
      else current = null;
    }
  }
  return blocks;
}
/** Merge provider blocks missing from an overlay into its llm-pi-ai row.
 * The device's own profile may declare providers (e.g. opencode-go) that the
 * workflow base profile does not ship; without them the route patch has no
 * adapter and dsh fails with NO_ADAPTER, so the selected model would be
 * refused even though it is fully configured on this device. */
function mergeProviders(overlay, extra) {
  if (!extra || !extra.size) return overlay;
  const lines = overlay.split(/\r?\n/);
  let rowStart = -1;
  for (let i = 0; i < lines.length; i += 1) {
    if (/^- id:\s*['"]?llm-pi-ai['"]?\s*(?:#.*)?$/.test(lines[i])) { rowStart = i; break; }
  }
  if (rowStart === -1) return overlay;
  let rowEnd = lines.length;
  for (let i = rowStart + 1; i < lines.length; i += 1) {
    if (/^- /.test(lines[i])) { rowEnd = i; break; }
  }
  const have = new Set();
  for (let i = rowStart; i < rowEnd; i += 1) {
    const name = lines[i].match(/^ {6}([A-Za-z0-9._-]+):\s*$/);
    if (name) have.add(name[1]);
  }
  const inject = [];
  for (const [name, block] of extra) if (!have.has(name)) inject.push(...block);
  if (!inject.length) return overlay;
  return [...lines.slice(0, rowEnd), ...inject, ...lines.slice(rowEnd)].join('\n').trimEnd();
}
function overlayProviders(patch) {
  const ids = new Set();
  let block = '';
  for (const line of patch.split(/\r?\n/)) {
    const idMatch = line.match(/^- id:\s*['"]?([A-Za-z0-9_-]+)/);
    if (idMatch) { block = idMatch[1]; continue; }
    if (block === 'llm-deepseek' || block === 'llm-deepseek-account') ids.add('deepseek-official');
    const providerMatch = block === 'llm-pi-ai' && line.match(/^ {6}([A-Za-z0-9._-]+):\s*$/);
    if (providerMatch) ids.add(providerMatch[1]);
  }
  return ids;
}
function makeProfile(baseProfile, provider, model) {
  let baseDir = join(dshHome, 'profiles', baseProfile);
  if (!existsSync(baseDir)) {
    baseDir = ['wf-deepseek-fast', 'wf-deepseek', 'desktop']
      .map((x) => join(dshHome, 'profiles', x)).find(existsSync);
  }
  if (!baseDir) fail(`no base profile found under ${join(dshHome, 'profiles')}`);
  const source = join(baseDir, 'cordis.patch.yml');
  if (!existsSync(source)) fail(`base profile patch missing: ${source}`);
  const overlay = mergeProviders(providerOverlay(source),
    providerBlocks(join(dshHome, 'profiles', 'desktop', 'cordis.patch.yml')));
  // Content-address the complete overlay, including provider endpoints/model
  // catalogs. Never reuse a copied desktop package.json or stale provider patch.
  const content = `${overlay}\n- id: agent-default-model\n  config:\n    provider: ${JSON.stringify(provider)}\n    model: ${JSON.stringify(model)}\n    reasoningEffort: null\n`;
  const key = createHash('sha256').update(`headless-overlay-v1\0${content}`).digest('hex').slice(0, 24);
  const name = `wf-plugin-route-${key}`;
  const dir = join(dshHome, 'profiles', name);
  const patch = join(dir, 'cordis.patch.yml');
  mkdirSync(dir, { recursive: true });
  if (!existsSync(patch) || readFileSync(patch, 'utf8') !== content) writeFileSync(patch, content, 'utf8');
  return name;
}
function runDsh(bin, args, input, env) {
  const js = extname(bin).toLowerCase() === '.js';
  if (js) return spawnSync(process.execPath, [bin, ...args], { input, encoding: 'utf8', env });
  if (process.platform === 'win32' && extname(bin).toLowerCase() === '.cmd') {
    // Keep command and arguments separate. Node quotes each argv item for
    // CreateProcess; embedding an already-quoted `/c` string would make cmd
    // treat the quotes as literal characters.
    return spawnSync(process.env.ComSpec || 'cmd.exe', ['/d', '/c', 'call', bin, ...args], { input, encoding: 'utf8', env, windowsHide: true });
  }
  return spawnSync(bin, args, { input, encoding: 'utf8', env });
}

let data;
try { data = JSON.parse(readFileSync(routerFile, 'utf8')); } catch (error) { fail(`cannot read model-router.json: ${error.message}`); }
const stage = firstHeader('Stage', 4000);
const round = firstHeader('ROUND', 20000);
const agent = firstHeader('Agent', 4000);
const selected = choose(data, stage, round, agent);
const profile = makeProfile(selected.profile || 'wf-deepseek-fast', selected.provider, selected.model);
const bin = findDsh();
if (!bin) fail('dsh executable not found; set DSH_BIN to dsh or dsh.cmd');
console.error(`wf-router: stage=${stage || '?'} agent=${agent || '?'} round=${round || '?'} -> provider=${selected.provider} model=${selected.model} (base=${profile})`);
const env = { ...process.env };
for (const key of ['DEEPSEEK_API_KEY', 'MAGPIE_GATEWAY_KEY', 'OPENCODE_GO_API_KEY']) {
  const value = credential(key);
  if (value) env[key] = value;
}
const routePatch = join(dshHome, 'profiles', profile, 'cordis.patch.yml');
// 选中的 provider 必须真的出现在路由 profile 里：桌面端启用的中转 provider
// （如 WorkBuddy 的 magpie）不会被复制进 headless overlay，继续调用只会得到
// NO_ADAPTER。这里提前失败并给出可选列表，避免模型错误被当成普通阶段失败。
const available = overlayProviders(readFileSync(routePatch, 'utf8'));
if (available.size && !available.has(selected.provider)) {
  fail(`provider "${selected.provider}" 不在路由 profile 的可用 provider 中（可用: ${[...available].sort().join(', ')}）。请在插件设置里重新选择模型`);
}
const result = runDsh(bin, ['--profile', 'headless', '--patch', routePatch, '-'], readFileSync(promptFile), env);
if (result.status === 0 && result.stdout) {
  writeFileSync(responseFile, result.stdout, 'utf8');
  process.exit(0);
}
if (result.stderr) process.stderr.write(result.stderr);
if (/DAILY_LIMIT_EXCEEDED|daily usage limit|usage limit has been reached/i.test(result.stderr || '')) console.error('wf-router: RATE_LIMIT: daily/quota exhausted');
else if (/429|RATE_LIMIT|rate[ _]limit|QUOTA|too many requests/i.test(result.stderr || '')) console.error('wf-router: RATE_LIMIT: throttled');
fail(`dsh call failed (profile=${profile} model=${selected.model} exit=${result.status ?? 1})`, result.status || 1);
