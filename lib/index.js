// ---------------------------------------------------------------------------
// 录题工作流 插件版 · Host 半边
//
// 职责：
//   1. 读写插件版自带配置（model-router.json / review-policy.json / prompts/*.md）。
//   2. 从当前 profile 的 cordis.patch.yml 解析出全部可用模型（官方 + 第三方），
//      供客户端下拉选择；每条携带真实 provider key。
//   3. 生成"生效的 local-agent 配置"：把 __PLUGIN_BACKEND__ 替换为真实路径，
//      让工作流（runner/auto/review_rounds）经由 MUDU_AGENT_CONFIG 走 wf-router.mjs。
//   4. 通过 webServer 暴露 REST 路由 /wf-plugin/*，客户端读写。
//   5. （可选）用 subprocess 拉起本地工作台 Python 后端，做数据面。
//
// 全程不修改 workflow/ 与 scripts/bin/wf-agent.sh：模型路由通过
// MUDU_AGENT_CONFIG + 插件自有 wf-router.mjs 注入实现。
// ---------------------------------------------------------------------------
import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync, unlinkSync, readlinkSync, watch } from 'node:fs';
import { dirname, join, resolve, basename } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import { spawn, execFileSync } from 'node:child_process';
import { createServer } from 'node:net';
import { randomUUID } from 'node:crypto';
import { createQuestionBankSettings } from './question-bank.js';
import { createUploadCommandHandler } from './upload-command.js';
import { createOCRSettings, OCR_MESSAGES } from './ocr-settings.js';
import { createOperationsSettings, operationsRoute } from './operations-settings.js';
import { createOperationsAgentManager } from './operations-agent.js';
import { createReleaseCoordinator } from './release-coordinator.js';
import { restoreRelease } from './code-restore.js';

export const name = 'wf-plugin';
// 只在需要时按需注入（webServer 只在 web profile 存在；subprocess 视情况）。
export const inject = [];

const HERE = dirname(fileURLToPath(import.meta.url)); // .../插件版/lib
const PLUGIN_DIR = resolve(HERE, '..');               // .../插件版
const CONFIG_DIR = join(PLUGIN_DIR, 'config');
const BACKEND_DIR = join(PLUGIN_DIR, 'backend');
// 每台设备选择的模型必须保存在本机插件状态目录，不能放仓库 config/：
// 仓库 config/model-router.json 只是默认模板（default 为空），升级/检出不能
// 覆盖用户在设置页选好的模型。
const POLICY_PATH = join(CONFIG_DIR, 'review-policy.json');
const PROMPTS_DIR = join(CONFIG_DIR, 'prompts');
const DSH_HOME = process.env.DSH_HOME || join(homedir(), '.dsh');
const LEGACY_ROUTER_PATH = join(CONFIG_DIR, 'model-router.json');
const ROUTER_PATH = process.env.WF_ROUTER_FILE
  || join(DSH_HOME, 'plugin-state', 'md-upload-tiku', 'model-router.json');
const PROFILES_DIR = join(DSH_HOME, 'profiles');
// Credentials stay outside this repository and are never returned by public config reads.
const questionBankSettings = createQuestionBankSettings({ stateDir: join(DSH_HOME, 'plugin-state', 'md-upload-tiku') });
const operationsSettings = createOperationsSettings({ stateDir: join(DSH_HOME, 'plugin-state', 'md-upload-tiku') });
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
const ocrSettings = createOCRSettings({ stateDir: join(DSH_HOME, 'plugin-state', 'md-upload-tiku'), python: resolvePython, workflowDir: WORKFLOW_DIR });
const WORKBENCH_PORT = Number(process.env.WF_WORKBENCH_PORT) || 8791;
// 后端子进程句柄（模块级，随插件生命周期；插件卸载时 kill）。
const backend = { proc: null, instanceId: '', port: WORKBENCH_PORT, ready: false, lastError: '' };

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

function emptyRouter() {
  return { default: {}, stages: {}, review: { rounds: {} }, roles: {} };
}

// Create the per-device router on first use, migrating a previously selected
// model from the repository config (older builds stored it there).
function ensureRouterFile() {
  try {
    if (existsSync(ROUTER_PATH)) return ROUTER_PATH;
    const legacy = readJson(LEGACY_ROUTER_PATH, null);
    const usable = legacy && typeof legacy === 'object' && !Array.isArray(legacy)
      && (legacy.default?.provider || (legacy.roles && Object.keys(legacy.roles).length)
          || (legacy.stages && Object.keys(legacy.stages).length));
    writeJsonAtomic(ROUTER_PATH, usable ? legacy : emptyRouter());
  } catch (error) {
    console.error('[wf-plugin] 初始化模型路由配置失败：', error?.message || error);
  }
  return ROUTER_PATH;
}

