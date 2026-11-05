const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const api = import('../lib/upload-command.js');
function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'muduluti-command-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const paper = path.join(root, '2014安徽 英语.docx');
  const audio = path.join(root, '英语音频.mp3');
  fs.writeFileSync(paper, Buffer.from([0x50, 0x4b, 1, 2, 3]));
  fs.writeFileSync(audio, Buffer.from('ID3 audio bytes'));
  return { root, paper, audio };
}
async function setup(t, options = {}) {
  const { createUploadCommandHandler } = await api;
  const files = fixture(t);
  const requests = [];
  let starts = 0;
  const handler = createUploadCommandHandler({
    attachments: { fileHostPath: (ref) => ref.hostPath },
    ensureBackend: async () => { starts++; return { ok: true, url: 'http://127.0.0.1:1/' }; },
    fetchImpl: async (url, request) => {
      requests.push({ url, ...request });
      return { ok: true, json: async () => ({ name: 'test-batch' }) };
    },
    ...options,
  });
  return { ...files, handler, requests, starts: () => starts };
}

test('slash command accepts the reported @quoted Word plus audio input', async (t) => {
  const f = await setup(t);
  const result = await f.handler({ rawInput: ` @"${f.paper}" @"${f.audio}" `, attachments: [] });
  assert.equal(result.kind, 'success', result.text);
  assert.equal(f.requests.length, 1);
  const form = f.requests[0].body;
  assert.equal(form.get('title'), null, 'file references must not become a paper title');
  const uploaded = form.getAll('files');
  assert.deepEqual(uploaded.map((v) => v.name), [path.basename(f.paper), path.basename(f.audio)]);
  assert.deepEqual(Buffer.from(await uploaded[0].arrayBuffer()), fs.readFileSync(f.paper));
  assert.deepEqual(Buffer.from(await uploaded[1].arrayBuffer()), fs.readFileSync(f.audio));
  assert.equal(f.requests[0].url, 'http://127.0.0.1:1/api/uploads');
});

test('attachment button input still uploads and preserves a custom title', async (t) => {
  const f = await setup(t);
  const result = await f.handler({ rawInput: '2014年安徽省中考英语', attachments: [
    { type: 'file', attachment: { hostPath: f.paper, name: '原卷.docx' } },
  ] });
  assert.equal(result.kind, 'success', result.text);
  assert.equal(f.requests[0].body.get('title'), '2014年安徽省中考英语');
  assert.equal(f.requests[0].body.get('files').name, '原卷.docx');
});

test('mixed attachments and references deduplicate the same file', async (t) => {
  const f = await setup(t);
  const result = await f.handler({ rawInput: `安徽试卷 @"${f.paper}" @"${f.audio}"`, attachments: [
    { type: 'file', attachment: { hostPath: f.paper, name: path.basename(f.paper) } },
  ] });
  assert.equal(result.kind, 'success', result.text);
  assert.equal(f.requests[0].body.getAll('files').length, 2);
  assert.equal(f.requests[0].body.get('title'), '安徽试卷');
});

test('a missing referenced file rejects the whole request without partial upload', async (t) => {
  const f = await setup(t);
  const result = await f.handler({ rawInput: `@"${f.paper}" @"${path.join(f.root, 'missing.mp3')}"` });
  assert.equal(result.kind, 'error');
  assert.match(result.text, /无法读取文件/);
  assert.equal(f.requests.length, 0);
  assert.equal(f.starts(), 0);
});

test('audio alone, unsupported files and empty input never create a batch', async (t) => {
  const f = await setup(t);
  for (const rawInput of [`@"${f.audio}"`, `@"${path.join(f.root, 'old.doc')}"`, '']) {
    const result = await f.handler({ rawInput });
    assert.equal(result.kind, 'error');
  }
  assert.equal(f.requests.length, 0);
});

test('quoted parsing handles spaces, home paths, bare paths, and malformed input', async () => {
  const { parseUploadInput } = await api;
  assert.deepEqual(parseUploadInput('试卷 @"/tmp/a b.docx" @\'/tmp/c d.mp3\''), {
    title: '试卷', paths: ['/tmp/a b.docx', '/tmp/c d.mp3'],
  });
  assert.deepEqual(parseUploadInput('@~/paper.pdf').paths, [path.join(os.homedir(), 'paper.pdf')]);
  assert.deepEqual(parseUploadInput('@/tmp/paper.pdf').paths, ['/tmp/paper.pdf']);
  assert.throws(() => parseUploadInput('@"/tmp/broken.docx'), /不完整/);
  assert.throws(() => parseUploadInput('@relative.docx'), /完整本地路径/);
});

test('remote failures are errors and cancellation signal is forwarded', async (t) => {
  let received;
  const controller = new AbortController();
  const f = await setup(t, { fetchImpl: async (_url, request) => {
    received = request.signal;
    return { ok: false, text: async () => '后端拒绝' };
  } });
  const result = await f.handler({ rawInput: `@"${f.paper}"`, signal: controller.signal });
  assert.equal(result.kind, 'error');
  assert.match(result.text, /后端拒绝/);
  assert.equal(received, controller.signal);
});

test('registered /muduluti command uses the new file reference handler', async (t) => {
  const { __plugin } = await import('../lib/index.js');
  let definition;
  __plugin.registerUploadCommand({ inject: (_services, callback) => callback({
    commands: { register: (value) => { definition = value; } },
    attachments: { fileHostPath: () => undefined },
  }) });
  assert.equal(definition.name, 'muduluti');
  assert.equal(definition.input.attachments, true);
  const result = await definition.handler({ rawInput: '@/nonexistent-command-fixture.docx', attachments: [] });
  assert.match(result.text, /无法读取文件/);
  assert.doesNotMatch(result.text, /命令没有收到附件/);
});
