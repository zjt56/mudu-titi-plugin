import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

// Optional operations assistance has its own configuration. It never rewrites
// the existing model router, workflow policy or per-batch execution state.
export function createOperationsSettings({ stateDir }) {
  const path = join(stateDir, 'operations.json');
  function read() {
    try {
      const value = JSON.parse(readFileSync(path, 'utf8'));
      if (typeof value.enabled !== 'boolean') throw new Error('invalid operations configuration');
      return { enabled: value.enabled };
    } catch (error) {
      if (error.code === 'ENOENT') return { enabled: true };
      return { enabled: false, error: 'AI运维配置无法读取，原有录题与自愈继续运行。' };
    }
  }
  function save(value) {
    if (!value || typeof value.enabled !== 'boolean') throw new Error('enabled 必须为布尔值');
    mkdirSync(stateDir, { recursive: true, mode: 0o700 });
    const temp = path + '.' + randomUUID() + '.tmp';
    writeFileSync(temp, JSON.stringify({ enabled: value.enabled }) + '\n', { mode: 0o600 });
    renameSync(temp, path);
    return read();
  }
  return { read, save };
}

export function operationsRoute(router) {
  const entry = router?.roles?.operations ?? router?.default;
  if (!entry || typeof entry.provider !== 'string' || !entry.provider.trim()
      || typeof entry.model !== 'string' || !entry.model.trim()) {
    throw new Error('AI运维需要有效的 operations 或默认模型配置；不会自动替换模型。');
  }
  return { provider: entry.provider, model: entry.model,
    ...(entry.reasoningEffort ? { reasoningEffort: entry.reasoningEffort } : {}) };
}
