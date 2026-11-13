// Guard: every host service read inside a ctx.inject([...], (scope) => {...})
// block must be declared in that block's dependency list. The host refuses to
// expose an undeclared injected service, which surfaced as
// `cannot get property "sessions" without inject` during batch deletion.
// This test scans lib/index.js and lib/client.js, pairs each inject block with
// the host services read inside it, and fails on an undeclared read.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const LIB = path.join(__dirname, '../lib');

// Services the Host exposes for injection. Reads of anything else off
// ctx/scope (ctx.effect, ctx.inject, ctx.config …) are Cordis APIs, always ok.
const HOST_SERVICES = new Set([
  'agents', 'sessions', 'sessionQuery', 'sessionPersistence', 'tools',
  'systemPrompt', 'llm', 'webServer', 'commands', 'attachments', 'slots',
]);

// Skip strings, template literals, and comments so brackets inside them don't
// confuse the matcher.
function skipTrivia(src, i, end) {
  const c = src[i];
  if (c === '/' && src[i + 1] === '/') {
    while (i < end && src[i] !== '\n') i += 1;
    return i;
  }
  if (c === '/' && src[i + 1] === '*') {
    i += 2;
    while (i < end && !(src[i] === '*' && src[i + 1] === '/')) i += 1;
    return Math.min(end, i + 2);
  }
  if (c === '"' || c === "'" || c === '`') {
    i += 1;
    while (i < end) {
      if (src[i] === '\\') { i += 2; continue; }
      if (src[i] === c) { i += 1; break; }
      i += 1;
    }
    return i;
  }
  return i;
}

// Return the index just past the bracket that closes the one at `open`.
function matchBracket(src, open, end) {
  const pairs = { '(': ')', '[': ']', '{': '}' };
  const stack = [];
  let i = open;
  while (i < end) {
    const c = src[i];
    if (c === '/' || c === '"' || c === "'" || c === '`') { i = skipTrivia(src, i, end); continue; }
    if (pairs[c]) stack.push(pairs[c]);
    else if (c === ')' || c === ']' || c === '}') {
      if (stack.pop() !== c) throw new Error(`unbalanced bracket at ${i}`);
      if (!stack.length) return i + 1;
    }
    i += 1;
  }
  return end;
}

function injectedBlocks(source) {
  const blocks = [];
  const marker = 'ctx.inject(';
  let from = 0;
  for (;;) {
    const at = source.indexOf(marker, from);
    if (at < 0) break;
    const callOpen = at + 'ctx.inject'.length; // '(' of the call
    const callEnd = matchBracket(source, callOpen, source.length);
    const callText = source.slice(at, callEnd);
    const arrOpen = callText.indexOf('[');
    const arrEnd = arrOpen < 0 ? -1 : matchBracket(callText, arrOpen, callText.length);
    const deps = new Set((arrEnd < 0 ? '' : callText.slice(arrOpen, arrEnd))
      .match(/'([^']+)'|"([^"]+)"/g) || []);
    const cleanDeps = new Set([...deps].map(s => s.slice(1, -1)));
    // The callback body is the braces INSIDE the call, i.e. locate the first
    // '{' within callText (not after the call) and match it there.
    const braceInCall = callText.indexOf('{');
    const body = braceInCall < 0 ? '' : callText.slice(braceInCall, matchBracket(callText, braceInCall, callText.length));
    blocks.push({ deps: cleanDeps, body });
    from = callEnd;
  }
  return blocks;
}

function reads(body) {
  const found = new Set();
  const re = /\b(?:ctx|scope)\.([A-Za-z_][A-Za-z0-9_]*)/g;
  let m;
  while ((m = re.exec(body))) {
    if (HOST_SERVICES.has(m[1])) found.add(m[1]);
  }
  return found;
}

test('each ctx.inject block declares every host service it reads', () => {
  const problems = [];
  for (const file of ['index.js', 'client.js']) {
    const source = fs.readFileSync(path.join(LIB, file), 'utf8');
    for (const block of injectedBlocks(source)) {
      if (!block.body) continue;
      for (const service of reads(block.body)) {
        if (!block.deps.has(service)) {
          problems.push(`${file}: reads ctx.${service} inside inject([${[...block.deps].join(', ')}]) but does not declare it`);
        }
      }
    }
  }
  assert.deepEqual(problems, [], `Undeclared injected host services:\n${problems.join('\n')}`);
});

// A scope handed to a factory is consumed by that factory's module, so services
// that module reads off ctx must also be declared by the inject block. This is
// the exact shape the original bug took: index.js never mentioned ctx.sessions,
// but content-agent.js read it and the host refused the undeclared service.
const SCOPE_FACTORIES = {
  createContentAgentManager: 'content-agent.js',
  createOperationsAgentManager: 'operations-agent.js',
};

test('each inject block declares what the modules it hands its scope to read', () => {
  const problems = [];
  const source = fs.readFileSync(path.join(LIB, 'index.js'), 'utf8');
  for (const block of injectedBlocks(source)) {
    if (!block.body) continue;
    for (const [factory, module] of Object.entries(SCOPE_FACTORIES)) {
      if (!block.body.includes(factory)) continue;
      const childSource = fs.readFileSync(path.join(LIB, module), 'utf8');
      // Child modules read the injected scope off `ctx` (the factory arg name).
      const used = reads(childSource);
      for (const service of used) {
        if (!block.deps.has(service)) {
          problems.push(`${factory} (${module}) reads ctx.${service} but the inject([${[...block.deps].join(', ')}]) block that passes it the scope does not declare it`);
        }
      }
    }
  }
  assert.deepEqual(problems, [], `Undeclared host services used by handed-off scopes:\n${problems.join('\n')}`);
});

test('content-agent scope declares sessions for its purge path', () => {
  const source = fs.readFileSync(path.join(LIB, 'index.js'), 'utf8');
  const blocks = injectedBlocks(source).filter(b => b.body.includes('createContentAgentManager'));
  assert.equal(blocks.length, 1, 'expected exactly one content-agent inject scope');
  assert.ok(blocks[0].deps.has('sessions'),
    'content-agent inject scope must declare sessions (used by purgeSessions)');
  assert.ok(blocks[0].deps.has('agents'));
  assert.ok(blocks[0].deps.has('sessionPersistence'));
});

test('session purge source still depends on ctx.sessions (regression anchor)', () => {
  const source = fs.readFileSync(path.join(LIB, 'content-agent.js'), 'utf8');
  assert.match(source, /ctx\??\.sessions\??\.get/,
    'purgeSessions must keep checking ctx.sessions.get; if this changes, update the inject guard');
});
