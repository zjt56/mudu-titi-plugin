    // ======================================================================
    // 上传试卷视图
    // ======================================================================
    const MATERIAL_ROLE_OPTIONS = [
      { value: 'auto', label: '自动识别' },
      { value: 'paper', label: '试卷正文' },
      { value: 'answers', label: '答案解析' },
      { value: 'listening_script', label: '听力材料' },
      { value: 'mix', label: '整卷/组合卷' },
      { value: 'structure_reference', label: '排版参考' },
      { value: 'other', label: '其他材料' },
    ];
    const MATERIAL_ROLE_LABELS = Object.fromEntries(MATERIAL_ROLE_OPTIONS.map((o) => [o.value, o.label]));
    MATERIAL_ROLE_LABELS.explanations = '答案解析';
    function isAudioFilename(name) {
      return /\.(mp3|wav|m4a|ogg|flac|aac)$/i.test(String(name || ''));
    }
    function isDocFilename(name) {
      return /\.(docx|pdf|doc)$/i.test(String(name || ''));
    }
    function predictUploadRole(filename) {
      const stem = String(filename || '').replace(/\.[^.]+$/, '');
      if (/含答案|带答案|教师版|带解析|组合卷/i.test(stem)) return 'mix';
      if (/听力(?:录音)?(?:材料|原文|文?稿)|录音(?:材料|原文|稿)|tapescript|listening[\s_-]*script/i.test(stem)) return 'listening_script';
      if (/答案|解析|详解|answer[\s_-]*key|\banswers?\b|explanations?/i.test(stem)) return 'answers';
      if (/排版参考|版式参考|扫描版/i.test(stem)) return 'structure_reference';
      if (/正文|原卷|试卷|试题|学生版|空白卷/i.test(stem)) return 'paper';
      return 'auto';
    }

    const SHARED_BATCHES_CACHE = { lite: null, full: null };

    function UploadView(props) {
      const React = require('react');
      const h = React.createElement;
      const isActive = props.isActive !== false;
      const [form, setForm] = React.useState({ title: '', year: '', region: '安徽', subject: 'english', source_type: '', grade: '' });
      const [files, setFiles] = React.useState([]);
      const [fileRoles, setFileRoles] = React.useState([]);
      const [taxonomy, setTaxonomy] = React.useState({ sources: [], years: [], regions: [] });
      const [busy, setBusy] = React.useState(false);
      const [err, setErr] = React.useState('');
      const [notice, setNotice] = React.useState('');
      const [batches, setBatches] = React.useState(() => (typeof SHARED_BATCHES_CACHE !== 'undefined' && (SHARED_BATCHES_CACHE.full || SHARED_BATCHES_CACHE.lite)) || []);
      const [sel, setSel] = React.useState('');
      const [tick, setTick] = React.useState(0);
      const [dragging, setDragging] = React.useState(false);
      const [advancing, setAdvancing] = React.useState('');
      const fileInputRef = React.useRef(null);
      const aliveRef = React.useRef(true);
      const refreshSequence = React.useRef(0);
      const [refreshError, setRefreshError] = React.useState('');
      const [delBatch, setDelBatch] = React.useState(null); // 继续处理区删除确认弹层 {name,value,busy,error}
      const [advanceFeedback, setAdvanceFeedback] = React.useState({});
      const advanceRequests = React.useRef(new Set());
      const prevQueueRef = React.useRef([]);
      const exitTimersRef = React.useRef([]);
      const [exitingRows, setExitingRows] = React.useState([]);
      const [promotedRanks, setPromotedRanks] = React.useState({});
      React.useEffect(() => () => {
        aliveRef.current = false;
        exitTimersRef.current.forEach((id) => clearTimeout(id));
        exitTimersRef.current = [];
      }, []);

      // 排队区严格按上传顺序排序，保证前一份完成后余下顺位绝不错乱。
      const sortQueueBatches = React.useCallback((list) => {
        return (list || []).slice().sort((a, b) => {
          const ta = Number(a && a.queue_order_ts) || Date.parse((a && a.created_at) || '') || 0;
          const tb = Number(b && b.queue_order_ts) || Date.parse((b && b.created_at) || '') || 0;
          if (ta !== tb) return ta - tb;
          const ca = String((a && a.created_at) || '');
          const cb = String((b && b.created_at) || '');
          if (ca !== cb) return ca < cb ? -1 : 1;
          return String((a && a.name) || '').localeCompare(String((b && b.name) || ''));
        });
      }, []);

      // 轮询批次进度（排队区 + 流程图共用）。使用轻量列表接口避免每 2 秒传输全量题目行。
      const refresh = React.useCallback(async () => {
        const sequence = ++refreshSequence.current;
        try {
          const d = await jget('/batches?lite=1');
          if (!aliveRef.current || sequence !== refreshSequence.current) return null;
          setRefreshError('');
          const list = d.batches || [];
          if (typeof SHARED_BATCHES_CACHE !== 'undefined') SHARED_BATCHES_CACHE.lite = list;
          setBatches(list);
          // 排队区只展示「处理中」的试卷（内容审核与上传格式检查结束的已流转到试卷审核）。
          // 当前选中的那份一旦结束，自动切到按上传顺序排在第一位的下一份，让右侧流程图
          // 立即接上新首位试卷，而不是停在已完成的旧卷上。
          setSel((cur) => {
            const pending = sortQueueBatches(list.filter((b) => !b.flow_done && !b.uploaded && !b.queue_removed));
            if (!pending.length) return '';
            return pending.some((b) => b.name === cur) ? cur : pending[0].name;
          });
          return list;
        } catch (e) {
          if (aliveRef.current && sequence === refreshSequence.current) setRefreshError('进度刷新失败，当前为上次结果：' + String(e.message || e));
          return null;
        }
      }, [sortQueueBatches]);
      React.useEffect(() => {
        (async () => {
          try { const d = await jget('/taxonomy'); if (aliveRef.current) setTaxonomy({ sources: d.sources || [], years: d.years || [], regions: d.regions || [] }); }
          catch (e) { /* taxonomy is optional for the upload shell */ }
        })();
      }, []);
      React.useEffect(() => {
        if (!isActive) return;
        refresh();
        let stopped = false, timer;
        async function poll() {
          if (typeof document === 'undefined' || !document.hidden) await refresh();
          if (!stopped) timer = setTimeout(poll, 2000);
        }
        timer = setTimeout(poll, 2000);
        const onFocus = () => { if (!document.hidden) refresh(); };
        window.addEventListener('focus', onFocus);
        document.addEventListener('visibilitychange', onFocus);
        return () => { stopped = true; refreshSequence.current++; clearTimeout(timer);
          window.removeEventListener('focus', onFocus); document.removeEventListener('visibilitychange', onFocus); };
      }, [refresh, isActive]);

      // 选择 / 拖放统一入口：原生 input 只保留可聚焦性与 accept，视觉完全交给自定义控件。
      function addFiles(list) {
        const incoming = Array.from(list || []);
        if (!incoming.length) return;
        const supported = /\.(docx|pdf|mp3|wav|m4a|ogg|flac|aac)$/i;
        const bad = incoming.filter((f) => !supported.test(f.name));
        // Keep every selected item visible, even equal-sized same-name files.
        // Unsupported items block submission until removed rather than vanishing.
        setFiles((prev) => prev.concat(incoming));
        setFileRoles((prev) => prev.concat(incoming.map((f) => (isDocFilename(f.name) ? 'auto' : ''))));
        setErr(bad.length ? '不支持以下文件，请移除后再上传：' + bad.map((f) => f.name).join('、') : '');
      }
      function onFiles(e) {
        addFiles(e.target.files);
        // 允许连续选择同一个文件（否则第二次不会触发 change）。
        try { e.target.value = ''; } catch (_) { /* 个别宿主不允许写，忽略 */ }
      }
      function onDrop(e) {
        e.preventDefault();
        setDragging(false);
        addFiles(e.dataTransfer && e.dataTransfer.files);
      }
      function removeFile(idx) {
        setFiles((prev) => prev.filter((_, i) => i !== idx));
        setFileRoles((prev) => prev.filter((_, i) => i !== idx));
      }
      function setFileRole(idx, role) {
        setFileRoles((prev) => prev.map((r, i) => (i === idx ? role : r)));
      }
      // 从当前阶段继续推进：宿主重启会丢掉后台推进线程，必须有一个可手动续跑
      // 的入口，否则批次会永远停在中间阶段。
      async function advance(name) {
        if (!name || advanceRequests.current.has(name)) return;
        advanceRequests.current.add(name);
        setAdvancing(name);
        setAdvanceFeedback((prev) => ({ ...prev, [name]: { message: '正在提交继续处理请求…' } }));
        try {
          const receipt = await requestAdvance(name, jpost, jget);
          if (!aliveRef.current) return;
          const feedback = advanceReceiptFeedback(receipt);
          setAdvanceFeedback((prev) => ({ ...prev, [name]: feedback }));
          // 队列并不等于结束；后续进度交给已有的统一轮询，避免另起20分钟等待循环。
          setBatches((prev) => prev.map((batch) => batch.name === name
            ? { ...batch, advance: { ...(batch.advance || {}), ...receipt } } : batch));
          await refresh();
        } catch (e) {
          if (aliveRef.current) setAdvanceFeedback((prev) => ({ ...prev, [name]: {
            error: true, message: '继续处理失败：' + String(e.message || e),
          } }));
        } finally {
          advanceRequests.current.delete(name);
          if (aliveRef.current) setAdvancing((current) => current === name ? '' : current);
        }
      }
      // 删除（继续处理区）：先请求在阶段边界停下当前运行/排队的批次，再彻底删除
      // 目录并清空本地缓存（后端 delete 端点串起 stop + purge）。确认需输入完整批次名。
      function openDel(name) { setDelBatch({ name, value: '', busy: false, error: '' }); }
      async function confirmDel() {
        if (!delBatch || delBatch.busy) return;
        if (delBatch.value !== delBatch.name) { setDelBatch({ ...delBatch, error: '请输入完整批次名以确认删除' }); return; }
        setDelBatch({ ...delBatch, busy: true, error: '' });
        try {
          await jpost('/batches/' + encodeURIComponent(delBatch.name) + '/delete', { confirm_name: delBatch.name }, { timeoutMs: 120000 });
          if (aliveRef.current) { setDelBatch(null); await refresh(); }
        } catch (e) {
          if (aliveRef.current) setDelBatch({ ...delBatch, busy: false, error: String((e && e.message) || e) });
        }
      }
      const delDialog = delBatch ? h('div', {
        style: { position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(16,24,40,.42)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 },
        onClick: (e) => { if (e.target === e.currentTarget && !delBatch.busy) setDelBatch(null); },
      },
        h('div', {
          role: 'dialog', 'aria-modal': 'true', 'aria-label': '删除批次确认',
          style: { width: '100%', maxWidth: 430, background: 'var(--dsw-alias-bg-layer-1)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 14, padding: 18, boxShadow: '0 18px 48px rgba(16,24,40,.28)', display: 'flex', flexDirection: 'column', gap: 10 },
        },
          h('div', { style: { fontSize: 15, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' } }, '删除批次'),
          h('div', { style: { ...T.hint, lineHeight: 1.6 } }, '将先停止批次「' + delBatch.name + '」当前运行的任务，再永久删除它的目录、收件箱副本与本地回收归档，并清空相关缓存，无法恢复。请输入完整批次名确认：'),
          h('div', { style: { display: 'flex', gap: 8, alignItems: 'stretch' } },
            h('input', {
              className: 'wf-input', autoFocus: true, value: delBatch.value,
              style: { ...T.input, flex: 1, minWidth: 0 },
              placeholder: delBatch.name,
              onChange: (e) => setDelBatch({ ...delBatch, value: e.target.value, error: '' }),
              onKeyDown: (e) => { if (e.key === 'Enter') confirmDel(); if (e.key === 'Escape' && !delBatch.busy) setDelBatch(null); },
            }),
            h('button', {
              type: 'button', className: 'wf-btn', style: { ...T.btn, flexShrink: 0, whiteSpace: 'nowrap' },
              disabled: delBatch.busy,
              title: '一键填入完整批次名',
              onClick: () => setDelBatch({ ...delBatch, value: delBatch.name, error: '' }),
            }, '一键填入'),
          ),
          delBatch.error ? h('div', { role: 'alert', style: { ...T.statusErr, padding: '6px 9px', borderRadius: 8 } }, delBatch.error) : null,
          h('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 } },
            h('button', { className: 'wf-btn', style: T.btn, disabled: delBatch.busy, onClick: () => setDelBatch(null) }, '取消'),
            h('button', { className: 'wf-btn-danger', style: T.btnDanger, disabled: delBatch.busy || delBatch.value !== delBatch.name, onClick: confirmDel }, delBatch.busy ? '正在删除…' : '确认删除'),
          ),
        ),
      ) : null;
      async function submit() {
        if (!files.length) { setErr('请选择原卷文件（.docx 或 .pdf，可带答案与音频）'); return; }
        // 上传前拦截旧版 .doc：二进制格式无法无损解析，提示另存为 .docx。
        const docFiles = files.filter((f) => /\.doc$/i.test(f.name));
        if (docFiles.length) {
          setErr('不支持旧版 .doc：' + docFiles.map((f) => f.name).join('、') + '。请在 Word/WPS 里另存为 .docx 后再上传（.docx 保真且无需安装任何工具）。');
          return;
        }
        const unsupportedFiles = files.filter((f) => !/\.(docx|pdf|mp3|wav|m4a|ogg|flac|aac)$/i.test(f.name));
        if (unsupportedFiles.length) {
          setErr('不支持以下文件，请移除后再上传：' + unsupportedFiles.map((f) => f.name).join('、'));
          return;
        }
        setBusy(true); setErr(''); setNotice('');
        try {
          const fd = new FormData();
          fd.append('title', form.title || '');
          fd.append('year', form.year || '');
          fd.append('region', form.region || '');
          fd.append('subject', form.subject || 'english');
          fd.append('source_type', form.source_type || '');
          fd.append('grade', form.grade || '');
          const explicitRoles = {};
          files.forEach((f, idx) => {
            const chosen = fileRoles[idx];
            if (isDocFilename(f.name) && chosen && chosen !== 'auto') {
              explicitRoles[String(idx)] = chosen;
            }
          });
          if (Object.keys(explicitRoles).length) {
            fd.append('file_roles', JSON.stringify(explicitRoles));
          }
          for (const f of files) fd.append('files', f, f.name);
          const r = await window.fetch(API + '/uploads', { method: 'POST', body: fd });
          if (!r.ok) throw new Error((await r.text()) || r.status);
          const data = await r.json();
          const firstFileName = (files && files[0] && files[0].name) ? String(files[0].name) : '';
          setFiles([]);
          setFileRoles([]);
          if (fileInputRef.current) { try { fileInputRef.current.value = ''; } catch (_) {} }
          setForm({ ...form, title: '', year: '' });
          setTick((x) => x + 1);
          // 乐观立即入队：拿到上传回执就立刻把新试卷写入排队区并选中，后台再静默刷新对齐。
          if (data && data.name && !(data.duplicate && data.uploaded)) {
            const optimisticPaper = data.paper
              ? { ...data.paper, original_filename: data.paper.original_filename || firstFileName }
              : { title: form.title || data.name, original_filename: firstFileName };
            const optimistic = {
              name: data.name,
              paper: optimisticPaper,
              stage: data.stage || { items: [] },
              advance: data.advance || { running: true },
              flow_done: Boolean(data.flow_done),
              uploaded: Boolean(data.uploaded),
              queue_removed: Boolean(data.queue_removed),
              created_at: data.created_at || new Date().toISOString(),
              updated_at: data.updated_at || new Date().toISOString(),
              queue_order_ts: data.queue_order_ts || (Date.now() * 1e6),
            };
            setBatches((prev) => {
              const base = Array.isArray(prev) ? prev : [];
              const idx = base.findIndex((b) => b && b.name === data.name);
              const next = idx >= 0
                ? base.map((b, i) => (i === idx ? { ...b, ...optimistic } : b))
                : base.concat([optimistic]);
              if (typeof SHARED_BATCHES_CACHE !== 'undefined') SHARED_BATCHES_CACHE.lite = next;
              return next;
            });
            setSel(data.name);
          }
          refresh();
          // 重复上传不会新建任务：明确告知，避免“批次任务不见了”的错觉。
          if (data && (data.duplicate || data.reused)) setNotice(data.message || '该试卷已存在，已定位到已有任务。');
          // 来源提示：说明本次用 Word 还是 PDF 作提取主源，以及为什么。
          else if (data && data.source_notice) setNotice(data.source_notice);
          if (props.onUploaded) props.onUploaded(data);
        } catch (e) { setErr(String(e.message || e)); }
        finally { setBusy(false); }
      }

      const inp = { ...T.input, width: '100%', padding: '6px 10px', fontSize: 12.5 };
      // 「处理中」= 内容审核与上传格式检查未结束且未上传，严格按上传顺序排序；「已完成」= 等待人工确认。
      const pendingBatches = React.useMemo(
        () => sortQueueBatches(batches.filter((b) => !b.flow_done && !b.uploaded && !b.queue_removed)),
        [batches, sortQueueBatches],
      );
      const pendingOrderKey = pendingBatches.map((b) => b.name).join('\0');
      React.useEffect(() => {
        const prev = prevQueueRef.current;
        prevQueueRef.current = pendingBatches;
        if (!prev.length) return;
        const currNames = new Set(pendingBatches.map((b) => b.name));
        const gone = [];
        prev.forEach((b, prevIdx) => {
          if (!currNames.has(b.name)) gone.push({ name: b.name, batch: b, prevIdx });
        });
        if (!gone.length) return;
        const prevIdxMap = new Map(prev.map((b, i) => [b.name, i]));
        const stamp = Date.now();
        const promoted = {};
        pendingBatches.forEach((b, newIdx) => {
          const oldIdx = prevIdxMap.get(b.name);
          if (oldIdx !== undefined && newIdx < oldIdx) promoted[b.name] = stamp;
        });
        const goneNames = new Set(gone.map((g) => g.name));
        setExitingRows((cur) => cur.filter((x) => !goneNames.has(x.name)).concat(gone));
        if (Object.keys(promoted).length) {
          setPromotedRanks((cur) => ({ ...cur, ...promoted }));
        }
        const tid = setTimeout(() => {
          if (!aliveRef.current) return;
          setExitingRows((cur) => cur.filter((x) => !goneNames.has(x.name)));
          setPromotedRanks((cur) => {
            const next = { ...cur };
            Object.keys(promoted).forEach((k) => { if (next[k] === stamp) delete next[k]; });
            return next;
          });
        }, 440);
        exitTimersRef.current.push(tid);
      }, [pendingOrderKey]);
      const finishedBatches = batches.filter((b) => b.flow_done && !b.uploaded);
      const sectionHead = (title) => h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 7 } },
        h('span', { style: T.sectionTitle }, title));

      const uptop = h('div', { style: { display: 'flex', flexDirection: 'column', flex: '0 0 auto' } },
        h('div', { style: { ...T.card, padding: '10px 12px', marginBottom: 0 } },
          sectionHead('试卷信息'),
          h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: '7px 12px' } },
            h('div', { style: { gridColumn: '1 / -1' } }, formField(TEXT.uploadTitle,
              h('input', { className: 'wf-input', style: inp, value: form.title, placeholder: '留空自动识别', onChange: (e) => setForm({ ...form, title: e.target.value }) }))),
            formField(TEXT.uploadYear,
              h('input', { className: 'wf-input', style: inp, value: form.year, placeholder: '自动识别', onChange: (e) => setForm({ ...form, year: e.target.value }) })),
            formField(TEXT.uploadRegion,
              h('input', { className: 'wf-input', style: inp, value: form.region, onChange: (e) => setForm({ ...form, region: e.target.value }) })),
            formField('题源分类',
              h('select', { className: 'wf-input', style: inp, value: form.source_type, onChange: (e) => setForm({ ...form, source_type: e.target.value }) },
                h('option', { value: '' }, '未选择'),
                taxonomy.sources.map((source) => h('option', { key: source, value: source }, source)))),
            formField('年级',
              h('select', { className: 'wf-input', style: inp, value: form.grade, onChange: (e) => setForm({ ...form, grade: e.target.value }) },
                h('option', { value: '' }, '未选择'),
                ['七年级', '八年级', '九年级'].map((grade) => h('option', { key: grade, value: grade }, grade)))),
          ),
          h('div', { style: { height: 1, background: 'var(--dsw-alias-border-l1)', margin: '9px 0 9px' } }),
          sectionHead(files.length ? '原卷文件 · 已选 ' + files.length + ' 个' : '原卷文件'),
          // 文件选择：中文浅灰控件。原生按钮（Choose Files / 深色描边 / 右侧英文状态）
          // 被隐藏，这里只保留一个浅灰文字入口，无底色、无黑框、无英文。
          h('div', { style: { display: 'flex', alignItems: 'stretch', gap: 10 } },
            h('label', {
              className: 'wf-file-drop' + (dragging ? ' wf-dragover' : ''),
              style: { flex: 1, minWidth: 0, width: 'auto' },
              onDragOver: (e) => { e.preventDefault(); setDragging(true); },
              onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false); },
              onDrop,
            },
              h('input', { ref: fileInputRef, className: 'wf-file-input', type: 'file', multiple: true, accept: '.docx,.pdf,.mp3,.wav,.m4a,.ogg,.flac,.aac', onChange: onFiles, 'aria-label': '选择上传文件' }),
              h('span', { style: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, color: 'var(--dsw-alias-label-secondary)' } }, uploadIcon(18)),
              h('span', { style: { display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 } },
                h('span', { className: 'wf-file-label' }, '选择上传文件')),
            ),
            h('button', { className: 'wf-btn-primary', style: { ...T.btnPrimary, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: submit }, busy ? TEXT.uploading : '上传'),
          ),
          files.length ? h('div', { style: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6, marginTop: 10 } },
            files.map((f, i) => {
              const isAudio = isAudioFilename(f.name);
              const isDoc = isDocFilename(f.name);
              const pred = isDoc ? predictUploadRole(f.name) : 'auto';
              const curRole = fileRoles[i] || 'auto';
              return h('span', { key: f.name + ':' + i, className: 'wf-file-chip', style: { gap: 6 } },
                h('span', { title: f.name, style: { maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, f.name),
                isAudio
                  ? h('span', { style: { fontSize: 10.5, padding: '1px 5px', borderRadius: 4, background: 'var(--dsw-alias-bg-layer-2, rgba(0,0,0,0.06))', color: 'var(--dsw-alias-label-secondary)' } }, '听力音频')
                  : (isDoc ? h('select', {
                      'aria-label': f.name + ' 材料角色',
                      value: curRole,
                      onChange: (e) => setFileRole(i, e.target.value),
                      style: { fontSize: 11, padding: '1px 4px', borderRadius: 4, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-base)', color: 'var(--dsw-alias-label-primary)', cursor: 'pointer', maxWidth: 118 },
                    }, MATERIAL_ROLE_OPTIONS.map((opt) => h('option', { key: opt.value, value: opt.value },
                      opt.value === 'auto' && pred !== 'auto' ? ('自动·' + (MATERIAL_ROLE_LABELS[pred] || pred)) : opt.label))) : null),
                h('button', { type: 'button', title: '移除', 'aria-label': '移除 ' + f.name, onClick: () => removeFile(i) }, closeIcon()),
              );
            })) : null,
          err ? h('div', { style: { ...T.statusErr, marginTop: 10, display: 'flex', alignItems: 'flex-start', gap: 7, lineHeight: 1.6 } },
            h('span', { style: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 15, height: 15, borderRadius: '50%', flexShrink: 0, marginTop: 2, background: 'var(--dsw-alias-state-error-primary)', color: '#fff', fontSize: 10, fontWeight: 700 } }, '!'),
            h('span', null, err)) : null,
          notice ? h('div', { role: 'status', 'aria-live': 'polite', style: { ...T.statusOk, marginTop: 10, lineHeight: 1.6 } }, notice) : null,
        ),
      );

      // 合并正在退场的已完成批次与当前排队批次：已完成项原地淡出收起，后续项立即更新序号并平滑上移。
      const queueDisplayRows = (() => {
        const rows = pendingBatches.map((b, i) => ({ kind: 'live', batch: b, rank: i + 1 }));
        if (exitingRows.length) {
          const sortedExits = exitingRows.slice().sort((a, b) => a.prevIdx - b.prevIdx);
          sortedExits.forEach((ex, offset) => {
            const pos = Math.max(0, Math.min(rows.length, ex.prevIdx + offset));
            rows.splice(pos, 0, { kind: 'exiting', batch: ex.batch, rank: 0 });
          });
        }
        return rows;
      })();

      // 左下：排队区常驻显示（即使没有处理中的试卷也保留这块区域和空状态）。
      // 只列「处理中」的试卷：内容审核与上传格式检查结束的已从队列移除，流转到试卷审核等待人工确认，
      // 但它们不再占用排队区，也不改变排队区本身始终存在。
      const queue = h('div', { style: { flex: 1, minHeight: 190, display: 'flex', flexDirection: 'column', marginTop: 12 } },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 } },
          h('span', { style: { fontSize: 14, fontWeight: 600 } }, '排队区'),
          h('span', { style: T.pill }, '共排队 ', h('span', { style: T.pillNum }, String(pendingBatches.length)), ' 份')),
        h('div', { className: 'wf-scroll', style: { flex: 1, overflow: 'auto', minHeight: 0, border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 12, background: 'var(--dsw-alias-bg-layer-1)', padding: 6, display: queueDisplayRows.length === 0 ? 'flex' : 'block', flexDirection: 'column' } },
          queueDisplayRows.length === 0
            ? h(EmptyState, {
                title: finishedBatches.length ? '暂无处理中的试卷' : '暂无试卷',
                hint: finishedBatches.length ? '上传新试卷会开始一轮新流程。' : '上传试卷后，会在这里排队。',
                flex: true, compact: true,
              })
            : queueDisplayRows.map((entry) => {
                const b = entry.batch;
                const exiting = entry.kind === 'exiting';
                const st = b.stage || {};
                const cur = st.current || {};
                const progress = compactProgress(st);
                const done = progress.done, total = progress.total;
                const pct = exiting ? 100 : (total ? Math.round(done / total * 100) : 0);
                const active = !exiting && b.name === sel;
                const blockedInQueue = !exiting && Boolean(b.stage_blocked);
                const promotedStamp = !exiting ? promotedRanks[b.name] : null;
                const rowCls = 'wf-queue-row' + (exiting ? ' wf-queue-row-exiting' : (promotedStamp ? ' wf-queue-row-promoted' : ''));
                return h('div', {
                  key: (exiting ? 'exit:' : '') + b.name,
                  className: rowCls,
                  onClick: exiting ? undefined : () => setSel(b.name),
                  style: {
                    display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px', borderRadius: 9, cursor: exiting ? 'default' : 'pointer',
                    background: active ? 'var(--wf-sel-bg)' : (blockedInQueue ? 'rgba(220, 38, 38, 0.05)' : 'transparent'),
                    border: blockedInQueue ? '1px solid rgba(220, 38, 38, 0.35)' : '1px solid transparent',
                    marginBottom: 3,
                    transition: 'background .16s',
                  },
                },
                  h('span', {
                    key: promotedStamp ? ('rank:' + entry.rank + ':' + promotedStamp) : ('rank:' + entry.rank),
                    className: promotedStamp ? 'wf-queue-rank-up' : '',
                    style: {
                      flex: '0 0 auto', width: 22, height: 22, borderRadius: 7,
                      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 11.5, fontWeight: 700,
                      background: exiting ? 'rgba(22,163,74,.16)' : (blockedInQueue ? 'rgba(220,38,38,.14)' : 'var(--dsw-alias-bg-layer-2)'),
                      color: exiting ? '#15803d' : (blockedInQueue ? 'var(--dsw-alias-state-error-primary)' : (active ? 'var(--wf-brand)' : 'var(--dsw-alias-label-secondary)')),
                    },
                  }, exiting ? '✓' : (blockedInQueue ? '!' : String(entry.rank))),
                  h('div', { style: { flex: 1, minWidth: 0 } },
                    // 排队区固定显示原始文件名（不随清洗后标题变化）；流程图上方仍按清洗后 paper.title 展示。
                    h('div', { style: { fontSize: 12.5, fontWeight: 600, color: exiting ? '#15803d' : (blockedInQueue ? 'var(--dsw-alias-state-error-primary)' : (active ? 'var(--wf-brand)' : 'var(--dsw-alias-label-primary)')), overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: (b.paper && b.paper.original_filename) || (b.paper && b.paper.title) || b.name }, (b.paper && b.paper.original_filename) || (b.paper && b.paper.title) || b.name),
                    h('div', { style: { ...T.hint, fontSize: 11, marginTop: 1, color: blockedInQueue ? 'var(--dsw-alias-state-error-primary)' : T.hint.color, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, exiting ? '已完成，已移出排队区' : ((cur.id ? shortStageName(cur) : (st.hint || '等待')) + (blockedInQueue ? ' · 异常停滞' : '') + (total ? ' · ' + done + '/' + total : ''))),
                    h('div', { style: { height: 3, borderRadius: 999, background: 'var(--dsw-alias-bg-layer-2)', marginTop: 6, overflow: 'hidden' } },
                      h('div', { style: { width: pct + '%', height: '100%', borderRadius: 999, background: exiting ? '#16a34a' : (blockedInQueue ? 'var(--dsw-alias-state-error-primary)' : 'var(--wf-brand)'), opacity: .75, transition: 'width .3s ease' } })),
                  ),
                  h('span', { style: { flex: '0 0 auto', fontSize: 11, color: exiting ? '#15803d' : 'var(--dsw-alias-label-secondary)', fontVariantNumeric: 'tabular-nums' } }, pct + '%'),
                );
              }),
        ),
      );



      // 右侧：12 类紧凑依赖图。判型后四路并行，听力只在内容审核汇合。
      // 整图随容器等比缩放；沿用现有主题与中性完成态，不展示常驻内部步骤。
      const selBatch = pendingBatches.find((b) => b.name === sel) || pendingBatches[0];
      const stage = (selBatch && selBatch.stage) || {};
      const sourceItems = stage.items || [];
      const isFlowOverview = !selBatch;
      const items = compactFlowStages(sourceItems, isFlowOverview);
      // “当前”可能有多个：并行阶段同时 active 时，当前环节集合就是它们对应的展示组，
      // 不再只指向单个 current，避免并行时节点轮流闪成唯一高亮。
      const curIds = new Set(items.filter((it) => it.inProgress).map((it) => it.id));
      const DONE = { completed: true, approved: true, skipped: true, completed_with_findings: true };
      const doneN = items.filter((it) => DONE[it.status]).length;
      // 简单蛇形布局：每行 3 个、共 4 行，节点按顺序首尾相接成一条线。
      // 不再画多条依赖分支线，只用一条折线把 12 个节点串起来（行末折回下一行）。
      // 尺寸整体收缩：更小的列宽/行距/边距，整图在容器里更紧凑。
      const SHOW = items.slice(0, 12);
      const COLS = 3;
      const R = 11, CW = 84, ROW_H = 86, PADX = 34, PADY = 22;
      const colX = (c) => PADX + c * CW;
      const rowY = (r) => PADY + r * ROW_H;
      const grid = SHOW.map((it, i) => {
        const row = Math.floor(i / COLS), col = i % COLS;
        // 偶数行从左到右，奇数行从右到左，相邻节点首尾折返成蛇形。
        const snakeCol = row % 2 === 0 ? col : COLS - 1 - col;
        return { it, i, row, col: snakeCol, x: colX(snakeCol), y: rowY(row) };
      });
      const svgW = PADX * 2 + (COLS - 1) * CW;
      const svgH = PADY * 2 + 3 * ROW_H + 58;
      // 节点名拆成最多两行：按中西文宽度估算，整词（中文按字）换行，超出才省略。
      function flowLabelLines(name) {
        const chars = Array.from(String(name == null ? '' : name).trim());
        const lines = [];
        let buf = '', width = 0, used = 0;
        for (let i = 0; i < chars.length; i++) {
          const ch = chars[i];
          const cw = /[\x20-\x7e]/.test(ch) ? 0.55 : 1;
          if (width + cw > 9 && buf) {
            lines.push(buf);
            buf = ''; width = 0;
            if (lines.length === 2) { used = i; break; }
          }
          buf += ch; width += cw; used = i + 1;
        }
        if (buf && lines.length < 2) lines.push(buf);
        if (used < chars.length && lines.length) {
          const last = lines[lines.length - 1];
          lines[lines.length - 1] = (last.length > 1 ? last.slice(0, -1) : last) + '…';
        }
        return lines.length ? lines : ['—'];
      }
      const todoLine = 'var(--wf-flow-line)';
      const doneLine = 'var(--wf-flow-line-done)';
      // 一条首尾相连的线：相邻节点之间连一段；同一行走水平直线穿到下一个节点，
      // 换行时在行末向下折、再沿下一行方向进入首节点。整条线共享一个“已完成”判定，
      // 只有当两端节点都已完成时该段才显示为已走过颜色。
      const segs = [], arrows = [];
      const lineFor = (a, b, reached) => {
        if (a.row === b.row) {
          const x1 = a.x + (b.x > a.x ? R : -R), x2 = b.x + (b.x > a.x ? -R : R);
          return { d: 'M' + x1 + ',' + a.y + ' L' + x2 + ',' + b.y, end: [x2, b.y], dir: b.x > a.x ? 'right' : 'left' };
        }
        // 换行折返：从行末节点向下，到下一行高度再水平进入下一行首节点。
        const dirX = b.x > a.x ? 1 : -1;
        const midY = (a.y + b.y) / 2 + (b.y > a.y ? -6 : 6);
        const x1 = a.x, y1 = a.y + (b.y > a.y ? R : -R);
        const x2 = b.x, y2 = b.y + (b.y > a.y ? -R : R);
        return { d: 'M' + x1 + ',' + y1 + ' L' + x1 + ',' + midY + ' L' + x2 + ',' + midY + ' L' + x2 + ',' + y2, end: [x2, y2], dir: 'down' };
      };
      for (let k = 0; k < grid.length - 1; k += 1) {
        const a = grid[k], b = grid[k + 1];
        const reached = !!DONE[a.it.status] && !!DONE[b.it.status];
        const live = !!a.it.inProgress || !!b.it.inProgress;
        const segLine = reached ? doneLine : live ? 'var(--wf-flow-line-live)' : todoLine;
        const seg = lineFor(a, b, reached);
        segs.push({ from: a.it.id, to: b.it.id, d: seg.d, reached, live, line: segLine });
        arrows.push({ x: seg.end[0], y: seg.end[1], dir: seg.dir, reached, live, line: segLine });
      }
      const nodes = grid;
      const flowFeedback = selBatch ? (advancing === selBatch.name
        ? { message: '正在提交继续处理请求…' }
        : advanceProgressFeedback(selBatch.advance, advanceFeedback[selBatch.name])) : { message: '' };
      const flowHead = h('div', { style: { flexShrink: 0 } },
        h('div', { style: { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' } },
          h('div', { style: { minWidth: 0, flex: 1 } },
            h('div', { style: { fontSize: 14, fontWeight: 600, letterSpacing: '-.01em' } }, isFlowOverview ? '流程总览' : '流程进度'),
            selBatch
              ? h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-label-primary)', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, (selBatch.paper && selBatch.paper.title) || selBatch.name)
              : null),
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end', paddingTop: 2 } },
            selBatch ? (() => { const queued = (selBatch.advance || {}).queued || (selBatch.advance || {}).waiting; const busy = advancing === selBatch.name || (selBatch.advance || {}).running || queued; const label = advancing === selBatch.name ? '提交中…' : ((selBatch.advance || {}).running ? '推进中…' : (queued ? ((selBatch.advance || {}).waiting ? '等待前一份…' : '排队中…') : '继续处理')); return h('button', { className: 'wf-btn-primary', style: { ...T.miniBtnPrimary, padding: '4px 12px' }, disabled: busy, title: '从当前阶段继续推进', onClick: () => advance(selBatch.name) }, label); })() : null),
            selBatch ? h('button', { className: 'wf-btn-danger', style: { ...T.btnDanger, padding: '4px 12px', color: 'var(--dsw-alias-state-error-primary)' }, title: '停止并彻底删除该批次（清空本地缓存）', onClick: () => openDel(selBatch.name) }, '删除') : null,
        ),
      );
      // 整图是一个自带 viewBox 的 SVG：随右栏宽度等比缩放，绝不会顶出卡片边界。
      // 悬停浮层里的子步骤状态：只显示“名称·状态”，保持极短，不显示长描述。
      function compactStatusLabel(status) {
        const map = { completed: '已完成', completed_with_findings: '有差异', approved: '已批准',
          skipped: '已跳过', failed: '失败', blocked: '受阻', needs_review: '待核验',
          invalidated: '需重跑', pending: '待处理', running: '处理中', warning: '提示' };
        return map[status] || statusText(status);
      }
      // 演示态没有真实子步骤时，按分类列出它包含的后台子流程名，供悬浮层说明。
      function flowOverviewSubsteps(it) {
        const names = (it.stage_ids || []).flatMap((id) => id === 'explanations'
          ? ['解析与大意填充', '听力录音材料直抄'] : [shortStageName({ id })]);
        return (names.length ? names : [shortStageName(it)]).map((name) => '· ' + name);
      }
      const flowDiagram = h('div', { className: 'wf-flow-canvas', style: { position: 'absolute', inset: '10px 8px 4px', display: 'flex', justifyContent: 'center', alignItems: 'center' } },
        h('svg', {
          viewBox: '0 0 ' + svgW + ' ' + svgH,
          role: 'group',
          // 双向自适应容器：宽高 100% 占满可视区，由 preserveAspectRatio 按 viewBox 等比完整容纳，
          // 面板变宽或变窄都不会撑高出纵向滚动条，始终一屏看全 12 个环节。
          preserveAspectRatio: 'xMidYMid meet',
          style: { display: 'block', width: '100%', height: '100%', maxWidth: '100%', maxHeight: '100%' },
          'aria-label': isFlowOverview ? '试卷处理流程总览：' + items.length + ' 个环节' : '试卷处理流程：' + items.length + ' 个阶段，已完成 ' + doneN + ' 个',
        },
          segs.map((s, si) => h('path', {
            key: 'p' + si, 'data-flow-from': s.from, 'data-flow-to': s.to, d: s.d, fill: 'none',
            stroke: s.line,
            strokeWidth: s.reached ? 2.2 : 1.8,
            strokeOpacity: s.reached ? .6 : 1,
            // 运行中的连线走流动虚线；已走过与未走保持原样。
            strokeDasharray: s.live ? '6 5' : null,
            className: s.live ? 'wf-flow-live' : null,
            strokeLinecap: 'round', strokeLinejoin: 'round',
          })),
          arrows.map((ar, ai) => {
            const s = 3.2;
            const d = ar.dir === 'right' ? 'M' + (ar.x - s) + ',' + (ar.y - s) + ' L' + (ar.x + s) + ',' + ar.y + ' L' + (ar.x - s) + ',' + (ar.y + s)
              : ar.dir === 'left' ? 'M' + (ar.x + s) + ',' + (ar.y - s) + ' L' + (ar.x - s) + ',' + ar.y + ' L' + (ar.x + s) + ',' + (ar.y + s)
                : 'M' + (ar.x - s) + ',' + (ar.y - s) + ' L' + ar.x + ',' + (ar.y + s) + ' L' + (ar.x + s) + ',' + (ar.y - s);
            return h('path', { key: 'a' + ai, d, fill: ar.line, stroke: 'none' });
          }),
          nodes.map((n) => {
            const it = n.it;
            const isCur = curIds.has(it.id);
            const done = !!DONE[it.status];
            const failed = it.status === 'blocked' || it.status === 'failed';
            const warn = it.status === 'needs_review' || it.status === 'invalidated' || it.status === 'completed_with_findings';
            // 深层中性色 + 状态只靠描边 / 对勾 / 中心点区分：
            // 已完成=实底浅灰 + 勾；未运行=浅空心；运行中=脉冲圈 + 呼吸点；失败/差异=彩色描边 + !
            const fillTone = done && !failed && !warn ? 'var(--wf-flow-node-done)'
              : failed || warn ? 'var(--wf-flow-node-alert)' : 'var(--wf-flow-node-idle)';
            const ring = failed ? 'var(--wf-flow-node-error)'
              : warn ? 'var(--wf-flow-node-warn)'
                : done ? 'var(--wf-flow-node-done-ring)'
                  : isCur ? 'var(--wf-flow-node-cur-ring)' : 'var(--wf-flow-node-ring)';
            const lines = flowLabelLines(shortStageName(it));
            // 悬浮/键盘读名要展开子流程：先给分类与状态，再逐条列出子步骤「名称·状态」。
            // 演示态（无任务）没有真实子步骤，给出各分类包含的子流程名作说明。
            const nodeLabel = isFlowOverview
              ? (shortStageName(it) + '\n' + flowOverviewSubsteps(it).join('\n'))
              : [shortStageName(it) + ' · ' + statusText(it.status)]
                .concat((it.substeps || []).map((m) => shortStageName(m) + ' · ' + compactStatusLabel(m.status)))
                .join('\n');
            return h('g', { key: it.id, role: 'img', tabIndex: 0, 'aria-label': nodeLabel, 'data-stage-ids': it.stage_ids.join(' '), 'data-status': it.status },
              // 运行中：脉冲外圈持续放大淡出，叠在实心环外侧。
              isCur ? h('circle', { className: 'wf-node-pulse', cx: n.x, cy: n.y, r: R, fill: 'none', stroke: 'var(--wf-flow-node-cur-ring)', strokeWidth: 1.4 }) : null,
              // 悬停和键盘读名共用真实短状态，不含长描述/备注。
              h('title', null, nodeLabel),
              h('circle', {
                cx: n.x, cy: n.y, r: R,
                fill: fillTone,
                stroke: ring, strokeWidth: 1.6,
              }),
              done && !warn ? h('text', { x: n.x, y: n.y + 4, textAnchor: 'middle', fontSize: 11, fontWeight: 700, fill: 'var(--wf-flow-node-done-mark)' }, '✓')
                : failed ? h('text', { x: n.x, y: n.y + 4, textAnchor: 'middle', fontSize: 11, fontWeight: 700, fill: 'var(--wf-flow-node-error)' }, '!')
                  : warn ? h('text', { x: n.x, y: n.y + 4, textAnchor: 'middle', fontSize: 11, fontWeight: 700, fill: 'var(--wf-flow-node-warn)' }, '!')
                    : isCur ? h('circle', { className: 'wf-node-dot', cx: n.x, cy: n.y, r: 3.6, fill: 'var(--wf-flow-node-cur-mark)' }) : null,
              // 节点名：两行居中；字号贴近正文（约 12px），与其他文字大小相近。
              lines.map((line, li) => h('text', {
                key: 'l' + li,
                x: n.x, y: n.y + R + 15 + li * 14,
                textAnchor: 'middle', fontSize: 11.5,
                fontWeight: isCur ? 700 : 500,
                fill: isCur ? 'var(--wf-brand)' : (done ? 'var(--dsw-alias-label-primary)' : 'var(--dsw-alias-label-secondary)'),
              }, line)),
            );
          }),
        ));
      const flow = h('div', { className: 'wf-upload-right', style: { minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', gap: 12 } },
        h('div', { className: 'wf-flow-card', style: { ...T.card, position: 'relative', paddingBottom: 12, flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', marginBottom: 0 } },
          flowHead,
          refreshError ? h('p', { role: 'alert', style: { ...T.statusErr, margin: '5px 0' } }, refreshError) : null,
          h('div', { className: 'wf-flow-stage', style: { flex: 1, minWidth: 0, minHeight: 0, position: 'relative', overflow: 'hidden', display: 'flex', justifyContent: 'center', alignItems: 'center' } },
            items.length === 0
              ? h('div', { style: { width: '100%', display: 'flex', flex: 1 } }, h(EmptyState, { title: '暂无流程', flex: true }))
              : flowDiagram),
          diagBanner(selBatch, stage, refresh, flowFeedback),
        ),
        h(OperationsPanel, { key: 'operations:' + (selBatch ? selBatch.name : ''), batchName: selBatch ? selBatch.name : '', isActive }),
      );

      return h('div', { className: 'wf-upload-layout' },
        h('div', { className: 'wf-upload-columns' },
          // 左列：上（上传）+ 下（排队区，只列处理中）
          h('div', { className: 'wf-upload-left wf-scroll' }, uptop, queue),
          // 右列：流程进度
          flow,
        ),
        delDialog,
      );
    }
    // AI 运维是附加观察面板；读取记录不启动接手，也不替代原有自愈。
    function operationsText(value) {
      if (value == null) return '';
      if (typeof value !== 'object') return String(value);
      try { return JSON.stringify(value); } catch (_) { return '无法读取详情'; }
    }
