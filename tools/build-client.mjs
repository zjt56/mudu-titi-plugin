#!/usr/bin/env node
// 生成 lib/client.js：按 src/client/manifest.json 的顺序拼接 src/client/*.js。
// lib/client.js 是生成文件——请改 src/client/ 下的片段，再运行本脚本。
// 片段共用宿主 __ModuleLoader__ factory 的同一个作用域，单个片段不是独立可运行的 JS。
//
// 用法：
//   node tools/build-client.mjs           生成 lib/client.js（内容不变则不写）
//   node tools/build-client.mjs --check   只校验一致性，不一致时退出码 1
//   node tools/build-client.mjs --adopt   有人直接改了 lib/client.js 时，按锚点把它拆回 src/client/
import { readFileSync, writeFileSync, renameSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const SRC_DIR = join(ROOT, 'src', 'client');
export const DIST = join(ROOT, 'lib', 'client.js');

export function loadManifest(srcDir = SRC_DIR) {
  return JSON.parse(readFileSync(join(srcDir, 'manifest.json'), 'utf8'));
}

export function joinSegments(manifest, read) {
  return manifest.segments.map((s) => read(s.file)).join('');
}

export function buildFromSource(srcDir = SRC_DIR) {
  const manifest = loadManifest(srcDir);
  return joinSegments(manifest, (f) => readFileSync(join(srcDir, f), 'utf8'));
}

// 按 manifest 锚点把整份 client.js 拆成片段；拼接结果与输入逐字节相同。
export function splitDist(text, manifest) {
  const lines = text.split('\n');
  const starts = [0];
  for (const seg of manifest.segments.slice(1)) {
    const hits = [];
    lines.forEach((l, i) => { if (l === seg.anchor) hits.push(i); });
    if (hits.length !== 1) {
      throw new Error(`锚点在 lib/client.js 中出现 ${hits.length} 次（须恰好 1 次）：${seg.file} ← ${JSON.stringify(seg.anchor)}`);
    }
    const start = hits[0] - (seg.offset || 0);
    if (start <= starts[starts.length - 1]) throw new Error(`锚点顺序与 manifest 不一致：${seg.file}`);
    starts.push(start);
  }
  return starts.map((a, i) => {
    const last = i === starts.length - 1;
    const b = last ? lines.length : starts[i + 1];
    return lines.slice(a, b).join('\n') + (last ? '' : '\n');
  });
}

function writeAtomic(path, text) {
  const tmp = `${path}.${process.pid}.tmp`;
  writeFileSync(tmp, text);
  renameSync(tmp, path);
}

export function main(argv = []) {
  const manifest = loadManifest();
  const current = existsSync(DIST) ? readFileSync(DIST, 'utf8') : null;
  if (argv.includes('--adopt')) {
    if (current === null) throw new Error('lib/client.js 不存在，无法拆回');
    const parts = splitDist(current, manifest);
    let changed = 0;
    parts.forEach((text, i) => {
      const p = join(SRC_DIR, manifest.segments[i].file);
      if (!existsSync(p) || readFileSync(p, 'utf8') !== text) { writeAtomic(p, text); changed += 1; }
    });
    console.log(`已从 lib/client.js 拆回 src/client/：${changed} 个片段有变化`);
    return 0;
  }
  const built = buildFromSource();
  if (argv.includes('--check')) {
    if (built !== current) {
      console.error('lib/client.js 与 src/client/ 不一致：请改 src/client/*.js 后运行 node tools/build-client.mjs；'
        + '如果是直接改了 lib/client.js，运行 node tools/build-client.mjs --adopt 拆回源码。');
      return 1;
    }
    console.log('lib/client.js 与 src/client/ 一致');
    return 0;
  }
  if (built === current) { console.log('lib/client.js 已是最新，未写入'); return 0; }
  writeAtomic(DIST, built);
  console.log(`已生成 lib/client.js（${manifest.segments.length} 个片段）`);
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  process.exitCode = main(process.argv.slice(2));
}
