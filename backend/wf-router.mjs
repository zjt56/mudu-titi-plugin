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
const routerFile = join(pluginDir, 'config', 'model-router.json');
const dshHome = process.env.DSH_HOME || join(homedir(), '.dsh');
const prompt = readFileSync(promptFile, 'utf8');

function fail(message, code = 1) { console.error(`wf-router: ${message}`); process.exit(code); }
function firstHeader(name, limit) {
  const match = prompt.slice(0, limit).match(new RegExp(`^${name}:[\\t ]*([A-Za-z0-9_-]+)`, 'm'));
  return match ? match[1] : '';
}
function valid(entry) { return entry && typeof entry === 'object' && entry.provider && entry.model; }
function choose(data, stage, round, agent) {
  const def = data.default;
  if (!valid(def)) fail('model-router.json has no valid default provider/model; select a model again in the plugin settings');
  let entry;
  if (stage === 'review') {
    entry = round && data.review?.rounds?.[round];
    if (!valid(entry)) entry = agent && data.roles?.[agent];
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
function makeProfile(baseProfile, provider, model) {
  let baseDir = join(dshHome, 'profiles', baseProfile);
  if (!existsSync(baseDir)) {
    baseDir = ['wf-deepseek-fast', 'wf-deepseek', 'desktop']
      .map((x) => join(dshHome, 'profiles', x)).find(existsSync);
  }
  if (!baseDir) fail(`no base profile found under ${join(dshHome, 'profiles')}`);
  const source = join(baseDir, 'cordis.patch.yml');
  if (!existsSync(source)) fail(`base profile patch missing: ${source}`);
  const overlay = providerOverlay(source);
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
const result = runDsh(bin, ['--profile', 'headless', '--patch', routePatch, '-'], readFileSync(promptFile), env);
if (result.status === 0 && result.stdout) {
  writeFileSync(responseFile, result.stdout, 'utf8');
  process.exit(0);
}
if (result.stderr) process.stderr.write(result.stderr);
if (/DAILY_LIMIT_EXCEEDED|daily usage limit|usage limit has been reached/i.test(result.stderr || '')) console.error('wf-router: RATE_LIMIT: daily/quota exhausted');
else if (/429|RATE_LIMIT|rate[ _]limit|QUOTA|too many requests/i.test(result.stderr || '')) console.error('wf-router: RATE_LIMIT: throttled');
fail(`dsh call failed (profile=${profile} model=${selected.model} exit=${result.status ?? 1})`, result.status || 1);
