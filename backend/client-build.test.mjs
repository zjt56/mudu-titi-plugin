// lib/client.js 是 src/client/ 片段的拼接产物：防止只改一边导致两份源码分叉。
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { SRC_DIR, DIST, loadManifest, buildFromSource, splitDist } from '../tools/build-client.mjs';

test('lib/client.js 与 src/client/ 片段拼接结果逐字节一致', () => {
  const built = buildFromSource();
  const dist = readFileSync(DIST, 'utf8');
  assert.ok(built === dist, 'lib/client.js 与 src/client/ 不一致：改 src/client/*.js 后运行 node tools/build-client.mjs；直接改了 lib/client.js 则运行 --adopt');
});

test('按锚点拆回的片段与 src/client/ 逐字一致（--adopt 无损往返）', () => {
  const manifest = loadManifest();
  const parts = splitDist(readFileSync(DIST, 'utf8'), manifest);
  assert.equal(parts.length, manifest.segments.length);
  parts.forEach((text, i) => {
    assert.ok(text === readFileSync(join(SRC_DIR, manifest.segments[i].file), 'utf8'), manifest.segments[i].file);
  });
});

test('src/client/ 下每个片段都登记在 manifest 中，且登记的片段都存在', () => {
  const listed = loadManifest().segments.map((s) => s.file).sort();
  const onDisk = readdirSync(SRC_DIR).filter((f) => f.endsWith('.js')).sort();
  assert.deepEqual(onDisk, listed);
  assert.equal(new Set(listed).size, listed.length);
});
