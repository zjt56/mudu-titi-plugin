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

test('审核列表保留已暂存批次并提供已上传筛选', () => {
  assert.match(server, /"uploaded":/);
  assert.match(client, /if \(b\.uploaded \|\| b\.upload_pending_confirmation\) return true/);
  assert.match(client, /if \(key === 'uploaded'\) return isBatchUploaded\(b\)/);
  assert.match(client, /const list = sourceFilteredBase\.filter\(\(b\) => matchReviewFilter\(b, reviewFilter\)\)/);
});
