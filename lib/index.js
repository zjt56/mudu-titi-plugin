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
import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync, unlinkSync, watch } from 'node:fs';
import { dirname, join, resolve, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import { spawn, execFileSync } from 'node:child_process';
import { createServer } from 'node:net';
import { createQuestionBankSettings } from './question-bank.js';
import { createUploadCommandHandler } from './upload-command.js';

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
// Credentials stay outside this repository and are never returned by public config reads.
const questionBankSettings = createQuestionBankSettings({ stateDir: join(DSH_HOME, 'plugin-state', 'md-upload-tiku') });
const QUESTION_BANK_MESSAGES = {
  invalid_config: '请检查接口地址、科目、超时和密钥格式。接口地址须为 HTTPS 或本机回环 HTTP，并以 /api 结尾。',
  storage_error: '无法访问本机题库配置，请检查配置目录权限。',
  missing_api_key: '请先填写 API Key，或保存一份密钥后再测试。',
  http_error: '题库服务器拒绝了目录查询，请检查 API Key、账号状态和查询权限。',
  unsafe_redirect: '接口返回了重定向，已停止发送凭据。请填写最终的安全 API 地址。',
  timeout: '连接超时，请检查 SSH 隧道、服务器地址和超时设置。',
  network_error: '无法连接题库，请检查 SSH 隧道或 HTTPS 地址后重试。',
};

// 录题工作台 Python 后端（原 workbench/server.py，原样复用，不改其代码）。
// 独立项目：使用插件版自带的代码副本（插件版/app），完全不影响外部工作流仓库。
const WORKFLOW_DIR = process.env.WF_WORKFLOW_DIR || resolve(PLUGIN_DIR, 'app');
const WORKBENCH_PORT = Number(process.env.WF_WORKBENCH_PORT) || 8791;
// 后端子进程句柄（模块级，随插件生命周期；插件卸载时 kill）。
const backend = { proc: null, port: WORKBENCH_PORT, ready: false, lastError: '' };

// ---------------------------------------------------------------------------
// Python 运行时探测：不再写死某一台机器的路径，换机器也能跑。
// 顺序：WF_PYTHON 环境变量 → 常见绝对路径 → PATH 上的 python3.12/python3/python。
// 要求 >= 3.9（docx 解析只需标准库；PDF/OCR 另需外部配置，见 ocr_backend）。
// ---------------------------------------------------------------------------
const PYTHON_MIN = [3, 9];
let pythonCache; // undefined=未探测, null=没有可用, string=已选定
function pythonErrorHint(tried) {
  return '未找到可用的 Python（需 ' + PYTHON_MIN.join('.') + '+）。已尝试：'
    + (tried.length ? tried.join('、') : '无')
    + '。请安装 Python 3.12（https://www.python.org/downloads/），'
    + '或在插件设置/环境变量 WF_PYTHON 中指定 python 可执行文件路径。';
}
// 便携 Python 路径（app/audio/vendor/python/<platform>/...），存在才返回。
function vendorPython() {
  try {
    const arch = process.arch === 'arm64' ? 'arm64' : 'x64';
    const key = process.platform === 'darwin' ? 'darwin-' + arch
      : process.platform === 'win32' ? 'win32-' + arch : 'linux-' + arch;
    const bin = process.platform === 'win32' ? 'python.exe' : join('bin', 'python3');
    const p = join(PLUGIN_DIR, 'app', 'audio', 'vendor', 'python', key, bin);
    return existsSync(p) ? p : null;
  } catch { return null; }
}

function resolvePython() {
  if (pythonCache !== undefined) return pythonCache;
  const candidates = [];
  if (process.env.WF_PYTHON) candidates.push(process.env.WF_PYTHON);
  // 随插件分发的便携 Python 优先：不依赖用户机器预装 Python。
  const vp = vendorPython();
  if (vp) candidates.push(vp);
  candidates.push(
    '/opt/homebrew/bin/python3.12',
    '/usr/local/bin/python3.12',
    join(homedir(), '.local', 'bin', 'python3.12'),
    '/usr/bin/python3',
    '/opt/homebrew/bin/python3',
    '/usr/local/bin/python3',
    'python3.12',
    'python3',
    'python',
  );
  const tried = [];
  for (const candidate of candidates) {
    if (!candidate) continue;
    try {
      const out = execFileSync(candidate, ['-c', 'import sys;print("%d.%d"%sys.version_info[:2])'], {
        encoding: 'utf8', timeout: 5000, stdio: ['ignore', 'pipe', 'ignore'],
      }).trim();
      const [major, minor] = out.split('.').map(Number);
      const ok = major > PYTHON_MIN[0] || (major === PYTHON_MIN[0] && minor >= PYTHON_MIN[1]);
      if (ok) {
        pythonCache = candidate;
        backend.lastError = '';
        return pythonCache;
      }
      tried.push(candidate + '(v' + out + ')');
    } catch {
      tried.push(candidate);
    }
  }
  pythonCache = null;
  backend.lastError = pythonErrorHint(tried);
  return null;
}

// 数据根：默认放用户目录（升级/重装插件不丢数据、也不污染安装目录）。
// 兼容老用户：若插件目录内已有 workflows/ 数据，继续沿用原位置，避免"数据凭空消失"。
function resolveDataRoot() {
  if (process.env.WF_DATA_DIR) return process.env.WF_DATA_DIR;
  if (existsSync(join(PLUGIN_DIR, 'workflows'))) return PLUGIN_DIR;
  return join(DSH_HOME, 'wf-plugin-data');
}


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
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(backendUrl() + 'api/health', { signal: ctrl.signal });
    // A health probe must release its response, not leave a full batch listing
    // unread on a pooled connection for every image and question request.
    if (r.body) await r.body.cancel();
    return r.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(t);
  }
}

