    // ---- 按需单题缓存（仅保留当前活跃题 + 微预取下 1 题，离卷/切页即清）----------
    var UNIT_CACHE = new Map();
    var UNIT_CACHE_ORDER = [];
    var UNIT_CACHE_MAX = 1;
    var UNIT_CACHE_PER_BATCH_MAX = 4;
    function unitCacheTouch(batchName) {
      const key = String(batchName || '');
      const idx = UNIT_CACHE_ORDER.indexOf(key);
      if (idx >= 0) UNIT_CACHE_ORDER.splice(idx, 1);
      UNIT_CACHE_ORDER.push(key);
      while (UNIT_CACHE_ORDER.length > UNIT_CACHE_MAX) {
        const old = UNIT_CACHE_ORDER.shift();
        if (old !== key) UNIT_CACHE.delete(old);
      }
    }
    function ensureBatchCacheEntry(batchName) {
      const key = String(batchName || '');
      let entry = UNIT_CACHE.get(key);
      if (!entry) {
        entry = { shared: null, units: new Map(), order: [], promise: null, loaded: false };
        UNIT_CACHE.set(key, entry);
      }
      unitCacheTouch(key);
      return entry;
    }
    function getCachedUnit(batchName, unitId) {
      const entry = UNIT_CACHE.get(String(batchName || ''));
      if (entry) {
        const key = String(unitId);
        const hit = entry.units.get(key);
        if (hit) {
          entry.units.delete(key);
          entry.units.set(key, hit);
          return hit;
        }
      }
      const tk = typeUnitCacheKey(batchName, unitId);
      if (TYPE_UNIT_CACHE.has(tk)) {
        const val = TYPE_UNIT_CACHE.get(tk);
        TYPE_UNIT_CACHE.delete(tk);
        TYPE_UNIT_CACHE.set(tk, val);
        return val;
      }
      return null;
    }
    function setCachedUnit(batchName, unitId, detail) {
      if (!batchName || !detail) return;
      const entry = ensureBatchCacheEntry(batchName);
      const key = String(unitId);
      entry.units.delete(key);
      entry.units.set(key, detail);
      if (detail.unit_id != null && String(detail.unit_id) !== key) {
        const alt = String(detail.unit_id);
        entry.units.delete(alt);
        entry.units.set(alt, detail);
      }
      while (entry.units.size > UNIT_CACHE_PER_BATCH_MAX) {
        const oldest = entry.units.keys().next().value;
        if (oldest === undefined || oldest === key) break;
        entry.units.delete(oldest);
      }
    }
    // 题型审核跨批次单题轻量 LRU（最多保留 3 题：当前题 + 微预取下 1 题 + 上 1 题），切页/切题型即清。
    var TYPE_UNIT_CACHE = new Map();
    var TYPE_UNIT_CACHE_MAX = 3;
    function clearTypeUnitCache() {
      TYPE_UNIT_CACHE.clear();
    }
    function clearAllUnitCaches() {
      UNIT_CACHE.clear();
      UNIT_CACHE_ORDER.length = 0;
      TYPE_UNIT_CACHE.clear();
    }
    function typeUnitCacheKey(batchName, unitId) {
      return String(batchName || '') + '\0' + String(unitId || '');
    }
    function getTypeCachedUnit(batchName, unitId) {
      const k = typeUnitCacheKey(batchName, unitId);
      if (TYPE_UNIT_CACHE.has(k)) {
        const val = TYPE_UNIT_CACHE.get(k);
        TYPE_UNIT_CACHE.delete(k);
        TYPE_UNIT_CACHE.set(k, val);
        return val;
      }
      return getCachedUnit(batchName, unitId);
    }
    function setTypeCachedUnit(batchName, unitId, detail) {
      if (!batchName || !detail) return;
      const k = typeUnitCacheKey(batchName, unitId);
      TYPE_UNIT_CACHE.delete(k);
      TYPE_UNIT_CACHE.set(k, detail);
      while (TYPE_UNIT_CACHE.size > TYPE_UNIT_CACHE_MAX) {
        const oldest = TYPE_UNIT_CACHE.keys().next().value;
        if (oldest === undefined) break;
        TYPE_UNIT_CACHE.delete(oldest);
      }
    }
    function invalidateTypeCachedBatch(batchName) {
      const prefix = String(batchName || '') + '\0';
      for (const k of Array.from(TYPE_UNIT_CACHE.keys())) {
        if (k.startsWith(prefix)) TYPE_UNIT_CACHE.delete(k);
      }
    }
    function invalidateBatchUnits(batchName) {
      const key = String(batchName || '');
      UNIT_CACHE.delete(key);
      const idx = UNIT_CACHE_ORDER.indexOf(key);
      if (idx >= 0) UNIT_CACHE_ORDER.splice(idx, 1);
      invalidateTypeCachedBatch(key);
    }
    function batchCacheSignature(b) {
      if (!b || typeof b !== 'object') return '';
      const st = b.stage || {};
      const cur = st.current || {};
      return [
        b.updated_at || '',
        b.flow_done ? '1' : '0',
        b.uploaded ? '1' : '0',
        b.unit_total || 0,
        b.open_findings || 0,
        b.needs_human || 0,
        b.anomaly_count || 0,
        b.stage_blocked ? '1' : '0',
        st.running ? '1' : '0',
        cur.id || '',
        cur.status || '',
      ].join('|');
    }
    function isCachedUnitStale(cached, row) {
      if (!cached) return true;
      const cmp = cached.comparison || {};
      const rounds = Array.isArray(cmp.rounds) ? cmp.rounds : [];
      const hasRound1 = Boolean(
        (cmp.merged && cmp.merged.has_round1)
        || rounds.some((r) => r && r.round_id === 'labels' && Array.isArray(r.checks) && r.checks.length)
      );
      if (cmp.stale) return true;
      if (row && row.needs_human && (!hasRound1 || !(Array.isArray(cached.label_findings) && cached.label_findings.length))) {
        return true;
      }
      if (row && row.state && row.state !== 'unverified' && !hasRound1) {
        return true;
      }
      if (row && row.is_anomaly && !cached.is_anomaly) {
        return true;
      }
      return false;
    }
    function loadBatchUnitsBundle(batchName, opts) {
      const force = !!(opts && opts.force);
      const entry = ensureBatchCacheEntry(batchName);
      if (!force && entry.loaded) return Promise.resolve(entry);
      if (!force && entry.promise) return entry.promise;
      const p = (async () => {
        const delays = [0, 500, 1200];
        let lastErr = null;
        for (let attempt = 0; attempt < delays.length; attempt++) {
          if (delays[attempt]) await new Promise((r) => setTimeout(r, delays[attempt]));
          try {
            const bundle = await jget('/batches/' + encodeURIComponent(batchName) + '/units');
            const shared = (bundle && bundle.shared) || {};
            const rawUnits = (bundle && bundle.units) || {};
            const nextUnits = new Map();
            for (const [uid, u] of Object.entries(rawUnits)) {
              if (!u || typeof u !== 'object') continue;
              const hydrated = { ...shared, ...u };
              nextUnits.set(String(uid), hydrated);
              if (hydrated.unit_id != null && String(hydrated.unit_id) !== String(uid)) {
                nextUnits.set(String(hydrated.unit_id), hydrated);
              }
            }
            entry.shared = shared;
            entry.units = nextUnits;
            entry.order = Array.isArray(bundle && bundle.order) ? bundle.order.map(String) : Array.from(nextUnits.keys());
            entry.loaded = true;
            return entry;
          } catch (e) {
            lastErr = e;
            const msg = String((e && e.message) || e);
            if (/\b(400|401|403|404)\b/.test(msg)) break;
          }
        }
        throw lastErr || new Error('整卷预加载失败');
      })().finally(() => {
        if (entry.promise === p) entry.promise = null;
      });
      entry.promise = p;
      return p;
    }

    function PreviewImage({ url, name, style }) {
      const React = require('react');
      const h = React.createElement;
      const [attempt, setAttempt] = React.useState(0);
      const [image, setImage] = React.useState(() => {
        const src = syncCachedImage(url);
        return src ? { url, src } : null;
      });
      React.useEffect(() => {
        let active = true;
        const syncSrc = syncCachedImage(url);
        if (syncSrc) {
          setImage((prev) => (prev && prev.url === url && prev.src === syncSrc) ? prev : { url, src: syncSrc });
          return () => { active = false; };
        }
        // 已在下载队列中时共享同一个 Promise，避免重复请求。
        const cached = (() => { try { return IMG_CACHE.get(imageRequestUrl(url)); } catch { return null; } })();
        if (cached) {
          cached.then((src) => { if (active) setImage({ url, src }); }).catch((error) => {
            if (active && error && error.name !== 'AbortError') setImage({ url, error: String(error.message || error) });
          });
          return () => { active = false; };
        }
        setImage(null);
        fetchPreviewImage(url).then((src) => {
          if (active) setImage({ url, src });
        }).catch((error) => {
          if (active && error.name !== 'AbortError') setImage({ url, error: String(error.message || error) });
        });
        return () => { active = false; };
      }, [url, attempt]);
      const resolved = (image && image.url === url) ? image : (() => {
        const src = syncCachedImage(url);
        return src ? { url, src } : null;
      })();
      if (!resolved) return h('span', { role: 'status', style: T.hint }, '图片加载中…');
      if (resolved.error) return h('div', { role: 'status', style: { ...T.hint, margin: '8px 0' } },
        '图片未显示：' + (name || '') + '（' + resolved.error + '） ',
        h('button', { className: 'wf-btn', style: T.btn, onClick: () => { try { const u = imageRequestUrl(url); IMG_CACHE.delete(u); const old = IMG_RESOLVED.get(u); IMG_RESOLVED.delete(u); revokeIfBlobUrl(old); } catch {} setAttempt((n) => n + 1); } }, '重试图片'));
      return h('img', {
        src: resolved.src, alt: name || '试题配图', className: 'wf-img',
        style: style || undefined,
        onError: () => setImage({ url, error: '图片解码失败' }),
      });
    }

    const BoundaryReact = require('react');
    class QuestionBoundary extends BoundaryReact.Component {
      constructor(props) { super(props); this.state = { error: null, retry: 0 }; }
      static getDerivedStateFromError(error) { return { error: String(error.message || error) }; }
      componentDidCatch(error, info) { console.error('[wf-plugin] question render failed', error, info); }
      render() {
        if (!this.state.error) return BoundaryReact.createElement(BoundaryReact.Fragment, { key: this.state.retry }, this.props.children);
        return BoundaryReact.createElement(EmptyState, {
          flex: true, tone: 'error',
          title: '本题显示失败：' + this.state.error + '。可以继续切换其他题目。',
          action: BoundaryReact.createElement('button', { className: 'wf-btn', style: T.btn,
            onClick: () => this.setState((s) => ({ error: null, retry: s.retry + 1 })) }, '重新显示本题'),
        });
      }
    }

    // 页签级错误边界：单个页签渲染异常时仅隔离该页签并给出明确报错与重试按钮，
    // 绝不拖垮整个 WfPanel 变成白屏，用户仍可正常切换其他页签。
    class TabBoundary extends BoundaryReact.Component {
      constructor(props) { super(props); this.state = { error: null, retry: 0 }; }
      static getDerivedStateFromError(error) { return { error: String((error && error.message) || error || '未知渲染错误') }; }
      componentDidCatch(error, info) { console.error('[wf-plugin] tab render failed:', this.props && this.props.name, error, info); }
      render() {
        if (!this.state.error) return BoundaryReact.createElement(BoundaryReact.Fragment, { key: this.state.retry }, this.props.children);
        const label = (this.props && this.props.label) || '当前页面';
        return BoundaryReact.createElement(EmptyState, {
          flex: true, tone: 'error',
          title: label + '加载异常：' + this.state.error,
          hint: '其他页签不受影响；可点击下方按钮重试。',
          action: BoundaryReact.createElement('button', {
            type: 'button', className: 'wf-btn', style: { ...T.btn, marginTop: 12 },
            onClick: () => this.setState((s) => ({ error: null, retry: s.retry + 1 })),
          }, '重新加载' + label),
        });
      }
    }

    function modelKey(m) { return m ? m.provider + '|' + m.model : ''; }

    // 试卷正文渲染：先转义，再只放行 <u><b><i><strong><em> 与 <br>，把 **x**→粗体、
    // __x__→下划线、全角空格包裹的题号 →下划线填空样式。与原工作台 markLine 口径一致。
    function escHtml(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function paperDisplayText(text, bulkBold) {
      const value = String(text == null ? '' : text);
      if (!bulkBold) return value;
      return value.split('\n').map((line) => {
        // Extracted runs can be adjacent, and assembly can leave empty
        // bold markers at the start of a field. Preserve other inline tags.
        const plain = line.replace(/<\/?(?:u|sup|sub|em)>|<br\s*\/?>/gi, '').trim();
        const whole = /^(?:\s*\*{2,}[^*]+\*{2,}\s*)+$/.test(plain);
        return whole || /^\*{2,}$/.test(plain) ? line.replace(/\*{2,}/g, '') : line;
      }).join('\n');
    }
    function markLine(text, bulkBold) {
      let out = escHtml(paperDisplayText(text, bulkBold));
      out = out.replace(/&lt;(\/?)(u|b|i|strong|em|br|sup|sub)&gt;/gi, '<$1$2>'); // 放行无属性的原文标记
      // Numbered blanks use one rule, whether Word encoded the digit as text
      // underlining, underscore runs, or both around an inline-marked digit.
      out = out
        .replace(/_{2,}\s*(?:<u>\s*)?(\d{1,3})(?:\s*<\/u>)?\s*_{2,}/g, '\u3000$1\u3000')
        .replace(/<u>[\s\u3000\xa0]*_{2,}\s*(\d{1,3})\s*_{2,}[\s\u3000\xa0]*<\/u>/g, '\u3000$1\u3000')
        .replace(/<u>([\s\u3000\xa0]*)(?:_{1,2}|__)(\d{1,3})(?:_{1,2}|__)([\s\u3000\xa0]*)<\/u>/g, '\u3000$2\u3000')
        .replace(/<u>([\s\u3000\xa0]+)(\d{1,3})[\s\u3000\xa0]+<\/u>/g, '\u3000$2\u3000');
      // __x__ 只还原库侧「题号空档」__数字__（对称后端 normalize_blank_text 的 __26__）；
      // 纯空 ______、单词+空 i______ 都不含数字，不得当标记。否则 __(.+?)__ 会把两段
      // 下划线之间的文字（如 "______ are ______"）误包成 <u>are</u>，或在原有 <u> 内
      // 再套一层 <u>，渲染出「很短、一粗一细两条线」的错乱下划线（用户 2026-11-19 反馈）。
      out = out.replace(/\*\*([\s\S]+?)\*\*/g, '<b>$1</b>').replace(/\*{2,}/g, '').replace(/__(\d{1,3})__/g, '<u>$1</u>');
      // 同一处空档在 Word 里常被拆成多段连续下划线（`<u>　</u><u>16</u><u>　</u><u> </u>`）：
      // 先按整段合并，否则数字不会走填空线样式，尾部空白下划线还会在窄格里换行成孤立「_」。
      out = out.replace(/(?:<u>[\s\u3000\xa0]*<\/u>)*<u>(\d{1,3})<\/u>(?:<u>[\s\u3000\xa0]*<\/u>)*/gi, '\u3000$1\u3000');
      out = out.replace(/\u3000(\d{1,3})\u3000/g, '<span class="wf-blank">$1</span>');
      out = out.replace(/[_\uff3f]{2,}\s*<u>([^<]{1,4})<\/u>\s*[_\uff3f]{2,}/gi, '<span class="wf-blank">$1</span>');
      // 全局所有填空/答题横线（3+ 连续下划线与纯空白 <u>）统一走同一套 CSS 1px 细线，
      // 避免同一题内短横线走字体 '_' 字符、长横线走 CSS 边框导致粗细不一致，同时全部自适应宽度、不折两行。
      const ruleUnits = (raw) => {
        const plain = String(raw || '').replace(/<\/?u>/gi, '');
        let units = 0;
        for (const ch of plain) units += ch === '\u3000' || ch === '\uff3f' ? 2 : 1;
        return units;
      };
      const inlineRuleSpan = (units) => {
        const tier = units >= 36 ? 'wf-rule-xl' : units >= 20 ? 'wf-rule-lg' : units >= 12 ? 'wf-rule-md' : 'wf-rule-sm';
        return '<span class="wf-fill-blank ' + tier + '"></span>';
      };
      // 1) 整行独立横线（含表格内 <br> 分隔行）-> 100% 宽度单行自适应横线
      out = out.replace(
        /(^|\n|<br>)[ \t\u3000\xa0]*((?:<u>[\s\u3000\xa0_\uff3f]+<\/u>|[_\uff3f]{3,})(?:[ \t\u3000\xa0]*(?:<u>[\s\u3000\xa0_\uff3f]+<\/u>|[_\uff3f]{3,}))*)[ \t\u3000\xa0]*(?=\n|<br>|$)/gi,
        (match, lead, body) => (ruleUnits(body) >= 12 ? lead + '<span class="wf-answer-line"></span>' : match),
      );
      // 2) 首字母填空等 `<u>i______</u>`：把连续下划线移出 <u>，防止 <u> 与横线叠出双线
      while (/<u>([^<]*?)([_\uff3f]{3,})([^<]*?)<\/u>/i.test(out)) {
        out = out.replace(/<u>([^<]*?)([_\uff3f]{3,})([^<]*?)<\/u>/gi, (_, pre, run, post) => (
          (pre ? '<u>' + pre + '</u>' : '') + inlineRuleSpan(ruleUnits(run)) + (post ? '<u>' + post + '</u>' : '')
        ));
      }
      // 3) 所有纯空白/下划线 <u>...</u>（无论长短）统一转为同一套自适应横线
      out = out.replace(
        /(?:<u>[\s\u3000\xa0_\uff3f]+<\/u>)(?:[ \t\u3000\xa0]*<u>[\s\u3000\xa0_\uff3f]+<\/u>)*/gi,
        (match) => inlineRuleSpan(ruleUnits(match)),
      );
      // 4) 所有连续下划线（3 个及以上）统一转为同一套自适应横线
      out = out.replace(/[_\uff3f]{3,}/g, (match) => inlineRuleSpan(ruleUnits(match)));
      return out;
    }
    function isHeaderRow(row) {
      return Array.isArray(row) && row.length > 0 && row.every((c) => String(c == null ? '' : c).length <= 10);
    }
    const SECTION_RE = /^(?:第[一二三四五六七八九十]+部分|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.|[IVX]{1,4}\.)/;
    function isHeading(text) { return SECTION_RE.test(String(text || '').trim()); }

    function BackendRestartControl(props) {
      const React = require('react');
      const h = React.createElement;
      const [status, setStatus] = React.useState({ running: true, busy: false, deferred: false, restarting: false });
      const [phase, setPhase] = React.useState('idle'); // idle | restarting | deferred | done | error
      const [err, setErr] = React.useState('');
      const prevBusyWaitRef = React.useRef(false);
      const aliveRef = React.useRef(true);
      const onRestartedRef = React.useRef(props && props.onRestarted);
      onRestartedRef.current = props && props.onRestarted;

      const refreshStatus = React.useCallback(async () => {
        try {
          const res = await window.fetch('/wf-plugin/workbench/status', { headers: { accept: 'application/json' } });
          if (!res.ok) return;
          const data = await res.json();
          if (!aliveRef.current || !data || typeof data !== 'object') return;
          setStatus(data);
          const waitingNow = Boolean(data.restarting);
          if (data.restarting) {
            setPhase('restarting');
          } else if (prevBusyWaitRef.current && data.running) {
            setPhase('done');
            if (typeof onRestartedRef.current === 'function') onRestartedRef.current();
          }
          prevBusyWaitRef.current = waitingNow;
        } catch {
          // 后端瞬时重拉期间探测可能短暂失败，保持现状等待下一次轮询。
        }
      }, []);

      const isControlActive = !props || props.isActive !== false;
      React.useEffect(() => {
        if (!isControlActive) return undefined;
        aliveRef.current = true;
        refreshStatus();
        const interval = (phase === 'restarting' || status.restarting) ? 2500 : 12000;
        const timer = setInterval(() => {
          if (typeof document === 'undefined' || !document.hidden) refreshStatus();
        }, interval);
        const onVisible = () => { if (typeof document === 'undefined' || !document.hidden) refreshStatus(); };
        if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
          document.addEventListener('visibilitychange', onVisible);
        }
        return () => {
          aliveRef.current = false;
          clearInterval(timer);
          if (typeof document !== 'undefined' && typeof document.removeEventListener === 'function') {
            document.removeEventListener('visibilitychange', onVisible);
          }
        };
      }, [refreshStatus, phase, status.restarting, isControlActive]);

      React.useEffect(() => {
        if (phase !== 'done') return undefined;
        const timer = setTimeout(() => { if (aliveRef.current) setPhase('idle'); }, 3500);
        return () => clearTimeout(timer);
      }, [phase]);

      const triggerRestart = async () => {
        setErr('');
        setPhase('restarting');
        try {
          const res = await window.fetch('/wf-plugin/workbench/restart', {
            method: 'POST',
            headers: { 'content-type': 'application/json', accept: 'application/json' },
            body: JSON.stringify({ force: true }),
          });
          const data = await res.json().catch(() => ({}));
          if (!aliveRef.current) return;
          if (!res.ok || data.ok === false) {
            setPhase('error');
            setErr(data.error || '重启失败');
            return;
          }
          prevBusyWaitRef.current = false;
          setStatus((cur) => ({ ...cur, deferred: false, restarting: false, running: true }));
          setPhase('done');
          if (typeof onRestartedRef.current === 'function') onRestartedRef.current();
        } catch (e) {
          if (!aliveRef.current) return;
          setPhase('error');
          setErr(String((e && e.message) || e || '重启请求失败'));
        }
      };

      const isRestarting = phase === 'restarting' || status.restarting;
      const prominent = Boolean(props && props.prominent);
      const btnStyle = prominent
        ? {
            ...T.miniBtn,
            padding: '5px 12px',
            fontSize: 12.5,
            fontWeight: 600,
            borderRadius: 8,
            background: 'var(--dsw-alias-bg-base)',
            border: '1px solid var(--dsw-alias-border-l2)',
            color: 'var(--dsw-alias-label-primary)',
            boxShadow: '0 1px 3px rgba(15, 23, 42, 0.08)',
            whiteSpace: 'nowrap',
          }
        : { ...T.miniBtn, whiteSpace: 'nowrap' };
      return h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0 } },
        phase === 'done' ? h('span', {
          style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', whiteSpace: 'nowrap' },
        }, '✓ 后端已重启') : null,
        phase === 'error' && err ? h('span', {
          style: { fontSize: 11.5, color: 'var(--dsw-alias-state-error-primary)', whiteSpace: 'nowrap' },
          title: err,
        }, err) : null,
        h('button', {
          type: 'button',
          className: 'wf-btn',
          style: btnStyle,
          disabled: isRestarting,
          title: '重启工作台后端进程',
          onClick: triggerRestart,
        }, isRestarting ? '正在重启…' : '重启后端'),
      );
    }

