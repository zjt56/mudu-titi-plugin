    // ======================================================================
    // 试卷审核视图：列表 + 统计（进入后切换到审阅）
    // ======================================================================
    function WorkbenchTab(props) {
      const React = require('react');
      const h = React.createElement;
      const isActive = props.isActive !== false;
      const [batches, setBatches] = React.useState(() => (typeof SHARED_BATCHES_CACHE !== 'undefined' && (SHARED_BATCHES_CACHE.review || SHARED_BATCHES_CACHE.full || SHARED_BATCHES_CACHE.lite)) || null);
      const [serverActiveWork, setServerActiveWork] = React.useState(false);
      const [active, setActive] = React.useState(null); // {name, unitId}
      const [err, setErr] = React.useState('');
      const [refreshing, setRefreshing] = React.useState(false);
      const [refreshHint, setRefreshHint] = React.useState('');
      const refreshTimer = React.useRef(null);
      const [del, setDel] = React.useState(null); // 彻底删除确认弹层 {name,value,busy,error}
      const [advBusy, setAdvBusy] = React.useState('');
      const [uploadStates, setUploadStates] = React.useState({});
      const [uploadNotice, setUploadNotice] = React.useState('');
      const uploadStatesRef = React.useRef({});
      const uploadGeneration = React.useRef({});
      const uploadChecks = React.useRef({});
      function setUploadState(name, value) {
        const previous = uploadStatesRef.current[name];
        const next = { ...uploadStatesRef.current, [name]: value };
        uploadStatesRef.current = next;
        setUploadStates(next);
        if (value.message && (value.attempted || value.state === 'failed' || value.state === 'idle') && (!previous || previous.state !== value.state)) setUploadNotice(name);
      }
      async function checkUpload(name) {
        function uploadMessage(value, fallback) {
          if (value == null) return fallback || '';
          if (typeof value === 'object') return uploadMessage(value.message || value.error || value.detail, fallback);
          const text = String(value).trim();
          if (!text) return fallback || '';
          if (/^[\[{]/.test(text)) {
            try { return uploadMessage(JSON.parse(text), fallback); } catch (_) { return fallback || '请检查后端日志并重试。'; }
          }
          return text;
        }
        const generation = (uploadGeneration.current[name] || 0);
        if (uploadChecks.current[name] === generation) return;
        uploadChecks.current[name] = generation;
        try {
          const priorState = uploadStatesRef.current[name];
          const useLite = Boolean(priorState && priorState.state === 'accepted');
          const st = await jget('/batches/' + encodeURIComponent(name) + '/upload-status' + (useLite ? '?lite=1' : ''));
          if (generation !== (uploadGeneration.current[name] || 0)) return;
          const previous = uploadStatesRef.current[name];
          const record = (value) => setUploadState(name, { ...value, requiresUnreviewedConfirmation: st.requires_unreviewed_confirmation, unreviewedRevision: st.unreviewed && st.unreviewed.revision });
          const state = st.state || (st.running ? 'running' : st.ok === false ? 'failed' : 'idle');
          // An empty status after submission cannot prove the request did not reach the server.
          if (state === 'idle' && previous && previous.attempted) {
            setUploadState(name, { state: 'unknown', attempted: true, message: '暂时无法确认上传结果，请在远端核实，勿重复上传。' });
          } else if (state === 'running') {
            setUploadState(name, { state, attempted: true, message: st.step || '正在处理上传…' });
          } else if (state === 'accepted') {
            const receipt = st.receipt && typeof st.receipt === 'object' ? uploadMessage(st.receipt.id, '') : uploadMessage(st.receipt, '');
            const attachments = st.receipt && Array.isArray(st.receipt.attachments) ? st.receipt.attachments : [];
            const attachmentText = attachments.length
              ? ' 审核附件已上传：' + attachments.length + ' 个（' + attachments.join('、') + '）。'
              : '';
            setUploadState(name, { state, attempted: true, message: '已提交到云端上传暂存，等待远端确认。' + attachmentText + (receipt ? ' 回执：' + receipt : '') });
            if (!previous || previous.state !== 'accepted') loadBatches();
          } else if (state === 'failed') {
            const error = uploadMessage(st.error || st.step, '未知错误');
            // Prepare reports are already summarized by the server; keep readable output.
            const output = uploadMessage(st.output, '');
            const detail = output && !/^上传准备未通过：\d+ 项阻断错误/.test(String(error)) && !String(error).includes(output) ? '\n' + output : '';
            record({ state, message: '上传未完成：' + error + detail });
          } else if (state === 'idle') {
            record({ state: 'idle', message: '' });
          } else {
            setUploadState(name, { state: 'unknown', attempted: true, message: '上传结果未知，请在远端核实，勿重复上传。' + (st.error ? ' ' + uploadMessage(st.error, '') : '') });
          }
        } catch (e) {
          if (generation !== (uploadGeneration.current[name] || 0)) return;
          const previous = uploadStatesRef.current[name];
          setUploadState(name, { state: 'unknown', attempted: !!(previous && previous.attempted), message: '无法连接后端确认上传状态，请检查网络并等待重连；确认结果前勿重复上传。' });
        } finally {
          if (uploadChecks.current[name] === generation) delete uploadChecks.current[name];
        }
      }
      const [menuFor, setMenuFor] = React.useState(''); // 卡片「⋯」菜单当前展开的批次
      // 筛选态：'all' | 'uploaded' | 'upload_anomaly'；来源筛选独立走弹窗选择（'' = 全部来源）
      const [reviewFilter, setReviewFilter] = React.useState('all');
      const [selectedSource, setSelectedSource] = React.useState('');
      const [sourceModalOpen, setSourceModalOpen] = React.useState(false);
      const [sourceModalFilter, setSourceModalFilter] = React.useState('');
      const [taxonomySources, setTaxonomySources] = React.useState([
        '中考真题', '中考模拟', '中考复习', '期末试题', '期中试题', '月考试题',
        '开学试题', '单元测验', '同步练习', '木牍原创', '木牍改编', '精品题源', '课本原创',
      ]);
      React.useEffect(() => {
        if (!isActive) return undefined;
        let cancelled = false;
        jget('/taxonomy').then((tx) => {
          if (!cancelled && tx && Array.isArray(tx.sources) && tx.sources.length) {
            setTaxonomySources(tx.sources.filter((s) => typeof s === 'string' && s.trim()));
          }
        }).catch(() => {});
        return () => { cancelled = true; };
      }, [isActive]);
      // 列表加载态：'idle' | 'loading' | 'ready' | 'error'。若已有共享缓存则直接就绪，避免切换标签闪出加载页。
      const [listState, setListState] = React.useState(() => (typeof SHARED_BATCHES_CACHE !== 'undefined' && (SHARED_BATCHES_CACHE.review || SHARED_BATCHES_CACHE.full || SHARED_BATCHES_CACHE.lite)) ? 'ready' : 'idle');
      const loadSeq = React.useRef(0);

      const loadBatches = React.useCallback(async (opts) => {
        const manual = Boolean(opts && opts.manual === true);
        // 每次加载递增序号，只让最新一次的结果落地（防止重试/手动刷新交叉覆盖）。
        const seq = ++loadSeq.current;
        if (manual) {
          setRefreshing(true);
          setRefreshHint('');
          if (refreshTimer.current) { clearTimeout(refreshTimer.current); refreshTimer.current = null; }
        }
        setListState((s) => (s === 'ready' ? s : 'loading'));
        setErr('');
        // 冷启动/后端重启时首包可能 5xx 或短暂连不上：指数退避重试几次，
        // 让面板自动“缓过来”，而不是把空列表丢给用户。
        const delays = [0, 600, 1200, 2000, 3000];
        let lastErr = null;
        for (let attempt = 0; attempt < delays.length; attempt++) {
          if (seq !== loadSeq.current) {
            if (manual) setRefreshing(false);
            return; // 已被更新的请求取代
          }
          if (delays[attempt]) await new Promise((r) => setTimeout(r, delays[attempt]));
          try {
            // 审核列表卡片不渲染整卷 rows/extraction/流程图详表，使用 review_list=1（自带 first_unit_id），
            // 同时返回未上传与已上传批次以支持分组筛选，进入单批时再按需拉取该批的 rows 与单元详情。
            const url = manual ? ('/batches?review_list=1&_t=' + Date.now()) : '/batches?review_list=1';
            const d = await jget(url);
            if (seq !== loadSeq.current) {
              if (manual) setRefreshing(false);
              return;
            }
            const list = d.batches || [];
            if (typeof SHARED_BATCHES_CACHE !== 'undefined') { SHARED_BATCHES_CACHE.review = list; }
            setServerActiveWork(Boolean(d && d.has_active_work));
            setBatches(list);
            setActive((current) => {
              if (!current) return current;
              const latest = (d.batches || []).find((b) => b.name === current.name);
              if (!latest) return current;
              // Preserve already-loaded rows for the currently open batch when list refreshes with lite=1.
              const mergedOverview = (current.overview && Array.isArray(current.overview.rows) && !Array.isArray(latest.rows))
                ? { ...latest, rows: current.overview.rows }
                : latest;
              return { ...current, overview: mergedOverview };
            });
            setListState('ready');
            setErr('');
            if (manual) {
              setRefreshing(false);
              setRefreshHint('已刷新');
              refreshTimer.current = setTimeout(() => setRefreshHint(''), 1800);
            }
            return;
          } catch (e) {
            lastErr = e;
            if (seq !== loadSeq.current) {
              if (manual) setRefreshing(false);
              return;
            }
            // 只有明确业务 4xx（400/401/403）直接放弃；热重载/重启窗口期宿主可能短暂返回 404，允许退避重试。
            const msg = String((e && e.message) || e);
            const retriable = !/\b(400|401|403)\b/.test(msg);
            if (!retriable) break;
          }
        }
        if (seq !== loadSeq.current) {
          if (manual) setRefreshing(false);
          return;
        }
        if (manual) setRefreshing(false);
        setListState('error');
        setErr(String((lastErr && lastErr.message) || lastErr || '加载失败'));
      }, []);
      const hasActiveWork = serverActiveWork || (batches || []).some((b) =>
        (b && !b.flow_done && !b.uploaded && !b.queue_removed)
        || (b && b.advance && (b.advance.running || b.advance.queued || b.advance.waiting))
        || (b && b.upload_status && b.upload_status.running),
      );
      React.useEffect(() => {
        if (!isActive) {
          setActive(null);
          return;
        }
        if (typeof SHARED_BATCHES_CACHE !== 'undefined' && (SHARED_BATCHES_CACHE.review || SHARED_BATCHES_CACHE.full || SHARED_BATCHES_CACHE.lite)) {
          const cached = SHARED_BATCHES_CACHE.review || SHARED_BATCHES_CACHE.full || SHARED_BATCHES_CACHE.lite;
          setBatches((prev) => prev || cached);
          setListState((s) => (s === 'idle' || s === 'loading' ? 'ready' : s));
        }
        loadBatches();
        return () => { loadSeq.current += 1; };
      }, [loadBatches, isActive, props.uploadedTick]);
      const inReview = !!active;
      React.useEffect(() => {
        if (!isActive || inReview) return;
        const intervalMs = hasActiveWork ? 2500 : 8000;
        const timer = setInterval(() => {
          if (typeof document === 'undefined' || document.visibilityState !== 'hidden') loadBatches();
        }, intervalMs);
        return () => { clearInterval(timer); };
      }, [loadBatches, hasActiveWork, inReview, isActive]);
      // 窗口重新获得焦点时自动刷新一次（仅当前标签可见时）。
      React.useEffect(() => {
        if (!isActive) return;
        const onVisible = () => {
          if (typeof document !== 'undefined' && document.visibilityState === 'visible') loadBatches();
        };
        const onFocus = () => loadBatches();
        if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisible);
        if (typeof window !== 'undefined') window.addEventListener('focus', onFocus);
        return () => {
          if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisible);
          if (typeof window !== 'undefined') window.removeEventListener('focus', onFocus);
        };
      }, [loadBatches, isActive]);

      // 批次状态变化（如内容审核完成、差异数更新、重跑等）时自动失效该批次的前端单元缓存，
      // 防止审核前预取的「待审核」旧快照残留在 UNIT_CACHE 中。
      const batchSigRef = React.useRef(new Map());
      React.useEffect(() => {
        const prev = batchSigRef.current;
        const next = new Map();
        for (const b of (batches || [])) {
          if (!b || !b.name) continue;
          const sig = batchCacheSignature(b);
          next.set(b.name, sig);
          if (prev.has(b.name) && prev.get(b.name) !== sig) {
            invalidateBatchUnits(b.name);
          }
        }
        batchSigRef.current = next;
      }, [batches]);

      // 审核列表展示时，仅在后台静默预热最靠前的 4 份已完成流程（flow_done）且未上传的试卷，
      // 避免在审核未结束前就把「待审核」空快照锁进缓存。
      // 按需加载模式：列表页不做整卷预取与悬停预取；仅在用户实际点击试卷卡片时按需加载。
      // Reconnect and page re-entry always consult durable status before enabling upload.
      React.useEffect(() => {
        if (!active) return;
        const name = active.name;
        if (!uploadStatesRef.current[name]) setUploadState(name, { state: 'checking', message: '正在查询上传状态…' });
        const initTimer = setTimeout(() => checkUpload(name), 120);
        const timer = setInterval(() => checkUpload(name), 3000);
        return () => { clearTimeout(initTimer); clearInterval(timer); uploadGeneration.current[name] = (uploadGeneration.current[name] || 0) + 1; };
      }, [active && active.name]);

      function openBatch(name) {
        clearAllUnitCaches();
        clearImageCache();
        const prior = uploadStatesRef.current[name];
        setUploadState(name, { state: 'checking', attempted: !!(prior && prior.attempted), message: '正在查询上传状态…' });
        const b = (batches || []).find((x) => x.name === name);
        const rows = (b && b.rows) || [];
        const firstRow = rows[0];
        const unitId = firstRow ? (firstRow.unit_id || firstRow.number) : ((b && b.first_unit_id) || '_');
        setActive({ name, unitId, overview: b || null });
        if (!b || !Array.isArray(b.rows)) {
          jget('/batches/' + encodeURIComponent(name)).then((full) => {
            if (!full || typeof full !== 'object') return;
            const fullRows = full.rows || [];
            const first = fullRows[0];
            setActive((cur) => (cur && cur.name === name
              ? { ...cur, unitId: cur.unitId === '_' && first ? (first.unit_id || first.number) : cur.unitId, overview: full }
              : cur));
          }).catch(() => {});
        }
      }
      async function doAdvance(name) {
        setAdvBusy(name);
        invalidateBatchUnits(name);
        try { await jpost('/batches/' + encodeURIComponent(name) + '/advance', {}); invalidateBatchUnits(name); await loadBatches(); }
        catch (e) { setErr(String(e.message || e)); }
        finally { setAdvBusy(''); }
      }
      // 上传批次：把本批写入云端题库的“上传暂存”。后台执行并轮询状态；
      // 成功后后端 overview.uploaded=true，该批次会从「试卷审核」列表消失。
      async function doUpload(name) {
        const current = uploadStatesRef.current[name];
        if (!current || (current.state !== 'idle' && current.state !== 'failed') || typeof current.requiresUnreviewedConfirmation !== 'boolean') return;
        const unreviewed = current.requiresUnreviewedConfirmation;
        if (unreviewed && !current.unreviewedRevision) return;
        const ok = window.confirm('确定把「' + name + '」整批上传到云端题库？\n\n'
          + (unreviewed ? '警告：该批次尚未完成审核。确认继续将上传未经审核的原始知识点标签。' : '上传前会核对结构和审核状态。'));
        if (!ok) return;
        uploadGeneration.current[name] = (uploadGeneration.current[name] || 0) + 1;
        setUploadState(name, { state: 'running', attempted: true, message: '正在提交上传请求…' });
        try {
          if (unreviewed) await jpost('/batches/' + encodeURIComponent(name) + '/upload', { allow_unreviewed: true, unreviewed_revision: current.unreviewedRevision });
          else await jpost('/batches/' + encodeURIComponent(name) + '/upload', {});
        } catch (e) {
          if ([400, 401, 403, 404, 422].includes(e && e.httpStatus)) {
            window.alert('上传未开始：' + String(e.message || '请重新检查后再试。'));
            setUploadState(name, { state: 'checking', message: '正在重新检查上传状态…' });
            await checkUpload(name);
            return;
          }
          setUploadState(name, { state: 'unknown', attempted: true, message: '上传请求未收到确认。结果可能未知，请在远端核实，勿重复上传。' });
        }
        await checkUpload(name);
      }
      // 重新执行：tag=从「题型与子类」起重跑打标签到审核结束；review=从「审核」起重跑两轮审核到内容审核与上传格式检查结束。
      async function doRerun(name, mode) {
        const label = mode === 'tag' ? '重新标签（从题型与子类起，重跑到审核结束）' : '重新审核（从审核起，重跑到内容审核与上传格式检查结束）';
        if (!window.confirm('确定对「' + name + '」执行' + label + '？已完成的后续阶段会失效并重跑。')) return;
        setAdvBusy(name); setErr('');
        invalidateBatchUnits(name);
        try {
          await jpost('/batches/' + encodeURIComponent(name) + '/rerun', { mode });
          // 后端返回 started=true/queued=true 后仍在后台排队或执行，持续跟踪并同步刷新进度。
          const deadline = Date.now() + 30 * 60 * 1000;
          let last;
          while (Date.now() < deadline) {
            await new Promise((r) => setTimeout(r, 2000));
            last = await jget('/batches/' + encodeURIComponent(name) + '/pipeline');
            loadBatches();
            if (!last || (!last.running && !last.queued && !last.waiting)) break;
          }
          if (last && (last.running || last.queued || last.waiting)) setErr('重新执行仍在后台运行，请稍后刷新查看；当前页面已停止等待。');
          invalidateBatchUnits(name);
          await loadBatches();
        } catch (e) { setErr(String(e.message || e)); }
        finally { setAdvBusy(''); }
      }
      // 彻底删除确认：不使用 window.prompt（Electron/嵌入式 WebView 不弹原生输入框，
      // 会导致点击“没反应”）。改用页面内确认弹层，输入完整批次名后调用 purge。
      function openDelete(name) { setDel({ name, value: '', busy: false, error: '' }); }
      async function confirmDelete() {
        if (!del || del.busy) return;
        if (del.value !== del.name) { setDel({ ...del, error: '请输入完整批次名以确认删除' }); return; }
        setDel({ ...del, busy: true, error: '' });
        try {
          await jpost('/batches/' + encodeURIComponent(del.name) + '/purge', { confirm_name: del.name }, { timeoutMs: 120000 });
          setDel(null);
          await loadBatches();
          setActive(null);
        } catch (e) { setDel({ ...del, busy: false, error: String((e && e.message) || e) }); }
      }
      const deleteDialog = del ? h('div', {
        style: { position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(16,24,40,.42)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 },
        onClick: (e) => { if (e.target === e.currentTarget && !del.busy) setDel(null); },
      },
        h('div', {
          role: 'dialog', 'aria-modal': 'true', 'aria-label': '彻底删除批次确认',
          style: { width: '100%', maxWidth: 430, background: 'var(--dsw-alias-bg-layer-1)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 14, padding: 18, boxShadow: '0 18px 48px rgba(16,24,40,.28)', display: 'flex', flexDirection: 'column', gap: 10 },
        },
          h('div', { style: { fontSize: 15, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' } }, '彻底删除批次'),
          h('div', { style: { ...T.hint, lineHeight: 1.6 } }, '将永久删除批次「' + del.name + '」的目录、收件箱副本与所有本地回收归档，无法恢复。请输入完整批次名确认：'),
          h('div', { style: { display: 'flex', gap: 8, alignItems: 'stretch' } },
            h('input', {
              className: 'wf-input', autoFocus: true, value: del.value,
              style: { ...T.input, flex: 1, minWidth: 0 },
              placeholder: del.name,
              onChange: (e) => setDel({ ...del, value: e.target.value, error: '' }),
              onKeyDown: (e) => { if (e.key === 'Enter') confirmDelete(); if (e.key === 'Escape' && !del.busy) setDel(null); },
            }),
            h('button', {
              type: 'button', className: 'wf-btn', style: { ...T.btn, flexShrink: 0, whiteSpace: 'nowrap' },
              disabled: del.busy,
              title: '一键填入完整批次名',
              onClick: () => setDel({ ...del, value: del.name, error: '' }),
            }, '一键填入'),
          ),
          del.error ? h('div', { role: 'alert', style: { ...T.statusErr, padding: '6px 9px', borderRadius: 8 } }, del.error) : null,
          h('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 } },
            h('button', { className: 'wf-btn', style: T.btn, disabled: del.busy, onClick: () => setDel(null) }, '取消'),
            h('button', { className: 'wf-btn-danger', style: T.btnDanger, disabled: del.busy || del.value !== del.name, onClick: confirmDelete }, del.busy ? '正在删除…' : '确认彻底删除'),
          ),
        ),
      ) : null;

      // 进入审阅：原工作台三栏布局（单元列表 + 工作区），键盘 ↑/↓ 切换题组。
      if (active) {
        let rows = (active.overview && Array.isArray(active.overview.rows) && active.overview.rows.length)
          ? active.overview.rows
          : [];
        if (!rows.length) {
          const cachedBundle = UNIT_CACHE.get(String(active.name || ''));
          if (cachedBundle && cachedBundle.loaded && Array.isArray(cachedBundle.order)) {
            rows = cachedBundle.order.map((uid) => {
              const u = cachedBundle.units.get(String(uid));
              return u && u.row ? u.row : null;
            }).filter(Boolean);
          }
        }
        const upload = uploadStates[active.name] || { state: 'checking', message: '正在查询上传状态…' };
        const refreshActiveOverview = () => {
          invalidateBatchUnits(active.name);
          jget('/batches/' + encodeURIComponent(active.name)).then((full) => {
            if (!full || typeof full !== 'object') return;
            setActive((cur) => (cur && cur.name === active.name ? { ...cur, overview: full } : cur));
          }).catch(() => {});
          loadBatches();
        };
        return h(React.Fragment, null, h('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--dsw-alias-bg-base)' } },
          // 来源横条：独立悬浮卡片，「返回列表」左对齐并用细竖线与来源名称隔开，其余操作按钮右对齐。
          h('div', { style: { ...T.toolbar, margin: '10px 14px 0', padding: '9px 14px', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 12, background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 4px 16px rgba(15, 23, 42, 0.08), 0 1px 3px rgba(15, 23, 42, 0.06)', flexShrink: 0 } },
            h('button', { className: 'wf-btn', style: { ...T.btn, flexShrink: 0 }, onClick: () => { clearAllUnitCaches(); clearImageCache(); setActive(null); } }, TEXT.back),
            h('span', { 'aria-hidden': 'true', style: { width: 1, height: 18, background: 'var(--dsw-alias-border-l2)', flexShrink: 0 } }),
            h('span', { style: T.toolbarTitle }, (active.overview?.paper?.title || active.name)),
            h('span', { style: { ...T.hint, marginLeft: 4 } }, '↑/↓ 或 k/j 切换题组'),
            h('span', { style: { flex: 1 } }),
            upload.state === 'idle' && typeof upload.requiresUnreviewedConfirmation !== 'boolean' ? h('span', { role: 'status', style: T.hint }, '无法确认审核状态，请更新后端并重新进入批次。') : null,
            upload.message && uploadNotice !== active.name && (upload.attempted || upload.state === 'failed' || upload.state === 'idle') ? h('button', { className: 'wf-btn', style: T.btn, onClick: () => setUploadNotice(active.name) }, '查看上传通知') : null,
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: () => doUpload(active.name), disabled: !['idle', 'failed'].includes(upload.state) || typeof upload.requiresUnreviewedConfirmation !== 'boolean' || advBusy === active.name }, upload.state === 'running' ? '上传中…' : '上传批次'),
            h('button', { className: 'wf-btn-danger', style: T.btnDanger, onClick: () => openDelete(active.name) }, '删除批次'),
          ),
          uploadNotice === active.name && upload.message ? (() => {
            const isErrorNotice = upload.state === 'failed' || upload.state === 'unknown';
            const isLongNotice = isErrorNotice || String(upload.message).length > 120 || String(upload.message).includes('\n');
            const noticeTitle = upload.state === 'failed' ? '上传失败' : upload.state === 'unknown' ? '上传状态未知' : '上传通知';
            return h('section', {
              role: 'dialog', 'aria-label': noticeTitle,
              style: {
                position: 'fixed', right: 20, bottom: 20, zIndex: 2500,
                width: 'min(420px, calc(100vw - 32px))',
                height: isLongNotice ? 'min(280px, calc(100vh - 140px))' : 'auto',
                maxHeight: 'min(280px, calc(100vh - 140px))',
                display: 'flex', flexDirection: 'column', overflow: 'hidden',
                boxSizing: 'border-box', borderRadius: 12,
                border: '1px solid ' + (isErrorNotice ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-border-l2)'),
                background: 'var(--dsw-alias-bg-layer-1)',
                boxShadow: '0 10px 30px rgba(0,0,0,.18)',
              },
            },
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderBottom: '1px solid var(--dsw-alias-border-l1)', flexShrink: 0 } },
                h('strong', { style: { flex: 1, minWidth: 0, fontSize: 13.5, fontWeight: 600, color: isErrorNotice ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-primary)' } }, noticeTitle),
                h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0 }, onClick: () => { try { navigator.clipboard && navigator.clipboard.writeText(upload.message); } catch (_) {} }, title: '复制通知内容' }, '复制'),
                h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0 }, onClick: () => setUploadNotice(''), 'aria-label': '关闭上传通知' }, '关闭')),
              h('div', {
                className: 'wf-scroll',
                role: isErrorNotice ? 'alert' : 'status',
                'aria-live': isErrorNotice ? 'assertive' : 'polite',
                style: { flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden', padding: '10px 14px', fontSize: 12.5, lineHeight: 1.65, color: 'var(--dsw-alias-label-primary)', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', wordBreak: 'break-word' },
              }, upload.message),
            );
          })() : null,
          h(ReviewView, {
            // 切题不重建组件：key 只随批次变化。若按 unitId 作 key，每次 ↑/↓
            // 都会重挂载，把题组列表与预览区的滚动条全部重置到顶端。
            key: 'review:' + active.name,
            batchName: active.name,
            rows,
            unitId: active.unitId,
            onSelectUnit: (uid) => setActive({ ...active, unitId: uid }),
            onUnitMutated: refreshActiveOverview,
          }),
        ), deleteDialog);
      }

      // 试卷来源标签：优先取识别到的 source_type（如 同步练习 / 中考真题 / 期末考试 / 月考 等）；
      // 缺失时命中课时特征归「同步练习」，其余归「未分类」。
      function batchSourceLabel(b) {
        const st = String((b && b.paper && b.paper.source_type) || (b && b.meta && b.meta.source_type) || '').trim();
        if (st && st !== '未提供' && st !== '未填写') return st;
        const label = String((b && b.paper && b.paper.title) || (b && b.name) || '');
        if (/课时|Section\s*[AB]|Grammar\s*Focus|Pronunciation|单元话题写作|综合阅读提升|综合训练|单元综合/i.test(label)) return '同步练习';
        return '未分类';
      }
      function batchCategory(b) {
        const st = String((b && b.paper && b.paper.source_type) || (b && b.meta && b.meta.source_type) || '').trim();
        if (st === '同步练习') return 'lesson';
        if (st && st !== '未提供' && st !== '未填写') return 'exam';
        const label = String((b && b.paper && b.paper.title) || (b && b.name) || '');
        return /课时|Section\s*[AB]|Grammar\s*Focus|Pronunciation|单元话题写作|综合阅读提升|综合训练|单元综合/i.test(label) ? 'lesson' : 'exam';
      }
      // 判断批次是否已上传到远端（含已写入云端上传暂存、待远端确认发布的批次）。
      function isBatchUploaded(b) {
        if (!b || typeof b !== 'object') return false;
        if (b.uploaded || b.upload_pending_confirmation) return true;
        const stages = Array.isArray(b.stages) ? b.stages : [];
        if (stages.some((s) => s && s.id === 'upload' && s.status === 'completed')) return true;
        const rec = b.upload_status && b.upload_status.receipt;
        return Boolean(rec && (rec.state === 'staged' || rec.submitted_at || rec.remote_confirmation_required));
      }

      // 判断批次是否属于「异常录入」（未成功上传，且存在上传失败、阶段阻断或单题异常）。
      function isBatchUploadAnomaly(b) {
        if (!b || typeof b !== 'object' || isBatchUploaded(b)) return false;
        const us = b.upload_status || {};
        if (us.state === 'failed' || Boolean(us.error)) return true;
        const stages = Array.isArray(b.stages) ? b.stages : [];
        if (stages.some((s) => s && s.id === 'upload' && s.status === 'failed')) return true;
        return Boolean(b.has_anomaly || Number(b.anomaly_count || 0) > 0 || b.stage_blocked);
      }

      // 判断批次是否仍在「上传试卷 → 排队区」且尚未完成；若批次已在当前阶段阻断/失败且无活跃执行，
      // 同步透出到「试卷审核」卡片页标红展示，避免异常试卷被隐藏。
      function isBatchPendingInQueue(b) {
        if (!b || typeof b !== 'object') return false;
        if (b.flow_done || isBatchUploaded(b) || b.queue_removed) return false;
        const adv = b.advance || {};
        if (b.stage_blocked && !(adv.running || adv.queued || adv.waiting)) return false;
        return true;
      }

      function matchReviewFilter(b, filterKey) {
        const key = String(filterKey || 'all');
        if (key === 'all') return true;
        if (key === 'exam') return batchCategory(b) === 'exam';
        if (key === 'lesson') return batchCategory(b) === 'lesson';
        if (key === 'uploaded') return isBatchUploaded(b);
        if (key === 'upload_anomaly') return isBatchUploadAnomaly(b);
        return true;
      }
      // 排除仍在「上传试卷 → 排队区」且尚未完成的批次；保留已完成或已上传的批次，按最新时间降序排列。
      const baseList = (batches || []).filter((b) => !isBatchPendingInQueue(b))
        .sort((a, b) => batchLatestTime(b) - batchLatestTime(a));
      const sourceEntries = (() => {
        const map = new Map();
        const orderIndex = new Map();
        (taxonomySources || []).forEach((name, idx) => {
          const clean = String(name || '').trim();
          if (!clean || map.has(clean)) return;
          orderIndex.set(clean, idx);
          map.set(clean, { name: clean, count: 0, uploaded: 0, anomaly: 0 });
        });
        for (const b of baseList) {
          const src = batchSourceLabel(b);
          const rec = map.get(src) || { name: src, count: 0, uploaded: 0, anomaly: 0 };
          rec.count += 1;
          if (isBatchUploaded(b)) rec.uploaded += 1;
          if (isBatchUploadAnomaly(b)) rec.anomaly += 1;
          map.set(src, rec);
        }
        return Array.from(map.values()).sort((a, b) => {
          const aUn = a.name === '未分类';
          const bUn = b.name === '未分类';
          if (aUn !== bUn) return aUn ? 1 : -1;
          const aHas = a.count > 0 ? 0 : 1;
          const bHas = b.count > 0 ? 0 : 1;
          if (aHas !== bHas) return aHas - bHas;
          if (b.count !== a.count) return b.count - a.count;
          const ai = orderIndex.has(a.name) ? orderIndex.get(a.name) : 9999;
          const bi = orderIndex.has(b.name) ? orderIndex.get(b.name) : 9999;
          if (ai !== bi) return ai - bi;
          return a.name.localeCompare(b.name, 'zh-Hans-CN');
        });
      })();
      const sourceFilteredBase = selectedSource
        ? baseList.filter((b) => batchSourceLabel(b) === selectedSource)
        : baseList;
      const uploadedCount = sourceFilteredBase.filter((b) => isBatchUploaded(b)).length;
      const uploadAnomalyCount = sourceFilteredBase.filter((b) => isBatchUploadAnomaly(b)).length;
      const list = sourceFilteredBase.filter((b) => matchReviewFilter(b, reviewFilter));
      const selectedSourceLabel = selectedSource || '全部来源';
      const emptyTitleMap = {
        uploaded: '暂无已上传批次',
        upload_anomaly: '暂无异常录入批次',
      };
      const chooseSource = (value) => {
        setSelectedSource(value || '');
        if (!value) setReviewFilter('all');
        setSourceModalOpen(false);
        setSourceModalFilter('');
      };
      const modalFilteredSources = (() => {
        const kw = String(sourceModalFilter || '').trim().toLowerCase();
        return kw
          ? sourceEntries.filter((s) => String(s.name || '').toLowerCase().includes(kw))
          : sourceEntries;
      })();
      const renderFilterChip = (id, label, count, opts) => {
        const activeChip = reviewFilter === id;
        const tone = opts && opts.tone;
        const isGreen = tone === 'green';
        const isRed = tone === 'red';
        const activeBg = isGreen ? '#16a34a' : (isRed ? '#dc2626' : '#2563eb');
        const activeShadow = isGreen
          ? '0 2px 8px rgba(22, 163, 74, 0.28)'
          : (isRed ? '0 2px 8px rgba(220, 38, 38, 0.28)' : '0 2px 8px rgba(37, 99, 235, 0.28)');
        const dotColor = !activeChip && opts && opts.dotColor;
        return h('button', {
          key: id,
          type: 'button',
          className: 'wf-filter-chip' + (activeChip ? ' wf-filter-chip-active' : ''),
          'aria-pressed': activeChip,
          onClick: () => setReviewFilter((cur) => (cur === id ? 'all' : id)),
          style: {
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '5px 12px',
            fontSize: 12.5,
            fontWeight: activeChip ? 600 : 500,
            lineHeight: 1.2,
            borderRadius: 999,
            border: '1px solid ' + (activeChip ? activeBg : 'var(--dsw-alias-border-l2)'),
            background: activeChip ? activeBg : 'var(--dsw-alias-bg-layer-1)',
            color: activeChip ? '#ffffff' : 'var(--dsw-alias-label-primary)',
            boxShadow: activeChip ? activeShadow : '0 1px 2px rgba(15, 23, 42, 0.05)',
            fontVariantNumeric: 'tabular-nums',
            whiteSpace: 'nowrap',
          },
        },
          dotColor ? h('span', { style: { width: 6, height: 6, borderRadius: 999, background: dotColor, flexShrink: 0 } }) : null,
          h('span', null, label),
          h('span', {
            style: {
              fontSize: 11,
              fontWeight: 600,
              padding: '1px 6px',
              borderRadius: 999,
              background: activeChip ? 'rgba(255, 255, 255, 0.22)' : 'rgba(15, 23, 42, 0.07)',
              color: activeChip ? '#ffffff' : 'var(--dsw-alias-label-secondary)',
            },
          }, String(count)),
        );
      };
      const reviewFilterBar = h('div', {
        role: 'region',
        'aria-label': '试卷分类筛选',
        style: {
          display: 'flex',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '8px 10px',
          margin: '10px 14px 0',
          padding: '9px 14px',
          borderRadius: 12,
          border: '1px solid var(--dsw-alias-border-l1)',
          background: 'var(--dsw-alias-bg-layer-1)',
          boxShadow: '0 4px 16px rgba(15, 23, 42, 0.08), 0 1px 3px rgba(15, 23, 42, 0.06)',
          flexShrink: 0,
        },
      },
        h('button', {
          type: 'button',
          className: 'wf-btn',
          'aria-haspopup': 'dialog',
          'aria-expanded': sourceModalOpen,
          onClick: () => { setSourceModalFilter(''); setSourceModalOpen(true); },
          style: {
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            padding: '5px 12px',
            fontSize: 12.5,
            fontWeight: (selectedSource || reviewFilter === 'all') ? 600 : 500,
            lineHeight: 1.2,
            borderRadius: 999,
            border: '1px solid ' + ((selectedSource || reviewFilter === 'all') ? '#2563eb' : 'var(--dsw-alias-border-l2)'),
            background: (selectedSource || reviewFilter === 'all') ? '#2563eb' : 'var(--dsw-alias-bg-layer-1)',
            color: (selectedSource || reviewFilter === 'all') ? '#ffffff' : 'var(--dsw-alias-label-primary)',
            boxShadow: (selectedSource || reviewFilter === 'all') ? '0 2px 8px rgba(37, 99, 235, 0.28)' : '0 1px 2px rgba(15, 23, 42, 0.05)',
            fontVariantNumeric: 'tabular-nums',
            whiteSpace: 'nowrap',
            cursor: 'pointer',
          },
          title: '点击按来源筛选试卷',
        },
          h('span', null, selectedSourceLabel),
          h('span', {
            style: {
              fontSize: 11,
              fontWeight: 600,
              padding: '1px 6px',
              borderRadius: 999,
              background: (selectedSource || reviewFilter === 'all') ? 'rgba(255, 255, 255, 0.22)' : 'rgba(15, 23, 42, 0.07)',
              color: (selectedSource || reviewFilter === 'all') ? '#ffffff' : 'var(--dsw-alias-label-secondary)',
            },
          }, String(sourceFilteredBase.length)),
          h('span', { style: { fontSize: 10.5, opacity: 0.85 } }, '▾'),
        ),
        renderFilterChip('uploaded', '已上传', uploadedCount, { tone: 'green', dotColor: uploadedCount > 0 ? '#16a34a' : '' }),
        renderFilterChip('upload_anomaly', '异常录入', uploadAnomalyCount, { tone: 'red', dotColor: uploadAnomalyCount > 0 ? '#dc2626' : '' }),
        h('span', { style: { flex: 1 } }),
        refreshHint ? h('span', { role: 'status', style: { ...T.hint, whiteSpace: 'nowrap' } }, refreshHint) : null,
        h('button', { className: 'wf-btn', style: T.btn, disabled: refreshing, onClick: () => loadBatches({ manual: true }) }, refreshing ? '刷新中…' : TEXT.reload),
      );
      const sourcePickerModal = sourceModalOpen ? h(ModalShell, {
        title: '选择来源',
        tag: selectedSourceLabel,
        width: 680,
        onClose: () => setSourceModalOpen(false),
        footer: [
          selectedSource ? h('button', { key: 'all', type: 'button', className: 'wf-btn', style: T.btn, onClick: () => chooseSource('') }, '选择全部来源') : null,
          h('button', { key: 'close', type: 'button', className: 'wf-btn', style: T.btn, onClick: () => setSourceModalOpen(false) }, TEXT.close),
        ].filter(Boolean),
      },
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 12 } },
          h('input', {
            className: 'wf-input',
            type: 'search',
            autoFocus: true,
            value: sourceModalFilter,
            placeholder: '筛选来源名称…',
            style: { ...T.input, width: '100%' },
            onChange: (e) => setSourceModalFilter(e.target.value),
          }),
          h('div', {
            role: 'listbox',
            'aria-label': '全部来源',
            style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 8 },
          },
            ...(!String(sourceModalFilter || '').trim() || '全部来源'.includes(String(sourceModalFilter || '').trim().toLowerCase()) ? [
              h('button', {
                key: '__all__',
                type: 'button',
                role: 'option',
                'aria-selected': selectedSource === '',
                className: 'wf-type-grid-item',
                onClick: () => chooseSource(''),
                style: {
                  display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4,
                  padding: '10px 12px', borderRadius: 10, textAlign: 'left',
                  border: '1px solid ' + (selectedSource === '' ? 'var(--wf-sel-border)' : 'var(--dsw-alias-border-l2)'),
                  background: selectedSource === '' ? 'var(--wf-sel-bg)' : 'var(--dsw-alias-bg-base)',
                  color: 'var(--dsw-alias-label-primary)',
                },
              },
                h('span', { style: { fontWeight: 600, fontSize: 13 } }, '全部来源'),
                h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', fontVariantNumeric: 'tabular-nums' } },
                  baseList.length + ' 份'),
              ),
            ] : []),
            ...modalFilteredSources.map((s) => {
              const isSelected = selectedSource === s.name;
              return h('button', {
                key: s.name,
                type: 'button',
                role: 'option',
                'aria-selected': isSelected,
                className: 'wf-type-grid-item',
                onClick: () => chooseSource(s.name),
                style: {
                  display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4,
                  padding: '10px 12px', borderRadius: 10, textAlign: 'left',
                  border: '1px solid ' + (isSelected ? 'var(--wf-sel-border)' : 'var(--dsw-alias-border-l2)'),
                  background: isSelected ? 'var(--wf-sel-bg)' : 'var(--dsw-alias-bg-base)',
                  color: 'var(--dsw-alias-label-primary)',
                },
              },
                h('span', { style: { fontWeight: isSelected ? 600 : 500, fontSize: 13 } }, s.name),
                h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', fontVariantNumeric: 'tabular-nums' } },
                  s.count + ' 份' + (s.anomaly > 0 ? (' · ' + s.anomaly + ' 异常') : '')),
              );
            }),
          ),
          modalFilteredSources.length === 0 && String(sourceModalFilter || '').trim()
            ? h('div', { style: { ...T.hint, textAlign: 'center', padding: 12 } }, '没有匹配的来源')
            : null,
        ),
      ) : null;
      return h(React.Fragment, null, h('div', { style: { flex: 1, display: 'flex', minHeight: 0, background: 'var(--dsw-alias-bg-base)' } },
        h('div', { style: T.main },
          reviewFilterBar,
          err ? h('div', { style: { padding: 14, ...T.statusErr } },
            '试卷列表加载失败：' + err + ' ',
            h('button', { className: 'wf-btn', style: { ...T.btn, marginLeft: 10 }, onClick: loadBatches }, '重试')) : null,
          h('div', { className: 'wf-scroll', style: { ...T.scroll, padding: '12px 14px 16px' } },
            listState === 'loading' && baseList.length === 0
              ? h(EmptyState, { title: '正在加载试卷列表…' })
              : listState === 'error' && baseList.length === 0
                ? h(EmptyState, { tone: 'error', title: '暂时连不上后端，未能取到试卷列表。点上方「重试」再试一次。' })
                : list.length === 0
                  ? h(EmptyState, { title: emptyTitleMap[reviewFilter] || (hasActiveWork ? '排队区的试卷尚未处理完成，完成后会自动出现在这里。' : TEXT.noPaper) })
                  : list.map((b) => {
              const uploadedCard = isBatchUploaded(b);
              const anomalyCard = !uploadedCard && Boolean(b.has_anomaly || Number(b.anomaly_count || 0) > 0 || b.stage_blocked);
              return h('div', {
                key: b.name,
                className: 'wf-card-hover' + (anomalyCard ? ' wf-card-anomaly' : ''),
                style: {
                  ...T.cardClickable,
                  width: '94%',
                  marginLeft: 'auto',
                  marginRight: 'auto',
                  ...(uploadedCard ? { background: 'rgba(34, 197, 94, 0.06)', borderColor: 'rgba(34, 197, 94, 0.24)' } : (anomalyCard ? { background: 'rgba(220, 38, 38, 0.06)', borderColor: 'rgba(220, 38, 38, 0.28)' } : null)),
                },
                onClick: () => openBatch(b.name),
              },
              // 第一行：标题 + 细字体统计（无底色，仅“待审”带底色）
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 10 } },
                h('div', { style: { ...T.paperCardTitle, flex: 1, minWidth: 0 } }, b.paper?.title || b.name),
                uploadedCard
                  ? h('span', {
                    style: { flexShrink: 0, fontSize: 11, fontWeight: 600, color: '#ffffff', background: '#16a34a', border: '1px solid #16a34a', borderRadius: 999, padding: '2px 9px', whiteSpace: 'nowrap' },
                    title: b.upload_pending_confirmation ? '已上传至远端暂存，待远端确认发布' : '已上传到云端题库',
                  }, '已上传')
                  : null,
                h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexShrink: 0, fontSize: 11.5, fontWeight: 400, color: 'var(--dsw-alias-label-secondary)', whiteSpace: 'nowrap' } },
                  h('span', null, (b.unit_total ?? 0) + ' 题组'),
                  h('span', null, (b.question_total ?? 0) + ' 小题'),
                  (b.needs_human ?? 0) > 0
                    ? h('span', { style: { color: 'var(--dsw-alias-state-warn-primary)', background: 'rgba(230,162,60,.12)', borderRadius: 999, padding: '1px 8px' } }, (b.needs_human ?? 0) + ' 待审')
                    : h('span', null, '0 待审'),
                ),
              ),
              flowTimingText(b) ? h('div', { style: { ...T.hint, marginTop: 6, fontVariantNumeric: 'tabular-nums' } }, flowTimingText(b)) : null,
              b.review_status ? h('div', { style: { ...T.hint, marginTop: 6 } },
                (b.review_status.rounds || []).map((r) => r.name + (r.completed ? ('已完成 ' + r.reviewed + (r.round_id === 'sample' ? ' 抽样核对' : ' 单元')) : r.state === 'needs_refresh' ? '已有审核记录，待更新' : '尚未执行')).join(' · ')
                + (b.review_status.execution_completed ? (' · ' + (b.review_status.quality_passed ? '审核无未决项' : (b.review_status.open_findings + ' 项待处理差异'))) : '')
                + (b.review_status.excluded_units ? (' · 听力跳过 ' + b.review_status.excluded_units + ' 题组') : '')) : null,
              // 第二行：批次/阶段 + 操作按钮
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 } },
                h('div', { style: { ...T.paperCardMeta, flex: 1, minWidth: 0 } }, '批次：' + b.name + (b.stage?.hint ? ' · ' + b.stage.hint : '')),
                h('div', { style: { display: 'flex', gap: 6, flexShrink: 0, position: 'relative' } },
                  h('button', { className: 'wf-btn-primary', style: { ...T.miniBtnPrimary, color: 'var(--wf-brand)' }, onClick: (e) => { e.stopPropagation(); openBatch(b.name); } }, '进入审核'),
                  h('button', {
                    className: 'wf-btn', style: { ...T.miniBtn, padding: '3px 9px', fontSize: 15, lineHeight: 1, fontWeight: 700, color: 'var(--dsw-alias-label-secondary)' },
                    title: '更多操作', 'aria-label': '更多操作', 'aria-expanded': menuFor === b.name,
                    onClick: (e) => { e.stopPropagation(); setMenuFor(menuFor === b.name ? '' : b.name); },
                  }, '⋯'),
                  menuFor === b.name ? h('div', { style: { display: 'contents' } },
                    // 透明遮罩：点菜单外任意处即收起，且不触发卡片进入审核。
                    h('div', { style: { position: 'fixed', inset: 0, zIndex: 40 }, onClick: (e) => { e.stopPropagation(); setMenuFor(''); } }),
                    h('div', {
                      style: { position: 'absolute', right: 0, top: 'calc(100% + 6px)', zIndex: 41, minWidth: 132,
                        display: 'flex', flexDirection: 'column', gap: 2, padding: 5, borderRadius: 10,
                        background: 'var(--dsw-alias-bg-layer-1)', border: '1px solid var(--dsw-alias-border-l2)',
                        boxShadow: '0 10px 30px rgba(16,24,40,.16)' },
                      onClick: (e) => e.stopPropagation(),
                    },
                      [['重新标签', () => doRerun(b.name, 'tag'), false],
                       ['重新审核', () => doRerun(b.name, 'review'), false],
                       ["彻底删除", () => openDelete(b.name), true]].map(([label, fn, danger]) =>
                        h('button', {
                          key: label, className: 'wf-btn',
                          style: { ...T.miniBtn, textAlign: 'left', border: 'none', background: 'transparent',
                            color: danger ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-primary)' },
                          onClick: (e) => { e.stopPropagation(); setMenuFor(''); fn(); },
                        }, label))),
                  ) : null,
                ),
              ),
            ); }),
          ),
        ),
      ), deleteDialog, sourcePickerModal);
    }

