import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const DEFAULTS = Object.freeze({ base_url: 'http://127.0.0.1:8089/api', subject_id: 'english', timeout_seconds: 30, knowledge_version_id: '', account: '001' });
const INPUT_KEYS = new Set([...Object.keys(DEFAULTS), 'api_key', 'clear_api_key', 'password', 'clear_password']);
const CONFIG_KEYS = new Set(['base_url', 'subject_id', 'timeout_seconds', 'knowledge_version_id', 'account', 'api_key_env']);
const REPO_ROOT = path.resolve(fileURLToPath(new URL('../', import.meta.url)));
const ERRORS = Object.freeze({
  invalid_config: 'Invalid question-bank settings.',
  storage_error: 'Question-bank settings could not be accessed.',
  missing_api_key: 'An API key is required to test the connection.',
  http_error: 'The question-bank server rejected the connection check.',
  unsafe_redirect: 'The question-bank server returned a redirect.',
  timeout: 'The question-bank connection check timed out.',
  network_error: 'The question-bank connection check failed.',
});

function fail(code) {
  const error = new Error(ERRORS[code]);
  error.code = code;
  throw error;
}
function object(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value)) ||
      Reflect.ownKeys(value).some(key => !keys.has(key))) fail('invalid_config');
}
function identifier(value, allowEmpty = false) {
  if (typeof value !== 'string' || value.length > 256 || /[\x00-\x1f\x7f]/.test(value) ||
      (!allowEmpty || value !== '') && !value.trim()) fail('invalid_config');
  return value;
}
function baseURL(value) {
  if (typeof value !== 'string' || value.length > 2048 || /[^\x21-\x7e]|[\\?#]/.test(value)) fail('invalid_config');
  const raw = /^(https?):\/\/([^/]+)(\/.*)$/.exec(value);
  if (!raw || raw[2].includes('@') || raw[3].includes('%') || raw[3].includes('//') ||
      !raw[3].endsWith('/api') || raw[3].split('/').some(segment => segment === '.' || segment === '..')) fail('invalid_config');
  let parsed;
  try { parsed = new URL(value); } catch { fail('invalid_config'); }
  if (!parsed.hostname || parsed.username || parsed.password || parsed.pathname !== raw[3]) fail('invalid_config');
  // Validate the original authority too: WHATWG URL rewrites numeric hosts,
  // default ports, and certain unsafe spellings before exposing its fields.
  const authority = /^(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9.-]+)(?::([0-9]+))?$/.exec(raw[2]);
  if (!authority || authority[1].toLowerCase() !== parsed.hostname.toLowerCase() ||
      authority[2] !== undefined && (!Number(authority[2]) || Number(authority[2]) > 65535)) fail('invalid_config');
  if (raw[1] === 'http' && !['localhost', '127.0.0.1', '[::1]'].includes(authority[1])) fail('invalid_config');
  return value;
}
function config(values) {
  const result = { ...DEFAULTS, ...values };
  result.base_url = baseURL(result.base_url);
  result.subject_id = identifier(result.subject_id);
  result.knowledge_version_id = identifier(result.knowledge_version_id, true);
  result.account = identifier(result.account, true);
  if (typeof result.timeout_seconds !== 'number' || !Number.isFinite(result.timeout_seconds) ||
      result.timeout_seconds < 1 || result.timeout_seconds > 120) fail('invalid_config');
  return result;
}
function passwordValue(value) {
  if (typeof value !== 'string' || !value || value.length > 4096 || /[\x00-\x1f\x7f]/.test(value)) fail('invalid_config');
  return value;
}
function token(value) {
  if (typeof value !== 'string' || value.length > 4096 || !/^[\x21-\x7e]*$/.test(value)) fail('invalid_config');
  return value;
}

/** Host-only settings. Only runtimeEnv() exposes the credential, for child spawn.
 * No repository config, arbitrary key environment variable, or remote body is read.
 * save/read/runtimeEnv are synchronous; test is async and never persists its input.
 */
export function createQuestionBankSettings({ stateDir, env = process.env, fetchImpl = globalThis.fetch } = {}) {
  if (typeof stateDir !== 'string' || !stateDir) fail('invalid_config');
  const root = path.resolve(stateDir);
  if (root === REPO_ROOT || root.startsWith(REPO_ROOT + path.sep)) fail('invalid_config');
  const directory = path.join(root, 'question-bank');
  const configPath = path.join(directory, 'upload-config.json');
  const keyPath = path.join(directory, 'api-key');
  const passwordPath = path.join(directory, 'login-password');

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
      if (saved.api_key_env !== 'MUDU_API_KEY') fail('invalid_config');
      const { api_key_env, ...settings } = saved;
      return config(settings);
    });
  }
  function secret() {
    return storage(() => {
      const saved = readFile(keyPath, 4096);
      // An empty key file is an explicit clear, including an inherited env key.
      return token(saved === undefined ? (env?.MUDU_API_KEY ?? '') : saved);
    });
  }
  function loginSecret() {
    return storage(() => {
      const saved = readFile(passwordPath, 4096);
      const value = saved === undefined ? (env?.MUDU_UPLOAD_PASSWORD ?? '') : saved;
      return value === '' ? '' : passwordValue(value);
    });
  }
  function candidate(payload) {
    object(payload, INPUT_KEYS);
    const { api_key, clear_api_key, password, clear_password, ...settings } = payload;
    if (clear_api_key !== undefined && typeof clear_api_key !== 'boolean') fail('invalid_config');
    if (clear_password !== undefined && typeof clear_password !== 'boolean') fail('invalid_config');
    if (api_key !== undefined) token(api_key);
    if (password !== undefined) passwordValue(password);
    if (clear_api_key === true && api_key) fail('invalid_config');
    if (clear_password === true && password) fail('invalid_config');
    return {
      settings: config({ ...load(), ...settings }),
      key: clear_api_key === true ? '' : api_key || secret(),
      writeKey: clear_api_key === true || !!api_key,
      password: clear_password === true ? '' : password || loginSecret(),
      writePassword: clear_password === true || !!password,
    };
  }
  function publicSettings(settings, key, password) {
    return { ...settings, has_api_key: !!key, has_password: !!password };
  }
  function persist(settings) {
    const { knowledge_version_id, account, ...rest } = settings;
    atomic(configPath, JSON.stringify({
      ...rest,
      api_key_env: 'MUDU_API_KEY',
      ...(knowledge_version_id ? { knowledge_version_id } : {}),
      ...(account && account !== '001' ? { account } : {}),
    }, null, 2) + '\n');
  }
  return {
    read() { return publicSettings(load(), secret(), loginSecret()); },
    save(payload) {
      const next = candidate(payload);
      storage(() => {
        persist(next.settings);
        if (next.writeKey) atomic(keyPath, next.key);
        if (next.writePassword) atomic(passwordPath, next.password);
      });
      return publicSettings(load(), secret(), loginSecret());
    },
    runtimeEnv() {
      const settings = load();
      const key = secret();
      const password = loginSecret();
      storage(() => persist(settings));
      return {
        MUDU_UPLOAD_CONFIG: configPath,
        MUDU_API_KEY: key,
        ...(settings.account ? { MUDU_UPLOAD_ACCOUNT: settings.account } : {}),
        ...(password ? { MUDU_UPLOAD_PASSWORD: password } : {}),
      };
    },
    async test(payload = {}) {
      let next;
      try { next = candidate(payload); }
      catch { return { ok: false, code: 'invalid_config', message: ERRORS.invalid_config }; }
      if (!next.key) return { ok: false, code: 'missing_api_key', message: ERRORS.missing_api_key };
      const controller = new AbortController();
      let timer;
      try {
        // Promise.race enforces a deadline even if an injected transport ignores abort.
        const deadline = new Promise((_, reject) => {
          timer = setTimeout(() => { controller.abort(); reject(new Error()); }, next.settings.timeout_seconds * 1000);
        });
        const response = await Promise.race([
          Promise.resolve().then(() => fetchImpl(next.settings.base_url + '/catalog/knowledge-versions', {
            method: 'GET', headers: { Accept: 'application/json', Authorization: 'Bearer ' + next.key },
            redirect: 'error', signal: controller.signal,
          })), deadline,
        ]);
        // Deliberately never read body, headers, statusText, URLs, or remote errors.
        try { Promise.resolve(response.body?.cancel?.()).catch(() => {}); } catch { /* No remote text is exposed. */ }
        if (!Number.isInteger(response.status) || response.status < 200 || response.status >= 300 || response.redirected) {
          const code = response.redirected || response.status >= 300 && response.status < 400 ? 'unsafe_redirect' : 'http_error';
          return { ok: false, code, message: ERRORS[code] };
        }
        return { ok: true, message: 'Question-bank connection succeeded.' };
      } catch {
        const code = controller.signal.aborted ? 'timeout' : 'network_error';
        return { ok: false, code, message: ERRORS[code] };
      } finally { clearTimeout(timer); }
    },
  };
}
