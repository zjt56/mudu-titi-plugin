const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { mkdtemp, readFile, mkdir, writeFile, rename, rm } = require('node:fs/promises');
const { tmpdir } = require('node:os');
const { resolve, join, dirname } = require('node:path');
const { pathToFileURL } = require('node:url');
const { createInterface } = require('node:readline');

const project = resolve(__dirname, '..');
const moduleReady = import(pathToFileURL(join(project, 'lib/operations-agent.js')));
const python = process.env.PYTHON || 'python3';
const sourceText = '1. This is the original paper. 原文证据。\n';
// Import the real Handler without main()/serve(): no supervisor or model starts.
// Preseed only discovery; locks, snapshots, claims and actions remain production code.
const serverScript = String.raw`
import json, sys
from pathlib import Path
from http.server import HTTPServer
from workflow import manifest as mf, operations as op
from workbench import server

root = Path(sys.argv[1]) / "workflows"
folder = root / "paper"
(folder / "workflow").mkdir(parents=True)
(folder / "01_source").mkdir()
(folder / "07_review").mkdir()
(folder / "01_source/正文.txt").write_bytes("1. This is the original paper. 原文证据。\n".encode("utf-8"))
manifest = mf.new_manifest("operations-http-fixture", folder, {}, {})
manifest["stages"]["format_gate"]["status"] = "failed"
mf.save(folder, manifest)
(folder / "07_review/upload-format.json").write_text(json.dumps({"passed": False, "errors": [{"code": "fixture_format", "message": "fixture requires deep repair"}]}), encoding="utf-8")
state = {"identity": op._identity(folder), "incident": {
    "incident_id": "http-incident", "batch_name": "paper", "batch_dir": str(folder),
    "stage": "format_gate", "detail": "fixture_format: fixture requires deep repair",
    "signature": "http-fixture", "status": "queued", "eligible": True,
    "queue_generation": 1, "fence": 0, "events": []
}, "receipts": [], "failures": {}}
(folder / "workflow/operations.json").write_text(json.dumps(state), encoding="utf-8")
server.WORKFLOWS = root
httpd = HTTPServer(("127.0.0.1", 0), server.Handler)
print(json.dumps({"port": httpd.server_port, "folder": str(folder)}), flush=True)
try:
    httpd.serve_forever(poll_interval=0.05)
finally:
    httpd.server_close()
`;

function deadline(promise, ms, label) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms);
  })]).finally(() => clearTimeout(timer));
}

async function fixture(t) {
  const temporary = await mkdtemp(join(tmpdir(), 'operations-http-'));
  const child = spawn(python, ['-u', '-c', serverScript, temporary], {
    cwd: temporary,
    env: { ...process.env, PYTHONPATH: join(project, 'app'), PYTHONDONTWRITEBYTECODE: '1' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-12000); });
  const exited = new Promise(resolve => child.once('exit', (code, signal) => resolve({ code, signal })));
  const lines = createInterface({ input: child.stdout });
  t.after(async () => {
    lines.close();
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
    try { await deadline(exited, 2000, 'Python shutdown'); }
    catch { child.kill('SIGKILL'); await deadline(exited, 2000, 'Python forced shutdown'); }
    // This exact absolute directory was returned by mkdtemp for this fixture.
    assert.equal(dirname(temporary), tmpdir());
    assert.ok(temporary.startsWith(join(tmpdir(), 'operations-http-')));
    await rm(temporary, { recursive: true, force: true });
  });
  const ready = await deadline(new Promise((resolve, reject) => {
    lines.once('line', line => { try { resolve(JSON.parse(line)); } catch (error) { reject(error); } });
    child.once('error', reject);
    exited.then(({ code, signal }) => reject(new Error(`Python exited (${code}/${signal}): ${stderr}`)));
  }), 10000, 'Python HTTP startup');
  const calls = [];
  async function backendRequest(path, init = {}) {
    const response = await fetch(`http://127.0.0.1:${ready.port}${path}`, {
      method: init.method || 'GET',
      headers: init.body ? { 'Content-Type': 'application/json' } : undefined,
      body: init.body ? JSON.stringify(init.body) : undefined,
      signal: init.signal ? AbortSignal.any([init.signal, AbortSignal.timeout(4000)]) : AbortSignal.timeout(4000),
    });
    const result = await response.json();
    calls.push({ path, method: init.method || 'GET', body: init.body, status: response.status, result });
    if (!response.ok) throw Object.assign(new Error(result.error || `HTTP ${response.status}`), { status: response.status });
    return result;
  }
  return { temporary, folder: ready.folder, calls, backendRequest,
    ledger: async () => JSON.parse(await readFile(join(ready.folder, 'workflow/operations.json'), 'utf8')) };
}

const base = '/api/batches/paper/operations';
const envelope = (claim, action, input = {}, request_id = `direct-${action}`) => ({
  incident_id: claim.incident_id, attempt_id: claim.attempt_id, session_id: claim.session_id,
  request_id, action, input,
});

