import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';

const REPO_ROOT = path.resolve(fileURLToPath(new URL('../', import.meta.url)));
const DEFAULTS = Object.freeze({ mode: 'auto', enabled: true, endpoint: 'https://paddleocr.aistudio-app.com/api/v2/ocr/jobs', model: 'PaddleOCR-VL-1.6', useDocOrientationClassify: false, useDocUnwarping: false, useChartRecognition: false, timeout_seconds: 300, poll_interval_seconds: 2 });
const INPUT_KEYS = new Set([...Object.keys(DEFAULTS), 'token', 'clear_token']);
const CONFIG_KEYS = new Set([...Object.keys(DEFAULTS), 'backend', 'token_file']);
export const OCR_MESSAGES = Object.freeze({ invalid_config: '请检查 OCR 地址、模型、超时、开关和测试文件。', storage_error: '无法访问本机 OCR 私有配置。', missing_token: '请填写 OCR Token，或先保存 Token。', python_unavailable: '未找到可用 Python 3.9+。', http_error: 'OCR 服务拒绝请求，请检查 Token 和接口权限。', unsafe_redirect: 'OCR 服务重定向已被拒绝，请填写最终安全地址。', network_error: '无法连接 OCR 服务。', timeout: 'OCR 完整测试超时；未自动重复提交任务。', job_failed: 'OCR 任务失败，请检查文件和服务状态。', invalid_result: 'OCR 返回结果不完整或格式无效。' });
function fail(code) { const error = new Error(OCR_MESSAGES[code] || OCR_MESSAGES.invalid_config); error.code = code; throw error; }
function object(value, keys) { if (!value || typeof value !== 'object' || Array.isArray(value) || ![Object.prototype, null].includes(Object.getPrototypeOf(value)) || Reflect.ownKeys(value).some(key => !keys.has(key))) fail('invalid_config'); }
function token(value) { if (typeof value !== 'string' || value.length > 4096 || !/^[\x21-\x7e]*$/.test(value)) fail('invalid_config'); return value; }
function config(values) {
  const result = { ...DEFAULTS, ...values };
  const value = result.endpoint;
  if (typeof value !== 'string' || value.length > 2048 || /[^\x21-\x7e]|[\\?#]/.test(value)) fail('invalid_config');
  let url;
  try { url = new URL(value); } catch { fail('invalid_config'); }
  const raw = /^(https?):\/\/([^/]+)(\/.*)$/.exec(value);
  if (!raw || url.username || url.password || raw[2].includes('@') || url.pathname !== raw[3] || !raw[3].endsWith('/jobs') || raw[3].includes('%') || raw[3].includes('//') || raw[3].split('/').some(part => part === '.' || part === '..')) fail('invalid_config');
  const authority = /^(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+)(?::([0-9]+))?$/.exec(raw[2]);
  if (!authority || authority[1].toLowerCase() !== url.hostname.toLowerCase() || authority[2] !== undefined && (!Number(authority[2]) || Number(authority[2]) > 65535) || raw[1] === 'http' && !['localhost', '127.0.0.1', '[::1]'].includes(authority[1])) fail('invalid_config');
  if (typeof result.model !== 'string' || !result.model.trim() || result.model.length > 256 || /[\x00-\x1f\x7f]/.test(result.model)) fail('invalid_config');
  // 三态开关：auto=按需复核，on=参与，off=不参与。旧配置只有 enabled 布尔值时
  // 迁移成对应 on/off，不能把旧的“关闭”悄悄解释成 auto。
  if (!Object.prototype.hasOwnProperty.call(values, 'mode')
      && typeof values.enabled === 'boolean') {
    result.mode = values.enabled ? 'on' : 'off';
  }
  if (!['auto', 'on', 'off'].includes(result.mode)) fail('invalid_config');
  if (Object.prototype.hasOwnProperty.call(values, 'enabled') && typeof values.enabled !== 'boolean') fail('invalid_config');
  result.enabled = result.mode !== 'off';
  for (const key of ['useDocOrientationClassify', 'useDocUnwarping', 'useChartRecognition']) if (typeof result[key] !== 'boolean') fail('invalid_config');
  for (const [key, low, high] of [['timeout_seconds', 1, 1800], ['poll_interval_seconds', 0.01, 60]]) if (typeof result[key] !== 'number' || !Number.isFinite(result[key]) || result[key] < low || result[key] > high) fail('invalid_config');
  return result;
}

// Reuses the question-bank private-storage policy: repository exclusion,
// symlink/hardlink refusal, private modes and atomic exclusive temp writes.
export function createOCRSettings({ stateDir, env = process.env, python = () => 'python3', workflowDir = path.join(REPO_ROOT, 'app'), spawnImpl = spawn } = {}) {
  if (typeof stateDir !== 'string' || !stateDir) fail('invalid_config');
  const root = path.resolve(stateDir);
  if (root === REPO_ROOT || root.startsWith(REPO_ROOT + path.sep)) fail('invalid_config');
  const directory = path.join(root, 'ocr');
  const configPath = path.join(directory, 'ocr-config.json');
  const keyPath = path.join(directory, 'token');
  // Live master-switch state. The workflow reads this file on every OCR call,
  // so toggling the switch takes effect without restarting the backend.
  const assistPath = path.join(directory, 'assist.json');
  function storage(action) {
    try { return action(); } catch (error) {
      if (error?.code === 'invalid_config' || error?.code === 'storage_error') throw error;
      fail('storage_error');
    }
  }
  function checkDirectory(create = false) {
    // Resolve the nearest existing ancestor before creating anything. A symlink
    // in a caller-supplied state path must not redirect credential storage into
    // the repository even when its final directories do not exist yet.
    let ancestor = root;
    const tail = [];
    let physical;
    while (physical === undefined) {
      try { physical = path.resolve(fs.realpathSync(ancestor), ...tail); }
      catch (error) {
        if (error.code !== 'ENOENT') throw error;
        tail.unshift(path.basename(ancestor));
        const parent = path.dirname(ancestor);
        if (parent === ancestor) throw error;
        ancestor = parent;
      }
    }
    const repository = fs.realpathSync(REPO_ROOT);
    if (physical === repository || physical.startsWith(repository + path.sep)) fail('storage_error');
    if (create) fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    try {
      const stat = fs.lstatSync(directory);
      if (!stat.isDirectory() || stat.isSymbolicLink()) fail('storage_error');
      fs.chmodSync(directory, 0o700);
    } catch (error) { if (error.code !== 'ENOENT' || create) throw error; }
  }
  function readFile(filename, maxBytes) {
    checkDirectory();
    let fd;
    try {
      const stat = fs.lstatSync(filename);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || stat.size > maxBytes) fail('storage_error');
      fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
      const actual = fs.fstatSync(fd);
      if (!actual.isFile() || actual.nlink !== 1 || actual.size > maxBytes) fail('storage_error');
      fs.fchmodSync(fd, 0o600);
      return fs.readFileSync(fd, 'utf8');
    } catch (error) { if (error.code === 'ENOENT') return undefined; throw error; }
    finally { if (fd !== undefined) fs.closeSync(fd); }
  }
  function atomic(filename, value) {
    checkDirectory(true);
    const temporary = path.join(directory, `.${path.basename(filename)}-${randomUUID()}.tmp`);
    let fd;
    try {
      fd = fs.openSync(temporary, fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_WRONLY, 0o600);
      fs.writeFileSync(fd, value, 'utf8');
      fs.fsyncSync(fd);
      fs.closeSync(fd); fd = undefined;
      fs.renameSync(temporary, filename);
    } finally {
      if (fd !== undefined) fs.closeSync(fd);
      try { fs.unlinkSync(temporary); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  }
  function load() {
    return storage(() => {
      const raw = readFile(configPath, 16384);
      if (raw === undefined) return config({});
      let saved;
      try { saved = JSON.parse(raw); } catch { fail('storage_error'); }
      object(saved, CONFIG_KEYS);
      if (saved.backend !== 'paddle_jobs' || saved.token_file !== keyPath) fail('invalid_config');
      const { backend, token_file, ...settings } = saved;
      return config(settings);
    });
  }
  function secret() { return storage(() => token(readFile(keyPath, 4096) ?? env?.MUDU_OCR_TOKEN ?? '')); }
  function candidate(payload) {
    object(payload, INPUT_KEYS);
    const { token: value, clear_token, ...settings } = payload;
    if (clear_token !== undefined && typeof clear_token !== 'boolean') fail('invalid_config');
    if (value !== undefined) token(value);
    if (clear_token === true && value) fail('invalid_config');
    return { settings: config({ ...load(), ...settings }), key: clear_token === true ? '' : value || secret(), writeKey: clear_token === true || !!value };
  }
  function publicSettings(settings, key) { return { ...settings, has_token: !!key }; }
  function persist(settings) { atomic(configPath, JSON.stringify({ backend: 'paddle_jobs', ...settings, token_file: keyPath }, null, 2) + '\n'); }
  function writeAssist(settings) { atomic(assistPath, JSON.stringify({ mode: (settings && settings.mode) || 'auto', enabled: (settings && settings.mode ? settings.mode !== 'off' : !!settings) }) + '\n'); }
  return {
    read() { return publicSettings(load(), secret()); },
    save(payload) {
      const next = candidate(payload);
      storage(() => {
        // Always materialize effective secret (including inherited env token),
        // so backend processes read fresh credentials after later saves/clears.
        atomic(keyPath, next.key);
        persist(next.settings);
        writeAssist(next.settings);
      });
      return publicSettings(load(), secret());
    },
    runtimeEnv() {
      // The OCR master switch defaults to off: text-layer/native extraction is
      // the default path until the user explicitly enables OCR assistance.
      return storage(() => {
        let settings = { mode: 'auto' };
        try { const loaded = load(); settings = { mode: loaded.mode || 'auto' }; } catch (error) {
          if (error?.code !== 'storage_error' && error?.code !== 'invalid_config') throw error;
        }
        try { writeAssist(settings); } catch { return { MUDU_PADDLE_OCR_CONFIG: configPath }; }
        return { MUDU_PADDLE_OCR_CONFIG: configPath, MUDU_OCR_ASSIST_STATE: assistPath };
      });
    },
    async test(payload = {}) {
      let next, file_url, file_path;
      try {
        const keys = new Set([...INPUT_KEYS, 'config', 'file_url', 'file_path']);
        object(payload, keys);
        ({ file_url, file_path } = payload);
        const { config: draft, file_url: unusedUrl, file_path: unusedPath, ...flat } = payload;
        if (draft !== undefined && Object.keys(flat).length) fail('invalid_config');
        next = candidate(draft === undefined ? flat : draft);
        if (!!file_url === !!file_path || file_url !== undefined && (typeof file_url !== 'string' || !file_url) || file_path !== undefined && (typeof file_path !== 'string' || !file_path || file_path.length > 8192 || /[\x00-\x1f]/.test(file_path))) fail('invalid_config');
      } catch (error) { const code = error?.code === 'storage_error' ? 'storage_error' : 'invalid_config'; return { ok: false, code, message: OCR_MESSAGES[code] }; }
      if (!next.key) return { ok: false, code: 'missing_token', message: OCR_MESSAGES.missing_token };
      const executable = python();
      if (!executable) return { ok: false, code: 'python_unavailable', message: OCR_MESSAGES.python_unavailable };
      return await new Promise(resolve => {
        let child, settled = false, output = '', timer;
        const finish = result => { if (settled) return; settled = true; clearTimeout(timer); resolve(result); };
        const failure = (code, result = {}) => {
          const clean = (value, limit) => typeof value === 'string' ? value.split(next.key).join('[REDACTED]').replace(/https?:\/\/[^\s"'<>]+/gi, '[URL REDACTED]').replace(/Bearer\s+[^\s"'<>]+/gi, 'Bearer [REDACTED]').replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, limit) : undefined;
          const error_message = clean(result.error_message, 500);
          const error_code = clean(result.error_code, 128);
          const http_status = Number.isInteger(result.http_status) && result.http_status >= 100 && result.http_status <= 599 ? result.http_status : undefined;
          finish({ ok: false, code, message: OCR_MESSAGES[code] + (error_message ? ' ' + error_message : ''), ...(http_status ? { http_status } : {}), ...(error_code ? { error_code } : {}), ...(error_message ? { error_message } : {}) });
        };
        try {
          child = spawnImpl(executable, ['-m', 'workflow.paddle_jobs'], { cwd: workflowDir, env: { ...env, PYTHONUNBUFFERED: '1' }, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
          timer = setTimeout(() => { child.kill(); failure('timeout'); }, next.settings.timeout_seconds * 1000 + 2000);
          child.stdout.on('data', data => {
            output += data.toString('utf8');
            if (Buffer.byteLength(output) > 8 * 1024 * 1024) { child.kill(); failure('invalid_result'); }
          });
          // Never expose subprocess stderr or raw errors; either can contain
          // remote responses, credentials or transient draft configuration.
          child.stderr.on('data', () => {});
          child.on('error', () => failure('python_unavailable'));
          child.stdin.on('error', () => failure('invalid_result'));
          child.on('close', code => {
            if (settled) return;
            try {
              const result = JSON.parse(output.split(next.key).join('[REDACTED]'));
              if (code !== 0 || result.ok !== true) return failure(Object.hasOwn(OCR_MESSAGES, result.code) ? result.code : 'invalid_result', result);
              if (!Array.isArray(result.pages) || !result.pages.length || typeof result.markdown !== 'string') return failure('invalid_result');
              finish({ ok: true, message: 'OCR 完整测试成功：已提交、轮询并读取识别结果。', pages: result.pages, markdown: result.markdown, job_id: result.job_id, seconds: result.seconds, warnings: result.warnings });
            } catch { failure('invalid_result'); }
          });
          child.stdin.end(JSON.stringify({ config: { ...next.settings, token: next.key }, ...(file_url ? { file_url } : { file_path }) }));
        } catch { if (child) child.kill(); failure('python_unavailable'); }
      });
    },
  };
}