function readRouter() {
  ensureRouterFile();
  const value = readJson(ROUTER_PATH, null);
  return value && typeof value === 'object' && !Array.isArray(value) ? value : emptyRouter();
}

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

function backendChildAlive(child) {
  return !!child && !!child.pid && child.exitCode === null && child.signalCode === null;
}

async function probeBackend(timeoutMs = 800) {
  const child = backend.proc;
  const instanceId = backend.instanceId;
  // Never adopt an unrelated or orphaned server merely because its health is 200.
  if (!backendChildAlive(child) || !instanceId) return false;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(backendUrl() + 'api/health', { signal: ctrl.signal });
    if (!r.ok) {
      if (r.body) await r.body.cancel();
      return false;
    }
    const health = await r.json();
    return backend.proc === child && backend.instanceId === instanceId
      && backendChildAlive(child) && health.ok === true && health.instance_id === instanceId;
  } catch {
    return false;
  } finally {
    clearTimeout(t);
  }
}

// 清理本插件遗留的 workbench.server 进程。
// 历史问题：宿主热重启时只停掉外层 bash，Python 子进程活了下来；每重启一次就
// 多占一个端口，累计 20 次后 8791–8810 全满，新后端绑定失败 exit code=1。
// 这里只杀 cwd 指向本插件 app/ 的 workbench.server，绝不按端口盲杀其他程序。
function decodeLsofPath(value) {
  // macOS lsof 把非 ASCII 路径写成 \xNN 字节转义；按 UTF-8 还原后再比较，
  // 否则中文插件路径永远匹配不上，孤儿进程就清不掉。
  const bytes = [];
  for (let i = 0; i < value.length; i++) {
    if (value[i] === '\\' && value[i + 1] === 'x' && /^[0-9a-fA-F]{2}$/.test(value.slice(i + 2, i + 4))) {
      bytes.push(parseInt(value.slice(i + 2, i + 4), 16));
      i += 3;
    } else {
      const code = value.codePointAt(i);
      if (code > 0xffff) {
        const pair = Buffer.from(String.fromCodePoint(code), 'utf8');
        for (const byte of pair) bytes.push(byte);
        i++;
      } else {
        const chunk = Buffer.from(String.fromCharCode(code), 'utf8');
        for (const byte of chunk) bytes.push(byte);
      }
    }
  }
  return Buffer.from(bytes).toString('utf8');
}

function workbenchProcessCwd(pid) {
  try {
    if (process.platform === 'linux') return readlinkSync('/proc/' + pid + '/cwd');
    if (process.platform === 'darwin') {
      const out = execFileSync('lsof', ['-a', '-p', String(pid), '-d', 'cwd', '-Fn'], {
        encoding: 'utf8', timeout: 2000, stdio: ['ignore', 'pipe', 'ignore'],
      });
      const line = String(out).split('\n').find((item) => item.startsWith('n'));
      return line ? decodeLsofPath(line.slice(1)) : '';
    }
  } catch { /* 进程可能已退出或无权读取 */ }
  return '';
}

function reapStaleWorkbenchProcesses(keepPid) {
  if (process.platform === 'win32') return 0; // Windows 由 taskkill /T 负责进程树
  let output = '';
  try {
    output = execFileSync('ps', ['-axo', 'pid=,command='], {
      encoding: 'utf8', timeout: 3000, stdio: ['ignore', 'pipe', 'ignore'],
    });
  } catch { return 0; }
  const keep = Number(keepPid || 0);
  const wanted = resolve(WORKFLOW_DIR);
  let killed = 0;
  for (const raw of String(output).split('\n')) {
    const match = raw.trim().match(/^(\d+)\s+(.*)$/);
    if (!match) continue;
    const pid = Number(match[1]);
    const command = match[2];
    if (!pid || pid === process.pid || pid === keep) continue;
    if (!command.includes('workbench.server') || !command.includes('--host 127.0.0.1')) continue;
    const cwd = workbenchProcessCwd(pid);
    if (!cwd || resolve(cwd) !== wanted) continue;
    try { process.kill(pid, 'SIGTERM'); killed++; } catch { /* 已退出 */ }
  }
  if (killed) console.log('[wf-plugin] cleaned ' + killed + ' stale workbench process(es)');
  return killed;
}

