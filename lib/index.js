// ---------------------------------------------------------------------------
// 录题工作流 插件版 · Host 半边
//
// 职责：
//   1. 读写插件版自带配置（model-router.json / review-policy.json / prompts/*.md）。
//   2. 从当前 profile 的 cordis.patch.yml 解析出全部可用模型（官方 + 第三方），
//      供客户端下拉选择；每条携带真实 provider key。
//   3. 生成"生效的 local-agent 配置"：把 __PLUGIN_BACKEND__ 替换为真实路径，
//      让工作流（runner/auto/review_rounds）经由 MUDU_AGENT_CONFIG 走 wf-router.sh。
//   4. 通过 webServer 暴露 REST 路由 /wf-plugin/*，客户端读写。
//   5. （可选）用 subprocess 拉起本地工作台 Python 后端，做数据面。
//
// 全程不修改 workflow/ 与 scripts/bin/wf-agent.sh：模型路由通过
// MUDU_AGENT_CONFIG + 插件自有 wf-router.sh 注入实现。
// ---------------------------------------------------------------------------
import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync, unlinkSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import { spawn } from 'node:child_process';

export const name = 'wf-plugin';
// 只在需要时按需注入（webServer 只在 web profile 存在；subprocess 视情况）。
export const inject = [];

const HERE = dirname(fileURLToPath(import.meta.url)); // .../插件版/lib
const PLUGIN_DIR = resolve(HERE, '..');               // .../插件版
const CONFIG_DIR = join(PLUGIN_DIR, 'config');
const BACKEND_DIR = join(PLUGIN_DIR, 'backend');
const ROUTER_PATH = join(CONFIG_DIR, 'model-router.json');
const POLICY_PATH = join(CONFIG_DIR, 'review-policy.json');
const PROMPTS_DIR = join(CONFIG_DIR, 'prompts');
const DSH_HOME = process.env.DSH_HOME || join(homedir(), '.dsh');
const PROFILES_DIR = join(DSH_HOME, 'profiles');

// 录题工作台 Python 后端（原 workbench/server.py，原样复用，不改其代码）。
// 独立项目：使用插件版自带的代码副本（插件版/app），完全不影响外部工作流仓库。
const WORKFLOW_DIR = process.env.WF_WORKFLOW_DIR || resolve(PLUGIN_DIR, 'app');
const WORKBENCH_PORT = Number(process.env.WF_WORKBENCH_PORT) || 8791;
const PYTHON = process.env.WF_PYTHON || '/Users/zjt/.local/bin/python3.12';
// 后端子进程句柄（模块级，随插件生命周期；插件卸载时 kill）。
const backend = { proc: null, port: WORKBENCH_PORT, ready: false, lastError: '' };


// 允许客户端编辑的提示词文件白名单。
const PROMPT_FILES = ['labels.md', 'knowledge.md', 'sample.md', 'stages.md'];

function readJson(path, fallback) {
  try {
    return JSON.parse(readFileSync(path, 'utf8'));
  } catch {
    return fallback;
  }
}
function writeJsonAtomic(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  const tmp = path + '.tmp-' + process.pid;
  writeFileSync(tmp, JSON.stringify(value, null, 2) + '\n', 'utf8');
  renameSync(tmp, path);
}

// ---------------------------------------------------------------------------
// 录题工作台后端生命周期：subprocess 拉起 `python3.12 -m workbench.server`。
// 幂等：已在跑则不重复起；崩溃后由 ensureBackend() 重新探测/重启。
// ---------------------------------------------------------------------------
function backendUrl() {
  return 'http://127.0.0.1:' + backend.port + '/';
}

async function probeBackend(timeoutMs = 800) {
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeoutMs);
    const r = await fetch(backendUrl() + 'api/batches', { signal: ctrl.signal });
    clearTimeout(t);
    return r.ok;
  } catch {
    return false;
  }
}