test('real Handler list and claim do not mistake their own locks for a busy worker', { timeout: 20000 }, async t => {
  const f = await fixture(t);
  const listed = await f.backendRequest('/api/operations/incidents');
  assert.equal(listed.incidents.length, 1, 'real execution_busy must admit the idle temporary batch');
  assert.equal(listed.incidents[0].batch_name, 'paper');
  const view = await f.backendRequest(base);
  assert.equal(view.incident.status, 'queued');
  const claim = await f.backendRequest(`${base}/claim`, { method: 'POST', body: {
    incident_id: view.incident.incident_id, session_id: 'direct-http-session',
  } });
  assert.equal(claim.status, 'claimed');
  assert.equal(claim.session_id, 'direct-http-session');
  assert.match(claim.attempt_id, /^[a-f0-9]{32}$/);
  assert.ok(claim.lease_expires_at > Date.now() / 1000);
  assert.equal((await f.ledger()).incident.attempt_id, claim.attempt_id);
  await assert.rejects(f.backendRequest(`${base}/claim`, { method: 'POST', body: {
    incident_id: claim.incident_id, session_id: 'competing-session',
  } }), /incident_not_claimable/);
  const receipt = await f.backendRequest(`${base}/action`, { method: 'POST', body:
    envelope(claim, 'release', { reason: 'direct HTTP contract checked' }) });
  assert.equal(receipt.status, 'completed');
  assert.equal(receipt.request_id, 'direct-release');
  assert.equal(receipt.evidence.released, true);
  assert.equal((await f.backendRequest('/api/operations/incidents')).incidents.length, 0, 'release backoff is real');
});

