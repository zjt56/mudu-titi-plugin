    // ---- 图片缓存 + 同步命中表 + 并发限流预取 ---------------------------------------
    // 同一 URL 的题图只下载/解码一次；IMG_RESOLVED 保存已就绪的 data/blob URL，
    // PreviewImage 首帧即可同步渲染 <img>，彻底消除切题时的“图片加载中…”闪烁。
    var IMG_CACHE = new Map();
    var IMG_RESOLVED = new Map();
    var IMG_CACHE_MAX = 24;        // 按需模式：仅保留当前题及相邻题图，最多 24 张；LRU 由 Map 插入序维护
    function revokeIfBlobUrl(src) {
      if (typeof src === 'string' && src.startsWith('blob:') && typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') {
        try { URL.revokeObjectURL(src); } catch {}
      }
    }
    function clearImageCache() {
      for (const src of IMG_RESOLVED.values()) revokeIfBlobUrl(src);
      IMG_RESOLVED.clear();
      IMG_CACHE.clear();
    }
    function imgCacheRemember(url, promise) {
      IMG_CACHE.delete(url);
      IMG_CACHE.set(url, promise);
      while (IMG_CACHE.size > IMG_CACHE_MAX) {
        const old = IMG_CACHE.keys().next().value;
        if (old === undefined || old === url) break;
        IMG_CACHE.delete(old);
        const oldSrc = IMG_RESOLVED.get(old);
        if (oldSrc !== undefined) {
          IMG_RESOLVED.delete(old);
          revokeIfBlobUrl(oldSrc);
        }
      }
    }
    function syncCachedImage(value) {
      if (!value) return null;
      let url;
      try { url = imageRequestUrl(value); } catch { return null; }
      if (/^(blob:|data:image\/)/i.test(url)) return url;
      return IMG_RESOLVED.get(url) || null;
    }
    // 提取预览块里的全部图片 URL（含表格单元格配图），明确跳过音频块。
    function collectBlockImageUrls(blocks) {
      const urls = [];
      const seen = new Set();
      const push = (u) => {
        if (typeof u !== 'string' || !u) return;
        if (!seen.has(u)) { seen.add(u); urls.push(u); }
      };
      for (const b of (blocks || [])) {
        if (!b || typeof b !== 'object') continue;
        if (b.type === 'image') {
          push(b.url);
        } else if (b.type === 'table') {
          for (const row of (b.cellImages || [])) {
            if (!Array.isArray(row)) continue;
            for (const cell of row) {
              if (!Array.isArray(cell)) continue;
              for (const im of cell) {
                if (im && typeof im === 'object') push(im.url);
              }
            }
          }
          for (const row of (b.structured || [])) {
            for (const cell of ((row && row.cells) || [])) {
              for (const im of ((cell && cell.images) || [])) {
                if (im && typeof im === 'object') push(im.url);
              }
            }
          }
        }
      }
      return urls;
    }
    // 单个图片请求（不含缓存）：带 503/网络错误重试，独立超时。下载本身不绑定任何
    // 调用方 signal——下载一次、结果进缓存，多个调用方共享。
    async function fetchImageOnce(value) {
      const url = imageRequestUrl(value);
      if (/^(blob:|data:image\/)/i.test(url)) return url;
      const local = url.startsWith('/wf-plugin/api/');
      const requestUrl = local ? url + (url.includes('?') ? '&' : '?') + 'format=data-url' : url;
      const delays = [0, 400, 1000, 2000]; // 冷启动/代理重连时的短退避
      let lastErr = null;
      for (let attempt = 0; attempt < delays.length; attempt++) {
        if (delays[attempt]) await new Promise((r) => setTimeout(r, delays[attempt]));
        const controller = new AbortController();
        let timer;
        const stalled = new Promise((resolve, reject) => {
          timer = setTimeout(() => { controller.abort(); reject(new Error('图片加载超时，请重试；可以继续切换题目')); }, 12000);
        });
        try {
          return await Promise.race([stalled, (async () => {
            const response = await window.fetch(requestUrl, { headers: { accept: local ? 'application/json' : 'image/*' }, signal: controller.signal });
            if (!response.ok) throw new Error('图片请求失败（' + response.status + '）');
            if (local) {
              const data = await response.json();
              if (!data || !/^data:image\/[a-z0-9.+-]+;base64,/i.test(data.src || '')) throw new Error('图片接口未返回有效图片');
              return data.src;
            }
            const blob = await response.blob();
            if (!/^image\//i.test(blob.type)) throw new Error('图片接口未返回图片');
            return URL.createObjectURL(blob);
          })()]);
        } catch (e) {
          lastErr = e;
          if (e && e.name === 'AbortError') throw e; // 内部超时取消，不重试
          const msg = String((e && e.message) || e);
          if (/\b(400|401|403|404)\b/.test(msg)) break; // 非瞬时，放弃
        } finally {
          clearTimeout(timer);
        }
      }
      throw lastErr || new Error('图片加载失败');
    }
    // 取共享下载 Promise（并发去重）：同一 URL 只下载一次，结果/失败都进缓存。
    function sharedImageDownload(url) {
      const cached = IMG_CACHE.get(url);
      if (cached) {
        // True LRU: refresh recency on cache hit.
        IMG_CACHE.delete(url);
        IMG_CACHE.set(url, cached);
        return cached;
      }
      const p = fetchImageOnce(url).then(
        (src) => { IMG_RESOLVED.set(url, src); return src; },
        (err) => {
          IMG_CACHE.delete(url);
          const oldSrc = IMG_RESOLVED.get(url);
          IMG_RESOLVED.delete(url);
          revokeIfBlobUrl(oldSrc);
          throw err;
        }
      );
      imgCacheRemember(url, p);
      return p;
    }
    // 带缓存的取图：命中缓存直接返回；否则共享下载。同时尊重【调用方】的取消与超时——
    // 调用方 abort 只取消它自己的等待（抛 AbortError），不会打断共享下载/污染缓存。
    function fetchPreviewImage(value, signal) {
      let url;
      try { url = imageRequestUrl(value); } catch (err) { return Promise.reject(err); }
      if (/^(blob:|data:image\/)/i.test(url)) return Promise.resolve(url);
      const download = sharedImageDownload(url);
      if (!signal) return download;
      if (signal.aborted) return Promise.reject(new DOMException('已取消图片加载', 'AbortError'));
      let abort;
      const cancelled = new Promise((resolve, reject) => {
        abort = () => reject(new DOMException('已取消图片加载', 'AbortError'));
        signal.addEventListener('abort', abort, { once: true });
      });
      return Promise.race([download, cancelled]).finally(() => {
        try { signal.removeEventListener('abort', abort); } catch {}
      });
    }
    // 限流后台预取：按给定顺序预热整卷配图，控制并发数避免打满代理连接。
    function prefetchImages(values, concurrency) {
      const limit = Math.max(1, Number(concurrency) || 4);
      const queue = [];
      const seen = new Set();
      for (const v of (values || [])) {
        try {
          const url = imageRequestUrl(v);
          if (!/^(blob:|data:image\/)/i.test(url) && !IMG_CACHE.has(url) && !seen.has(url)) {
            seen.add(url);
            queue.push(url);
          }
        } catch {}
      }
      if (!queue.length) return Promise.resolve();
      let cursor = 0;
      const workers = [];
      const workerCount = Math.min(limit, queue.length);
      for (let i = 0; i < workerCount; i++) {
        workers.push((async () => {
          while (cursor < queue.length) {
            const nextUrl = queue[cursor++];
            if (!IMG_CACHE.has(nextUrl)) {
              await fetchPreviewImage(nextUrl).catch(() => {});
            }
          }
        })());
      }
      return Promise.all(workers).then(() => undefined);
    }

