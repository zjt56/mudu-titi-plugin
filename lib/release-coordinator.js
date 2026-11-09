import { readFileSync, writeFileSync, unlinkSync } from 'node:fs';

// Host-owned activation for isolated code repair. The backend writes the marker
// before touching sources (`prepared`) and after all replacements (`installed`);
// the Host pauses its fs watcher, performs exactly one restart, and removes the
// marker only after a healthy boot. A failed boot leaves an explicit `failed`
// marker instead of retrying forever, because a restart loop cannot fix code.
export function createReleaseCoordinator(options = {}) {
  const markerPath = options.markerPath;
  const restart = options.restart || (async () => ({ ok: false, error: 'restart_unavailable' }));
  const rollback = options.rollback || null;
  const log = options.log || (() => {});
  const intervalMs = options.intervalMs || 500;
  const staleMs = options.staleMs || 300000;
  const now = options.now || Date.now;
  let timer = null;
  let busy = false;
  let stopped = false;
  const status = { paused: false, state: '', lastError: '' };

  function read() {
    try { return JSON.parse(readFileSync(markerPath, 'utf8')); } catch { return null; }
  }
  function write(body) {
    try { writeFileSync(markerPath, JSON.stringify(body, null, 1)); } catch {}
  }
  function clear() {
    try { unlinkSync(markerPath); } catch {}
  }

  async function check() {
    if (busy || stopped || !markerPath) return status;
    const marker = read();
    if (!marker) {
      if (status.state !== '') status.state = '';
      status.paused = false;
      return status;
    }
    const state = String(marker.state || '');
    status.state = state;
    if (state === 'prepared') {
      // Install is rewriting sources right now: a watcher restart would boot a
      // half-installed tree. Suppress it, but never suppress a crashed install.
      const numeric = typeof marker.at === 'number' || (typeof marker.at === 'string' && /^\d+(?:\.\d+)?$/.test(marker.at));
      const started = numeric ? Number(marker.at) * (Number(marker.at) > 1e12 ? 1 : 1000) : Date.parse(marker.at);
      const age = now() - started;
      status.paused = Number.isFinite(started) && age >= 0 && age < staleMs;
      status.lastError = Number.isFinite(started) ? '' : 'prepared_timestamp_invalid';
      return status;
    }
    if (state === 'rolled_back') {
      status.paused = false;
      clear();
      return status;
    }
    if (state === 'failed') {
      status.paused = false;
      return status;
    }
    if (state !== 'installed') return status;
    busy = true;
    status.paused = true;
    try {
      const result = await restart();
      if (stopped) return status;
      if (!result || result.ok !== true) throw new Error(result?.error || 'restart_failed');
      clear();
      status.state = 'active';
      status.lastError = '';
      log('release active after one healthy restart');
    } catch (error) {
      status.lastError = String(error?.message || error).slice(0, 500);
      log('release restart failed: ' + status.lastError);
      // A release that cannot boot is not fixed by restarting it again, so put
      // the previous files back and boot those exactly once. If the restore
      // itself is impossible, keep the marker and stop: the tree needs a human.
      if (stopped) return status;
      let reverted;
      try { reverted = rollback ? await rollback({ journal: marker.journal, plan_id: marker.plan_id }) : null; }
      catch (failure) { reverted = { ok: false, error: String(failure?.message || failure) }; }
      if (stopped) return status;
      if (reverted && reverted.ok === true) {
        write({ state: 'rolled_back', at: Math.floor(now() / 1000), plan_id: marker.plan_id || '',
          restored: reverted.restored || [], error: status.lastError });
        const second = await restart().catch((failure) => ({ ok: false, error: String(failure?.message || failure) }));
        if (stopped) return status;
        if (second && second.ok === true) {
          clear();
          status.state = 'rolled_back';
          log('release reverted after unhealthy boot and old code is serving');
        } else {
          status.state = 'failed';
          status.lastError = 'rollback_boot_failed: ' + String(second?.error || '');
          write({ state: 'failed', at: Math.floor(now() / 1000), plan_id: marker.plan_id || '',
            files: Array.isArray(marker.files) ? marker.files : [], error: status.lastError });
        }
      } else {
        status.state = 'failed';
        status.lastError = 'rollback_failed: ' + String(reverted?.error || 'no_rollback_handler');
        write({ state: 'failed', at: Math.floor(now() / 1000), plan_id: marker.plan_id || '',
          files: Array.isArray(marker.files) ? marker.files : [], error: status.lastError });
      }
    } finally {
      busy = false;
      status.paused = false;
    }
    return status;
  }

  function start() {
    stopped = false;
    if (!timer) {
      timer = setInterval(() => { check().catch(() => {}); }, intervalMs);
      timer.unref?.();
    }
    return status;
  }
  function stop() {
    stopped = true;
    if (timer) { clearInterval(timer); timer = null; }
  }
  return { start, stop, check, status };
}