test('manager tools round-trip real Python receipts, preserve failure truth and fence archived identities', { timeout: 25000 }, async t => {
  const f = await fixture(t);
  const { createOperationsAgentManager, OPERATIONS_TOOL_NAMES } = await moduleReady;
  const agents = [], events = [], results = [];
  const ctx = { agents: {} };
  async function build(init) {
    const tools = new Map(), definitions = new Map(), listeners = new Map();
    const restrictions = [], guards = [], sections = [], messages = [];
    const record = { init, definitions, restrictions, guards, sections, messages, disposed: false, conclusions: 0 };
    const agent = {
      model: { select: route => { record.route = route; } },
      inbox: { nextTurn: [], nextStep: [] },
      followup: message => messages.push(message),
      cancel: () => {},
      whenIdle: async () => {
        assert.equal(messages.length, 1);
        assert.deepEqual([...tools.keys()].sort(), [...OPERATIONS_TOOL_NAMES].sort());
        assert.deepEqual(restrictions, [{ allow: [] }]);
        assert.match(guards[0]({ name: 'upload' }), /Only incident-scoped/);
        assert.equal(guards[0]({ name: 'operations_read_evidence' }), undefined);
        const call = async (name, input = {}) => {
          const value = await tools.get(name).execute(input, { concludeTurn: () => { record.conclusions++; } });
          results.push({ name, value });
          return value;
        };
        const diagnosis = await call('operations_diagnose');
        assert.equal(diagnosis.stage, 'format_gate');
        assert.ok(diagnosis.evidence_paths.includes('01_source/正文.txt'));
        const evidence = await call('operations_read_evidence', { path: '01_source/正文.txt' });
        assert.equal(evidence.text, sourceText);
        await assert.rejects(call('operations_finish', { outcome: 'resolved', summary: 'Cannot claim success' }), { code: 'VERIFICATION_REQUIRED' });
        await call('operations_record_finding', {
          summary: `paper (${f.folder}): fixture_format requires deep repair; original evidence retained`,
          repro: 'Read 07_review/upload-format.json', expected: 'format_gate passes genuine checks',
          actual: 'fixture_format failed', paths: ['01_source/正文.txt', '07_review/upload-format.json'],
        });
        const finished = await call('operations_finish', { outcome: 'needs_deep_repair', summary: 'paper: fixture_format remains unresolved; original evidence retained' });
        assert.equal(finished.outcome, 'needs_deep_repair');
        assert.equal(finished.verified, false);
      },
    };
    await init.setup({
      tools: {
        register: definition => { tools.set(definition.name, definition); definitions.set(definition.name, definition); return () => tools.delete(definition.name); },
        restrict: value => { restrictions.push(value); return () => {}; },
        guard: value => { guards.push(value); return () => {}; },
      },
      systemPrompt: { section: value => { sections.push(value); return () => {}; } },
      on: (name, listener) => { listeners.set(name, listener); return () => listeners.delete(name); },
    }, agent);
    agents.push(record);
    return { agent, dispose: async () => { record.disposed = true; listeners.get('agent/disposed')?.(); } };
  }
  ctx.agents.create = build;
  ctx.agents.resume = async () => { assert.fail('new incident must create its persisted session'); };
  const manager = createOperationsAgentManager(ctx, {
    cwd: project, backendRequest: f.backendRequest,
    resolveRoute: () => ({ provider: 'fake-provider', model: 'fake-model' }),
    makeSessionId: () => 'manager-http-session', intervalMs: 60000, heartbeatMs: 60000,
    requestTimeoutMs: 4000, maxAttemptMs: 10000, onProgress: event => events.push(event),
  });
  t.after(() => deadline(manager.dispose(), 5000, 'manager disposal'));
  manager.start();
  await manager.tick();
  await deadline(manager.drain(), 12000, 'manager attempt');
  assert.equal(agents.length, 1, JSON.stringify({ status: manager.status(), events, calls: f.calls }));
  assert.deepEqual(results.map(row => row.name), ['operations_diagnose', 'operations_read_evidence', 'operations_record_finding', 'operations_finish']);
  const agent = agents[0];
  assert.equal(agent.init.sessionId, 'manager-http-session');
  assert.deepEqual(agent.route, { provider: 'fake-provider', model: 'fake-model' });
  assert.equal(agent.disposed, true);
  assert.equal(agent.conclusions, 1);
  assert.match(agent.sections[0].text, /不得上传/);
  const readSchema = agent.definitions.get('operations_read_evidence').parameters;
  assert.deepEqual(readSchema.required, ['path']);
  assert.equal(readSchema.properties.path.type, 'string');
  assert.equal(readSchema.additionalProperties, false);
  const findingSchema = agent.definitions.get('operations_record_finding').parameters;
  assert.equal(findingSchema.properties.paths.type, 'array');
  assert.equal(findingSchema.properties.paths.items.type, 'string');
  assert.deepEqual(agent.definitions.get('operations_finish').parameters.properties.outcome.enum, ['resolved', 'needs_deep_repair']);
  const state = await f.ledger();
  assert.equal(state.incident.status, 'needs_deep_repair');
  assert.equal(state.incident.session_id, 'manager-http-session');
  assert.equal(state.incident.attempt_id, null);
  assert.equal(state.incident.eligible, false);
  assert.equal(state.incident.lease_expires_at, 0);
  assert.match(state.incident.findings[0].summary, /fixture_format/);
  assert.equal(state.incident.findings[0].actual, 'fixture_format failed');
  const actionCalls = f.calls.filter(row => row.body?.action);
  assert.deepEqual(actionCalls.map(row => row.body.action), ['progress', 'heartbeat', 'diagnose', 'read_evidence', 'record_finding', 'finish']);
  assert.equal(new Set(actionCalls.map(row => row.body.request_id)).size, actionCalls.length);
  assert.equal(state.receipts.length, actionCalls.length);
  for (const row of actionCalls) {
    assert.equal(row.body.session_id, 'manager-http-session');
    assert.equal(row.body.incident_id, 'http-incident');
    assert.match(row.body.attempt_id, /^[a-f0-9]{32}$/);
    assert.deepEqual(state.receipts.find(receipt => receipt.request_id === row.body.request_id), row.result);
    assert.equal(row.result.status, 'completed');
    assert.ok(row.result.evidence && typeof row.result.evidence === 'object');
  }
  assert.deepEqual(state.incident.events.map(event => event.action), ['claim', ...actionCalls.map(row => row.body.action)]);
  assert.ok(events.some(event => event.state === 'needs_deep_repair'));
  assert.ok(!events.some(event => ['resolved', 'session_failed', 'lease_uncertain', 'idle_incomplete'].includes(event.state)), JSON.stringify(events));
  assert.ok(f.calls.every(row => row.path === '/api/operations/incidents' || row.path.startsWith(base)));
  const manifest = JSON.parse(await readFile(join(f.folder, 'workflow/manifest.json'), 'utf8'));
  assert.equal(manifest.stages.format_gate.status, 'failed');
  assert.deepEqual(manifest.side_effects, []);
  assert.equal(await readFile(join(f.folder, '01_source/正文.txt'), 'utf8'), sourceText);
  await manager.stop();
  await manager.dispose();

  // Archive inside this unique fixture only; a same-name new folder must not
  // accept a late request carrying the archived folder's event/session/attempt.
  const archived = join(f.temporary, 'archived-paper');
  assert.equal(resolve(f.folder), join(f.temporary, 'workflows', 'paper'));
  await rename(f.folder, archived);
  await mkdir(join(f.folder, 'workflow'), { recursive: true });
  await writeFile(join(f.folder, 'workflow/manifest.json'), JSON.stringify(manifest));
  const archivedLedger = await readFile(join(archived, 'workflow/operations.json'), 'utf8');
  const oldClaim = f.calls.find(row => row.path.endsWith('/claim')).result;
  await assert.rejects(f.backendRequest(`${base}/action`, { method: 'POST', body:
    envelope(oldClaim, 'progress', { summary: 'late old attempt' }, 'archived-late-request') }), /incident_mismatch/);
  assert.equal(await readFile(join(archived, 'workflow/operations.json'), 'utf8'), archivedLedger);
  await assert.rejects(readFile(join(f.folder, 'workflow/operations.json')), { code: 'ENOENT' });
});
