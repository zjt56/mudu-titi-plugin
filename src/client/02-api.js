    // ---- API helpers（全部走 /wf-plugin/api/*）----
    async function jget(path, signal) {
      const controller = new AbortController();
      let timer;
      let abort;
      const cancelled = new Promise((resolve, reject) => {
        abort = () => { controller.abort(); reject(new DOMException('已取消题目请求', 'AbortError')); };
        if (signal && signal.aborted) abort();
        else if (signal) signal.addEventListener('abort', abort, { once: true });
        timer = setTimeout(() => {
          controller.abort();
          reject(new Error('请求超过15秒，请重试；也可以继续选择其他题目'));
        }, 15000);
      });
      try {
        return await Promise.race([cancelled, (async () => {
          const r = await window.fetch(API + path, { headers: { accept: 'application/json' }, signal: controller.signal });
          if (!r.ok) throw new Error((await r.text()) || r.status);
          return r.json();
        })()]);
      } finally {
        clearTimeout(timer);
        if (signal) signal.removeEventListener('abort', abort);
      }
    }
    async function jpost(path, body, opts) {
      const timeoutMs = Number(opts && opts.timeoutMs) > 0 ? Number(opts.timeoutMs) : 30000;
      const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      let timer;
      const stalled = new Promise((_, reject) => {
        timer = setTimeout(() => {
          if (controller) controller.abort();
          reject(new Error('请求超时，请检查状态后重试'));
        }, timeoutMs);
      });
      try {
        return await Promise.race([stalled, (async () => {
          const r = await window.fetch(API + path, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify(body || {}),
            ...(controller ? { signal: controller.signal } : {}),
          });
          if (!r.ok) {
            const text = await r.text();
            let message = text || String(r.status);
            try { const data = JSON.parse(text); message = data.error || data.message || message; } catch (_) {}
            const error = new Error(message);
            error.httpStatus = r.status;
            throw error;
          }
          return r.json();
        })()]);
      } finally {
        clearTimeout(timer);
      }
    }
    // 页面 origin 可能是 dsh-app://app（自定义 scheme），<img>/<audio> 原生加载器
    // 无法访问 /wf-plugin 路由（只有 fetch 被宿主补丁路由）。因此图片/音频必须用
    // 宿主真实 HTTP origin 的绝对 URL。启动时从 /wf-plugin/health 取 origin 缓存。
    var MEDIA_ORIGIN = '';       // 例 http://127.0.0.1:53682
    var mediaOriginPromise = null;
    function ensureMediaOrigin() {
      if (MEDIA_ORIGIN) return Promise.resolve(MEDIA_ORIGIN);
      if (mediaOriginPromise) return mediaOriginPromise;
      mediaOriginPromise = window.fetch('/wf-plugin/health', { headers: { accept: 'application/json' } })
        .then((r) => r.json())
        .then((d) => {
          if (d && d.origin) MEDIA_ORIGIN = String(d.origin).replace(/\/$/, '');
          // origin 拿不到时退回 location.origin（多数 web 场景即 http origin）
          if (!MEDIA_ORIGIN && typeof location !== 'undefined' && location.origin && /^https?:/.test(location.origin)) MEDIA_ORIGIN = location.origin;
          return MEDIA_ORIGIN;
        })
        .catch(() => { mediaOriginPromise = null; return MEDIA_ORIGIN; });
      return mediaOriginPromise;
    }
    // 把后端返回的 /api/... 转成插件代理路径；有真实 http origin 时补成绝对 URL，
    // 这样 <img>/<audio> 原生加载器也能取到（dsh-app:// 下相对路径会 404）。
    function mediaUrl(u) {
      if (!u) return u;
      if (/^https?:/i.test(u) || u.startsWith('data:') || u.startsWith('blob:')) return u;
      const p = u.startsWith('/api/') ? '/wf-plugin' + u : u;
      return MEDIA_ORIGIN ? MEDIA_ORIGIN + p : p;
    }
    // 同一组件内只允许最新请求提交；切题卸载时同时取消网络与结果写回。
    function createRequestGate() {
      let generation = 0;
      let controller = null;
      return {
        begin() {
          if (controller) controller.abort();
          controller = new AbortController();
          const currentController = controller;
          const id = ++generation;
          return { signal: currentController.signal, current: () => id === generation && !currentController.signal.aborted };
        },
        cancel() { generation++; if (controller) controller.abort(); },
      };
    }

    // 桌面端 fetch 由 Harness 代理；img 原生请求不能假定享有相同路由和访问上下文。
    // 本地题图始终经 fetch 获取，再创建当前页面可访问的 Blob URL。
    function imageRequestUrl(value) {
      if (typeof value !== 'string' || !value.trim()) throw new Error('图片地址为空');
      const u = value.trim();
      if (u.startsWith('/api/')) return '/wf-plugin' + u;
      if (u.startsWith('/wf-plugin/')) return u;
      if (/^(https?:|blob:|data:image\/)/i.test(u)) return u;
      throw new Error('不支持的图片地址');
    }