// 端口避让：每台机器环境不同，8791 可能被别的程序占用。
// 启动前先探测，占用则顺延；扫描范围放宽到 100 个，并在全占时先清理本插件孤儿。
function portIsFree(port) {
  return new Promise((resolve) => {
    const srv = createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    srv.listen(port, '127.0.0.1');
  });
}
async function pickPort(start) {
  for (let port = start; port < start + 100; port++) {
    if (await portIsFree(port)) return port;
  }
  reapStaleWorkbenchProcesses(backend.proc && backend.proc.pid);
  for (let port = start; port < start + 100; port++) {
    if (await portIsFree(port)) return port;
  }
  return start; // 全部占用：保留明确错误，交给 spawn/health 报出来
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
  const instanceId = randomUUID();
  // 关键：工作流默认会读 workflow/local-agent.json（走 wf-agent.sh = profile 默认的
  // magpie 第三方中转），不会读插件里选的官方模型。这里生成插件版生效配置，并通过
  // MUDU_AGENT_CONFIG 注入；配置命令使用 Node 原生 backend/wf-router.mjs，按
  // model-router.json 的 provider/model 调模型，不依赖 Windows 上不存在的 /bin/sh。
  let agentConfigPath = '';
  try {
    agentConfigPath = materializeAgentConfig();
  } catch (e) {
    console.error('[wf-plugin] 生成模型路由配置失败：', e && e.message ? e.message : e);
  }
  // POSIX 下建立独立进程组：停止时 kill(-pid) 能连同外层 bash 与真正的 Python
  // 子进程一起清掉，避免热重启留下孤儿。
  const useGroup = process.platform !== 'win32';
  const child = spawn(python, args, {
    cwd: WORKFLOW_DIR,
    detached: useGroup,
    env: {
      ...process.env,
      ...(agentConfigPath ? { MUDU_AGENT_CONFIG: agentConfigPath } : {}),
      PYTHONUNBUFFERED: '1',
      // Windows 控制台/管道默认 cp936：后端与阶段子进程统一按 UTF-8 输出，
      // 否则中文日志要么变成 U+FFFD，要么在回显时抛 UnicodeEncodeError 把
      // 整个阶段卡在 running（2020 安徽卷 extract 卡死即因此而起）。
      PYTHONIOENCODING: 'utf-8',
      WF_WORKFLOWS_DIR: join(dataRoot, 'workflows'),
      WF_INBOX_DIR: join(dataRoot, 'inbox'),
      // 听力切段适配器：运行时/模型放数据根 audio/，与后端用同一处。
      WF_AUDIO_DATA_DIR: join(dataRoot, 'audio'),
      // 上传后自动识别题源分类/卷名/地区/年份；识别不出留空，失败不阻断。
      WF_AUTO_IDENTIFY_META: '1',
      // 用户要求：运行期零人工干预。工作流不再因待复核/人工闸门停住；
      // 远端上传/发布仍不自动执行（外部不可逆）。
      WF_ZERO_TOUCH: '1',
      ...questionBankSettings.runtimeEnv(),
      ...ocrSettings.runtimeEnv(),
      WF_BACKEND_INSTANCE_ID: instanceId,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  backend.proc = child;
  // 真 ChildProcess 才有 spawnfile；测试里的假进程不会走到 kill(-pid)。
  if (child.spawnfile) child.__wfDetached = useGroup;
  backend.instanceId = instanceId;
  backend.ready = false;
  backend.lastError = '';
  child.stdout.on('data', (d) => { /* 后端日志：保持安静，仅有错误时记录 */ });
  child.stderr.on('data', (d) => {
    if (backend.proc === child) backend.lastError = String(d).slice(-800);
  });
  child.on('exit', (code, signal) => {
    if (backend.proc !== child) return;
    backend.proc = null;
    backend.instanceId = '';
    backend.ready = false;
    if (code !== 0 && code !== null) backend.lastError = 'backend exited code=' + code + (signal ? ' sig=' + signal : '');
  });
  child.on('error', (e) => {
    if (backend.proc !== child) return;
    // A failed kill can emit error while the process is still alive: retain ownership.
    if (!backendChildAlive(child)) {
      backend.proc = null;
      backend.instanceId = '';
    }
    backend.ready = false;
    backend.lastError = String(e && e.message || e);
  });
  return child;
}

// 确保后端在跑并可用；返回 {ok, url, error}。
// 启动后轮询若干次等待 listen（首次需要几秒 import workflow 包）。
let backendStartPromise = null;
let backendStopVersion = 0;
async function ensureBackend(options = {}) {
  const stopVersion = backendStopVersion;
  if (backendStartPromise) {
    if (!options.restart) return backendStartPromise;
    await backendStartPromise;
    if (stopVersion !== backendStopVersion) {
      return { ok: false, url: backendUrl(), error: 'backend startup cancelled by stop' };
    }
    // Recheck after waiting: another restart may have acquired the lifecycle.
    return ensureBackend(options);
  }
  const pending = ensureBackendOnce(options);
  backendStartPromise = pending;
  try { return await pending; }
  finally { if (backendStartPromise === pending) backendStartPromise = null; }
}
async function ensureBackendOnce({ restart = false } = {}) {
  const stopVersion = backendStopVersion;
  const cancelled = () => stopVersion !== backendStopVersion;
  const stoppedResult = () => ({ ok: false, url: backendUrl(), error: 'backend startup cancelled by stop' });
  if (!restart && await probeBackend()) {
    if (cancelled()) return stoppedResult();
    backend.ready = true;
    return { ok: true, url: backendUrl() };
  }
  if (cancelled()) return stoppedResult();
  // 一次 800ms 探测失败不等于后端已死：繁忙时（模型调用、大批量装配）健康接口可能
  // 慢一秒以上。给两次更长窗口再判；只有确实不应答才整树回收，避免把在途 agent
  // 子进程连根杀掉。
  if (!restart && backend.proc && backendChildAlive(backend.proc)) {
    const slow = await probeBackend(4000) || await probeBackend(4000);
    if (slow) {
      if (cancelled()) return stoppedResult();
      backend.ready = true;
      return { ok: true, url: backendUrl() };
    }
    if (cancelled()) return stoppedResult();
  }
  // Stop an unhealthy owned child too; a slow/hung child must not be orphaned.
  if (backend.proc && !(await retireBackend())) {
    return { ok: false, url: backendUrl(), error: backend.lastError };
  }
  if (cancelled()) return stoppedResult();
  // 每次拉起前先清掉本插件上一次热重启遗留的 workbench.server，避免占满端口。
  reapStaleWorkbenchProcesses(backend.proc && backend.proc.pid);
  // Only inspect/reuse its port after the owned process has actually exited.
  // 端口避让：目标端口被别的程序占用时顺延，避免"后端起不来"。
  if (!(await portIsFree(backend.port))) {
    const next = await pickPort(WORKBENCH_PORT);
    if (next !== backend.port) {
      console.log('[wf-plugin] port ' + backend.port + ' busy, switching to ' + next);
      backend.port = next;
      backend.ready = false;
    }
  }
  if (cancelled()) return stoppedResult();
  const child = spawnBackend();
  if (!child) {
    // 没有可用 Python：直接返回明确原因，不做无意义的 20s 轮询。
    return { ok: false, url: backendUrl(), error: backend.lastError || 'Python 不可用' };
  }
  // 首次立即探测，随后短间隔退避；不让固定 500ms 睡眠成为每次启动的必付成本。
  for (let i = 0; i < 40; i++) {  // 最多约 20s
    if (cancelled()) return stoppedResult();
    if (backend.proc !== child) break;
    if (await probeBackend(i === 0 ? 250 : 500)) {
      if (cancelled()) return stoppedResult();
      backend.ready = true;
      return { ok: true, url: backendUrl() };
    }
    if (backend.proc !== child) break; // 子进程已退出或被停止
    const delay = i < 4 ? 25 : i < 12 ? 100 : 500;
    await new Promise((r) => setTimeout(r, delay));
  }
  return { ok: false, url: backendUrl(), error: backend.lastError || 'backend not ready' };
}

// 停止后端：优先清整个进程组；Windows 用 taskkill /T。绝不按端口盲杀。
function terminateProcessTree(child, signal) {
  if (!child || !child.pid) return false;
  if (process.platform === 'win32') {
    try {
      execFileSync('taskkill', ['/pid', String(child.pid), '/T', '/F'], {
        stdio: 'ignore', timeout: 5000,
      });
      return true;
    } catch {
      try { return child.kill(signal); } catch { return false; }
    }
  }
  if (child.__wfDetached) {
    try { process.kill(-child.pid, signal); return true; } catch { /* 回退到单进程 */ }
  }
  try { return child.kill(signal); } catch { return false; }
}

function signalAndWaitForExit(child, signal, timeoutMs) {
  if (!backendChildAlive(child)) return Promise.resolve(true);
  return new Promise((resolve) => {
    let timer;
    const finish = (exited) => {
      clearTimeout(timer);
      child.removeListener('exit', onExit);
      resolve(exited);
    };
    const onExit = () => finish(true);
    child.once('exit', onExit);
    timer = setTimeout(() => finish(!backendChildAlive(child)), timeoutMs);
    if (!terminateProcessTree(child, signal)) finish(!backendChildAlive(child));
  });
}

function stopBackend() {
  // Invalidate a startup that is still awaiting a probe, port check, or exit.
  backendStopVersion++;
  return retireBackend();
}

async function retireBackend() {
  const child = backend.proc;
  backend.ready = false;
  if (!child) return true;
  let exited = await signalAndWaitForExit(child, 'SIGTERM', 3000);
  if (!exited) exited = await signalAndWaitForExit(child, 'SIGKILL', 1000);
  if (!exited) {
    if (backend.proc === child) backend.lastError = 'backend did not exit; refusing to start another process';
    return false;
  }
  if (backend.proc === child) {
    backend.proc = null;
    backend.instanceId = '';
  }
  return true;
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
let backendWatchTimer = null;
let restartTimer = null;
let restarting = false;
let releaseCoordinator = null;
function startReleaseCoordinator() {
  if (releaseCoordinator) return releaseCoordinator;
  releaseCoordinator = createReleaseCoordinator({
    // The backend's source_root is the repository containing app/workflow.
    markerPath: join(resolve(WORKFLOW_DIR, '..'), '.operations-release.json'),
    intervalMs: 500,
    restart: async () => {
      const result = await ensureBackend({ restart: true });
      return result.ok ? { ok: true } : { ok: false, error: result.error };
    },
    rollback: async (marker) => {
      if (!marker?.journal) return { ok: false, error: 'journal_missing' };
      try {
        return restoreRelease(marker.journal, { expectedRoot: resolve(WORKFLOW_DIR, '..') });
      } catch (error) {
        return { ok: false, error: String(error?.message || error) };
      }
    },
    log: (message) => console.log('[wf-plugin] ' + message),
  });
  releaseCoordinator.start();
  return releaseCoordinator;
}
const RESTART_DEFER_MS = 20000;
// 延后重启不能无限期拖住代码更新：繁忙超过这个时长仍会重启（并留日志）。
const RESTART_MAX_DEFER_MS = 30 * 60 * 1000;
let deferredRestart = null;
let deferredRestartTimer = null;

// 后端是否仍有在途工作（advance 线程、排队批次、切段任务）。判据由后端 /api/health
// 的 busy 字段给出：它只在真正有工作时为真，探测失败按“不忙”处理，避免把挂死的
// 后端误判为忙而永远不重启。
async function backendBusy(timeoutMs = 1500) {
  if (!backend.ready || !backend.proc) return false;
  try {
    const res = await fetch(backendUrl() + 'api/health', { signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) return false;
    const body = await res.json();
    return !!(body && body.busy === true);
  } catch {
    return false;
  }
}

// 纯判定：忙且未超过最大等待窗口 → 延后。
function restartShouldDefer(busy, elapsedMs, maxMs = RESTART_MAX_DEFER_MS) {
  return !!busy && Number(elapsedMs) < Number(maxMs);
}

async function deferRestart(reason) {
  if (!deferredRestart) deferredRestart = { reason: reason || '', since: Date.now() };
  else if (reason) deferredRestart.reason = reason;
  if (deferredRestartTimer) return;
  deferredRestartTimer = setTimeout(() => {
    deferredRestartTimer = null;
    runDeferredRestart();
  }, RESTART_DEFER_MS);
  if (deferredRestartTimer.unref) deferredRestartTimer.unref();
}

async function runDeferredRestart() {
  if (!deferredRestart) return;
  const elapsed = Date.now() - deferredRestart.since;
  if (restartShouldDefer(await backendBusy(), elapsed)) {
    deferredRestartTimer = setTimeout(() => {
      deferredRestartTimer = null;
      runDeferredRestart();
    }, RESTART_DEFER_MS);
    if (deferredRestartTimer.unref) deferredRestartTimer.unref();
    return;
  }
  const reason = deferredRestart.reason;
  deferredRestart = null;
  console.log('[wf-plugin] 后端已空闲或等待超限，执行延后的重启（' + (reason || '') + '）');
  await restartBackendSoon(reason);
}

async function restartBackendSoon(reason) {
  // A release restart is host-owned and single: never race an install.
  if (releaseCoordinator?.status.paused) return;
  // 有在途模型调用/批次推进时不整树杀掉后端：那会把 agent 子进程连同诊断结果一起
  // 丢失（表现为 self-heal 事件停在 diagnosing）。改为空闲后重启。
  if (!restarting && await backendBusy()) {
    console.log('[wf-plugin] backend busy, deferring restart (' + (reason || '') + ')');
    await deferRestart(reason || '');
    return;
  }
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
  const schedule = (filename) => {
    if (!backendWatcher || !isBackendSource(filename)) return;
    if (releaseCoordinator?.status.paused) return;
    if (backendWatchTimer) clearTimeout(backendWatchTimer);
    // 去抖：编辑器保存常伴多次事件（写临时文件再改名），合并成一次重启。
    backendWatchTimer = setTimeout(() => {
      backendWatchTimer = null;
      if (backendWatcher) restartBackendSoon(filename);
    }, 800);
    if (backendWatchTimer.unref) backendWatchTimer.unref();
  };
  try {
    // macOS / Windows 支持 recursive；Linux 上降级为仅监听 app/ 顶层。
    backendWatcher = watch(WORKFLOW_DIR, { recursive: true }, (event, filename) => schedule(filename));
  } catch (e) {
    try { backendWatcher = watch(WORKFLOW_DIR, (event, filename) => schedule(filename)); }
    catch (e2) { backendWatcher = null; console.error('[wf-plugin] backend watch failed:', e2); return null; }
  }
  backendWatcher.on('error', (e) => console.error('[wf-plugin] backend watch error:', e));
  startReleaseCoordinator();
  console.log('[wf-plugin] watching backend sources under', WORKFLOW_DIR, 'for auto-restart');
  return backendWatcher;
}
function stopBackendWatch() {
  if (backendWatchTimer) { clearTimeout(backendWatchTimer); backendWatchTimer = null; }
  if (releaseCoordinator) { releaseCoordinator.stop(); releaseCoordinator = null; }
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

// 读取某个 profile 当前选中的 Harness 默认模型（agent-default-model）。
// 仓库不固定任何模型：每台设备跟随自己 Harness 菜单里选的模型。
function readProfileDefaultModel(profileName) {
  const path = join(PROFILES_DIR, String(profileName || 'desktop'), 'cordis.patch.yml');
  if (!existsSync(path)) return null;
  const lines = readFileSync(path, 'utf8').split(/\r?\n/);
  let inside = false;
  let provider = '';
  let model = '';
  for (const line of lines) {
    if (/^-\s*id:\s*['"]?agent-default-model['"]?\s*(?:#.*)?$/.test(line)) { inside = true; continue; }
    if (inside) {
      if (/^-\s/.test(line)) break;
      const p = line.match(/^\s+provider:\s*['"]?([^'"#\s]+)/);
      if (p) provider = p[1];
      const m = line.match(/^\s+model:\s*['"]?([^'"#]+?)['"]?\s*(?:#.*)?$/);
      if (m) model = m[1].trim();
    }
  }
  return provider && model ? { provider, model, profile: String(profileName || 'desktop') } : null;
}

function harnessDefaultModel() {
  const names = [process.env.DSH_PROFILE, 'desktop'].filter(Boolean);
  for (const name of names) {
    const found = readProfileDefaultModel(name);
    if (!found) continue;
    // 只有该 profile 自己声明了对应 provider（会被路由 overlay 复制）时才算
    // 可调用；WorkBuddy 的 magpie 等宿主独有 provider 在 headless 里会
    // NO_ADAPTER，不能作为“跟随 Harness”的默认模型展示。
    const declared = new Set(parseProfileModels(name).map((m) => m.provider));
    if (!declared.size || !declared.has(found.provider)) continue;
    return found;
  }
  // 没有显式 agent-default-model 时，用 Harness profile 里第一个可服务模型。
  // 插件不需要用户另填 API Key：凭据由 Harness 提供，这里只选模型。
  const desktopModels = parseProfileModels('desktop');
  const fallback = desktopModels[0] || parseProfileModels('wf-deepseek')[0];
  return fallback ? { ...fallback, profile: desktopModels.length ? 'desktop' : 'wf-deepseek-fast' } : null;
}

// 汇总当前可用模型。
// 路由 profile 以 base profile（wf-deepseek / wf-deepseek-fast）为底，wf-router
// 会把用户 desktop profile 里额外配置的 provider（如 opencode-go）合入路由 profile。
// 因此：base 里的 provider/model 全部可选；desktop 里 provider 不在 base 的条目也可选，
// 但同一 provider 已由 base 声明时只保留 base 的模型，避免展示路由不会使用的条目。
function mergeDetectedModels(base, extra) {
  const providers = new Set(base.map((m) => m.provider));
  const seen = new Set(base.map((m) => m.provider + '|' + m.model));
  const merged = base.slice();
  for (const m of extra || []) {
    const key = m.provider + '|' + m.model;
    if (seen.has(key) || providers.has(m.provider)) continue;
    seen.add(key);
    merged.push(m);
  }
  return merged;
}

/** Host models are only advertised for providers the route overlay declares:
 * the overlay copies llm-pi-ai / llm-deepseek rows only, so a host-only relay
 * such as WorkBuddy's magpie would fail with dsh NO_ADAPTER. */
function servableHostModels(detected, host) {
  const declared = new Set(detected.map((m) => m.provider));
  return (host || []).filter((m) => declared.has(m.provider));
}

async function collectModels(ctx) {
  let detected = parseProfileModels('wf-deepseek');
  const overlay = parseProfileModels('desktop');
  if (!detected.length) {
    // 兜底：base 缺失时退回 desktop，但仅作展示，路由可能失败
    detected = overlay.slice();
  } else {
    detected = mergeDetectedModels(detected, overlay);
  }
  const nameMap = new Map();
  const host = [];
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
            if (!mid) continue;
            nameMap.set(pid + '|' + mid, m.name || m.displayName || mid);
            host.push({ provider: pid, model: mid });
          }
        } catch {}
      }
    }
  } catch {}
  // 宿主里已启用、但 profile 没声明的 provider（workbuddy 的 magpie 中转等）
  // 不能列出来：wf-router.mjs 生成的路由 profile 只包含 base profile 里的
  // llm-pi-ai / llm-deepseek 条目，选中未声明的 provider 会在 dsh 里直接
  // NO_ADAPTER。只保留已声明 provider 的宿主模型用来补显示名。
  detected = mergeDetectedModels(detected, servableHostModels(detected, host));
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
// Operations model resolution: an explicit AI运维 role wins, then the model the
// user selected in plugin settings, then this device's Harness model. Never
// silently substitutes a different family; if none is usable the caller reports
// the configuration error.
function operationsRouteFor(router, harness) {
  const explicit = router?.roles?.operations;
  if (explicit) return operationsRoute(router);
  if (router?.default?.provider && router?.default?.model) return operationsRoute(router);
  if (harness?.provider && harness?.model) {
    return operationsRoute({ ...(router || {}), default: harness });
  }
  return operationsRoute(router || {});
}

function registerRoutes(ctx, getModels, bankSettings = questionBankSettings, ocr = ocrSettings, operations = null) {
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
        if (sub === '/operations/status' || sub === '/operations/config') {
          res.setHeader('Cache-Control', 'no-store');
          if (req.method === 'GET') return send(200, operations ? operations.status() : { available: false, enabled: false, error: '当前宿主未挂载AI运维服务，原有录题与自愈继续运行。' });
          if (sub !== '/operations/config' || req.method !== 'POST') return send(405, { error: '不支持的请求方法。' });
          if (!operations) return send(503, { error: '当前宿主未挂载AI运维服务。' });
          if (!/^application\/json(?:;|$)/i.test(req.headers['content-type'] || '')) return send(415, { error: '请使用 JSON 提交运维设置。' });
          const payload = JSON.parse(await readBody(req, 4096) || '{}');
          return send(200, await operations.configure(payload));
        }
        if (sub === '/ocr' || sub === '/ocr/test') {
          res.setHeader('Cache-Control', 'no-store');
          try {
            if (req.method === 'GET' && sub === '/ocr') return send(200, { ok: true, config: ocr.read() });
            if (req.method !== 'POST') return send(405, { ok: false, error: '不支持的请求方法。' });
            if (!/^application\/json(?:;|$)/i.test(req.headers['content-type'] || '')) return send(415, { ok: false, error: '请使用 JSON 提交 OCR 设置。' });
            const payload = JSON.parse(await readBody(req, 32 * 1024) || '{}');
            if (sub === '/ocr/test') {
              const result = await ocr.test(payload);
              return send(result.ok ? 200 : 400, result);
            }
            return send(200, { ok: true, config: ocr.save(payload) });
          } catch (error) {
            const code = error?.code === 'storage_error' ? 'storage_error' : 'invalid_config';
            return send(400, { ok: false, code, error: OCR_MESSAGES[code], message: OCR_MESSAGES[code] });
          }
        }
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
          // 手动重启也要避开在途任务：整树重启会丢失正在跑的模型调用与诊断结果。
          if (await backendBusy()) {
            await deferRestart('manual');
            return send(200, { ok: true, deferred: true, busy: true, port: backend.port });
          }
          const r = await ensureBackend({ restart: true });
          return send(r.ok ? 200 : 503, { ok: r.ok, error: r.error || undefined });
        }
        if (req.method === 'GET' && sub === '/models') {
          const models = await getModels(ctx);
          return send(200, { models });
        }
        if (req.method === 'GET' && sub === '/config') {
          return send(200, {
            router: readRouter(),
            harnessDefault: harnessDefaultModel(),
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
          ensureRouterFile();
          writeJsonAtomic(ROUTER_PATH, body);
          return send(200, { ok: true, router: readRouter() });
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
  if (router.roles) for (const k of Object.keys(router.roles)) fill(router.roles[k]);
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
  let operationsManager = null;
  let operationsError = '';
  async function operationsBackendRequest(path, { method = 'GET', body, signal } = {}) {
    if (!path.startsWith('/api/')) throw new Error('Invalid operations backend path');
    signal?.throwIfAborted();
    if (!backend.ready) {
      const ready = await ensureBackend();
      if (!ready.ok) throw new Error(ready.error || '工作台后端未就绪');
    }
    signal?.throwIfAborted();
    const response = await fetch('http://127.0.0.1:' + backend.port + path, {
      method, signal, headers: { 'content-type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '运维请求失败：' + response.status);
    return result;
  }
  const operationsAvailability = (available, signal = AbortSignal.timeout(5000)) =>
    operationsBackendRequest('/api/operations/service', { method: 'POST', body: { available }, signal });
  const operations = {
    status() {
      const config = operationsSettings.read();
      const state = operationsManager?.status() || { available: false, incidentCount: 0 };
      return { ...state, enabled: config.enabled,
        error: config.error || operationsError || state.configError || (!state.available ? 'Harness会话服务尚未就绪，原有录题与自愈继续运行。' : '') };
    },
    async configure(value) {
      const saved = operationsSettings.save(value);
      if (!saved.enabled) {
        // Revoke immediately, then fence any positive poll already in flight.
        const stopped = operationsManager?.stop();
        try { await operationsAvailability(false); }
        finally {
          await stopped;
          await operationsAvailability(false);
        }
      } else operationsManager?.start();
      return operations.status();
    },
  };
  // Optional native Agent integration. Absence/failure does not replace or stop
  // the existing Python watchdog, pipeline, upload UI or model routing.
  if (typeof ctx.inject === 'function') {
    ctx.inject(['agents', 'tools', 'systemPrompt', 'sessionPersistence'], (scope) => {
      try {
        const manager = createOperationsAgentManager(scope, {
          cwd: PLUGIN_DIR,
          enabled: () => operationsSettings.read().enabled,
          resolveRoute: () => operationsRouteFor(readRouter(), harnessDefaultModel()),
          // Re-arm the workbench before each inspection: a server restart must
          // not silently leave the 5s loop scanning a dead port.
          async ensureBackendReady() {
            if (!backend.ready) {
              const ready = await ensureBackend();
              if (!ready.ok) throw new Error(ready.error || '工作台后端未就绪');
            }
          },
          async hasWork({ signal } = {}) {
            if (!operationsSettings.read().enabled || !scope.agents?.create || !scope.agents?.resume) {
              await operationsAvailability(false, signal);
              return { has_work: false, batches: [] };
            }
            try {
              const route = operationsRouteFor(readRouter(), harnessDefaultModel());
              if (!route?.provider || !route?.model) throw new Error('Operations provider/model must be explicitly configured');
            } catch (error) {
              // An unusable route must not make the executor yield to this host.
              await operationsAvailability(false, signal);
              throw error;
            }
            await operationsAvailability(true, signal);
            const result = await operationsBackendRequest('/api/operations/work-state', { signal });
            // 交给运维面板的“流程进行中”提示：批次名原样透传，不在这里改写判定。
            return { has_work: result.has_work === true,
              batches: Array.isArray(result.batches) ? result.batches : [] };
          },
          backendRequest: operationsBackendRequest,
        });
        operationsManager = manager;
        operationsError = '';
        scope.effect(() => () => { if (operationsManager === manager) operationsManager = null; }, 'wf-plugin: operations reference');
        manager.start();
      } catch (error) {
        operationsError = String(error?.message || error);
        console.error('[wf-plugin] optional operations service unavailable:', operationsError);
      }
    });
  }
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
      try { registerRoutes(scope, getModels, questionBankSettings, ocrSettings, operations); }
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
  mergeDetectedModels,
  servableHostModels,
  harnessDefaultModel,
  operationsRouteFor,
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
  reapStaleWorkbenchProcesses,
  pickPort,
  WORKFLOW_DIR,
  PLUGIN_DIR,
};
