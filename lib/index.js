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
import { readFileSync, writeFileSync, existsSync, mkdirSync, renameSync, unlinkSync, readlinkSync, watch, appendFileSync, statSync } from 'node:fs';
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
import { createOperationsAgentManager, operationsHttpError } from './operations-agent.js';
import { createContentAgentManager } from './content-agent.js';
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
function isWindowsStoreAliasPath(p) {
  return /[\\/]Microsoft[\\/]WindowsApps[\\/]python(?:3(?:\.\d+)?)?\.exe$/i.test(String(p || ''));
}
function isWindowsStoreStubOutput(text) {
  const s = String(text || '');
  return /Microsoft Store|应用商店|App Execution Aliases|应用执行别名/i.test(s);
}
function pythonErrorHint(tried, { storeAliasDetected = false } = {}) {
  let msg = '未找到可用的 Python（需 ' + PYTHON_MIN.join('.') + '+）。已尝试：'
    + (tried.length ? tried.join('、') : '无') + '。';
  if (storeAliasDetected) {
    msg += '检测到 PATH 中的 python 指向 Microsoft Store 应用执行别名占位程序，'
      + '请在 Windows「设置 → 应用 → 高级应用设置 → 应用执行别名」中关闭 python.exe/python3.exe 别名，';
  }
  return msg
    + '请安装 Python 3.12（https://www.python.org/downloads/），'
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
  const pushCandidate = (cmd, prefixArgs = [], label = '') => {
    if (!cmd) return;
    candidates.push({
      cmd,
      prefixArgs,
      label: label || (prefixArgs.length ? cmd + ' ' + prefixArgs.join(' ') : cmd),
    });
  };
  if (process.env.WF_PYTHON) pushCandidate(process.env.WF_PYTHON);
  // 随插件分发的便携 Python 优先：不依赖用户机器预装 Python。
  const vp = vendorPython();
  if (vp) pushCandidate(vp);
  if (process.platform === 'win32') {
    // Windows 优先尝试 py 启动器与官方常见安装目录，避开 PATH 里微软商店占位程序。
    pushCandidate('py', ['-3.12'], 'py -3.12');
    pushCandidate('py', ['-3.13'], 'py -3.13');
    pushCandidate('py', ['-3.11'], 'py -3.11');
    pushCandidate('py', ['-3'], 'py -3');
    const localAppData = process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local');
    const winPaths = [
      join(localAppData, 'Programs', 'Python', 'Python312', 'python.exe'),
      join(localAppData, 'Programs', 'Python', 'Python313', 'python.exe'),
      join(localAppData, 'Programs', 'Python', 'Python311', 'python.exe'),
      join(localAppData, 'Programs', 'Python', 'Python310', 'python.exe'),
      'C:\\Python312\\python.exe',
      'C:\\Python311\\python.exe',
    ];
    for (const wp of winPaths) {
      if (existsSync(wp)) pushCandidate(wp);
    }
    pushCandidate('python3.12');
    pushCandidate('python3');
    pushCandidate('python');
  } else {
    for (const p of [
      '/opt/homebrew/bin/python3.12',
      '/usr/local/bin/python3.12',
      join(homedir(), '.local', 'bin', 'python3.12'),
      '/usr/bin/python3',
      '/opt/homebrew/bin/python3',
      '/usr/local/bin/python3',
      'python3.12',
      'python3',
      'python',
    ]) pushCandidate(p);
  }
  const tried = [];
  let storeAliasDetected = false;
  const probeCode = 'import sys;print("%d.%d|%s"%(sys.version_info[0],sys.version_info[1],sys.executable))';
  for (const { cmd, prefixArgs, label } of candidates) {
    try {
      const raw = execFileSync(cmd, [...prefixArgs, '-c', probeCode], {
        encoding: 'utf8', timeout: 5000, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
      }).trim();
      const [verPart, exePart = ''] = raw.split('|');
      const resolvedExe = exePart.trim();
      if (process.platform === 'win32' && (isWindowsStoreAliasPath(resolvedExe) || isWindowsStoreAliasPath(cmd) || !verPart)) {
        storeAliasDetected = true;
        tried.push(label + '(Microsoft Store 占位)');
        continue;
      }
      const [major, minor] = verPart.split('.').map(Number);
      const ok = major > PYTHON_MIN[0] || (major === PYTHON_MIN[0] && minor >= PYTHON_MIN[1]);
      if (ok) {
        pythonCache = (prefixArgs.length && resolvedExe) ? resolvedExe : cmd;
        backend.lastError = '';
        return pythonCache;
      }
      tried.push(label + '(v' + verPart + ')');
    } catch (err) {
      const errText = String((err && err.stderr) || '') + ' ' + String((err && err.stdout) || '');
      if (process.platform === 'win32' && (
        isWindowsStoreStubOutput(errText)
        || isWindowsStoreAliasPath(cmd)
        || ((cmd === 'python' || cmd === 'python3') && err && err.status === 9009)
      )) {
        storeAliasDetected = true;
        tried.push(label + '(Microsoft Store 占位)');
      } else {
        tried.push(label);
      }
    }
  }
  pythonCache = null;
  backend.lastError = pythonErrorHint(tried, { storeAliasDetected });
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
  try {
    const value = JSON.parse(readFileSync(ROUTER_PATH, 'utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new Error('router root must be an object');
    }
    return value;
  } catch (error) {
    throw new Error('模型路由配置损坏，无法读取：' + (error?.message || error));
  }
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

function backendAuthHeaders() {
  return backend.token ? { 'x-workbench-token': backend.token } : {};
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
    const r = await fetch(backendUrl() + 'api/health', { headers: backendAuthHeaders(), signal: ctrl.signal });
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

function reapStaleWorkerProcesses(keepPid) {
  const keep = Number(keepPid || 0);
  if (process.platform === 'win32') {
    // Windows 下 worker 是独立子进程：按本插件数据根记录的 worker-endpoint.json PID 清理残留进程树。
    try {
      const epPath = join(resolveDataRoot(), 'runtime', 'worker-endpoint.json');
      if (!existsSync(epPath)) return 0;
      const ep = JSON.parse(readFileSync(epPath, 'utf8'));
      const pid = Number(ep && ep.pid || 0);
      if (!pid || pid === process.pid || pid === keep) return 0;
      const info = execFileSync('tasklist', ['/FI', 'PID eq ' + pid, '/FO', 'CSV', '/NH'], {
        encoding: 'utf8', timeout: 3000, stdio: ['ignore', 'pipe', 'ignore'], windowsHide: true,
      });
      if (!/python/i.test(String(info || ''))) return 0;
      execFileSync('taskkill', ['/pid', String(pid), '/T', '/F'], {
        stdio: 'ignore', timeout: 5000, windowsHide: true,
      });
      recordWorkerLifecycle('reaped_stale', { count: 1, keep_pid: keep || null, reaped_pid: pid });
      console.log('[wf-plugin] cleaned 1 stale worker process (pid ' + pid + ')');
      return 1;
    } catch {
      return 0;
    }
  }
  let output = '';
  try {
    output = execFileSync('ps', ['-axo', 'pid=,command='], {
      encoding: 'utf8', timeout: 3000, stdio: ['ignore', 'pipe', 'ignore'],
    });
  } catch { return 0; }
  const wanted = resolve(WORKFLOW_DIR);
  let killed = 0;
  for (const raw of String(output).split('\n')) {
    const match = raw.trim().match(/^(\d+)\s+(.*)$/);
    if (!match) continue;
    const pid = Number(match[1]);
    const command = match[2];
    if (!pid || pid === process.pid || pid === keep) continue;
    if (!command.includes('workbench.worker') || !command.includes('--host 127.0.0.1')) continue;
    const cwd = workbenchProcessCwd(pid);
    if (!cwd || resolve(cwd) !== wanted) continue;
    try { process.kill(pid, 'SIGTERM'); killed++; } catch { /* 已退出 */ }
  }
  if (killed) {
    recordWorkerLifecycle('reaped_stale', { count: killed, keep_pid: keep || null });
    console.log('[wf-plugin] cleaned ' + killed + ' stale worker process(es)');
  }
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

const SPLIT_WORKER_ENABLED = String(process.env.WF_SPLIT_WORKER ?? '1').trim().toLowerCase() !== '0';
const workerDaemon = { proc: null, instanceId: '', token: '', startedAt: 0, lastError: '', draining: false };

// worker 生命周期留痕：阶段子进程跑在 worker 进程组里，worker 一退出在途阶段就无人收尾，
// 留下无锁 running 被判僵死（九上62、九上15 即找不到原因）。每次启动/退出/停止请求/排空/
// 孤儿回收追加一行 JSONL 到数据根 runtime/worker-lifecycle.jsonl，超过 1MB 轮转一份。
// 仅诊断用途：写失败静默忽略，绝不影响 worker 调度。
const WORKER_LIFECYCLE_MAX_BYTES = 1024 * 1024;
function recordWorkerLifecycle(event, fields = {}) {
  try {
    const dir = join(resolveDataRoot(), 'runtime');
    mkdirSync(dir, { recursive: true });
    const file = join(dir, 'worker-lifecycle.jsonl');
    try {
      if (statSync(file).size > WORKER_LIFECYCLE_MAX_BYTES) renameSync(file, file + '.1');
    } catch { /* 首次写入或轮转失败：继续追加 */ }
    appendFileSync(file, JSON.stringify({ at: new Date().toISOString(), event, host_pid: process.pid, ...fields }) + '\n');
  } catch { /* 诊断留痕不得影响调度 */ }
}

function spawnWorkerDaemon() {
  if (!SPLIT_WORKER_ENABLED) return null;
  if (workerDaemon.proc && backendChildAlive(workerDaemon.proc)) return workerDaemon.proc;
  reapStaleWorkerProcesses(workerDaemon.proc && workerDaemon.proc.pid);
  const python = resolvePython();
  if (!python) return null;
  const dataRoot = resolveDataRoot();
  try { mkdirSync(join(dataRoot, 'workflows'), { recursive: true }); } catch {}
  try { mkdirSync(join(dataRoot, 'inbox'), { recursive: true }); } catch {}
  try { mkdirSync(join(dataRoot, 'runtime'), { recursive: true }); } catch {}
  let agentConfigPath = '';
  try { agentConfigPath = materializeAgentConfig(); } catch {}
  const instanceId = randomUUID();
  const workerToken = randomUUID();
  const useGroup = process.platform !== 'win32';
  const child = spawn(python, ['-m', 'workbench.worker', '--host', '127.0.0.1', '--port', '0'], {
    cwd: WORKFLOW_DIR,
    detached: useGroup,
    env: {
      ...process.env,
      ...(agentConfigPath ? { MUDU_AGENT_CONFIG: agentConfigPath } : {}),
      PYTHONUNBUFFERED: '1',
      PYTHONIOENCODING: 'utf-8',
      WF_WORKFLOWS_DIR: join(dataRoot, 'workflows'),
      WF_INBOX_DIR: join(dataRoot, 'inbox'),
      WF_AUDIO_DATA_DIR: join(dataRoot, 'audio'),
      WF_AUTO_IDENTIFY_META: '1',
      WF_ZERO_TOUCH: '1',
      WF_SPLIT_WORKER: '0',
      WF_MAX_CONCURRENT_BATCHES: process.env.WF_MAX_CONCURRENT_BATCHES || '2',
      ...questionBankSettings.runtimeEnv(),
      ...ocrSettings.runtimeEnv(),
      WF_WORKER_INSTANCE_ID: instanceId,
      WF_WORKER_TOKEN: workerToken,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  workerDaemon.proc = child;
  if (child.spawnfile) child.__wfDetached = useGroup;
  workerDaemon.instanceId = instanceId;
  workerDaemon.token = workerToken;
  workerDaemon.startedAt = Date.now();
  workerDaemon.draining = false;
  workerDaemon.lastError = '';
  workerDaemon.stopReason = '';
  const workerSpawnedAt = workerDaemon.startedAt;
  recordWorkerLifecycle('spawn', { pid: child.pid, instance_id: instanceId });
  child.stderr.on('data', (d) => {
    if (workerDaemon.proc === child) workerDaemon.lastError = String(d).slice(-800);
  });
  child.on('exit', (code, signal) => {
    recordWorkerLifecycle('exit', { pid: child.pid, instance_id: instanceId, code, signal,
      current: workerDaemon.proc === child, draining: workerDaemon.proc === child && workerDaemon.draining,
      stop_reason: workerDaemon.proc === child ? (workerDaemon.stopReason || '') : 'superseded',
      uptime_ms: Date.now() - workerSpawnedAt });
    if (workerDaemon.proc !== child) return;
    const wasDraining = workerDaemon.draining;
    workerDaemon.proc = null;
    workerDaemon.instanceId = '';
    workerDaemon.startedAt = 0;
    workerDaemon.draining = false;
    if (code !== 0 && code !== null) {
      workerDaemon.lastError = 'worker exited code=' + code + (signal ? ' sig=' + signal : '');
    }
    if (wasDraining) {
      console.log('[wf-plugin] worker daemon drained cleanly, respawning with updated code…');
      ensureWorkerDaemon();
    }
  });
  child.on('error', (e) => {
    if (workerDaemon.proc !== child) return;
    if (!backendChildAlive(child)) {
      workerDaemon.proc = null;
      workerDaemon.instanceId = '';
    }
    workerDaemon.lastError = String(e && e.message || e);
  });
  return child;
}

function ensureWorkerDaemon() {
  if (!SPLIT_WORKER_ENABLED) return null;
  return spawnWorkerDaemon();
}

async function stopWorkerDaemon(reason = 'stop') {
  const child = workerDaemon.proc;
  if (!child) return true;
  workerDaemon.stopReason = String(reason || 'stop');
  recordWorkerLifecycle('stop_requested', { pid: child.pid, instance_id: workerDaemon.instanceId, reason: workerDaemon.stopReason });
  let exited = await signalAndWaitForExit(child, 'SIGTERM', 3000);
  if (!exited) exited = await signalAndWaitForExit(child, 'SIGKILL', 1000);
  if (workerDaemon.proc === child && exited) {
    workerDaemon.proc = null;
    workerDaemon.instanceId = '';
    workerDaemon.draining = false;
  }
  return exited;
}

async function drainAndRestartWorker(reason) {
  if (!SPLIT_WORKER_ENABLED || !workerDaemon.proc || workerDaemon.draining) return;
  workerDaemon.draining = true;
  try {
    const epPath = join(resolveDataRoot(), 'runtime', 'worker-endpoint.json');
    if (!existsSync(epPath)) {
      workerDaemon.draining = false;
      return;
    }
    const ep = JSON.parse(readFileSync(epPath, 'utf8'));
    if (!ep || !ep.port) {
      workerDaemon.draining = false;
      return;
    }
    const res = await fetch(`http://${ep.host || '127.0.0.1'}:${ep.port}/internal/drain`, {
      method: 'POST',
      headers: { 'X-Worker-Token': String(ep.token || '') },
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) {
      workerDaemon.draining = false;
      return;
    }
    workerDaemon.stopReason = 'drain:' + (reason || '');
    recordWorkerLifecycle('drain_requested', { pid: workerDaemon.proc && workerDaemon.proc.pid, reason: reason || '' });
    console.log('[wf-plugin] worker daemon draining for reload (' + (reason || '') + ')');
  } catch {
    workerDaemon.draining = false;
  }
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
  const authToken = randomUUID();
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
      WF_SPLIT_WORKER: SPLIT_WORKER_ENABLED ? '1' : '0',
      // 双批次并发推进（16GB 内存安全预算：单批 4 路 × 双批 = 8 路，安全低于 16 路阈值）；
      // 可通过环境变量 WF_MAX_CONCURRENT_BATCHES=1 随时切回单批严格串行。
      WF_MAX_CONCURRENT_BATCHES: process.env.WF_MAX_CONCURRENT_BATCHES || '2',
      ...questionBankSettings.runtimeEnv(),
      ...ocrSettings.runtimeEnv(),
      WF_BACKEND_INSTANCE_ID: instanceId,
      WF_WORKBENCH_TOKEN: authToken,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  backend.proc = child;
  // 真 ChildProcess 才有 spawnfile；测试里的假进程不会走到 kill(-pid)。
  if (child.spawnfile) child.__wfDetached = useGroup;
  backend.instanceId = instanceId;
  backend.token = authToken;
  backend.startedAt = Date.now();
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
    backend.startedAt = 0;
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
    return { ok: true, url: backendUrl(), token: backend.token };
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
      return { ok: true, url: backendUrl(), token: backend.token };
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
      return { ok: true, url: backendUrl(), token: backend.token };
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
// 后端存活看门狗：批次推进队列与 supervisor 只活在后端进程内，后端一死，
// 界面仍是"排队中"，但没有任何人能把它拉回来——此前 16:52–17:13 就因此空了 21 分钟。
// 这里按固定间隔检查子进程是否还活着，已退出就自动重启；只为"进程不在"兜底，
// 不介入进行中的热重启，也不因健康探测偶发超时就杀掉繁忙后端。
// ---------------------------------------------------------------------------
let backendWatchdogRunning = false;
let backendWatchdogTimer = null;
let backendWatchdogTicking = false;
let backendWatchdogFailures = 0;
let backendWatchdogAdopted = false;
let backendWatchdogIntervalMs = 15000;
const BACKEND_WATCHDOG_MAX_MS = 60000;

function backendRestartInFlight() {
  // `restarting` 定义在热重启小节；单独跑这一段的测试里没有它。
  return typeof restarting !== 'undefined' && !!restarting;
}

function backendWatchdogDelay() {
  const factor = Math.min(backendWatchdogFailures, 3);
  return Math.min(backendWatchdogIntervalMs * Math.pow(2, factor), BACKEND_WATCHDOG_MAX_MS);
}

async function probeUnmanagedWorkbench(url, timeoutMs = 3000) {
  // 本插件重启后可能留下仍健康、但归属已丢失的 workbench.server（Windows 上
  // 无法按 cwd 回收孤儿）。此时必须复用而不是再拉一个：两个后端各有一个推进器，
  // 会同时抢同一批次的执行锁。只认带 workflow_runtime_version 的本项目后端。
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const response = await fetch(url + 'api/health', { headers: backendAuthHeaders(), signal: ctrl.signal });
    if (!response.ok) {
      if (response.body) await response.body.cancel();
      return false;
    }
    const data = await response.json().catch(() => null);
    if (!(data && typeof data.workflow_runtime_version === 'string')) return false;
    // Do not adopt a token-protected unmanaged server if our current token is rejected.
    const authCheck = await fetch(url + 'api/operations/work-state', { headers: backendAuthHeaders(), signal: ctrl.signal });
    if (authCheck.body) await authCheck.body.cancel().catch(() => {});
    if (authCheck.status === 401 || authCheck.status === 403) return false;
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function backendWatchdogTick() {
  if (backendWatchdogTicking) return { ok: false, skipped: 'ticking' };
  if (backendRestartInFlight()) return { ok: false, skipped: 'restarting' };
  if (backendChildAlive(backend.proc)) {
    if (SPLIT_WORKER_ENABLED && !workerDaemon.draining && !backendChildAlive(workerDaemon.proc)) {
      ensureWorkerDaemon();
    }
    backendWatchdogFailures = 0;
    backendWatchdogAdopted = false;
    return { ok: true, skipped: 'alive' };
  }
  backendWatchdogTicking = true;
  try {
    if (await probeUnmanagedWorkbench(backendUrl())) {
      backendWatchdogFailures = 0;
      backend.ready = true; // 复用已在服务的后端：代理直接用它，不再另起进程。
      if (!backendWatchdogAdopted) {
        backendWatchdogAdopted = true;
        console.log('[wf-plugin] backend watchdog reused the running workbench at', backendUrl(),
                    '(no duplicate spawned)');
      }
      return { ok: true, skipped: 'unmanaged' };
    }
    backendWatchdogAdopted = false;
    const result = await ensureBackend();
    if (result.ok) {
      backendWatchdogFailures = 0;
      console.log('[wf-plugin] backend watchdog restored workbench at', result.url);
    } else {
      backendWatchdogFailures += 1;
      console.error('[wf-plugin] backend watchdog restart failed:', result.error);
    }
    return result;
  } catch (e) {
    backendWatchdogFailures += 1;
    console.error('[wf-plugin] backend watchdog error:', e);
    return { ok: false, url: backendUrl(), error: String((e && e.message) || e) };
  } finally {
    backendWatchdogTicking = false;
  }
}

function startBackendWatchdog(options = {}) {
  if (Number(options.intervalMs) > 0) backendWatchdogIntervalMs = Number(options.intervalMs);
  if (backendWatchdogRunning) return false;
  backendWatchdogRunning = true;
  const arm = (ms) => {
    const timer = setTimeout(async () => {
      if (!backendWatchdogRunning) return;
      try {
        await backendWatchdogTick();
      } catch (e) {
        console.error('[wf-plugin] backend watchdog timer error:', e);
      }
      if (backendWatchdogRunning) arm(backendWatchdogDelay());
    }, ms);
    if (timer.unref) timer.unref();
    backendWatchdogTimer = timer;
  };
  arm(backendWatchdogIntervalMs);
  return true;
}

function stopBackendWatchdog() {
  backendWatchdogRunning = false;
  if (backendWatchdogTimer) {
    clearTimeout(backendWatchdogTimer);
    backendWatchdogTimer = null;
  }
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
let operationsManager = null;
async function workerBusy(timeoutMs = 1500) {
  if (!SPLIT_WORKER_ENABLED || !workerDaemon.proc || !backendChildAlive(workerDaemon.proc)) return false;
  try {
    const res = await fetch(backendUrl() + 'api/health', { headers: backendAuthHeaders(), signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) {
      if (res.body) await res.body.cancel().catch(() => {});
      return true;
    }
    const body = await res.json();
    if (body && typeof body.worker_busy === 'boolean') return body.worker_busy;
    return true;
  } catch {
    return true;
  }
}

// 发布安装后重启 worker 必须走排空：worker 等在途阶段跑完再退出，由 exit 处理器用新代码重拉。
// 不得直接 stopWorkerDaemon——它按进程组 SIGTERM/SIGKILL，会杀掉正在跑的阶段子进程，留下
// 无锁的 running 标记被误判僵死（2026-11-24 九上37 预装配即因此丢失）。canRestart 只采样一次，
// 后端重启的数秒内 worker 的 supervisor 仍可能派发新阶段，所以这里不能假定 worker 仍空闲。
// 排空请求失败时：worker 确认空闲才直接重拉；忙则延后重试，绝不硬杀。
const RELEASE_WORKER_RELOAD_RETRY_MS = 5000;
const RELEASE_WORKER_RELOAD_MAX_TRIES = 360;
let releaseWorkerReloadTimer = null;
async function reloadWorkerAfterRelease(attempt = 0) {
  if (!SPLIT_WORKER_ENABLED) return { ok: true, mode: 'disabled' };
  if (releaseWorkerReloadTimer) { clearTimeout(releaseWorkerReloadTimer); releaseWorkerReloadTimer = null; }
  if (!workerDaemon.proc || !backendChildAlive(workerDaemon.proc)) {
    ensureWorkerDaemon();
    return { ok: true, mode: 'spawned' };
  }
  if (workerDaemon.draining) return { ok: true, mode: 'draining' };
  await drainAndRestartWorker('release');
  if (workerDaemon.draining) return { ok: true, mode: 'draining' };
  if (!(await workerBusy())) {
    const exited = await stopWorkerDaemon('release_idle_restart');
    ensureWorkerDaemon();
    return { ok: exited, mode: 'idle_restart' };
  }
  if (attempt + 1 >= RELEASE_WORKER_RELOAD_MAX_TRIES) {
    console.error('[wf-plugin] worker stayed busy after release; keeping it running (no hard kill)');
    return { ok: false, mode: 'busy_gave_up' };
  }
  releaseWorkerReloadTimer = setTimeout(() => {
    releaseWorkerReloadTimer = null;
    reloadWorkerAfterRelease(attempt + 1).catch((e) => console.error('[wf-plugin] worker reload retry error:', e));
  }, RELEASE_WORKER_RELOAD_RETRY_MS);
  return { ok: true, mode: 'deferred' };
}

function startReleaseCoordinator() {
  if (releaseCoordinator) return releaseCoordinator;
  releaseCoordinator = createReleaseCoordinator({
    // The backend's source_root is the repository containing app/workflow.
    markerPath: join(resolve(WORKFLOW_DIR, '..'), '.operations-release.json'),
    intervalMs: 500,
    canRestart: async () => !(await backendBusy()) && !(await workerBusy()),
    alreadyRestarted: async (_marker, markerMs) => {
      if (!Number.isFinite(markerMs) || !backend.ready || !backend.proc) return false;
      const startedAt = await backendStartedAt();
      return Number.isFinite(startedAt) && startedAt > markerMs + 1000;
    },
    restart: async () => {
      const result = await ensureBackend({ restart: true });
      if (result.ok && SPLIT_WORKER_ENABLED) await reloadWorkerAfterRelease();
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
const RESTART_DEFER_MS = 5000;
// 延后重启不能无限期拖住代码更新：繁忙超过这个时长仍会重启（并留日志）。
const RESTART_MAX_DEFER_MS = 30 * 60 * 1000;
let deferredRestart = null;
let deferredRestartTimer = null;

// 后端健康三态：idle（明确空闲）、busy（在途任务）、unknown（探测超时或异常）。
// 超时/异常绝不按空闲处理，避免保存或上传持锁期间默认安全重启打断在途任务。
async function backendHealthState(timeoutMs = 1500) {
  if (operationsManager?.busyForRestart?.()) return 'busy';
  if (!backend.proc) return 'idle';
  try {
    const res = await fetch(backendUrl() + 'api/health', { headers: backendAuthHeaders(), signal: AbortSignal.timeout(timeoutMs) });
    if (!res.ok) {
      if (res.body) await res.body.cancel().catch(() => {});
      return 'unknown';
    }
    const body = await res.json();
    if (body && typeof body.busy === 'boolean') return body.busy ? 'busy' : 'idle';
    return 'unknown';
  } catch {
    return 'unknown';
  }
}

async function backendStartedAt(timeoutMs = 1500) {
  if (!backend.ready || !backend.proc) return 0;
  try {
    const res = await fetch(backendUrl() + 'api/health', { headers: backendAuthHeaders(), signal: AbortSignal.timeout(timeoutMs) });
    if (res.ok) {
      const body = await res.json();
      if (body && typeof body.started_at === 'number' && Number.isFinite(body.started_at) && body.started_at > 0) {
        return Math.round(body.started_at * 1000);
      }
    } else if (res.body) {
      await res.body.cancel().catch(() => {});
    }
  } catch { /* fall back to local spawn time */ }
  return Number(backend.startedAt || 0);
}

async function backendBusy(timeoutMs = 1500) {
  return (await backendHealthState(timeoutMs)) !== 'idle';
}

// 纯判定：非明确空闲（busy 或 unknown）且未超过最大等待窗口 → 延后。
function restartShouldDefer(busyOrState, elapsedMs, maxMs = RESTART_MAX_DEFER_MS) {
  const notIdle = typeof busyOrState === 'string' ? busyOrState !== 'idle' : !!busyOrState;
  return notIdle && Number(elapsedMs) < Number(maxMs);
}

async function deferRestart(reason) {
  if (!deferredRestart) deferredRestart = { reason: reason || '', since: Date.now() };
  else if (reason) deferredRestart.reason = reason;
  if (deferredRestartTimer) return;
  deferredRestartTimer = setTimeout(() => {
    deferredRestartTimer = null;
    runDeferredRestart().catch((e) => console.error('[wf-plugin] deferred restart error:', e));
  }, RESTART_DEFER_MS);
  if (deferredRestartTimer.unref) deferredRestartTimer.unref();
}

async function runDeferredRestart() {
  if (!deferredRestart) return;
  const elapsed = Date.now() - deferredRestart.since;
  const busyState = await backendBusy();
  if (restartShouldDefer(busyState, elapsed)) {
    deferredRestartTimer = setTimeout(() => {
      deferredRestartTimer = null;
      runDeferredRestart().catch((e) => console.error('[wf-plugin] deferred restart error:', e));
    }, RESTART_DEFER_MS);
    if (deferredRestartTimer.unref) deferredRestartTimer.unref();
    return;
  }
  const reason = deferredRestart.reason;
  deferredRestart = null;
  const bypassDefer = !operationsManager?.busyForRestart?.();
  console.log('[wf-plugin] 后端已空闲或等待超限，执行延后的重启（' + (reason || '') + '）');
  await restartBackendSoon(reason, { bypassDefer });
}

async function restartBackendSoon(reason, { bypassDefer = false } = {}) {
  // A release restart is host-owned and single: never race an install.
  if (releaseCoordinator?.blocksAdmission?.() || releaseCoordinator?.status.paused) return;
  // 有在途模型调用/批次推进时不整树杀掉后端：那会把 agent 子进程连同诊断结果一起
  // 丢失（表现为 self-heal 事件停在 diagnosing）。改为空闲后重启。
  if (!restarting && !bypassDefer && await backendBusy()) {
    console.log('[wf-plugin] backend busy, deferring restart (' + (reason || '') + ')');
    await deferRestart(reason || '');
    return;
  }
  if (restarting) {
    // 已有重启在进行：安排一次收尾重启，确保最后一次改动也生效。
    if (restartTimer) clearTimeout(restartTimer);
    restartTimer = setTimeout(() => {
      restartTimer = null;
      restartBackendSoon(reason, { bypassDefer }).catch((e) => console.error('[wf-plugin] restart timer error:', e));
    }, 900);
    return;
  }
  restarting = true;
  try {
    console.log('[wf-plugin] backend code changed (' + (reason || '') + '), restarting workbench…');
    const r = await ensureBackend({ restart: true });
    if (r.ok) console.log('[wf-plugin] workbench restarted at', r.url);
    else console.error('[wf-plugin] workbench restart failed:', r.error);
    // In split-worker mode, changes outside app/workbench/ (or to worker.py /
    // execution_client.py) trigger a graceful worker drain so in-flight stages
    // finish cleanly at a stage boundary before respawning.
    const changedPath = String(reason || '').replace(/\\/g, '/');
    const needsWorkerReload = !changedPath.startsWith('workbench/')
      || changedPath === 'workbench/worker.py'
      || changedPath === 'workbench/execution_client.py';
    if (SPLIT_WORKER_ENABLED && changedPath && needsWorkerReload) {
      drainAndRestartWorker(changedPath).catch(() => {});
    }
  } catch (e) {
    console.error('[wf-plugin] workbench restart error:', e);
  } finally {
    restarting = false;
  }
}
function startBackendWatch() {
  if (backendWatcher) return backendWatcher;
  if (!existsSync(WORKFLOW_DIR)) return null;
  startReleaseCoordinator();
  const schedule = (filename) => {
    if (!backendWatcher || !isBackendSource(filename)) return;
    if (releaseCoordinator?.blocksAdmission?.() || releaseCoordinator?.status.paused) return;
    if (backendWatchTimer) clearTimeout(backendWatchTimer);
    // 去抖：编辑器保存常伴多次事件（写临时文件再改名），合并成一次重启。
    backendWatchTimer = setTimeout(() => {
      backendWatchTimer = null;
      if (backendWatcher) {
        restartBackendSoon(filename).catch((e) => console.error('[wf-plugin] watch restart error:', e));
      }
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
  console.log('[wf-plugin] watching backend sources under', WORKFLOW_DIR, 'for auto-restart');
  return backendWatcher;
}
function stopBackendWatch() {
  if (releaseWorkerReloadTimer) { clearTimeout(releaseWorkerReloadTimer); releaseWorkerReloadTimer = null; }
  if (backendWatchTimer) { clearTimeout(backendWatchTimer); backendWatchTimer = null; }
  if (typeof deferredRestartTimer !== 'undefined' && deferredRestartTimer) { clearTimeout(deferredRestartTimer); deferredRestartTimer = null; }
  if (typeof deferredRestart !== 'undefined') deferredRestart = null;
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
  const explicit = router?.roles?.operations ?? router?.stages?.operations;
  if (explicit !== undefined && explicit !== null) return operationsRoute(router);
  const configuredDefault = router?.default;
  if (configuredDefault !== undefined && configuredDefault !== null
      && !(configuredDefault && typeof configuredDefault === 'object'
        && !Array.isArray(configuredDefault)
        && Object.keys(configuredDefault).length === 0)) {
    return operationsRoute(router);
  }
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
        if (res.headersSent || res.writableEnded || res.destroyed) return;
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
          let ready = false;
          let busy = false;
          const child = backend.proc;
          const instanceId = backend.instanceId;
          if (backendChildAlive(child) && instanceId) {
            try {
              const healthRes = await fetch(backendUrl() + 'api/health', {
                headers: backendAuthHeaders(),
                signal: AbortSignal.timeout(1500),
              });
              if (healthRes.ok) {
                const health = await healthRes.json();
                if (backend.proc === child && backend.instanceId === instanceId
                    && backendChildAlive(child) && health.ok === true && health.instance_id === instanceId) {
                  ready = true;
                  busy = health.busy !== false;
                }
              } else if (healthRes.body) {
                await healthRes.body.cancel().catch(() => {});
              }
            } catch {
              ready = false;
              busy = false;
            }
          }
          return send(200, {
            running: ready,
            busy,
            deferred: Boolean(deferredRestart),
            deferredReason: deferredRestart?.reason || '',
            restarting: backendRestartInFlight(),
            port: backend.port,
            pid: backend.proc?.pid || null,
            error: backend.lastError,
          });
        }
        if (req.method === 'POST' && sub === '/workbench/start') {
          const r = await ensureBackend({ restart: false });
          return send(r.ok ? 200 : 503, { ok: r.ok, error: r.error || undefined });
        }
        if (req.method === 'POST' && sub === '/workbench/restart') {
          let payload = {};
          try {
            const raw = await readBody(req, 4096);
            if (raw && raw.trim()) payload = JSON.parse(raw);
          } catch { payload = {}; }
          const force = payload?.force === true;
          // 默认安全模式只在明确 idle 时立即重启；busy 或 unknown 均延后，显式 force=true 才强拉。
          const health = force ? 'idle' : await backendHealthState();
          if (!force && health !== 'idle') {
            await deferRestart('manual');
            return send(200, { ok: true, deferred: true, busy: health === 'busy', health, port: backend.port });
          }
          if (deferredRestartTimer) { clearTimeout(deferredRestartTimer); deferredRestartTimer = null; }
          deferredRestart = null;
          const r = await ensureBackend({ restart: true });
          return send(r.ok ? 200 : 503, {
            ok: r.ok,
            restarted: r.ok,
            port: backend.port,
            pid: backend.proc?.pid || null,
            error: r.error || undefined,
          });
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
        if (req.method === 'GET' && sub.startsWith('/vendor/')) {
          const VENDOR_WHITELIST = new Set(['jszip.min.js', 'docx-preview.min.js']);
          const file = sub.slice('/vendor/'.length);
          if (!VENDOR_WHITELIST.has(file)) return send(404, { error: 'not found' });
          const full = join(HERE, 'vendor', file);
          if (!existsSync(full)) return send(404, { error: 'vendor asset missing' });
          const body = readFileSync(full);
          res.writeHead(200, {
            'content-type': 'application/javascript; charset=utf-8',
            'cache-control': 'public, max-age=86400',
            'x-content-type-options': 'nosniff',
            'content-length': String(body.length),
          });
          res.end(body);
          return;
        }
        // 透明代理：把 /wf-plugin/api/* 原样转发到 Python 后端，
        // 让插件面板调用 REST 接口（含二进制媒体、multipart 上传）。
        if (sub.startsWith('/api/')) {
          return await proxyToBackend(req, res, sub, operations);
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
async function proxyToBackend(req, res, subPath, operations = null) {
  // 确保后端已起（幂等，第二次是空操作）。
  if (!backend.ready) {
    const r = await ensureBackend();
    if (!r.ok) {
      res.writeHead(503, { 'content-type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ error: '后端未就绪：' + (r.error || '') }));
      return;
    }
  }
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
  const baseHeaders = {};
  if (req.headers['content-type']) baseHeaders['content-type'] = req.headers['content-type'];
  if (req.headers['accept']) baseHeaders['accept'] = req.headers['accept'];
  if (req.headers['if-none-match']) baseHeaders['if-none-match'] = req.headers['if-none-match'];
  const suffix = subPath + (req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : '');
  const isIdempotent = req.method === 'GET' || req.method === 'HEAD';
  const formatFetchError = (e) => {
    const msg = String((e && e.message) || e || '');
    const cause = e && e.cause;
    const detail = cause ? String(cause.code || cause.message || cause) : '';
    return detail && !msg.includes(detail) ? `${msg} (${detail})` : msg;
  };
  const isLongOp = req.method === 'POST' && (
    subPath === '/api/uploads'
    || /^\/api\/batches\/[^/]+\/(?:delete|purge)$/.test(subPath)
  );
  const upstreamTimeoutMs = isLongOp ? 120000 : 30000;
  let clientAborted = false;
  const attemptFetch = async () => {
    const url = 'http://127.0.0.1:' + backend.port + suffix;
    const sentToken = backend.token || '';
    const attemptHeaders = { ...baseHeaders, ...backendAuthHeaders() };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), upstreamTimeoutMs);
    const onClose = () => {
      if (!res.writableEnded) {
        clientAborted = true;
        controller.abort();
      }
    };
    res.once('close', onClose);
    try {
      const upstream = await fetch(url, { method: req.method, headers: attemptHeaders, body, signal: controller.signal });
      const buf = Buffer.from(await upstream.arrayBuffer());
      return { upstream, buf, sentToken };
    } finally {
      clearTimeout(timer);
      res.removeListener('close', onClose);
    }
  };
  try {
    let result;
    try {
      result = await attemptFetch();
    } catch (firstErr) {
      // Client-side abort/disconnect (e.g. rapid question switching) does not mean
      // the backend is unhealthy; never flip backend.ready=false or trigger reconnect.
      if (clientAborted || res.destroyed) return;
      backend.ready = false;
      // 幂等 GET/HEAD 遇到瞬时断连或后端重启窗口时，自动确认/拉起后端并重试一次，
      // 避免轮询因为单次 socket 抖动直接向界面抛出「连接后端失败：fetch failed」。
      if (isIdempotent && typeof ensureBackend === 'function') {
        const r = await ensureBackend().catch(() => ({ ok: false }));
        if (r && r.ok && !clientAborted && !res.destroyed) {
          result = await attemptFetch();
        } else {
          throw firstErr;
        }
      } else {
        throw firstErr;
      }
    }
    // 后端刚重启换了新令牌时，在途请求可能拿着旧令牌命中新后端并收到 401；
    // 若令牌已轮换，或幂等请求可重新确认后端，则用最新令牌重试一次。
    if (result && result.upstream && result.upstream.status === 401 && !clientAborted && !res.destroyed) {
      if (backend.token && backend.token !== result.sentToken) {
        result = await attemptFetch();
      } else if (isIdempotent && typeof ensureBackend === 'function') {
        backend.ready = false;
        const r = await ensureBackend().catch(() => ({ ok: false }));
        if (r && r.ok && !clientAborted && !res.destroyed) {
          result = await attemptFetch();
        }
      }
    }
    const { upstream, buf } = result;
    if (clientAborted || res.destroyed) return;
    const outHeaders = {};
    const ct = upstream.headers.get('content-type');
    if (ct) outHeaders['content-type'] = ct;
    if (upstream.status !== 304) outHeaders['content-length'] = String(buf.length);
    const etag = upstream.headers.get('etag');
    if (etag) outHeaders['etag'] = etag;
    const cc = upstream.headers.get('cache-control');
    if (cc) outHeaders['cache-control'] = cc;
    const ar = upstream.headers.get('accept-ranges');
    if (ar) outHeaders['accept-ranges'] = ar;
    const nosniff = upstream.headers.get('x-content-type-options');
    if (nosniff) outHeaders['x-content-type-options'] = nosniff;
    const csp = upstream.headers.get('content-security-policy');
    if (csp) outHeaders['content-security-policy'] = csp;
    res.writeHead(upstream.status, outHeaders);
    res.end(buf);
    if (req.method === 'POST' && upstream.status >= 200 && upstream.status < 300
        && /^\/(?:api\/)?batches\/[^/]+\/operations\/(takeover|unfreeze|reconcile-unknown)$/.test(subPath)) {
      operations?.wake?.();
    }
  } catch (e) {
    if (clientAborted || res.destroyed) return;
    backend.ready = false;
    res.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ error: '连接后端失败：' + formatFetchError(e) }));
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
  operationsManager = null;
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
      method, signal, headers: { ...backendAuthHeaders(), 'content-type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const result = await response.json();
    if (!response.ok) throw operationsHttpError(result, response.status);
    return result;
  }
  const operationsAvailability = (available, signal = AbortSignal.timeout(5000), extra = {}) =>
    operationsBackendRequest('/api/operations/service', { method: 'POST', body: { available, ...extra }, signal });
  const operations = {
    status() {
      const config = operationsSettings.read();
      const state = operationsManager?.status() || { available: false, incidentCount: 0 };
      return { ...state, enabled: config.enabled,
        error: config.error || operationsError || state.configError || (!state.available ? 'Harness会话服务尚未就绪，原有录题与自愈继续运行。' : '') };
    },
    wake() {
      operationsManager?.wake?.();
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
    // `sessions` is the host session store used by the content-agent purge path
    // (ctx.sessions.get) to refuse removing a live session. Declaring it here is
    // required: the host refuses to read an undeclared injected service.
    // `sessions`/`sessionQuery` are host session services the content-agent
    // purge path reads (ctx.sessions.get to refuse a live session, then
    // ctx.sessionQuery.searchSessions to reconcile the index). The host refuses
    // to read an undeclared injected service, so both must be declared here.
    ctx.inject(['agents', 'sessions', 'sessionQuery', 'tools', 'systemPrompt', 'sessionPersistence'], (scope) => {
      // Advertise native execution only while the actual host scope is alive.
      // This is independent of optional AI operations and never authorizes upload.
      try {
        if (scope.agents?.create && scope.agents?.resume) {
          const manager = createContentAgentManager({
            ctx: scope, cwd: PLUGIN_DIR, maxConcurrent: 6,
            async request(path, options = {}) {
              if (path === '/api/content-agent/tasks' && (!options.method || options.method === 'GET')) {
                await operationsBackendRequest('/api/content-agent/service', {
                  method: 'POST', body: { available: true }, signal: options.signal,
                });
              }
              return operationsBackendRequest(path, options);
            },
            resolveRoute(task) {
              if (!task.route?.provider || !task.route?.model) throw new Error('内容任务缺少绑定的 provider/model');
              return { provider: task.route.provider, model: task.route.model };
            },
          });
          manager.start();
          scope.effect(() => () => {
            manager.stop();
            operationsBackendRequest('/api/content-agent/service', {
              method: 'POST', body: { available: false }, signal: AbortSignal.timeout(3000),
            }).catch(() => {});
          }, 'wf-plugin: native content agent');
        }
      } catch (error) {
        console.error('[wf-plugin] native content agent unavailable:', error?.message || error);
      }
      try {
        const manager = createOperationsAgentManager(scope, {
          cwd: PLUGIN_DIR,
          maxConcurrent: 2,
          canAdmit: () => !releaseCoordinator?.blocksAdmission?.(),
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
            const hb = await operationsAvailability(true, signal, { include_work_state: true });
            const result = typeof hb?.has_work === 'boolean'
              ? hb
              : await operationsBackendRequest('/api/operations/work-state', { signal });
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
  // 随插件启动拉起独立后台执行进程与工作台 API 后端；插件卸载/停用时分别清理。
  ensureWorkerDaemon();
  ensureBackend().then((r) => {
    if (r.ok) console.log('[wf-plugin] workbench backend ready at', r.url);
    else console.error('[wf-plugin] workbench backend not ready:', r.error);
  }).catch((e) => console.error('[wf-plugin] backend start failed:', e));
  if (typeof ctx.effect === 'function') {
    try {
      ctx.effect(() => () => { stopBackend(); stopWorkerDaemon(); }, 'wf-plugin: backend lifecycle');
    } catch {}
    // 后端源码热重启：监听 app/ 下 .py 改动，去抖后自动重启 Python 后端。
    try {
      ctx.effect(() => { startBackendWatch(); return () => stopBackendWatch(); }, 'wf-plugin: backend auto-restart');
    } catch {}
    // 后端存活看门狗：进程意外退出时自动拉起，队列不再因后端缺席而静默停摆。
    try {
      ctx.effect(() => { startBackendWatchdog(); return () => stopBackendWatchdog(); }, 'wf-plugin: backend watchdog');
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
  startBackendWatchdog,
  stopBackendWatchdog,
  backendWatchdogTick,
  registerUploadCommand,
  registerRoutes,
  resolvePython,
  resolveDataRoot,
  reapStaleWorkbenchProcesses,
  pickPort,
  WORKFLOW_DIR,
  PLUGIN_DIR,
};
