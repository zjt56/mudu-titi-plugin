    // ======================================================================
    // 审阅视图（原工作台三栏：单元列表 + 工作区原文/小题/标签/待人工）
    // ======================================================================
    function ReviewView(props) {
      const React = require('react');
      const h = React.createElement;
      const { batchName, unitId, rows, disableBatchBundle, hideListCol, disableKeyNav, hidePaperPreview, hideAiSupplement, contextPaper, onUnitMutated } = props;
      const isViewActive = props.isActive !== false;
      const writeCachedUnit = disableBatchBundle ? setTypeCachedUnit : setCachedUnit;
      const onSelectUnit = props.onSelectUnit || (() => {});
      const cacheKey = String(batchName || '') + '\0' + String(unitId || '');
      const initialCached = getCachedUnit(batchName, unitId);
      const [detailPair, setDetailPair] = React.useState(() => initialCached ? { key: cacheKey, data: initialCached } : null);
      const detail = (detailPair && detailPair.key === cacheKey) ? detailPair.data : getCachedUnit(batchName, unitId);
      const [labels, setLabels] = React.useState(() => initialCached ? (initialCached.labels || {}) : null);
      const [busy, setBusy] = React.useState('');
      const [err, setErr] = React.useState('');
      const [filter, setFilter] = React.useState('all'); // all | open | blocked | unverified
      const [q, setQ] = React.useState('');
      const [labelDlg, setLabelDlg] = React.useState(false);
      const [labelError, setLabelError] = React.useState('');
      const [labelNotice, setLabelNotice] = React.useState('');
      const [content, setContent] = React.useState(() => initialCached ? makeContentSnapshot(initialCached) : null);
      const [contentOriginal, setContentOriginal] = React.useState(() => initialCached ? makeContentSnapshot(initialCached) : null);
      const [contentDlg, setContentDlg] = React.useState(false); // 「原卷修改」弹窗
      const [contentError, setContentError] = React.useState('');
      const [aiBusy, setAiBusy] = React.useState('');
      const [aiNotice, setAiNotice] = React.useState('');
      const [aiModal, setAiModal] = React.useState(null);
      const [aiModalOpen, setAiModalOpen] = React.useState(false);
      const [contentEditDlg, setContentEditDlg] = React.useState(false);
      const [contentEditError, setContentEditError] = React.useState('');
      const [paperDlg, setPaperDlg] = React.useState(false);
      const [paperData, setPaperData] = React.useState(null);
      const [paperLoading, setPaperLoading] = React.useState(false);
      const [paperError, setPaperError] = React.useState('');
      const contentSave = React.useRef(null);
      const contentPatch = React.useMemo(() => buildContentPatch(content, contentOriginal), [content, contentOriginal]);
      const contentDirty = !!contentPatch;

      const requestGate = React.useRef(null);
      if (!requestGate.current) requestGate.current = createRequestGate();
      const mounted = React.useRef(false);
      const listScrollRef = React.useRef(null);
      const applyDetail = React.useCallback((d) => {
        setDetailPair(d ? { key: cacheKey, data: d } : null);
        setLabels(d ? (d.labels || {}) : null);
        if (d) initContent(d);
        else { setContent(null); setContentOriginal(null); }
      }, [cacheKey]);
      const currentRow = React.useMemo(
        () => (Array.isArray(rows) ? rows.find((r) => r && String(r.unit_id || r.number) === String(unitId)) : null),
        [rows, unitId]
      );
      const currentRowRef = React.useRef(currentRow);
      currentRowRef.current = currentRow;
      const currentRowKey = currentRow
        ? (String(currentRow.state || '') + ':' + (currentRow.needs_human ? '1' : '0') + ':' + (currentRow.is_anomaly ? '1' : '0') + ':' + String(currentRow.open_findings || 0) + ':' + String(currentRow.anomaly_summary || ''))
        : '';
      const load = React.useCallback(async (opts) => {
        if (!mounted.current) return;
        if (!unitId || unitId === '_') return; // 等待真实首题 ID，避免无意义的 /units/_ 404 往返
        const force = !!(opts && opts.force);
        const silent = !!(opts && opts.silent);
        const request = requestGate.current.begin();
        if (!silent) setErr('');
        if (!force) {
          const cached = getCachedUnit(batchName, unitId);
          if (cached && !isCachedUnitStale(cached, currentRowRef.current)) {
            applyDetail(cached);
            setErr('');
            return;
          }
          // 若悬停/列表预取已在途，给它极短宽限（120ms）直接复用；若超时未回，立即改发轻量单题请求，
          // 避免整卷打包拖慢首题与「标签审核」的首次渲染。
          const existingEntry = UNIT_CACHE.get(String(batchName || ''));
          if (existingEntry && existingEntry.promise) {
            const fromBundle = await Promise.race([
              existingEntry.promise.then(() => getCachedUnit(batchName, unitId)).catch(() => null),
              new Promise((resolve) => setTimeout(() => resolve(null), 120)),
            ]);
            if (!request.current()) return;
            if (fromBundle && !isCachedUnitStale(fromBundle, currentRowRef.current)) {
              applyDetail(fromBundle);
              setErr('');
              return;
            }
          }
        }
        // 冷启动/网络抖动时单次失败会让题目区空白：对可重试错误做几次短退避重试，
        // 每次都以 requestGate 的 current() 为准，切题即作废旧请求。
        const delays = [0, 500, 1200, 2400];
        let lastErr = null;
        for (let attempt = 0; attempt < delays.length; attempt++) {
          if (!request.current()) return;
          if (delays[attempt]) await new Promise((r) => setTimeout(r, delays[attempt]));
          if (!request.current()) return;
          try {
            const d = await jget('/batches/' + encodeURIComponent(batchName) + '/units/' + encodeURIComponent(unitId), request.signal);
            if (!request.current()) return;
            writeCachedUnit(batchName, unitId, d);
            applyDetail(d);
            setErr('');
            if (!force && !disableBatchBundle) loadBatchUnitsBundle(batchName).catch(() => {});
            return;
          } catch (e) {
            if (!request.current()) return;
            lastErr = e;
            const msg = String((e && e.message) || e);
            if (/\b(400|401|403|404)\b/.test(msg)) break; // 非瞬时错误，不重试
          }
        }
        if (request.current() && !silent) setErr(String((lastErr && lastErr.message) || lastErr || '加载失败'));
      }, [batchName, unitId, applyDetail, disableBatchBundle]);
      // 进卷后台预加载整卷全部单元与题图：若当前题已在缓存或整卷已在途则立即执行，
      // 否则稍作让路（100ms），避免冷启动时整卷请求与首题标签审核抢占后端。
      // 题型审核跨批次分页模式（disableBatchBundle=true）严禁整卷预拉取，避免大批缓存膨胀。
      React.useEffect(() => {
        if (disableBatchBundle) return undefined;
        let cancelled = false;
        const startBundle = () => {
          if (cancelled) return;
          loadBatchUnitsBundle(batchName).then((entry) => {
            if (cancelled || !entry) return;
            const cur = entry.units.get(String(unitId));
            const orderedUrls = [];
            if (cur) orderedUrls.push(...collectBlockImageUrls(cur.preview_blocks));
            for (const uid of (entry.order || [])) {
              if (String(uid) === String(unitId)) continue;
              const u = entry.units.get(String(uid));
              if (u) orderedUrls.push(...collectBlockImageUrls(u.preview_blocks));
            }
            prefetchImages(orderedUrls, 4);
          }).catch(() => {});
        };
        const existingEntry = UNIT_CACHE.get(String(batchName || ''));
        if (getCachedUnit(batchName, unitId) || (existingEntry && (existingEntry.loaded || existingEntry.promise))) {
          startBundle();
          return () => { cancelled = true; };
        }
        const timer = setTimeout(startBundle, 400);
        return () => { cancelled = true; clearTimeout(timer); };
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, [batchName, disableBatchBundle]);
      React.useEffect(() => {
        mounted.current = true;
        const cached = getCachedUnit(batchName, unitId);
        if (cached && !isCachedUnitStale(cached, currentRowRef.current)) {
          applyDetail(cached);
          setErr('');
        } else {
          if (cached) {
            applyDetail(cached);
            invalidateBatchUnits(batchName);
            if (!disableBatchBundle) loadBatchUnitsBundle(batchName, { force: true }).catch(() => {});
          }
          load({ force: !!cached, silent: !!cached });
        }
        return () => { mounted.current = false; requestGate.current.cancel(); };
      }, [batchName, unitId, currentRowKey, applyDetail, load, disableBatchBundle]);
      React.useEffect(() => { setLabelNotice(''); setLabelError(''); setLabelDlg(false); setContentDlg(false); setAiNotice(''); setContentEditDlg(false); setContentEditError(''); setBusy(''); setErr(''); }, [batchName, unitId]);
      // 仅在当前视图可见且批次仍在后台运行时才每 8 秒轮询；隐藏页签时彻底暂停轮询与窗口聚焦监听。
      const lastFocusRefreshRef = React.useRef(0);
      const hasBgWork = Boolean((detail && detail.stage && detail.stage.running) || aiBusy);
      React.useEffect(() => {
        if (!isViewActive || contentDirty || labelDlg || contentDlg || busy) return;
        const refresh = () => {
          if (typeof document === 'undefined' || document.visibilityState !== 'hidden') load({ force: true, silent: true });
        };
        const onFocus = () => {
          const now = Date.now();
          if (now - lastFocusRefreshRef.current < 15000) return;
          lastFocusRefreshRef.current = now;
          refresh();
        };
        const timer = hasBgWork ? setInterval(refresh, 8000) : null;
        if (typeof window !== 'undefined') window.addEventListener('focus', onFocus);
        return () => {
          if (timer) clearInterval(timer);
          if (typeof window !== 'undefined') window.removeEventListener('focus', onFocus);
        };
      }, [isViewActive, load, contentDirty, labelDlg, contentDlg, busy, hasBgWork]);
      // 媒体 origin 异步到达后触发一次重渲染，使图片/音频切到绝对 URL。
      const [, _bumpOrigin] = React.useState(0);
      React.useEffect(() => { ensureMediaOrigin().then(() => _bumpOrigin((x) => x + 1)); }, []);

      const tax = (detail && detail.taxonomy) || {};
      const subs = (detail && detail.subquestions) || [];
      const findings = (detail && detail.label_findings) || [];
      const fmtFindings = (detail && detail.format_findings) || [];
      // 一个单元只做一次知识点标签比对，因此只呈现一条「待人工修改」。
      // 旧协议记录会按每个小题、每个字段各拆一条（十几条其实只是同一次比对），
      // single=true 时整单元合并为一条；single=false 时格式疑点按类型归并。
      // 两种模式都保留全部 finding_id，一次决定覆盖整组，绝不丢证据、不伪造通过。
      function consolidateFindings(list, single) {
        const out = [];
        const byKey = new Map();
        for (const f of (list || [])) {
          const raw = String(f.question || f.message || f.reason || f.field || '').trim();
          const head = raw.split(/[：:]/)[0].replace(/^第\s*\d+\s*题/, '').trim();
          const field = String(f.field || '').trim().toLowerCase();
          const key = single ? 'single' : (field || 'other') + '|' + (head || raw);
          let g = byKey.get(key);
          if (!g) {
            g = { key, ids: [], items: [], numbers: [], fields: [],
                  head: single ? '标签比对差异' : (head || raw || f.field || '待人工确认') };
            byKey.set(key, g); out.push(g);
          }
          if (f.finding_id) g.ids.push(f.finding_id);
          g.items.push(f);
          if (field && g.fields.indexOf(field) < 0) g.fields.push(field);
          const m = raw.match(/第\s*(\d+)\s*题/);
          if (m && g.numbers.indexOf(m[1]) < 0) g.numbers.push(m[1]);
        }
        for (const g of out) g.numbers.sort((a, b) => (Number(a) || 0) - (Number(b) || 0));
        return out;
      }
      const groupedFindings = consolidateFindings(findings, true);
      const groupedFmt = consolidateFindings(fmtFindings, false);

      function setL(key, val) { setLabels((x) => ({ ...(x || {}), [key]: val })); }
      async function saveLabels() {
        setBusy('save');
        setLabelError('');
        setLabelNotice('');
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/units/' + encodeURIComponent(unitId), { labels });
          setLabelDlg(false);
          invalidateBatchUnits(batchName);
          await load({ force: true, silent: true });
          setLabelNotice('标签修改已保存');
          if (typeof onUnitMutated === 'function') onUnitMutated();
        } catch (e) { setLabelError(String(e.message || e)); }
        finally { setBusy(''); }
      }

      React.useEffect(() => {
        if (hideAiSupplement) return undefined;
        let cancelled = false;
        setAiBusy('');
        setAiNotice('');
        setAiModal(null);
        setAiModalOpen(false);
        jget('/batches/' + encodeURIComponent(batchName) + '/ai-supplement').then((st) => {
          if (cancelled || !st || st.status !== 'running') return;
          setAiBusy(String(st.mode || 'running'));
          setAiModal(st);
        }).catch(() => {});
        return () => { cancelled = true; };
      }, [batchName, hideAiSupplement]);

      React.useEffect(() => {
        if (!aiModal || aiModal.status !== 'running') return undefined;
        let cancelled = false;
        const poll = async () => {
          try {
            const st = await jget('/batches/' + encodeURIComponent(batchName) + '/ai-supplement');
            if (cancelled) return;
            setAiModal(st);
            if (st.status === 'done') {
              setAiBusy('');
              setAiNotice(String(st.message || (st.applied ? `✓ 已生成 ${st.applied} 项（已标注 AI 生成）` : '无可补充缺项')));
              invalidateBatchUnits(batchName);
              if (!disableBatchBundle) loadBatchUnitsBundle(batchName, { force: true }).catch(() => {});
              load({ force: true, silent: true });
            } else if (st.status === 'error') {
              setAiBusy('');
              setAiNotice('补充失败：' + String(st.error || st.message || '未知错误'));
            } else if (st.status !== 'running') {
              setAiBusy('');
            }
          } catch (e) {
            if (!cancelled) {
              setAiBusy('');
              setAiModal((prev) => prev ? { ...prev, status: 'error', error: String(e.message || e) } : null);
            }
          }
        };
        const timer = setInterval(poll, 900);
        return () => { cancelled = true; clearInterval(timer); };
      }, [aiModal?.status, batchName, load]);

      async function openPaperPreview() {
        setPaperDlg(true);
        setPaperLoading(true);
        setPaperError('');
        try {
          const res = await jget('/batches/' + encodeURIComponent(batchName) + '/paper-preview');
          setPaperData(res);
        } catch (e) {
          setPaperError(String(e.message || e));
        } finally {
          setPaperLoading(false);
        }
      }

      async function saveContentEdit(patch) {
        if (busy) return;
        setBusy('contentEdit');
        setContentEditError('');
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/content-edit', patch);
          setContentEditDlg(false);
          setLabelNotice('已保存文章大意/答案解析');
          await load({ force: true, silent: true });
          if (typeof onUnitMutated === 'function') onUnitMutated();
        } catch (e) {
          setContentEditError(String(e.message || e));
        } finally {
          setBusy('');
        }
      }

      async function runAiSupplement(mode) {
        if (aiBusy) {
          if (aiModal) setAiModalOpen(true);
          return;
        }
        setAiBusy(mode);
        setAiNotice('');
        setAiModal({ status: 'running', mode, total: 0, completed: 0, applied: 0, message: '正在扫描并启动生成…', error: '' });
        setAiModalOpen(true);
        try {
          const res = await jpost('/batches/' + encodeURIComponent(batchName) + '/ai-supplement', { mode });
          setAiModal(res);
          if (res.status === 'done') {
            setAiBusy('');
            setAiNotice(String(res.message || (res.applied ? `✓ 已生成 ${res.applied} 项（已标注 AI 生成）` : '无可补充缺项')));
            invalidateBatchUnits(batchName);
            if (!disableBatchBundle) loadBatchUnitsBundle(batchName, { force: true }).catch(() => {});
            await load({ force: true, silent: true });
          } else if (res.status === 'error') {
            setAiBusy('');
            setAiNotice('补充失败：' + String(res.error || res.message || '未知错误'));
          }
        } catch (e) {
          setAiBusy('');
          setAiModal({ status: 'error', mode, total: 0, completed: 0, applied: 0, message: '生成失败', error: String(e.message || e) });
          setAiNotice('补充失败：' + String(e.message || e));
        }
      }

      // ---- 内容编辑：题干/选项/答案（逐小题）+ 正文/解析（整组共用）----
      // 选项 [{label,text}] ↔ 多行文本，编辑时不显示 [object Object]，保存时保留字母标签。
      function optionsToText(options) {
        if (!Array.isArray(options)) return typeof options === 'string' ? options : '';
        return options.map((o) => {
          if (o && typeof o === 'object') {
            const label = String(o.label || '').replace(/[.．、]\s*$/, '');
            const text = o.text != null ? o.text : (o.content != null ? o.content : '');
            return label ? (label + '. ' + text) : String(text);
          }
          return String(o);
        }).join('\n');
      }
      function textToOptions(text, original) {
        const lines = String(text || '').split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
        return lines.map((line, index) => {
          const m = line.match(/^([A-Ha-h])\s*[.．、:：]\s*(.*)$/);
          if (m) return { label: m[1].toUpperCase(), text: m[2] };
          const old = Array.isArray(original) ? original[index] : null;
          const label = old && typeof old === 'object' && old.label ? old.label : String.fromCharCode(65 + index);
          return { label, text: line };
        });
      }
      // 保留加载时显示的字符串，保存时只比较实际改动，不重写未触碰的字段（原卷弹窗仅维护材料与小题）。
      function makeContentSnapshot(d) {
        const item = (d && d.item) || {};
        return {
          material: item.material != null ? String(item.material) : '',
          subs: ((d && d.subquestions) || []).map((s) => ({
            number: s.number,
            display_number: s.display_number,
            stem: s.stem != null ? String(s.stem) : '',
            optionsText: optionsToText(s.options),
            options: s.options,
            answer: s.answer != null ? String(s.answer) : '',
          })),
        };
      }
      function initContent(d) {
        const snapshot = makeContentSnapshot(d);
        setContent(snapshot);
        setContentOriginal(snapshot);
      }
      function buildContentPatch(edited, original) {
        if (!edited || !original) return null;
        const unit = {};
        const expected = {};
        for (const key of ['material']) {
          if (edited[key] !== original[key]) {
            unit[key] = edited[key];
            expected[key] = original[key];
          }
        }
        const items = [];
        edited.subs.forEach((sub, i) => {
          const before = original.subs[i];
          const item = { number: sub.number };
          if (sub.stem !== before.stem) item.stem = sub.stem;
          if (sub.optionsText !== before.optionsText) item.options = textToOptions(sub.optionsText, before.options);
          if (sub.answer !== before.answer) item.answer = sub.answer;
          if (Object.keys(item).length > 1) items.push(item);
        });
        const patch = {};
        if (Object.keys(unit).length) Object.assign(patch, { unit, expected });
        if (items.length) patch.items = items;
        return Object.keys(patch).length ? patch : null;
      }
      function setSubField(idx, key, val) {
        if (contentSave.current) return;
        setContent((c) => {
          const subs = c.subs.slice();
          subs[idx] = { ...subs[idx], [key]: val };
          return { ...c, subs };
        });
      }
      function setUnitField(key, val) {
        if (contentSave.current) return;
        setContent((c) => ({ ...c, [key]: val }));
      }
      function openContent() {
        // 已发出的后台刷新也不能替换本次编辑的基线。
        requestGate.current.cancel();
        setContentError('');
        setContentDlg(true);
      }
      function closeContent() {
        if (contentSave.current) return;
        setContent(contentOriginal);
        setContentError('');
        setContentDlg(false);
        load({ force: true, silent: true });
      }
      async function saveContent() {
        if (!contentPatch || contentSave.current) return;
        const request = {};
        contentSave.current = request;
        setBusy('content');
        setContentError('');
        setLabelNotice('');
        try {
          const saved = await jpost('/batches/' + encodeURIComponent(batchName) + '/units/' + encodeURIComponent(unitId), contentPatch);
          if (contentSave.current !== request) return;
          const formal = saved.applied?.applied;
          const draft = contentPatch.items || Object.hasOwn(contentPatch.unit || {}, 'material');
          if (formal) {
            setLabelNotice('正式内容已保存；体检、审核与上传需要重新处理'
              + (draft ? '。材料、题干、选项或答案的改动仅保存为草稿' : ''));
          } else if (draft) {
            setLabelNotice('改动仅保存为草稿，未写入正式批次');
          } else {
            setLabelNotice('正式内容未变化');
          }
          setContentDlg(false);
          load({ force: true, silent: true });
          if (typeof onUnitMutated === 'function') onUnitMutated();
        } catch (e) {
          if (contentSave.current === request) setContentError(String(e.message || e));
        } finally {
          if (contentSave.current === request) {
            contentSave.current = null;
            setBusy('');
          }
        }
      }
      async function decide(f, status) {
        // 合并后的卡片携带整组 finding_id：逐条落库，仍由后端各自校验与记决定日志。
        const ids = (Array.isArray(f.ids) && f.ids.length) ? f.ids : [f.finding_id];
        try {
          for (const id of ids) {
            if (!id) continue;
            await jpost('/batches/' + encodeURIComponent(batchName) + '/decisions', {
              item: { finding_id: id, status, note: f.resolution_note || '' },
            });
          }
          await load({ force: true, silent: true });
          if (typeof onUnitMutated === 'function') onUnitMutated();
        } catch (e) { setErr(String(e.message || e)); }
      }

      // ---- 单元列表：过滤 + 搜索 ----
      const allRows = rows || [];
      const filteredRows = React.useMemo(() => {
        return allRows.filter((r) => {
          const rowAnomaly = Boolean(r && !r.excluded && (r.is_anomaly || r.state === 'blocked' || r.verdict === 'error' || r.severity === 'blocker' || (r.format_count || 0) > 0 || !String(r.type || '').trim()));
          if (filter === 'open' && !r.needs_human && !rowAnomaly) return false;
          if (q) {
            const hay = (String(r.label || '') + String(r.number || '') + String(r.type || '') + String((r.knowledge || []).join(' '))).toLowerCase();
            if (!hay.includes(q.toLowerCase())) return false;
          }
          return true;
        });
      }, [allRows, filter, q]);

      // 题组列表滚动跟随当前题：↑/↓ 切题时把选中行滚进可视区（只滚最小距离），
      // 不再整体回到顶部。仅作用于左侧列表容器，不影响右侧预览/审核区。
      React.useEffect(() => {
        const box = listScrollRef.current;
        if (!box) return;
        let row = null;
        for (const el of box.querySelectorAll('[data-unit]')) {
          if (el.getAttribute('data-unit') === String(unitId)) { row = el; break; }
        }
        if (!row) return;
        const br = row.getBoundingClientRect();
        const bc = box.getBoundingClientRect();
        if (br.top < bc.top + 4) box.scrollTop -= (bc.top - br.top) + 8;
        else if (br.bottom > bc.bottom - 4) box.scrollTop += (br.bottom - bc.bottom) + 8;
      }, [unitId, filteredRows]);

      // 兜底确保当前题的图片（含表格单元格配图，排除音频）已进入缓存。
      React.useEffect(() => {
        if (!detail) return;
        prefetchImages(collectBlockImageUrls(detail.preview_blocks), 4);
      }, [detail, unitId]);

      // ---- 键盘 ↑/↓（或 j/k）切换题组；输入框内不拦截 ----
      React.useEffect(() => {
        if (disableKeyNav) return undefined;
        function onKey(e) {
          if (e.metaKey || e.ctrlKey || e.altKey) return;
          const tag = (e.target && e.target.tagName || '').toLowerCase();
          if (tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target && e.target.isContentEditable)) return;
          if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'j' && e.key !== 'k') return;
          const list = filteredRows;
          if (!list.length) return;
          let idx = list.findIndex((r) => (r.unit_id || r.number) === unitId);
          if (idx < 0) idx = 0;
          const delta = (e.key === 'ArrowDown' || e.key === 'j') ? 1 : -1;
          const next = list[Math.min(list.length - 1, Math.max(0, idx + delta))];
          if (next && (next.unit_id || next.number) !== unitId) {
            e.preventDefault();
            onSelectUnit(next.unit_id || next.number);
          } else if (next) {
            e.preventDefault();
          }
        }
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
      }, [filteredRows, unitId, onSelectUnit, disableKeyNav]);

      const sel = (v) => ({ value: v || '', onChange: (e) => { /* handled by caller */ } });

      // 原文预览块 -> React
      // 选项标签：形如 "A." / "\tB." / "（C）" 的短文本。
      function optionLabel(text) {
        const t = String(text == null ? '' : text).replace(/<[^>]*>/g, '').replace(/\*/g, '').replace(/[\s\u3000]+/g, '');
        return /^[A-Ha-h][.．、)）]?$/.test(t) || /^[（(][A-Ha-h][)）]$/.test(t) ? t : null;
      }
      function optionLetter(label) {
        const match = String(label || '').match(/[A-H]/i);
        return match ? match[0].toUpperCase() : '';
      }
      function renderBlocks(blocks, bulkBold) {
        const list = blocks || [];
        const out = [];
        const isOptLabel = (b) => b && b.type === 'text' && optionLabel(b.text) !== null;
        const isImg = (b) => b && b.type === 'image';
        // 选项组识别：以选项标签 A/B/C/D 为锚，图跟着标签走（letter 由标签决定）。
        // 先聚出「连续选项段」：标签之间可夹图；再判断该段是图在前(`[IMG]A.`)还是
        // 图在后(`A.[IMG]`)，统一配对，避免夹在选项间的图被错判。
        const labelToImg = new Map();   // 块索引(标签) → 图块
        const consumedImg = new Set();  // 已被选项消费的图块索引
        // 判定每个选项标签配哪张图：先看紧邻前块是否为「尚未被消费」的图（图在前形态
        // `[IMG]A.`），是则归本标签；否则看紧邻后块是否为图（图在后形态 `A.[IMG]`）。
        // 按块顺序遍历，前图先被左侧标签消费，右侧重复的图（本属下一选项）自然跳过。
        for (let k = 0; k < list.length; k++) {
          if (!isOptLabel(list[k])) continue;
          if (isImg(list[k - 1]) && !consumedImg.has(k - 1)) {
            labelToImg.set(k, list[k - 1]); consumedImg.add(k - 1);
          } else if (isImg(list[k + 1]) && !consumedImg.has(k + 1)) {
            labelToImg.set(k, list[k + 1]); consumedImg.add(k + 1);
          }
        }
        for (let i = 0; i < list.length; i++) {
          const b = list[i];
          if (consumedImg.has(i)) continue;  // 该图已作为选项图渲染
          // 以连续的「有图的选项标签」组建成一排。
          if (isOptLabel(b) && labelToImg.has(i)) {
            const cells = [];
            let j = i;
            while (j < list.length && isOptLabel(list[j]) && labelToImg.has(j)) {
              const lab = optionLabel(list[j].text);
              cells.push({ label: lab, letter: optionLetter(lab), block: labelToImg.get(j) });
              j++;
              // 跳过夹在选项之间的「已作为某选项图消费」的图块，直到下一个选项标签。
              while (j < list.length && consumedImg.has(j) && isImg(list[j])) j++;
            }
            if (cells.length >= 1) {
              out.push(h('div', { key: 'optrow:' + i, className: 'wf-opt-row' },
                cells.map((c, ci) => {
                  const optionIndex = Math.max(0, (c.letter || 'A').charCodeAt(0) - 65);
                  return h('div', {
                    key: (c.letter || ci) + ':' + ci,
                    className: 'wf-opt-cell',
                    style: { gridColumn: String((optionIndex % 4) + 1), gridRow: String(Math.floor(optionIndex / 4) + 1) },
                  },
                    h('span', { className: 'wf-opt-label' }, c.label),
                    h('div', { className: 'wf-opt-frame' },
                      h(PreviewImage, { url: c.block.url, name: c.block.name })));
                })));
              i = j - 1;
              continue;
            }
          }
          if (b.type === 'text') {
            const display = paperDisplayText(b.text, bulkBold);
            if (bulkBold && !display.trim() && String(b.text || '').trim()) continue;
            const heading = isHeading(display);
            out.push(h('div', {
              key: i,
              className: 'wf-line' + (heading ? ' wf-heading' : ''),
              dangerouslySetInnerHTML: { __html: markLine(display) },
            }));
            continue;
          }
          if (b.type === 'image') {
            out.push(h('div', { key: 'body-image:' + String(b.url || b.name || '') + ':' + i, className: 'wf-body-image-frame' },
              h(PreviewImage, { url: b.url, name: b.name })));
            continue;
          }
          if (b.type === 'table') {
            // 有 Word 合并跨度时按真实 rowspan/colspan 渲染；没有就退回平面网格。
            const structured = Array.isArray(b.structured) ? b.structured : null;
            const cellImages = b.cellImages || [];
            const cellSpans = b.cellSpans || [];
            const rows = [];
            if (structured) {
              structured.forEach((row) => {
                const cells = (row.cells || []).map((cell) => ({
                  text: String(cell.text || ''),
                  images: cell.images || [],
                  rowspan: Number(cell.rowspan) || 1,
                  colspan: Number(cell.colspan) || 1,
                  header: !!cell.header,
                }));
                // 纯平面行沿用原来的短文本表头判定；有合并跨度的行不靠猜。
                const plain = cells.every((cell) => cell.rowspan === 1 && cell.colspan === 1);
                if (plain && isHeaderRow(cells.map((cell) => cell.text))) {
                  cells.forEach((cell) => { cell.header = true; });
                }
                rows.push(cells);
              });
            } else {
              (b.rows || []).forEach((row, ri) => {
                const head = isHeaderRow(row);
                const rowCells = cellImages[ri] || [];
                rows.push((row || []).map((cell, ci) => ({
                  text: cell == null ? '' : String(cell),
                  images: (rowCells[ci] || []).filter((im) => im && im.url),
                  rowspan: Number.isInteger((cellSpans[ri] || [])[ci]?.rowspan) ? cellSpans[ri][ci].rowspan : 1,
                  colspan: Number.isInteger((cellSpans[ri] || [])[ci]?.colspan) ? cellSpans[ri][ci].colspan : 1,
                  header: head,
                })));
              });
            }
            out.push(h('div', { key: i, style: { margin: '8px 0', maxWidth: '100%', minWidth: 0 } },
              h('table', { className: 'wf-table' },
                h('tbody', null, rows.map((row, ri) => h('tr', { key: ri }, row.map((cell, ci) => {
                  // 单元格内的图片与该单元格文字一起显示，图片在前、文字在后。
                  const imgs = (cell.images || []).filter((im) => im && im.url);
                  const children = [];
                  imgs.forEach((im, k) => children.push(
                    h('span', { key: 'img' + k, className: 'wf-table-thumb' },
                      h(PreviewImage, { url: im.url, name: im.name }))));
                  if (cell.text !== '') children.push(
                    h('span', { key: 'txt', dangerouslySetInnerHTML: { __html: markLine(cell.text, bulkBold) } }));
                  const props = { key: ci, className: imgs.length ? 'wf-table-imgcell' : undefined };
                  if (cell.rowspan > 1) props.rowSpan = cell.rowspan;
                  if (cell.colspan > 1) props.colSpan = cell.colspan;
                  return cell.header ? h('th', props, children) : h('td', props, children);
                })))))));
            continue;
          }
        }
        return out;
      }

      // 预览小字标签：灰色小标 + 值
      function plItem(k, v) {
        if (v === null || v === undefined || v === '') return null;
        return h('span', { className: 'wf-preitem' },
          h('span', { style: { color: 'var(--dsw-alias-label-secondary)', opacity: .8, marginRight: 3 } }, k),
          h('span', { style: { color: 'var(--dsw-alias-label-primary)' } }, String(v)));
      }

      // ---- 左栏标签审核面板：知识点对比 + 难度/来源/分类对比 ----
      // 取某一轮（一审/二审）的单元级 assessment；未执行审核时返回 null。
      function roundAssessment(comparison, roundId) {
        const rounds = (comparison && comparison.rounds) || [];
        const r = rounds.find((x) => x.round_id === roundId);
        if (!r) return null;
        const checks = (r.checks || []).filter((c) => c && c.assessment && Object.keys(c.assessment).length);
        // 二审按小题记录，不能过滤带 number 的 check，也不能把一条抽样结论当作整组结论。
        const unit = checks.find((c) => c.number == null);
        return { status: r.status, assessment: unit ? unit.assessment : null, checks, unit };
      }
      // 比较与后端 review_consensus._normal 同源：完整路径集合，忽略顺序和重复。
      function labelSetKey(value) {
        return JSON.stringify(Array.isArray(value) ? [...new Set(value)].sort() : value);
      }
      // 把知识点数组取末级短名，方便对照。
      function kpShort(list) {
        if (!Array.isArray(list)) return String(list == null ? '' : list);
        return list.map((x) => {
          const s = String(x == null ? '' : x).trim();
          const parts = s.split(/[\/>＞]/).map((p) => p.trim()).filter(Boolean);
          return parts.length > 1 ? parts[parts.length - 1] : s;
        }).filter(Boolean);
      }
      function answerPointDiff(check) {
        const expected = check?.expected_answer_points;
        const reviewed = check?.assessment?.answer_points;
        return Array.isArray(reviewed) && Array.isArray(expected) && expected.length > 0
          && labelSetKey(expected) !== labelSetKey(reviewed);
      }
      function reviewedLabels(check) {
        const assessment = check?.assessment;
        if (!assessment) return null;
        return [...new Set([...(assessment.knowledge || []),
          ...(assessment.answer_points || []).map((name) => '词汇/答案考点/' + name)])];
      }
      // 每一块：左对齐次级标题 + 轻量标签胶囊流；仅将存在差异的标签标红高亮，一致的保持中性色。
      // 「标签合并」与上方各行样式一致，不加底色；仅人工修改时显示「已修改」。
      function cmpRow(label, value, opts) {
        const o = opts || {};
        const empty = value == null || value === '' || (Array.isArray(value) && !value.length);
        const isList = !empty && Array.isArray(value);
        const text = empty ? (o.pending ? '待审核' : (o.mismatch ? '（漏标）' : '—')) : (Array.isArray(value) ? value.join('、') : String(value));
        const color = (!isList && o.mismatch)
          ? 'var(--dsw-alias-state-error-primary)'
          : (empty ? 'var(--dsw-alias-label-secondary)' : 'var(--dsw-alias-label-primary)');
        const title = Array.isArray(o.titles) && o.titles.length ? o.titles.join('、') : undefined;
        let valueNode;
        if (isList) {
          const chips = value.map((item, idx) => {
            const fullPath = Array.isArray(o.titles) && o.titles[idx] != null ? o.titles[idx] : item;
            const isDiff = o.diffPaths instanceof Set && o.diffPaths.has(fullPath);
            return h('span', {
              key: String(fullPath) + ':' + idx,
              title: fullPath,
              style: {
                display: 'inline-flex',
                alignItems: 'center',
                maxWidth: '100%',
                padding: '2px 8px',
                borderRadius: 6,
                fontSize: 12,
                lineHeight: 1.45,
                wordBreak: 'break-word',
                fontWeight: isDiff ? 600 : 400,
                color: isDiff ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-primary)',
                background: isDiff
                  ? 'color-mix(in srgb, var(--dsw-alias-state-error-primary) 10%, var(--dsw-alias-bg-base))'
                  : 'var(--dsw-alias-bg-layer-2)',
                border: '1px solid ' + (isDiff
                  ? 'color-mix(in srgb, var(--dsw-alias-state-error-primary) 42%, var(--dsw-alias-border-l2))'
                  : 'var(--dsw-alias-border-l1)'),
              },
            }, item);
          });
          valueNode = h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 5, width: '100%', minWidth: 0 } }, ...chips);
        } else {
          valueNode = h('div', {
            title,
            style: { width: '100%', minWidth: 0, fontSize: 12, lineHeight: 1.5, color, fontWeight: o.mismatch ? 600 : 400, wordBreak: 'break-word' },
          }, text);
        }
        const statusTag = o.isFinal
          ? (o.modified
              ? h('span', { style: { flex: '0 0 auto', fontSize: 11, fontWeight: 600, color: 'var(--dsw-alias-state-warn-primary)' } }, '已修改')
              : null)
          : null;
        const headRow = h('div', {
          style: {
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 6,
            width: '100%',
            fontSize: 11.5,
            fontWeight: 500,
            lineHeight: 1.4,
            color: 'var(--dsw-alias-label-secondary)',
          },
        },
          h('span', null, label),
          statusTag,
        );
        return h('div', {
          style: { display: 'flex', flexDirection: 'column', alignItems: 'stretch', gap: 5, marginBottom: 10 },
        }, headRow, valueNode);
      }
      function labelAuditPanels(detail) {
        const comp = detail.comparison || {};
        const orig = (comp.original && comp.original.labels) || {};
        const cur = detail.labels || {};
        const r1 = roundAssessment(comp, 'labels');   // 一审
        const kpSame = (a, b) => labelSetKey(a) === labelSetKey(b);
        const cleanPaths = (list) => Array.isArray(list) ? list.filter((x) => String(x == null ? '' : x).trim()) : list;

        // 比较保留完整路径；同名叶子也必须能看出所属目录分支。
        const hasOriginalKnowledge = Array.isArray(orig.knowledge);
        const kpOrig = hasOriginalKnowledge ? orig.knowledge : (cur.knowledge || []);
        const kp1 = r1 && r1.assessment ? r1.assessment.knowledge : null;
        const mergedInfo = comp.merged && typeof comp.merged === 'object' ? comp.merged : null;
        const autoMerged = mergedInfo && Array.isArray(mergedInfo.auto_merged)
          ? mergedInfo.auto_merged
          : [...new Set([...kpOrig, ...(Array.isArray(kp1) ? kp1 : [])])];
        const kpModified = !!(cur.knowledge_manually_saved || (mergedInfo && mergedInfo.manually_edited));
        const answerPaths = [
          ...(cur.knowledge || []).filter((path) => path.startsWith('词汇/答案考点/')),
          ...(cur.answer_point_tags || []).map((name) => '词汇/答案考点/' + name),
        ];
        const displayedOrigPaths = cleanPaths([...new Set([...kpOrig, ...answerPaths])]);
        const origPathSet = new Set([...kpOrig, ...answerPaths]);
        function reviewedDiffSet(check, reviewedPaths) {
          if (!Array.isArray(reviewedPaths) || !reviewedPaths.length) return null;
          const hasExpectedAp = Array.isArray(check?.expected_answer_points) && check.expected_answer_points.length > 0;
          const expectedApSet = hasExpectedAp
            ? new Set(check.expected_answer_points.map((name) => '词汇/答案考点/' + name))
            : null;
          const diff = new Set();
          reviewedPaths.forEach((path) => {
            const isAp = typeof path === 'string' && path.startsWith('词汇/答案考点/');
            const matched = isAp && expectedApSet ? expectedApSet.has(path) : origPathSet.has(path);
            if (!matched) diff.add(path);
          });
          return diff;
        }
        const r1Paths = cleanPaths(reviewedLabels(r1?.unit));
        const r1Mismatch = !!(r1?.assessment && !kpSame(kp1, kpOrig)) || answerPointDiff(r1?.unit);
        const r1Diff = r1Mismatch ? reviewedDiffSet(r1?.unit, r1Paths) : null;
        // 原标签里若存在一审判定未包含的标签，也只突出显示该差异标签。
        const origDiffPaths = new Set();
        if (r1Mismatch && Array.isArray(r1Paths)) {
          const r1Set = new Set(r1Paths);
          displayedOrigPaths.forEach((path) => {
            if (!r1Set.has(path)) origDiffPaths.add(path);
          });
        }
        // 标签合并：合并原标签与一审知识点（去重并集）；人工在「修改标签」保存过则以人工结果为准。
        const hasMerged = kpModified || !!(r1 && r1.assessment) || !!(mergedInfo && mergedInfo.has_round1);
        const mergedBase = kpModified
          ? (Array.isArray(cur.knowledge) ? cur.knowledge : (mergedInfo && Array.isArray(mergedInfo.knowledge) ? mergedInfo.knowledge : autoMerged))
          : (mergedInfo && Array.isArray(mergedInfo.knowledge) ? mergedInfo.knowledge : autoMerged);
        const mergedPaths = hasMerged ? cleanPaths([...new Set([...mergedBase, ...answerPaths])]) : null;
        const kpPending = !r1 || !r1.assessment;
        const kpStatusColor = kpPending
          ? 'var(--dsw-alias-label-secondary)'
          : (r1Mismatch ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-secondary)');
        const kpStatusText = kpPending ? '待审核' : (r1Mismatch ? '有差异' : '一致');
        const kpRows = h('div', { style: { padding: '0 0 2px' } },
          h('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, margin: '0 0 12px' } },
            h('span', { style: { fontSize: 13.5, fontWeight: 600, letterSpacing: '-.01em', color: 'var(--dsw-alias-label-primary)' } }, '知识点标签'),
            h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11.5, fontWeight: (!kpPending && r1Mismatch) ? 600 : 500, color: kpStatusColor, whiteSpace: 'nowrap' } },
              (!kpPending && r1Mismatch) ? h('span', { style: { width: 6, height: 6, borderRadius: '50%', background: 'var(--dsw-alias-state-error-primary)', flexShrink: 0 } }) : null,
              kpStatusText),
          ),
          cmpRow('原标签', kpShort(displayedOrigPaths), { titles: displayedOrigPaths, diffPaths: origDiffPaths.size ? origDiffPaths : undefined }),
          cmpRow('一审标签', kpShort(r1Paths), { pending: kpPending, mismatch: r1Mismatch, diffPaths: r1Mismatch && (r1Diff?.size || origDiffPaths.size) ? r1Diff : undefined, titles: r1Paths }),
          cmpRow('标签合并', kpShort(mergedPaths), { pending: !hasMerged, modified: kpModified, isFinal: true, titles: mergedPaths }),
        );

        // 题型 / 子类：始终展示到最末级（叶子子类，如 单词拼写 / 句意 vs 单词拼写 / 汉语提示）；
        // 上级题型相同、末级子类不同时，仅把有差异的末级叶子标红加粗。
        const panels = [h('div', { key: 'kp' }, kpRows)];
        const r1Assess = r1 && r1.assessment ? r1.assessment : null;
        const baseType = String(orig.type || cur.type || '').trim();
        const r1Type = r1Assess ? String(r1Assess.type || '').trim() : '';
        const origSubs = cleanPaths(Array.isArray(orig.subtypes) ? orig.subtypes : (Array.isArray(cur.subtypes) ? cur.subtypes : [])) || [];
        const r1Subs = r1Assess && Array.isArray(r1Assess.subtypes) ? cleanPaths(r1Assess.subtypes) : null;
        const hasTypeInfo = !!(baseType || r1Type || origSubs.length || (Array.isArray(r1Subs) && r1Subs.length));
        if (hasTypeInfo) {
          const typeMismatch = !!(baseType && r1Type && baseType !== r1Type);
          const subMismatch = !!(Array.isArray(r1Subs) && labelSetKey(r1Subs) !== labelSetKey(origSubs));
          const origSubSet = new Set(origSubs);
          const r1SubSet = new Set(r1Subs || []);
          function typeLeafRow(rowLabel, mainType, subs, otherSubSet, opts) {
            const o = opts || {};
            const empty = !mainType && (!Array.isArray(subs) || !subs.length);
            let valueNode;
            if (empty) {
              valueNode = h('span', {
                style: { flex: 1, minWidth: 0, fontSize: 12.5, color: 'var(--dsw-alias-label-secondary)', wordBreak: 'break-word' },
              }, o.pending ? '待审核' : '—');
            } else {
              const parts = [];
              if (mainType) {
                parts.push(h('span', {
                  key: 't',
                  title: '题型：' + mainType,
                  style: o.typeDiff
                    ? { color: 'var(--dsw-alias-state-error-primary)', fontWeight: 600 }
                    : { color: 'var(--dsw-alias-label-primary)', fontWeight: 400 },
                }, mainType));
              }
              const leafList = Array.isArray(subs) && subs.length
                ? subs
                : (o.subDiff ? ['（无子类）'] : []);
              if (leafList.length) {
                if (mainType) {
                  parts.push(h('span', { key: 'sep', style: { color: 'var(--dsw-alias-label-secondary)', margin: '0 4px' } }, '/'));
                }
                leafList.forEach((leaf, idx) => {
                  const isDiff = o.subDiff && (leaf === '（无子类）' || !otherSubSet.has(leaf));
                  if (idx > 0) parts.push('、');
                  parts.push(h('span', {
                    key: 's:' + leaf + ':' + idx,
                    title: '子类：' + leaf,
                    style: isDiff
                      ? { color: 'var(--dsw-alias-state-error-primary)', fontWeight: 600 }
                      : { color: 'var(--dsw-alias-label-primary)', fontWeight: 400 },
                  }, leaf));
                });
              }
              valueNode = h('span', { style: { flex: 1, minWidth: 0, fontSize: 12.5, color: 'var(--dsw-alias-label-primary)', wordBreak: 'break-word' } }, ...parts);
            }
            return h('div', { style: { display: 'flex', gap: 8, alignItems: 'baseline', padding: '3px 0' } },
              h('span', { style: { flex: '0 0 auto', width: 54, fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', whiteSpace: 'nowrap' } }, rowLabel),
              valueNode,
            );
          }
          const typeRows = h('div', { style: { marginTop: 12, padding: '10px 0 2px', borderTop: '1px dashed var(--dsw-alias-border-l1)' } },
            h('div', { style: { fontSize: 12.5, fontWeight: 600, margin: '0 0 6px', color: 'var(--dsw-alias-label-primary)' } }, '题型 / 子类'),
            typeLeafRow('原标签', baseType, origSubs, r1SubSet, { typeDiff: typeMismatch, subDiff: subMismatch }),
            typeLeafRow('一审标签', r1Type, r1Subs || [], origSubSet, { pending: !r1Assess, typeDiff: typeMismatch, subDiff: subMismatch }),
          );
          panels.push(h('div', { key: 'type' }, typeRows));
        }
        return panels;
      }

      // 审核说明：完整展示题型/子类差异及一审差异原因（不截断），长文本由卡片内可滑动区域承载。
      // 难度/来源/分类等基本标签不参与审核，也不在这里罗列。
      function auditNotes(detail) {
        const comp = detail.comparison || {};
        const orig = (comp.original && comp.original.labels) || {};
        const cur = detail.labels || {};
        const r1 = roundAssessment(comp, 'labels');
        const baseType = String(orig.type || cur.type || '').trim();
        const joinList = (v) => (Array.isArray(v) ? v.filter(Boolean).join('、') : (v == null ? '' : String(v)));
        const baseSub = joinList(orig.subtypes || cur.subtypes);
        const mainDiffs = [];
        const subDiffs = [];
        const checkType = (name, a) => {
          if (!a) return;
          const t = String(a.type || '').trim();
          if (t && baseType && t !== baseType) mainDiffs.push(name + '「' + t + '」');
          const st = joinList(a.subtypes);
          if (Array.isArray(a.subtypes) && labelSetKey(a.subtypes) !== labelSetKey(orig.subtypes || cur.subtypes)) subDiffs.push(name + '「' + (st || '无') + '」');
        };
        checkType('一审', r1 && r1.assessment);
        const notes = [];
        if (mainDiffs.length) notes.push('题型差异：' + mainDiffs.join('、') + '（原「' + (baseType || '无') + '」）');
        if (subDiffs.length) notes.push('子类差异：' + subDiffs.join('、') + '（原「' + (baseSub || '无') + '」）');
        const kpDiff = (typeof hasKnowledgeDiff === 'function') ? hasKnowledgeDiff(detail) : false;
        const checks = (r1 && r1.checks) || [];
        const hasFlaggedCheck = checks.some((c) => c && (c.verdict === 'error' || c.verdict === 'uncertain'));
        if (notes.length || kpDiff || hasFlaggedCheck) {
          const reasons = [];
          const seen = new Set();
          for (const c of checks) {
            const text = String((c && c.rationale) || '').trim();
            if (!text || seen.has(text)) continue;
            seen.add(text);
            reasons.push(text);
          }
          if (reasons.length) {
            reasons.forEach((r) => notes.push('差异原因：' + r));
          } else if (kpDiff && !notes.length) {
            notes.push('差异原因：一审与原标签判断不同');
          }
        }
        return notes;
      }
      // 是否有一审与原标签不一致的知识点（仅用于审核说明的空态文案）。
      function hasKnowledgeDiff(detail) {
        const comp = detail.comparison || {};
        const orig = (comp.original && comp.original.labels) || {};
        const cur = detail.labels || {};
        const same = (a, b) => labelSetKey(a) === labelSetKey(b);
        const base = orig.knowledge || cur.knowledge || [];
        const r1 = roundAssessment(comp, 'labels');
        if (r1?.assessment && (!same(r1.assessment.knowledge, base) || answerPointDiff(r1.unit))) return true;
        return false;
      }

      // 两轮审核（一审全量 / 二审抽 20%）统一、紧凑的展示。
      // 原则：只显示一次；正常即"全通过"；有问题的在对应审核节点上点名；
      // 只讲差异，不堆完整层级，标签保持短小。
      const ROUND_SHORT = { labels: '一审' };
      function roundName(r) { return ROUND_SHORT[r.round_id] || r.name || r.round_id || '审核'; }
      // 值裁剪：知识点等长串只保留末级，避免把整条层级拉得很长。
      function shortVal(v) {
        const one = (x) => {
          const s = String(x == null ? '' : x).trim();
          if (!s) return '—';
          const parts = s.split(/[\/>＞]/).map((p) => p.trim()).filter(Boolean);
          return parts.length > 1 ? parts[parts.length - 1] : s;
        };
        if (Array.isArray(v)) return v.length ? v.map(one).join('、') : '—';
        return one(v);
      }
      // 一个审核节点是否"通过"：有判定且 verdict=ok，且无差异。
      // 返回 {kind, note}：kind ∈ ok/diff/uncertain/na/pending；note 为需要在该节点上点名的短说明。
      function nodeState(r) {
        const chs = r.checks || [];
        if (!chs.length) {
          const st = String(r.status || '').trim();
          if (st === '未涉及' || st === '未覆盖') return { kind: 'na' };
          if (st === '已提供' || st === '已覆盖') return { kind: 'ok' };
          // 历史来源未验证 / 待重新审核 / 未提供 —— 都是该节点尚未完成的真实状态，原样点名。
          return { kind: 'pending', note: st || '待审核' };
        }
        const diff = chs.find((c) => c.verdict === 'error' || (c.differences || []).length);
        if (diff) {
          const d = (diff.differences || [])[0];
          return { kind: 'diff', note: d ? ('原 ' + shortVal(d.original) + ' → ' + shortVal(d.reviewed)) : '' };
        }
        const unc = chs.find((c) => c.verdict === 'uncertain');
        if (unc) return { kind: 'uncertain', note: unc.rationale ? String(unc.rationale).slice(0, 40) : '' };
        if (chs.every((c) => c.verdict === 'ok')) return { kind: 'ok' };
        return { kind: 'pending', note: String(r.status || '待审核') };
      }
      function stateChip(kind) {
        const map = {
          ok: ['✓', 'var(--dsw-alias-label-primary)', '通过'],
          diff: ['差异', 'var(--dsw-alias-state-warn-primary)', '有差异'],
          uncertain: ['疑点', 'var(--dsw-alias-state-warn-primary)', '有疑点'],
          na: ['—', 'var(--dsw-alias-label-secondary)', '未涉及'],
          pending: ['待审', 'var(--dsw-alias-label-secondary)', '待审核'],
        };
        const [txt, color, title] = map[kind] || map.pending;
        return h('span', { title, style: { color, fontWeight: 600, flexShrink: 0 } }, txt);
      }
      // 单个审核节点一行：一审/二审 + 状态；未完成时直接显示其真实状态文字
      // （如"历史来源未验证""待重新审核"），不再叠一个笼统的"待审"造成重复。
      function roundNodeRow(r, st) {
        const label = h('span', { style: { fontWeight: 600, flexShrink: 0 } }, roundName(r));
        if (st.kind === 'pending' && st.note) {
          return h('div', { key: r.round_id, style: { display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 } },
            label,
            h('span', { title: '待处理', style: { color: 'var(--dsw-alias-state-warn-primary)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 } }, st.note));
        }
        return h('div', { key: r.round_id, style: { display: 'flex', alignItems: 'baseline', gap: 6, minWidth: 0 } },
          label,
          stateChip(st.kind),
          st.note ? h('span', { style: { color: 'var(--dsw-alias-label-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 } }, st.note) : null);
      }
      // 单元级：一条紧凑汇总。两轮都通过 → 一行"全通过"；否则只点出没过的节点。
      function renderRounds(comparison) {
        const rounds = ((comparison && comparison.rounds) || []).filter((r) => r && r.round_id !== 'sample');
        if (!rounds.length) return h('div', { style: T.hint }, '无审核轮次记录');
        const states = rounds.map((r) => ({ r, st: nodeState(r) }));
        const allOk = states.every((x) => x.st.kind === 'ok');
        const box = { background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 8, padding: '7px 10px', marginBottom: 10, fontSize: 12 };
        if (allOk) {
          return h('div', { style: box },
            h('span', { style: { color: 'var(--dsw-alias-label-primary)', fontWeight: 600 } }, '✓ 一审通过'));
        }
        return h('div', { style: box },
          h('div', { style: { display: 'flex', flexDirection: 'column', gap: 3 } },
            states.map(({ r, st }) => roundNodeRow(r, st))));
      }
      // 单条问题：只显示"这一字段"上一审的结果，且跳过通过/未涉及的节点。
      function renderFindingRounds(finding) {
        const comp = finding && finding.comparison;
        const rounds = ((comp && comp.rounds) || []).filter((r) => r && r.round_id !== 'sample');
        if (!rounds.length) return null;
        const items = [];
        let anyJudged = false; // 该字段是否至少有一轮真实判定
        for (const r of rounds) {
          const chs = (r.checks || []).filter((c) => c && c.verdict !== undefined);
          // 该字段本身在这一轮没有判定（如该轮整体"历史来源未验证/待重新审核"）时，
          // 不在每条问题下重复输出——这类整体状态只在上方单元级汇总里显示一次。
          if (!chs.length) continue;
          anyJudged = true;
          const st = nodeState(r);
          if (st.kind === 'ok' || st.kind === 'na') continue; // 通过/未涉及的节点也不重复
          items.push({ r, st });
        }
        if (!items.length) {
          // 该字段在各轮都无判定 → 交给上方单元级汇总显示，不在每条问题下重复；
          // 有真实判定且都通过 → 明确写"与该问题一致"。
          if (!anyJudged) return null;
          return h('div', { style: { ...T.hint, marginTop: 4, color: 'var(--dsw-alias-label-primary)' } }, '✓ 一审与该问题一致');
        }
        return h('div', { style: { marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2, fontSize: 12 } },
          items.map(({ r, st }) => roundNodeRow(r, st)));
      }

      // 知识点：多选（简化为逗号输入 + 快捷树）
      const knowledgeStr = (labels?.knowledge || []).join(', ');

      // ---- 中栏：题组列表（过滤 + 搜索 + 键盘高亮）----
      // 列表筛选只保留「全部 / 待审」两个入口。
      const filterTabs = [['all', '全部'], ['open', '待审']];
      const listCol = h('div', { className: 'wf-review-list-col', style: { flex: '0 1 220px', minWidth: 140, borderRight: '1px solid var(--dsw-alias-border-l1)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--dsw-alias-bg-layer-1)' } },
        h('div', { style: { padding: '10px 10px 8px', borderBottom: '1px solid var(--dsw-alias-border-l1)' } },
          h('div', { style: { display: 'flex', gap: 4, flexWrap: 'nowrap', marginBottom: 8 } },
            filterTabs.map(([id, lbl]) => h('button', {
              key: id, onClick: () => setFilter(id),
              style: {
                flex: '1 1 0', minWidth: 0, padding: '3px 3px', borderRadius: 7, fontSize: 11.5, cursor: 'pointer',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', textAlign: 'center',
                border: '1px solid var(--dsw-alias-border-l2)',
                background: filter === id ? 'var(--wf-sel-bg)' : 'transparent',
                border: '1px solid ' + (filter === id ? 'var(--wf-sel-border)' : 'var(--dsw-alias-border-l2)'),
                color: filter === id ? 'var(--dsw-alias-label-primary)' : 'var(--dsw-alias-label-secondary)',
              },
            }, lbl))),
          h('input', { className: 'wf-input', style: { ...T.input, width: '100%', padding: '5px 9px' }, value: q, placeholder: '题号 / 题型 / 知识点', onChange: (e) => setQ(e.target.value) }),
        ),
        h('div', { className: 'wf-scroll', ref: listScrollRef, style: { flex: 1, overflow: 'auto', padding: '7px 7px 3px' } },
          filteredRows.length === 0 ? h('div', { style: { ...T.hint, padding: 12 } }, '没有符合条件的题组') : filteredRows.map((r) => {
            const uid = r.unit_id || r.number;
            const active = uid === unitId;
            const isAnomaly = Boolean(!r.excluded && (r.is_anomaly || r.state === 'blocked' || r.verdict === 'error' || r.severity === 'blocker' || (r.format_count || 0) > 0 || !String(r.type || '').trim()));
            const hasIssue = !isAnomaly && Boolean(r.needs_human || r.state === 'attention');
            return h('div', {
              key: uid, className: 'wf-paper' + (active ? ' wf-active' : '') + (isAnomaly ? ' wf-paper-anomaly' : ''),
              'data-unit': uid,
              style: T.paperRow(active),
              onClick: () => onSelectUnit(uid),
            },
              // 题号列只放原卷印刷题号：无号题组用「—」，异常题只对题号标红。
              h('span', { style: { width: 44, flexShrink: 0, fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', ...(isAnomaly ? { color: 'var(--dsw-alias-state-error-primary)' } : null) }, title: Array.isArray(r.printed_numbers) && r.printed_numbers.length === 0 ? String((r.source_section || {}).title || '') : '' },
                Array.isArray(r.printed_numbers) && r.printed_numbers.length === 0
                  ? '—'
                  : String(r.label || r.number)),
              // 题号列表只展示目录主题型（语音题/单词拼写/阅读理解…），不展示子类、子题或题干；
              // 原卷大题标题只作悬停依据，题型缺失明确显示未判定。
              h('span', { title: r.source_section ? [r.source_section.context, r.source_section.title].filter(Boolean).join(' · ') : '', style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 11.5, opacity: .85 } },
                String(r.type || '').trim() || '未判定'),
              r.excluded
                ? h('span', { style: { ...T.badge(''), flexShrink: 0, fontSize: 10.5 } }, '听力跳过')
                : h('span', {
                  className: 'wf-review-row-dot', role: 'img',
                  'aria-label': isAnomaly ? '有异常问题' : (hasIssue ? '有审核问题' : (r.state === 'unverified' ? '尚未核对' : '无待审问题')),
                  title: isAnomaly ? (r.anomaly_summary || r.open_summary || '该题存在校验或内容异常，请查看详情') : (hasIssue ? (r.open_summary || '有审核问题，请查看详情') : undefined),
                  style: { width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                    background: isAnomaly ? 'var(--dsw-alias-state-error-primary)' : (hasIssue ? 'var(--dsw-alias-state-warn-primary)' : 'var(--dsw-alias-label-primary)') },
                }),
            );
          })),
      );

      // ---- 右栏：工作区 ----
      let workCol;
      if (err) {
        workCol = h(EmptyState, {
          flex: true, tone: 'error', title: '加载失败：' + err,
          action: h('button', { className: 'wf-btn', style: { ...T.btn, marginTop: 12 }, onClick: () => load({ force: true }) }, '重试本题'),
        });
      } else if (!detail) {
        workCol = h(EmptyState, { flex: true, title: TEXT.loading });
      } else {
        const detailRow = detail.row || currentRow || {};
        const detailAnomalyReasons = Array.isArray(detail.anomaly_reasons) && detail.anomaly_reasons.length
          ? detail.anomaly_reasons
          : (Array.isArray(detailRow.anomaly_reasons) && detailRow.anomaly_reasons.length
            ? detailRow.anomaly_reasons
            : (currentRow && Array.isArray(currentRow.anomaly_reasons) ? currentRow.anomaly_reasons : []));
        const detailIsAnomaly = Boolean(
          !detailRow.excluded && (
            detail.is_anomaly || detailRow.is_anomaly || (currentRow && currentRow.is_anomaly)
            || detailRow.state === 'blocked' || detailRow.verdict === 'error'
            || !String(detail.type || detailRow.type || '').trim()
            || (detail.format_open || 0) > 0
            || detailAnomalyReasons.length > 0
          )
        );
        const detailAnomalyText = detailAnomalyReasons.length
          ? detailAnomalyReasons.join('；')
          : (detail.anomaly_summary || detailRow.anomaly_summary || (currentRow && currentRow.anomaly_summary) || (!String(detail.type || detailRow.type || '').trim() ? '题型缺失（未判定）' : '存在校验或内容异常'));
        workCol = h('div', { style: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 } },
          contextPaper ? h('div', {
            style: {
              display: 'flex', alignItems: 'center', gap: 8, padding: '6px 14px',
              borderBottom: '1px solid var(--dsw-alias-border-l1)',
              background: 'var(--dsw-alias-bg-base)',
              fontSize: 12, color: 'var(--dsw-alias-label-secondary)',
              flexShrink: 0, overflow: 'hidden',
            },
          },
            h('span', { style: { fontWeight: 600, color: 'var(--dsw-alias-label-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: contextPaper.title || contextPaper.batchName },
              '所属试卷：' + (contextPaper.title || contextPaper.batchName)),
            contextPaper.batchName && contextPaper.title !== contextPaper.batchName
              ? h('span', { style: { fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', opacity: .85 }, title: contextPaper.batchName }, '（' + contextPaper.batchName + '）')
              : null,
            contextPaper.uploaded ? h('span', { style: { ...T.badge(''), flexShrink: 0, fontSize: 10.5 } }, '已上传') : null,
          ) : null,
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', height: 44, borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0, flexWrap: 'nowrap', overflow: 'hidden' } },
            h('span', {
              style: { fontWeight: 600, fontSize: 14.5, whiteSpace: 'nowrap', flexShrink: 0 },
              title: detail.source_section ? [detail.source_section.context, detail.source_section.title].filter(Boolean).join(' · ') : '',
            }, detail.printed_numbers && detail.printed_numbers.length === 0 ? '原卷未标小题号' : '第 ' + (detail.label || '') + ' 题'),
            detail.source_section && h('span', {
              title: [detail.source_section.context, detail.source_section.title].filter(Boolean).join(' · '),
              style: { ...T.hint, whiteSpace: 'nowrap', flexShrink: 0 },
            }, '原卷第' + detail.source_section.occurrence + '处大题'),
            detail.row && detail.row.excluded
              ? h('span', { style: { ...T.badge(''), flexShrink: 0, whiteSpace: 'nowrap' } }, '听力跳过')
              : (detail.needs_human
                ? h('span', { style: { ...T.badge('warn'), flexShrink: 0, whiteSpace: 'nowrap' }, title: (detail.row && detail.row.open_summary) || (detail.open_summary || '') || '标签与本轮审核判断不一致，需人工核对' }, '待审标签')
                : h('span', { style: { ...T.badge(''), flexShrink: 0, whiteSpace: 'nowrap' } }, '无待审')),
            (() => {
              const srcType = String((detail.labels && detail.labels.source_type) || (detail.item && detail.item.source_type) || '').trim();
              const yearVal = String((detail.labels && detail.labels.year) || (detail.item && detail.item.year) || '').trim();
              const diffVal = String(
                (detail.labels && detail.labels.difficulty)
                || (detail.preview_labels && detail.preview_labels.difficulty)
                || (detail.row && detail.row.difficulty)
                || ''
              ).trim();
              const rawCats = (detail.labels && Array.isArray(detail.labels.categories) && detail.labels.categories)
                || (detail.item && detail.item.filterMetadata && Array.isArray(detail.item.filterMetadata.categories) && detail.item.filterMetadata.categories)
                || [];
              const tags = [];
              const seen = new Set();
              const pushTag = (text, title) => {
                const v = String(text || '').trim();
                if (!v || v === '未填写' || seen.has(v)) return;
                seen.add(v);
                tags.push({ text: v, title });
              };
              pushTag(srcType, '来源：' + srcType);
              pushTag(yearVal, '年份：' + yearVal);
              pushTag(diffVal, '难度：' + diffVal);
              rawCats.forEach((c) => pushTag(c, '分类：' + c));
              if (!tags.length) return null;
              return h('div', {
                style: { display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0, overflow: 'hidden', flexShrink: 1 },
              },
                tags.map((t, i) => h('span', {
                  key: 'meta-' + i,
                  style: { ...T.badge('info'), flexShrink: 0, whiteSpace: 'nowrap' },
                  title: t.title,
                }, t.text)),
              );
            })(),
            h('span', { style: { flex: 1, minWidth: 4 } }),
            labelNotice ? h('span', { role: 'status', style: { ...T.hint, whiteSpace: 'nowrap', flexShrink: 0 } }, labelNotice) : null,
            hidePaperPreview ? null : h('button', {
              type: 'button',
              className: 'wf-btn',
              style: { ...T.btn, whiteSpace: 'nowrap' },
              title: '左栏查看已组装试卷，右栏对照 Word 原文件排版',
              onClick: openPaperPreview,
            }, '预览对照'),
            // 修改/通过 放在题号与标签行右侧（已取消驳回）。
            (() => {
              const decideAll = async (status) => {
                setBusy(status);
                try {
                  const ids = [];
                  (groupedFindings || []).forEach((g) => { (g.ids && g.ids.length ? g.ids : [g.finding_id]).forEach((id) => { if (id) ids.push(id); }); });
                  (fmtFindings || []).forEach((f) => { if (f.finding_id) ids.push(f.finding_id); });
                  for (const id of ids) await jpost('/batches/' + encodeURIComponent(batchName) + '/decisions', { item: { finding_id: id, status, note: '' } });
                  setBusy('');
                  invalidateBatchUnits(batchName);
                  await load({ force: true, silent: true });
                  if (typeof onUnitMutated === 'function') onUnitMutated();
                } catch (e) { setErr(String(e.message || e)); setBusy(''); }
              };
              return h('div', { style: { display: 'inline-flex', gap: 8, flexShrink: 0, marginLeft: 'auto' } },
                h('button', { type: 'button', className: 'wf-btn', style: { ...T.btn, whiteSpace: 'nowrap' }, onClick: () => setLabelDlg(true) }, '修改'),
                h('button', { type: 'button', className: 'wf-btn-primary', style: { ...T.btnPrimary, whiteSpace: 'nowrap' }, disabled: busy === 'accepted', onClick: () => decideAll('accepted') }, busy === 'accepted' ? '处理中…' : '通过'),
              );
            })(),
          ),
          h('div', { className: 'wf-scroll wf-review-container' },
           h('div', { className: 'wf-review-layout' },
            // 左：原卷预览 + 小题
            h('div', { className: 'wf-review-main', style: { minWidth: 0 } },
              h('div', { style: T.card },
                h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 10 } },
                  h('span', { style: { fontSize: 13.5, fontWeight: 600, whiteSpace: 'nowrap' } }, TEXT.sourcePreview),
                  // 「原卷修改」入口：放在原卷预览卡片右上角。
                  h('button', {
                    className: 'wf-btn-primary', style: { ...T.btnPrimary, marginLeft: 'auto' }, disabled: !content,
                    onClick: () => setContentDlg(true),
                  }, '原卷修改'),
                ),
                (detail.audio || []).map((a, i) => h('audio', { key: (a.url || i) + '|' + (typeof MEDIA_ORIGIN === 'string' ? MEDIA_ORIGIN : ''), controls: true, preload: 'metadata', src: mediaUrl(a.url), style: { width: '100%', margin: '4px 0 8px' } })),
                h('div', { className: 'wf-paper' }, renderBlocks(detail.preview_blocks, detail.paper_bulk_bold)),
              ),
              // 内容预览紧随原卷：先看原文，再看文章大意与解析；只读，不在这里改内容。
              h(ContentPreviewCard, {
                key: 'content:' + String(detail.unit_id || detail.label || ''),
                detail,
                onEdit: () => { setContentEditError(''); setContentEditDlg(true); },
              }),
            ),
            // 右：悬浮等宽三按钮 + 标签审核（对比）+ 审核说明（差异说明 + 修改/通过/驳回按钮）
            h('div', { className: 'wf-review-notes' },
              hideAiSupplement ? null : h('div', { style: { display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 8 } },
                h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, width: '100%' } },
                  h('button', {
                    className: 'wf-btn',
                    style: { ...T.btn, width: '100%', padding: '5px 8px', fontSize: 12, textAlign: 'center', whiteSpace: 'nowrap' },
                    disabled: !!aiBusy,
                    title: '为本批非听力/非写作缺解析或已标注AI解析的题目生成/重新生成解析（≤50字）',
                    onClick: () => runAiSupplement('explanation'),
                  }, aiBusy === 'explanation' ? '生成中…' : 'AI解析'),
                  h('button', {
                    className: 'wf-btn',
                    style: { ...T.btn, width: '100%', padding: '5px 8px', fontSize: 12, textAlign: 'center', whiteSpace: 'nowrap' },
                    disabled: !!aiBusy,
                    title: '为本批长材料题组缺失或已标注AI的大意生成/重新生成概括（≤50字）',
                    onClick: () => runAiSupplement('articleSummary'),
                  }, aiBusy === 'articleSummary' ? '生成中…' : 'AI文章大意'),
                  h('button', {
                    className: 'wf-btn',
                    style: { ...T.btn, width: '100%', padding: '5px 8px', fontSize: 12, textAlign: 'center', whiteSpace: 'nowrap' },
                    disabled: !!aiBusy,
                    title: '根据本地录音转写为本批缺听力原文的听力题去播报、去两遍重复并补全（标注 AI转录）',
                    onClick: () => runAiSupplement('listeningScript'),
                  }, aiBusy === 'listeningScript' ? '转录中…' : '听力材料补全'),
                ),
                (aiModal && aiModal.status === 'running' && !aiModalOpen)
                  ? h('button', {
                      type: 'button',
                      className: 'wf-btn',
                      style: { ...T.btn, alignSelf: 'flex-start', fontSize: 12, padding: '3px 10px' },
                      onClick: () => setAiModalOpen(true),
                    }, `后台生成中（${Number(aiModal.completed || 0)}/${Number(aiModal.total || 0)}）· 查看进度`)
                  : null,
                aiNotice
                  ? h('span', { role: 'status', style: { ...T.badge(aiNotice.startsWith('补充失败') ? 'err' : 'ok'), alignSelf: 'flex-start', maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: aiNotice }, aiNotice)
                  : null,
              ),
              h('div', { style: T.card },
                ...labelAuditPanels(detail),
              ),
              // 审核说明：描述异常说明及一审与原标签的差异；修改/通过按钮已移至顶部题号行右侧
              (() => {
                const lines = auditNotes(detail);
                const kpDiff = hasKnowledgeDiff(detail);
                const anomalyItems = detailIsAnomaly
                  ? (detailAnomalyReasons.length ? detailAnomalyReasons : [detailAnomalyText])
                  : [];
                return h('div', { style: T.card },
                  h('div', { style: { ...T.cardTitle } }, '审核说明'),
                  h('div', { className: 'wf-audit-notes-body' },
                    anomalyItems.map((reason, idx) => h('div', {
                      key: 'anomaly-' + idx,
                      style: {
                        fontSize: 12,
                        color: 'var(--dsw-alias-state-error-primary)',
                        fontWeight: 500,
                        padding: '3px 0',
                        lineHeight: 1.6,
                        borderTop: idx ? '1px dashed var(--dsw-alias-border-l1)' : 'none',
                      },
                    }, '· 异常：' + reason)),
                    lines.length
                      ? lines.map((t, i) => {
                          const isDiffNote = /^(?:题型差异|子类差异)：/.test(String(t || ''));
                          return h('div', {
                            key: i,
                            style: {
                              fontSize: 12,
                              color: isDiffNote ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-primary)',
                              fontWeight: isDiffNote ? 600 : 400,
                              padding: '3px 0',
                              lineHeight: 1.6,
                              borderTop: (i || anomalyItems.length) ? '1px dashed var(--dsw-alias-border-l1)' : 'none',
                            },
                          }, '· ' + t);
                        })
                      : (!anomalyItems.length ? h('div', { style: T.hint }, kpDiff ? '差异已在上方「知识点标签」中标出。' : '各轮与原标签一致，无差异。') : null),
                  ),
                );
              })(),
            ),
           ),
          ),
          labelDlg ? h(LabelModal, {
            tax, labels, setL, knowledgeStr, warnings: detail.label_warnings || [],
            unitLabel: detail.label, unitKind: detail.unit_kind, numbers: detail.numbers,
            busy, saveError: labelError, onSave: saveLabels, onClose: () => { setLabels(detail.labels || {}); setLabelError(''); setLabelDlg(false); },
          }) : null,
          (!hideAiSupplement && aiModal && aiModalOpen) ? (() => {
            const title = aiModal.mode === 'listeningScript'
              ? 'AI 听力材料转录补全'
              : (aiModal.mode === 'articleSummary' ? 'AI 文章大意批量生成' : 'AI 解析批量生成');
            const total = Number(aiModal.total || 0);
            const completed = Number(aiModal.completed || 0);
            const pct = aiModal.status === 'done' ? 100 : (total > 0 ? (completed >= total ? 100 : Math.min(99, Math.round((completed / total) * 100))) : 12);
            const barColor = aiModal.status === 'error'
              ? 'var(--dsw-alias-state-error-primary)'
              : (aiModal.status === 'done' ? 'var(--dsw-alias-state-success-primary)' : 'var(--dsw-alias-brand-primary)');
            return h(ModalShell, {
              title,
              tag: aiModal.status === 'running' ? '生成中' : (aiModal.status === 'done' ? '已完成' : '失败'),
              width: 420,
              onClose: () => setAiModalOpen(false),
              footer: h('button', {
                className: aiModal.status === 'done' ? 'wf-btn-primary' : 'wf-btn',
                style: aiModal.status === 'done' ? T.btnPrimary : T.btn,
                onClick: () => {
                  setAiModalOpen(false);
                  if (aiModal.status !== 'running') setAiModal(null);
                },
              }, aiModal.status === 'running' ? '后台运行并关闭' : '完成'),
            },
              h('div', { style: { display: 'flex', flexDirection: 'column', gap: 12 } },
                (aiModal.model_display || aiModal.model) ? h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, padding: '6px 10px', borderRadius: 6, background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)', fontSize: 12 } },
                  h('span', { style: T.hint }, '当前模型'),
                  h('span', { style: { color: 'var(--dsw-alias-label-primary)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: String(aiModal.model_display || aiModal.model) },
                    String(aiModal.model_display || aiModal.model) + (aiModal.model_source === 'custom' ? '（自定义）' : (aiModal.model_source ? '（跟随默认）' : ''))),
                ) : null,
                h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12.5 } },
                  h('span', { style: { color: 'var(--dsw-alias-label-primary)', fontWeight: 500 } },
                    aiModal.status === 'running' ? (aiModal.message || '正在调用 AI 生成…')
                      : (aiModal.status === 'done' ? '生成完成' : '生成失败')),
                  h('span', { style: { ...T.hint, fontVariantNumeric: 'tabular-nums' } },
                    total > 0 ? `${completed} / ${total} 项 (${pct}%)` : `${pct}%`),
                ),
                h('div', { style: { height: 8, borderRadius: 999, background: 'var(--dsw-alias-bg-layer-2)', overflow: 'hidden', border: '1px solid var(--dsw-alias-border-l1)' } },
                  h('div', { style: { height: '100%', width: pct + '%', background: barColor, borderRadius: 999, transition: 'width .25s ease' } }),
                ),
                aiModal.status === 'done'
                  ? h('div', { style: { padding: '9px 12px', borderRadius: 8, background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)', fontSize: 12.5, color: 'var(--dsw-alias-label-primary)', lineHeight: 1.6 } },
                      '✓ ' + String(aiModal.message || `已完成 ${aiModal.applied || 0} 项生成并标注 AI 生成`) + '（均控制在 50 字以内；如需重生成可再次点击按钮覆盖）。')
                  : null,
                aiModal.status === 'error'
                  ? h('div', { style: { padding: '9px 12px', borderRadius: 8, background: 'rgba(220,38,38,.08)', border: '1px solid var(--dsw-alias-state-error-primary)', fontSize: 12.5, color: 'var(--dsw-alias-state-error-primary)', lineHeight: 1.6 } },
                      String(aiModal.error || aiModal.message || '生成失败'))
                  : null,
              ),
            );
          })() : null,
          (contentDlg && content) ? h(ContentModal, {
            content, detail, busy, dirty: contentDirty,
            onSub: setSubField, onUnit: setUnitField,
            onSave: saveContent, onClose: closeContent, saveError: contentError,
          }) : null,
          contentEditDlg ? h(ContentEditModal, {
            detail, busy, saveError: contentEditError,
            onSave: saveContentEdit,
            onClose: () => setContentEditDlg(false),
          }) : null,
          (!hidePaperPreview && paperDlg) ? h(PaperPreviewModal, {
            batchName, data: paperData, loading: paperLoading, error: paperError,
            onReload: openPaperPreview,
            onClose: () => setPaperDlg(false),
          }) : null,
        );
      }

      if (hideListCol) {
        return h('div', { style: { flex: 1, display: 'flex', minHeight: 0, minWidth: 0 } }, workCol);
      }
      return h('div', { style: { flex: 1, display: 'flex', minHeight: 0 } }, listCol, workCol);
    }