function spawnBackend() {
  if (backend.proc) return backend.proc;
  const args = ['-m', 'workbench.server', '--host', '127.0.0.1', '--port', String(backend.port)];
  // 数据根：固定使用插件目录自身（上传插件版/），试卷数据落在 上传插件版/workflows、上传插件版/inbox，
  // 不再指向外层仓库，也不继承外部 WF_* 环境变量（避免被指回旧仓库写数据）。
  // 确需改位置时显式设置 WF_DATA_DIR 才生效。
  const dataRoot = process.env.WF_DATA_DIR || PLUGIN_DIR;
  const child = spawn(PYTHON, args, {
    cwd: WORKFLOW_DIR,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      WF_WORKFLOWS_DIR: join(dataRoot, 'workflows'),
      WF_INBOX_DIR: join(dataRoot, 'inbox'),
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  backend.proc = child;
  backend.ready = false;
  backend.lastError = '';
  child.stdout.on('data', (d) => { /* 后端日志：保持安静，仅有错误时记录 */ });
  child.stderr.on('data', (d) => {
    backend.lastError = String(d).slice(-800);
  });
  child.on('exit', (code, signal) => {
    backend.proc = null;
    backend.ready = false;
    if (code !== 0 && code !== null) backend.lastError = 'backend exited code=' + code + (signal ? ' sig=' + signal : '');
  });
  child.on('error', (e) => {
    backend.proc = null;
    backend.ready = false;
    backend.lastError = String(e && e.message || e);
  });
  return child;
}

// 确保后端在跑并可用；返回 {ok, url, error}。
// 启动后轮询若干次等待 listen（首次需要几秒 import workflow 包）。
async function ensureBackend({ restart = false } = {}) {
  if (!restart && await probeBackend()) {
    backend.ready = true;
    return { ok: true, url: backendUrl() };
  }
  if (restart && backend.proc) {
    try { backend.proc.kill('SIGTERM'); } catch {}
    backend.proc = null;
  }
  spawnBackend();
  for (let i = 0; i < 40; i++) {  // 最多约 20s
    await new Promise((r) => setTimeout(r, 500));
    if (await probeBackend(500)) {
      backend.ready = true;
      return { ok: true, url: backendUrl() };
    }
    if (!backend.proc) break; // 子进程已退出
  }
  return { ok: false, url: backendUrl(), error: backend.lastError || 'backend not ready' };
}

function stopBackend() {
  if (backend.proc) {
    try { backend.proc.kill('SIGTERM'); } catch {}
    backend.proc = null;
  }
  backend.ready = false;
}


// 从 profile 的 cordis.patch.yml 解析 provider -> [model ids]。
// 结构（见 wf-deepseek*）：llm-pi-ai.config.providers.<provider>.models[].id
//                        llm-deepseek.config.models[].id（provider 记为 deepseek-official）
function parseProfileModels(profileName) {
  const path = join(PROFILES_DIR, String(profileName || 'desktop'), 'cordis.patch.yml');
  if (!existsSync(path)) return [];
  const lines = readFileSync(path, 'utf8').split('\n');
  const result = [];
  let mode = null;        // 'pi' | 'deepseek'
  let provider = null;
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    const row = ln.match(/^-\s*id:\s*([\w-]+)/);
    if (row) {
      mode = null; provider = null;
      const id = row[1];
      if (id === 'llm-pi-ai') mode = 'pi';
      else if (id === 'llm-deepseek') mode = 'deepseek';
      continue;
    }
    if (mode === 'pi') {
      const prov = ln.match(/^ {6}([a-zA-Z0-9_-]+):\s*$/);
      if (prov) { provider = prov[1]; continue; }
      const model = ln.match(/^ {10}-\s*id:\s*(.+?)\s*$/);
      if (model && provider) {
        result.push({ provider, model: model[1].trim() });
      }
    } else if (mode === 'deepseek') {
      const model = ln.match(/^ {6}-\s*id:\s*(.+?)\s*$/);
      if (model) result.push({ provider: 'deepseek-official', model: model[1].trim() });
    }
  }
  // 去重
  const seen = new Set();
  return result.filter((m) => {
    const k = m.provider + '|' + m.model;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

// 汇总当前可用模型。
// 重要：临时 profile 是从 base profile（wf-deepseek / wf-deepseek-fast）复制而来，
// 所以可选模型必须是 base profile 里真实存在的那些，否则路由会 UNKNOWN_MODEL。
// 因此模型清单以 base profile 为准；desktop 的名字只用于补显示名。
async function collectModels(ctx) {
  const detected = parseProfileModels('wf-deepseek');
  if (!detected.length) {
    // 兜底：base 缺失时退回 desktop，但仅作展示，路由可能失败
    detected.push(...parseProfileModels('desktop'));
  }
  const nameMap = new Map();
  try {
    if (ctx && ctx.llm && typeof ctx.llm.listProviders === 'function') {
      const providers = await ctx.llm.listProviders();
      for (const p of providers || []) {
        const pid = p.id || p.provider || p.name;
        if (!pid || typeof ctx.llm.listModels !== 'function') continue;
        try {
          const models = await ctx.llm.listModels(pid);
          for (const m of models || []) {
            const mid = m.id || m.model || m.name;
            if (mid) nameMap.set(pid + '|' + mid, m.name || m.displayName || mid);
          }
        } catch {}
      }
    }
  } catch {}
  return detected.map((m) => ({
    provider: m.provider,
    model: m.model,
    label: nameMap.get(m.provider + '|' + m.model) || m.model,
  }));
}

// 写出生效的 local-agent 配置（替换 __PLUGIN_BACKEND__），返回路径。
function materializeAgentConfig() {
  const src = join(BACKEND_DIR, 'local-agent.plugin.json');
  const cfg = readJson(src, null);
  if (!cfg) throw new Error('缺少 backend/local-agent.plugin.json');
  cfg.command = (cfg.command || []).map((tok) =>
    typeof tok === 'string' ? tok.replace('__PLUGIN_BACKEND__', BACKEND_DIR) : tok,
  );
  const out = join(BACKEND_DIR, '.local-agent.effective.json');
  writeJsonAtomic(out, cfg);
  return out;
}

// 读取提示词文件（不存在返回空串）。
function readPrompt(file) {
  if (!PROMPT_FILES.includes(file)) throw new Error('不支持的提示词文件');
  const p = join(PROMPTS_DIR, file);
  try { return readFileSync(p, 'utf8'); } catch { return ''; }
}
function writePrompt(file, text) {
  if (!PROMPT_FILES.includes(file)) throw new Error('不支持的提示词文件');
  mkdirSync(PROMPTS_DIR, { recursive: true });
  writeFileSync(join(PROMPTS_DIR, file), String(text ?? ''), 'utf8');
}

function readBody(req, limit = 512 * 1024) {
  return new Promise((resolvePromise, reject) => {
    const chunks = [];
    let total = 0;
    req.on('data', (c) => {
      total += c.length;
      if (total > limit) { reject(new Error('请求体过大')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      try { resolvePromise(Buffer.concat(chunks).toString('utf8')); }
      catch (e) { reject(e); }
    });
    req.on('error', reject);
  });
}

// 注册 REST 路由 /wf-plugin/*
function registerRoutes(ctx, getModels) {
  const route = {
    name: 'wf-plugin-api',
    kind: 'prefix',
    path: '/wf-plugin',
    handler: async (req, res) => {
      const url = new URL(req.url, 'http://localhost');
      const sub = url.pathname.replace(/^\/wf-plugin/, '') || '/';
      const send = (status, body) => {
        const data = JSON.stringify(body);
        res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
        res.end(data);
      };
      try {
        if (req.method === 'GET' && sub === '/health') {
          let origin = '';
          try { origin = ctx.webServer && typeof ctx.webServer.getOrigin === 'function' ? ctx.webServer.getOrigin() : ''; } catch {}
          return send(200, { ok: true, pluginDir: PLUGIN_DIR, dshHome: DSH_HOME, workflowDir: WORKFLOW_DIR, origin });
        }
        // 工作台后端：状态 / 启动 / 重启 / 访问 URL
        if (req.method === 'GET' && sub === '/workbench/status') {
          const ready = await probeBackend();
          return send(200, { running: ready, port: backend.port, url: backendUrl(), error: backend.lastError });
        }
        if (req.method === 'GET' && sub === '/workbench/url') {
          const res0 = await ensureBackend();
          return send(res0.ok ? 200 : 503, { ok: res0.ok, url: res0.url, error: res0.error || undefined });
        }
        if (req.method === 'POST' && sub === '/workbench/start') {
          const r = await ensureBackend({ restart: false });
          return send(r.ok ? 200 : 503, { ok: r.ok, url: r.url, error: r.error || undefined });
        }
        if (req.method === 'POST' && sub === '/workbench/restart') {
          const r = await ensureBackend({ restart: true });
          return send(r.ok ? 200 : 503, { ok: r.ok, url: r.url, error: r.error || undefined });
        }
        if (req.method === 'GET' && sub === '/models') {
          const models = await getModels(ctx);
          return send(200, { models });
        }
        if (req.method === 'GET' && sub === '/config') {
          return send(200, {
            router: readJson(ROUTER_PATH, {}),
            policy: readJson(POLICY_PATH, {}),
            profiles: ['wf-deepseek', 'wf-deepseek-fast'],
            promptFiles: PROMPT_FILES,
          });
        }
        if (req.method === 'GET' && sub === '/prompt') {
          const file = url.searchParams.get('file') || 'labels.md';
          return send(200, { file, text: readPrompt(file) });
        }
        if (req.method === 'POST' && sub === '/router') {
          const body = JSON.parse(await readBody(req) || '{}');
          enrichRouterProfiles(body);
          writeJsonAtomic(ROUTER_PATH, body);
          return send(200, { ok: true, router: readJson(ROUTER_PATH, {}) });
        }
        if (req.method === 'POST' && sub === '/policy') {
          const body = JSON.parse(await readBody(req) || '{}');
          writeJsonAtomic(POLICY_PATH, body);
          return send(200, { ok: true, policy: readJson(POLICY_PATH, {}) });
        }
        if (req.method === 'POST' && sub === '/prompt') {
          const body = JSON.parse(await readBody(req) || '{}');
          writePrompt(body.file, body.text);
          return send(200, { ok: true });
        }
        if (req.method === 'POST' && sub === '/materialize') {
          const path = materializeAgentConfig();
          return send(200, { ok: true, path });
        }
        // 透明代理：/wf-plugin/api/* 与 /wf-plugin/preview/* 原样转发到 Python 后端，
        // 让原生面板直接调用与旧工作台完全相同的接口（含二进制媒体、multipart 上传）。
        if (sub.startsWith('/api/') || sub.startsWith('/preview') || sub.startsWith('/vendor/')) {
          return await proxyToBackend(req, res, sub);
        }
        return send(404, { error: 'not found' });
      } catch (error) {
        return send(400, { error: String(error?.message ?? error) });
      }
    },
  };
  ctx.effect(() => ctx.webServer.register(route), 'wf-plugin: routes');
}

// 把请求原样代理到 Python 后端，流式返回（支持 JSON、图片、音频、multipart）。
async function proxyToBackend(req, res, subPath) {
  // 确保后端已起（幂等，第二次是空操作）。
  if (!(await probeBackend(300))) {
    const r = await ensureBackend();
    if (!r.ok) {
      res.writeHead(503, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: '后端未就绪：' + (r.error || '') }));
      return;
    }
  }
  const target = 'http://127.0.0.1:' + backend.port + subPath + (req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : '');
  // 收集请求体（二进制安全）。
  const chunks = [];
  let total = 0;
  const LIMIT = 200 * 1024 * 1024; // 200MB（试卷+音频上传）
  await new Promise((resolve0, reject0) => {
    req.on('data', (c) => {
      total += c.length;
      if (total > LIMIT) { reject0(new Error('请求体过大')); req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', resolve0);
    req.on('error', reject0);
  });
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const headers = {};
  if (req.headers['content-type']) headers['content-type'] = req.headers['content-type'];
  if (req.headers['accept']) headers['accept'] = req.headers['accept'];
  let upstream;
  try {
    upstream = await fetch(target, { method: req.method, headers, body });
  } catch (e) {
    res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ error: '连接后端失败：' + String(e.message || e) }));
    return;
  }
  // 透传状态码与内容类型；流式回写。
  const outHeaders = {};
  const ct = upstream.headers.get('content-type');
  if (ct) outHeaders['content-type'] = ct;
  const cl = upstream.headers.get('content-length');
  if (cl) outHeaders['content-length'] = cl;
  const ar = upstream.headers.get('accept-ranges');
  if (ar) outHeaders['accept-ranges'] = ar;
  res.writeHead(upstream.status, outHeaders);
  const buf = Buffer.from(await upstream.arrayBuffer());
  res.end(buf);
}

// 写入 router 时，为每个条目补一个合理的 profile 基底（未给时按 provider 推断）。
function enrichRouterProfiles(router) {
  const pick = (provider) =>
    provider && provider.indexOf('deepseek') === 0 ? 'wf-deepseek-fast' : 'wf-deepseek-fast';
  const fill = (entry) => {
    if (!entry || typeof entry !== 'object') return entry;
    if (!entry.profile) entry.profile = pick(entry.provider);
    return entry;
  };
  fill(router.default);
  if (router.stages) for (const k of Object.keys(router.stages)) fill(router.stages[k]);
  if (router.review) {
    fill(router.review.default);
    if (router.review.rounds) for (const k of Object.keys(router.review.rounds)) fill(router.review.rounds[k]);
  }
  return router;
}

export function apply(ctx) {
  const modelsCache = { at: 0, value: [] };
  const getModels = async () => {
    const now = Date.now();
    if (now - modelsCache.at < 5000 && modelsCache.value.length) return modelsCache.value;
    const v = await collectModels(ctx);
    modelsCache.at = now;
    modelsCache.value = v;
    return v;
  };
  if (typeof ctx.inject === 'function') {
    ctx.inject(['webServer'], (scope) => {
      try { registerRoutes(scope, getModels); }
      catch (e) { console.error('[wf-plugin] route skipped:', e); }
    });
  }
  // 随插件启动拉起工作台后端；插件卸载/停用时结束该子进程，避免端口悬挂。
  ensureBackend().then((r) => {
    if (r.ok) console.log('[wf-plugin] workbench backend ready at', r.url);
    else console.error('[wf-plugin] workbench backend not ready:', r.error);
  }).catch((e) => console.error('[wf-plugin] backend start failed:', e));
  if (typeof ctx.effect === 'function') {
    try {
      ctx.effect(() => () => stopBackend(), 'wf-plugin: backend lifecycle');
    } catch {}
  }
}

// 供仓库测试用。
export const __plugin = {
  parseProfileModels,
  materializeAgentConfig,
  enrichRouterProfiles,
  ensureBackend,
  stopBackend,
  backendUrl,
  WORKFLOW_DIR,
  PLUGIN_DIR,
};
