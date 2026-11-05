import { readFileSync, statSync, realpathSync } from 'node:fs';
import { basename, extname, isAbsolute, join } from 'node:path';
import { homedir } from 'node:os';

const DOCUMENTS = new Set(['.docx', '.pdf']);
const SUPPORTED = new Set([...DOCUMENTS, '.mp3', '.wav', '.m4a', '.ogg', '.flac']);

// Harness local file chips serialize as @"/path with spaces" in rawInput.
// They are explicit inputs to THIS command, not stored attachment blocks.
export function parseUploadInput(rawInput = '') {
  const paths = [];
  const text = String(rawInput);
  const title = text.replace(/(^|\s)@(?:"([^"]*)"|'([^']*)'|([^\s]+))/gu,
    (_match, space, doubleQuoted, singleQuoted, bare) => {
      const path = doubleQuoted ?? singleQuoted ?? bare;
      if (!path || path.startsWith('"') || path.startsWith("'")) {
        throw new Error('文件引用不完整，请将含空格的完整路径放在双引号内：@"/完整路径/试卷.docx"。');
      }
      const expanded = path.startsWith('~/') ? join(homedir(), path.slice(2)) : path;
      if (!isAbsolute(expanded)) {
        throw new Error('文件引用须使用完整本地路径：@"/完整路径/试卷.docx"。');
      }
      paths.push(expanded);
      return space;
    }).replace(/[ \t]+/g, ' ').trim();
  return { paths, title };
}

export function createUploadCommandHandler({ attachments, ensureBackend, fetchImpl = globalThis.fetch }) {
  return async (invocation) => {
    try {
      const { paths, title } = parseUploadInput(invocation.rawInput);
      const candidates = [];
      for (const block of invocation.attachments || []) {
        if (block?.type !== 'file' || !block.attachment) {
          throw new Error('录题上传仅接受 docx/pdf 和配套音频，请移除图片等其它附件。');
        }
        const ref = block.attachment;
        const path = attachments.fileHostPath(ref);
        if (!path) throw new Error('无法读取所选附件，请重新选择该文件。');
        candidates.push({ path, name: ref.name || basename(path) });
      }
      candidates.push(...paths.map((path) => ({ path, name: basename(path) })));
      if (!candidates.length) {
        return { kind: 'error', text: '请附上 docx/pdf，或在 /muduluti 后引用本地文件：@"/完整路径/试卷.docx"；也可同时附上音频。' };
      }
      const files = [];
      const seen = new Set();
      for (const file of candidates) {
        const suffix = extname(file.name).toLowerCase();
        if (!SUPPORTED.has(suffix)) throw new Error('不支持文件「' + file.name + '」，请使用 docx/pdf 或配套音频。');
        let canonical;
        try {
          canonical = realpathSync(file.path);
          if (!statSync(canonical).isFile()) throw new Error('not a regular file');
        } catch {
          throw new Error('无法读取文件「' + file.name + '」，请确认文件存在且有读取权限。');
        }
        if (seen.has(canonical)) continue;
        seen.add(canonical);
        files.push({ ...file, path: canonical, suffix });
      }
      if (!files.some((file) => DOCUMENTS.has(file.suffix))) {
        throw new Error('请同时提供原卷 docx/pdf，不能只上传音频。');
      }
      const fd = new FormData();
      if (title) fd.append('title', title);
      for (const file of files) {
        fd.append('files', new Blob([readFileSync(file.path)]), file.name);
      }
      const backend = await ensureBackend();
      if (!backend.ok) return { kind: 'error', text: '工作台后端未就绪：' + (backend.error || '') };
      const response = await fetchImpl(backend.url + 'api/uploads', {
        method: 'POST', body: fd, ...(invocation.signal ? { signal: invocation.signal } : {}),
      });
      if (!response.ok) return { kind: 'error', text: '上传失败：' + await response.text() };
      const data = await response.json();
      return { kind: 'success', text: '已建批次「' + (data.name || '') + '」，已开始自动提取与打标签；在侧边栏「录题系统」面板可查看进度与审核。' };
    } catch (error) {
      return { kind: 'error', text: '上传失败：' + String(error?.message || error) };
    }
  };
}
