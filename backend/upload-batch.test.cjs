const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const client = fs.readFileSync(path.join(root, 'lib/client.js'), 'utf8');
const server = fs.readFileSync(path.join(root, 'app/workbench/server.py'), 'utf8');

test('审核界面右上角提供「上传批次」，上传到云端暂存', () => {
  // 按钮文案
  assert.match(client, /'上传批次'/);
  assert.match(client, /上传中…/);
  // 调用后端上传接口并轮询状态
  assert.match(client, /\/upload', \{\}\)/);
  assert.match(client, /upload-status/);
});

test('上传走 prepare-upload → 闸门批准 → upload 三步，不绕过闸门', () => {
  assert.match(server, /def start_batch_upload/);
  assert.match(server, /"prepare-upload", "--batch-dir"/);
  assert.match(server, /"--stage", "upload_gate",\s*\n?\s*"--status", "approved"/);
  assert.match(server, /"upload", "--batch-dir"/);
  assert.match(server, /"--yes"/);
});

test('已上传的批次从试卷审核列表消失', () => {
  // 后端给出 uploaded 标记（manifest 的 upload 阶段 completed）
  assert.match(server, /"uploaded":/);
  assert.match(server, /get\("upload"\) or \{\}\)\.get\("status"\)/);
  // 前端过滤掉 uploaded
  assert.match(client, /\(batches \|\| \[\]\)\.filter\(\(b\) => !b\.uploaded\)/);
});
