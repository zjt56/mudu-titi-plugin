#!/usr/bin/env node
/** Strict JSON image transport over the installed Harness pi-ai provider adapters.
 * stdin (or first file argument): {prompt, images:[{path,id}], max_tokens?}.
 * stdout (or second file argument): one JSON result; never a text CLI session.
 */
import { existsSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { randomUUID } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { homedir } from 'node:os';
import { dirname, join, resolve, delimiter } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const script = fileURLToPath(import.meta.url);
const object = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
function problem(code, message) { const error = new Error(message); error.code = code; throw error; }
function entry(value, label) {
  if (value === undefined || value === null || (object(value) && !Object.keys(value).length)) return null;
  if (!object(value) || typeof value.provider !== 'string' || !value.provider.trim()
    || typeof value.model !== 'string' || !value.model.trim()) problem('INVALID_ROUTE', `${label} requires provider and model; refusing fallback`);
  return value;
}
function header(prompt, key, limit = 4000) {
  return prompt.slice(0, limit).match(new RegExp(`^${key}:[\\t ]*([A-Za-z0-9_-]+)`, 'm'))?.[1] || '';
}
/** Same override order as wf-router; corrupt explicit overrides fail closed. */
export function chooseRoute(data, prompt, harnessDefault) {
  if (!object(data)) problem('INVALID_ROUTE', 'model-router.json must contain an object');
  const stage = header(prompt, 'Stage'); const agent = header(prompt, 'Agent'); const round = header(prompt, 'ROUND', 20000);
  const fallback = entry(data.default, 'default') || harnessDefault();
  if (!fallback) problem('MISSING_ROUTE', 'no selected provider/model; no bootstrap model fallback is permitted');
  const candidates = stage === 'review'
    ? [[data.review?.rounds?.[round], `review.rounds.${round}`], [data.roles?.[agent], `roles.${agent}`], [data.roles?.review, 'roles.review'], [data.stages?.review, 'stages.review'], [data.review?.default, 'review.default']]
    : [[data.roles?.[agent], `roles.${agent}`], [data.stages?.[stage], `stages.${stage}`]];
  for (const [value, label] of candidates) { const found = entry(value, label); if (found) return { ...found }; }
  return { ...fallback };
}
function findLauncher(env) {
  const candidates = [env.DSH_BIN, join(env.DSH_HOME || join(homedir(), '.dsh'), '.local', 'bin', 'dsh'), join(homedir(), '.local', 'bin', 'dsh'), ...(env.PATH || '').split(delimiter).map(p => join(p, process.platform === 'win32' ? 'dsh.cmd' : 'dsh'))].filter(Boolean);
  for (const file of candidates) { if (existsSync(file)) return realpathSync(file); }
  return null;
}
/** Adapter discovery changes no routing; a discovered package must serve the exact model. */
export function discoverRuntime(env = process.env, platform = process.platform) {
  if (env.WF_PI_AI_ROOT) return { root: resolve(env.WF_PI_AI_ROOT), executable: env.WF_VISION_NODE || process.execPath };
  const launcher = findLauncher(env);
  const resources = launcher && resolve(dirname(launcher), '../../..');
  const local = env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local');
  const bundles = [resources, process.resourcesPath,
    platform === 'darwin' ? '/Applications/DeepSeek Harness.app/Contents/Resources' : null,
    platform === 'win32' ? join(local, 'Programs/DeepSeek Harness/resources') : null,
    platform === 'linux' ? '/opt/DeepSeek Harness/resources' : null].filter(Boolean);
  for (const base of bundles) {
    const archive = join(base, 'app.asar');
    const executable = platform === 'darwin' ? resolve(base, '../MacOS/DeepSeek Harness')
      : resolve(base, platform === 'win32' ? '../DeepSeek Harness.exe' : '../deepseek-harness');
    if (existsSync(archive) && existsSync(executable)) return { root: join(archive, 'dsh/node_modules/@earendil-works/pi-ai'), executable };
    const root = join(base, 'app.asar.unpacked/dsh/node_modules/@earendil-works/pi-ai');
    if (existsSync(join(root, 'dist/index.js'))) return { root, executable: process.execPath };
  }
  const root = join(env.DSH_HOME || join(homedir(), '.dsh'), 'profiles/node_modules/@earendil-works/pi-ai');
  if (existsSync(join(root, 'dist/index.js'))) return { root, executable: process.execPath };
  problem('PI_AI_UNAVAILABLE', 'installed Harness pi-ai not found; set WF_PI_AI_ROOT to its package directory');
}
function loadYaml(root, env) {
  for (const base of [join(root, 'package.json'), join(env.DSH_HOME || join(homedir(), '.dsh'), 'profiles/package.json')]) {
    try { return createRequire(base)('yaml'); } catch (error) { if (error.code !== 'MODULE_NOT_FOUND') throw error; }
  }
  problem('YAML_UNAVAILABLE', 'Harness yaml parser is unavailable');
}
export function imageContent(images) {
  if (!Array.isArray(images) || !images.length) problem('INVALID_IMAGES', 'images must be a non-empty array');
  const ids = new Set(); const content = []; const metadata = [];
  for (const [index, image] of images.entries()) {
    if (!object(image) || typeof image.path !== 'string' || !image.path) problem('INVALID_IMAGES', `images[${index}].path is required`);
    const id = image.id ?? String(index + 1);
    if (typeof id !== 'string' || !id || ids.has(id)) problem('INVALID_IMAGES', `images[${index}].id must be a unique non-empty string`);
    ids.add(id);
    const bytes = readFileSync(resolve(image.path));
    let mimeType;
    if (bytes.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) mimeType = 'image/png';
    else if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) mimeType = 'image/jpeg';
    else if (['GIF87a', 'GIF89a'].includes(bytes.subarray(0, 6).toString())) mimeType = 'image/gif';
    else if (bytes.subarray(0, 4).toString() === 'RIFF' && bytes.subarray(8, 12).toString() === 'WEBP') mimeType = 'image/webp';
    else problem('UNSUPPORTED_IMAGE', `image ${id} must be a PNG, JPEG, GIF or WebP file`);
    content.push({ type: 'text', text: `Image ID: ${JSON.stringify(id)}` }, { type: 'image', data: bytes.toString('base64'), mimeType });
    metadata.push({ id, mime_type: mimeType, bytes: bytes.length });
  }
  return { content, metadata };
}
export async function runVision(input, { root, env = process.env } = {}) {
  if (!object(input) || typeof input.prompt !== 'string' || !input.prompt.trim()) problem('INVALID_REQUEST', 'prompt must be a non-empty string');
  if (input.max_tokens !== undefined && (!Number.isInteger(input.max_tokens) || input.max_tokens < 1)) problem('INVALID_REQUEST', 'max_tokens must be a positive integer');
  const yaml = loadYaml(root, env);
  const dshHome = env.DSH_HOME || join(homedir(), '.dsh');
  const patch = (name) => {
    if (typeof name !== 'string' || !/^[A-Za-z0-9._-]+$/.test(name)) problem('INVALID_PROFILE', 'invalid Harness profile name');
    const file = join(dshHome, 'profiles', name, 'cordis.patch.yml');
    if (!existsSync(file)) return null;
    const rows = yaml.parse(readFileSync(file, 'utf8'));
    if (!Array.isArray(rows)) problem('INVALID_PROFILE', `profile ${name} is not a patch list`);
    return rows;
  };
  const state = join(dshHome, 'plugin-state/md-upload-tiku/model-router.json');
  const routerFile = env.WF_ROUTER_FILE || (existsSync(state) ? state : join(env.WF_PLUGIN_DIR || resolve(dirname(script), '..'), 'config/model-router.json'));
  const selected = chooseRoute(JSON.parse(readFileSync(routerFile, 'utf8')), input.prompt, () => {
    const name = env.DSH_PROFILE || 'desktop';
    const row = patch(name)?.find(row => row.id === 'agent-default-model');
    const current = row && entry(row.config, `profile ${name} agent-default-model`);
    return current ? { ...current, profile: name } : null;
  });
  if (input.route_only === true) return { provider: selected.provider, model: selected.model, profile: selected.profile || env.DSH_PROFILE || 'wf-deepseek-fast' };
  const images = imageContent(input.images);
  const profiles = [...new Set([selected.profile || env.DSH_PROFILE || 'wf-deepseek-fast', 'desktop'])];
  let spec;
  for (const name of profiles) {
    const providers = patch(name)?.find(row => row.id === 'llm-pi-ai')?.config?.providers;
    if (providers && Object.hasOwn(providers, selected.provider)) { spec = providers[selected.provider]; break; }
  }
  if (!object(spec)) problem('NO_ADAPTER', `selected provider ${selected.provider} is not declared in the selected or desktop Harness profile`);
  const pi = await import(pathToFileURL(join(root, 'dist/index.js')).href);
  const { builtinProviders } = await import(pathToFileURL(join(root, 'dist/providers/all.js')).href);
  const catalog = builtinProviders().find(p => p.id === selected.provider);
  const catalogModels = catalog?.getModels() || [];
  const declared = spec.models?.length ? spec.models : catalogModels;
  if (!Array.isArray(declared)) problem('INVALID_PROVIDER', 'provider models must be an array');
  if (spec.modelOverrides !== undefined && (!object(spec.modelOverrides) || (spec.models?.length && Object.keys(spec.modelOverrides).length))) problem('INVALID_PROVIDER', 'modelOverrides must be an object and cannot accompany an explicit models list');
  const override = spec.modelOverrides?.[selected.model];
  if (override !== undefined && (!object(override) || !catalogModels.some(m => m.id === selected.model) || Object.hasOwn(override, 'id'))) problem('INVALID_PROVIDER', 'modelOverrides requires a known catalog model and cannot override its id');
  const listed = declared.find(m => m.id === selected.model);
  const configured = listed && { ...listed, ...override };
  if (!configured) problem('UNKNOWN_MODEL', `selected model ${selected.provider}/${selected.model} is absent from the installed/configured catalog; refusing fallback`);
  const base = catalogModels.find(m => m.id === selected.model);
  const apis = [...new Set(catalogModels.map(m => m.api))];
  const api = spec.api ?? base?.api ?? (apis.length === 1 ? apis[0] : undefined);
  const baseUrl = spec.baseURL ?? base?.baseUrl ?? catalog?.baseUrl;
  if (!api || !baseUrl) problem('INCOMPLETE_PROVIDER', `selected model ${selected.provider}/${selected.model} lacks an authoritative api/baseURL; refusing endpoint inference`);
  const model = { ...base, ...configured, id: selected.model, provider: selected.provider, api, baseUrl,
    name: configured.name ?? base?.name ?? selected.model,
    contextWindow: configured.contextWindow ?? base?.contextWindow ?? spec.defaultContextWindow,
    maxTokens: configured.maxTokens ?? base?.maxTokens ?? spec.defaultMaxTokens,
    input: configured.input ?? base?.input ?? spec.defaultInput ?? ['text'],
    reasoning: base?.reasoning ?? false,
    cost: base?.cost ?? { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
  if (!model.input.includes('image')) problem('UNSUPPORTED_CONTENT', `selected model ${selected.provider}/${selected.model} does not support image input`);
  if (![model.contextWindow, model.maxTokens].every(n => Number.isInteger(n) && n > 0)) problem('INCOMPLETE_PROVIDER', 'model contextWindow/maxTokens must be positive integers');
  if (configured.compat || spec.compat) model.compat = { ...base?.compat, ...spec.compat, ...configured.compat };
  if (configured.reasoningEfforts === false) model.reasoning = false;
  else if (object(configured.reasoningEfforts)) {
    model.reasoning = true;
    model.thinkingLevelMap = Object.fromEntries(['off','minimal','low','medium','high','xhigh','max'].map(level => [level, configured.reasoningEfforts[level] ?? null]));
  }
  let provider;
  if (catalog && spec.api === undefined) provider = { ...catalog, getModels: () => [model] };
  else {
    const modules = { 'openai-completions': ['openai-completions', 'openAICompletionsApi'], 'openai-responses': ['openai-responses', 'openAIResponsesApi'], 'anthropic-messages': ['anthropic-messages', 'anthropicMessagesApi'] };
    const module = modules[api];
    if (!module) problem('UNSUPPORTED_API', `explicit provider api ${api} is unsupported`);
    const factory = (await import(pathToFileURL(join(root, `dist/api/${module[0]}.lazy.js`)).href))[module[1]];
    provider = pi.createProvider({ id: selected.provider, name: spec.displayName || selected.provider, baseUrl, models: [model], api: factory(), auth: { apiKey: { name: selected.provider, resolve: ({ credential }) => Promise.resolve({ auth: { apiKey: credential?.key }, source: selected.provider }) } } });
  }
  const models = pi.createModels(); models.setProvider(provider);
  const ref = spec.apiKeyEnv;
  let apiKey;
  if (ref !== undefined) {
    if (typeof ref !== 'string' || !ref) problem('INVALID_CREDENTIAL_REF', 'provider apiKeyEnv must be a non-empty string');
    const credentialsFile = join(dshHome, '.credentials.yaml');
    const credentials = existsSync(credentialsFile) ? yaml.parse(readFileSync(credentialsFile, 'utf8')) : {};
    apiKey = env[ref] || credentials?.refs?.[ref] || credentials?.[ref];
    if (typeof apiKey !== 'string' || !apiKey.trim()) problem('MISSING_CREDENTIAL', `selected provider credential ${ref} is not configured`);
  }
  const timeout = Number(env.WF_VISION_TIMEOUT_MS || 180000);
  if (!Number.isFinite(timeout) || timeout <= 0) problem('INVALID_TIMEOUT', 'WF_VISION_TIMEOUT_MS must be positive');
  const maxTokens = input.max_tokens ?? Math.min(4096, model.maxTokens);
  if (maxTokens > model.maxTokens) problem('INVALID_REQUEST', 'max_tokens exceeds selected model output limit');
  let answer;
  try {
    answer = await models.completeSimple(model, { messages: [{ role: 'user', content: [{ type: 'text', text: input.prompt }, ...images.content], timestamp: Date.now() }] }, {
      ...(apiKey ? { apiKey } : {}), ...(spec.headers ? { headers: spec.headers } : {}), maxTokens,
      ...(spec.reasoning ? { reasoning: spec.reasoning } : {}), sessionId: `wf-vision-${randomUUID()}`, signal: AbortSignal.timeout(timeout), maxRetries: 0,
    });
  } catch (error) {
    // Provider diagnostics can contain request headers. Never serialize raw errors.
    problem('PROVIDER_CALL_FAILED', `selected provider ${selected.provider}/${selected.model} call failed (${error.code || error.name || 'error'}); no fallback attempted`);
  }
  if (['error', 'aborted'].includes(answer.stopReason)) problem('PROVIDER_CALL_FAILED', `selected provider ${selected.provider}/${selected.model} returned ${answer.stopReason}; no fallback attempted`);
  const text = answer.content.filter(b => b.type === 'text').map(b => b.text).join('');
  if (!text.trim()) problem('EMPTY_RESPONSE', `selected model ${selected.provider}/${selected.model} returned no text`);
  if (answer.stopReason === 'length') problem('TRUNCATED_RESPONSE', 'selected model reached max_tokens; response is incomplete');
  return { provider: selected.provider, model: selected.model, text, stop_reason: answer.stopReason, usage: answer.usage,
    images: images.metadata, transport: 'pi-ai', adapter_version: JSON.parse(readFileSync(join(root, 'package.json'), 'utf8')).version };
}
async function main() {
  const runtime = discoverRuntime();
  if (!process.env.WF_VISION_WORKER && runtime.executable !== process.execPath) {
    const child = spawnSync(runtime.executable, [script, ...process.argv.slice(2)], { input: process.argv[2] ? undefined : readFileSync(0), encoding: 'utf8', maxBuffer: 16 * 1024 * 1024,
      env: { ...process.env, ELECTRON_RUN_AS_NODE: '1', WF_VISION_WORKER: '1', WF_PI_AI_ROOT: runtime.root } });
    // Do not forward arbitrary runtime logs (which can contain headers).
    if (child.stdout) {
      let output;
      try { output = JSON.parse(child.stdout); } catch { problem('RUNTIME_START_FAILED', 'installed Harness image runtime returned non-JSON output'); }
      if (!object(output) || (!output.error && !(typeof output.provider === 'string' && typeof output.model === 'string'))) problem('RUNTIME_START_FAILED', 'installed Harness image runtime returned invalid output');
      process.stdout.write(JSON.stringify(output) + '\n');
    }
    if (child.error) problem('RUNTIME_START_FAILED', 'cannot start installed Harness image runtime');
    if (child.signal) problem('RUNTIME_START_FAILED', `installed Harness image runtime stopped (${child.signal})`);
    process.exitCode = child.status ?? 1; return;
  }
  const [inputFile, outputFile] = process.argv.slice(2);
  const input = JSON.parse(readFileSync(inputFile || 0, 'utf8'));
  const result = await runVision(input, { root: runtime.root });
  const json = JSON.stringify(result) + '\n';
  if (outputFile) writeFileSync(outputFile, json, 'utf8'); else process.stdout.write(json);
}
if (process.argv[1] && resolve(process.argv[1]) === script) main().catch(error => {
  const safe = new Set(['INVALID_ROUTE','MISSING_ROUTE','PI_AI_UNAVAILABLE','YAML_UNAVAILABLE','INVALID_IMAGES','UNSUPPORTED_IMAGE','INVALID_REQUEST','INVALID_PROFILE','NO_ADAPTER','INVALID_PROVIDER','UNKNOWN_MODEL','INCOMPLETE_PROVIDER','UNSUPPORTED_CONTENT','UNSUPPORTED_API','INVALID_CREDENTIAL_REF','MISSING_CREDENTIAL','INVALID_TIMEOUT','PROVIDER_CALL_FAILED','EMPTY_RESPONSE','TRUNCATED_RESPONSE','RUNTIME_START_FAILED']);
  process.stdout.write(JSON.stringify({ error: { code: error.code || 'VISION_FAILED', message: safe.has(error.code) ? error.message : 'vision transport failed while reading input/configuration; no fallback attempted' } }) + '\n');
  process.exitCode = 1;
});