// 端口避让：每台机器环境不同，8791 可能被别的程序占用。
// 启动前先探测，占用则顺延到下一个空闲端口（客户端走代理，换端口对面板透明）。
function portIsFree(port) {
  return new Promise((resolve) => {
    const srv = createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    srv.listen(port, '127.0.0.1');
  });
}
async function pickPort(start) {
  for (let port = start; port < start + 20; port++) {
    if (await portIsFree(port)) return port;
  }
  return start; // 全被占：仍用原端口，让后续错误更直观
}

function spawnBackend() {
  if (backend.proc) return backend.proc;
  const python = resolvePython();
  if (!python) {
    // 没有可用 Python：不抛异常（否则整个插件加载失败），把原因写进 lastError 供面板显示。
    console.error('[wf-plugin] ' + backend.lastError);
    return null;
  }
  const args = ['-m', 'workbench.server', '--host', '127.0.0.1', '--port', String(backend.port)];
  // 数据根：默认用户目录（~/.dsh/wf-plugin-data）；老用户已有插件目录内数据时沿用原位置。
  const dataRoot = resolveDataRoot();
  try { mkdirSync(join(dataRoot, 'workflows'), { recursive: true }); } catch {}
  try { mkdirSync(join(dataRoot, 'inbox'), { recursive: true }); } catch {}
  const child = spawn(python, args, {
    cwd: WORKFLOW_DIR,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      WF_WORKFLOWS_DIR: join(dataRoot, 'workflows'),
      WF_INBOX_DIR: join(dataRoot, 'inbox'),
      // 听力切段适配器：运行时/模型放数据根 audio/，与后端用同一处。
      WF_AUDIO_DATA_DIR: join(dataRoot, 'audio'),
      ...questionBankSettings.runtimeEnv(),
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
let backendStartPromise = null;
async function ensureBackend(options = {}) {
  if (backendStartPromise) {
    if (!options.restart) return backendStartPromise;
    await backendStartPromise;
  }
  backendStartPromise = ensureBackendOnce(options);
  try { return await backendStartPromise; }
  finally { backendStartPromise = null; }
}
async function ensureBackendOnce({ restart = false } = {}) {
  if (!restart && await probeBackend()) {
    backend.ready = true;
    return { ok: true, url: backendUrl() };
  }
  if (restart && backend.proc) {
    try { backend.proc.kill('SIGTERM'); } catch {}
    backend.proc = null;
  }
  // 端口避让：目标端口被别的程序占用时顺延，避免"后端起不来"。
  if (!(await portIsFree(backend.port))) {
    const next = await pickPort(WORKBENCH_PORT);
    if (next !== backend.port) {
      console.log('[wf-plugin] port ' + backend.port + ' busy, switching to ' + next);
      backend.port = next;
      backend.ready = false;
    }
  }
  const child = spawnBackend();
  if (!child) {
    // 没有可用 Python：直接返回明确原因，不做无意义的 20s 轮询。
    return { ok: false, url: backendUrl(), error: backend.lastError || 'Python 不可用' };
  }
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

// ---------------------------------------------------------------------------
// 后端代码热重启：监听 app/ 下 .py 改动，去抖后自动重启 Python 后端。
// 目的：改完 workflow/*.py、workbench/*.py 等后端逻辑不必手动重启进程，
//       否则面板仍用旧代码判定（曾误显示“未执行”）。
// 只监听源码文件，忽略 __pycache__/*.pyc、隐藏文件与编辑器临时文件，
// 避免后端写字节码缓存时触发自我重启死循环。
// ---------------------------------------------------------------------------
function isBackendSource(filename) {
  if (!filename) return false;
  const name = String(filename).split('\\').join('/');
  if (!name.endsWith('.py')) return false;
  const segs = name.split('/');
  if (segs.some((s) => s === '__pycache__' || (s.startsWith('.') && s !== ''))) return false;
  if (/\.(swp|swx|tmp|bak|pyc)$/i.test(name)) return false;
  if (/~$/.test(name)) return false;
  return true;
}

let backendWatcher = null;
let restartTimer = null;
let restarting = false;
async function restartBackendSoon(reason) {
  if (restarting) {
    // 已有重启在进行：安排一次收尾重启，确保最后一次改动也生效。
    if (restartTimer) clearTimeout(restartTimer);
    restartTimer = setTimeout(() => { restartTimer = null; restartBackendSoon(reason); }, 900);
    return;
  }
  restarting = true;
  try {
    console.log('[wf-plugin] backend code changed (' + (reason || '') + '), restarting workbench…');
    const r = await ensureBackend({ restart: true });
    if (r.ok) console.log('[wf-plugin] workbench restarted at', r.url);
    else console.error('[wf-plugin] workbench restart failed:', r.error);
  } catch (e) {
    console.error('[wf-plugin] workbench restart error:', e);
  } finally {
    restarting = false;
  }
}
function startBackendWatch() {
  if (backendWatcher) return backendWatcher;
  if (!existsSync(WORKFLOW_DIR)) return null;
  let pending = null;
  const schedule = (filename) => {
    if (!isBackendSource(filename)) return;
    if (pending) clearTimeout(pending);
    // 去抖：编辑器保存常伴多次事件（写临时文件再改名），合并成一次重启。
    pending = setTimeout(() => { pending = null; restartBackendSoon(filename); }, 800);
    if (pending.unref) pending.unref();
  };
  try {
    // macOS / Windows 支持 recursive；Linux 上降级为仅监听 app/ 顶层。
    backendWatcher = watch(WORKFLOW_DIR, { recursive: true }, (event, filename) => schedule(filename));
  } catch (e) {
    try { backendWatcher = watch(WORKFLOW_DIR, (event, filename) => schedule(filename)); }
    catch (e2) { backendWatcher = null; console.error('[wf-plugin] backend watch failed:', e2); return null; }
  }
  backendWatcher.on('error', (e) => console.error('[wf-plugin] backend watch error:', e));
  console.log('[wf-plugin] watching backend sources under', WORKFLOW_DIR, 'for auto-restart');
  return backendWatcher;
}
function stopBackendWatch() {
  if (backendWatcher) { try { backendWatcher.close(); } catch {} backendWatcher = null; }
  if (restartTimer) { clearTimeout(restartTimer); restartTimer = null; }
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
function registerRoutes(ctx, getModels, bankSettings = questionBankSettings) {
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
        if (sub === '/question-bank' || sub === '/question-bank/test') {
          res.setHeader('Cache-Control', 'no-store');
          try {
            if (req.method === 'GET' && sub === '/question-bank') return send(200, { ok: true, config: bankSettings.read() });
            if (req.method !== 'POST') return send(405, { error: '不支持的请求方法。' });
            if (!/^application\/json(?:;|$)/i.test(req.headers['content-type'] || '')) return send(415, { error: '请使用 JSON 提交题库连接设置。' });
            const payload = JSON.parse(await readBody(req, 16 * 1024) || '{}');
            if (sub === '/question-bank/test') {
              const result = await bankSettings.test(payload);
              return send(result.ok ? 200 : 400, { ok: result.ok, ...(result.code ? { code: result.code } : {}), message: result.ok ? '目录连接成功；此测试只读目录，未提交题目，也不代表已验证上传权限。' : (QUESTION_BANK_MESSAGES[result.code] || '连接测试失败，请检查连接配置。') });
            }
            return send(200, { ok: true, config: bankSettings.save(payload) });
          } catch (error) {
            const code = error?.code === 'storage_error' ? 'storage_error' : 'invalid_config';
            return send(400, { ok: false, code, error: QUESTION_BANK_MESSAGES[code] });
          }
        }
        if (req.method === 'GET' && sub === '/health') {
          let origin = '';
          try { origin = ctx.webServer && typeof ctx.webServer.getOrigin === 'function' ? ctx.webServer.getOrigin() : ''; } catch {}
          return send(200, {
            ok: true, pluginDir: PLUGIN_DIR, dshHome: DSH_HOME, workflowDir: WORKFLOW_DIR,
            dataRoot: resolveDataRoot(), python: resolvePython() || null, origin,
          });
        }
        // 工作台后端：只提供 REST API，不再有任何浏览器页面。
        if (req.method === 'GET' && sub === '/workbench/status') {
          const ready = await probeBackend();
          return send(200, { running: ready, port: backend.port, error: backend.lastError });
        }
        if (req.method === 'POST' && sub === '/workbench/start') {
          const r = await ensureBackend({ restart: false });
          return send(r.ok ? 200 : 503, { ok: r.ok, error: r.error || undefined });
        }
        if (req.method === 'POST' && sub === '/workbench/restart') {
          const r = await ensureBackend({ restart: true });
          return send(r.ok ? 200 : 503, { ok: r.ok, error: r.error || undefined });
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
        // 透明代理：把 /wf-plugin/api/* 原样转发到 Python 后端，
        // 让插件面板调用 REST 接口（含二进制媒体、multipart 上传）。
        if (sub.startsWith('/api/')) {
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
  if (!backend.ready) {
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
  // GET/HEAD have no upload body. The host may already have ended their
  // stream while probeBackend awaited; waiting for a second end hangs forever.
  if (req.method !== 'GET' && req.method !== 'HEAD' && !req.readableEnded) {
    await new Promise((resolve0, reject0) => {
      if (req.destroyed || req.aborted) { reject0(new Error('请求已取消')); return; }
      req.on('data', (c) => {
        total += c.length;
        if (total > LIMIT) { reject0(new Error('请求体过大')); req.destroy(); return; }
        chunks.push(c);
      });
      req.once('end', resolve0);
      req.once('error', reject0);
      req.once('aborted', () => reject0(new Error('请求已取消')));
    });
  }
  const body = chunks.length ? Buffer.concat(chunks) : undefined;
  const headers = {};
  if (req.headers['content-type']) headers['content-type'] = req.headers['content-type'];
  if (req.headers['accept']) headers['accept'] = req.headers['accept'];
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const onClose = () => { if (!res.writableEnded) controller.abort(); };
  res.once('close', onClose);
  try {
    const upstream = await fetch(target, { method: req.method, headers, body, signal: controller.signal });
    const buf = Buffer.from(await upstream.arrayBuffer());
    if (res.destroyed) return;
    const outHeaders = {};
    const ct = upstream.headers.get('content-type');
    if (ct) outHeaders['content-type'] = ct;
    outHeaders['content-length'] = String(buf.length);
    const ar = upstream.headers.get('accept-ranges');
    if (ar) outHeaders['accept-ranges'] = ar;
    res.writeHead(upstream.status, outHeaders);
    res.end(buf);
  } catch (e) {
    backend.ready = false;
    if (!res.destroyed) {
      res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: '连接后端失败：' + String(e.message || e) }));
    }
  } finally {
    clearTimeout(timer);
    res.removeListener('close', onClose);
  }
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

// 对话框命令：在 Harness 聊天里附上原卷 + 输入 /录题上传，即可建批次并推进流程。
// 与插件面板的上传流程复用同一后端接口 /api/uploads，两条入口等价。
function registerUploadCommand(ctx) {
  if (typeof ctx.inject !== 'function') return;
  ctx.inject(['commands', 'attachments'], (scope) => {
    try {
      scope.commands.register({
        name: 'muduluti',
        description: '题库录入：把原卷（docx/pdf，可带答案卷与听力音频）上传建批次，并自动进入提取 → 打标签 → 审核流程。',
        input: { hint: '附上 docx/pdf，或引用 @"本地完整路径"；可同时附上音频，也可填写试卷名', attachments: true },
        handler: createUploadCommandHandler({ attachments: scope.attachments, ensureBackend }),
      });
    } catch (e) { console.error('[wf-plugin] upload command register failed:', e); }
  });
}

export function apply(ctx) {
  const modelsCache = { at: 0, value: [] };  const getModels = async () => {
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
  // 对话框入口：/录题上传（与面板上传复用同一后端）。
  try { registerUploadCommand(ctx); } catch (e) { console.error('[wf-plugin] upload command failed:', e); }
  // 随插件启动拉起工作台后端；插件卸载/停用时结束该子进程，避免端口悬挂。
  ensureBackend().then((r) => {
    if (r.ok) console.log('[wf-plugin] workbench backend ready at', r.url);
    else console.error('[wf-plugin] workbench backend not ready:', r.error);
  }).catch((e) => console.error('[wf-plugin] backend start failed:', e));
  if (typeof ctx.effect === 'function') {
    try {
      ctx.effect(() => () => stopBackend(), 'wf-plugin: backend lifecycle');
    } catch {}
    // 后端源码热重启：监听 app/ 下 .py 改动，去抖后自动重启 Python 后端。
    try {
      ctx.effect(() => { startBackendWatch(); return () => stopBackendWatch(); }, 'wf-plugin: backend auto-restart');
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
  isBackendSource,
  startBackendWatch,
  stopBackendWatch,
  registerUploadCommand,
  registerRoutes,
  resolvePython,
  resolveDataRoot,
  WORKFLOW_DIR,
  PLUGIN_DIR,
};
