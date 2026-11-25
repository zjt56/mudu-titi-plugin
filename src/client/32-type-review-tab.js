    // ======================================================================
    // 题型审核视图：跨批次按题型分页审核（每页最多 20 题，禁用整卷预拉取以控缓存）
    // ======================================================================
    var TYPE_REVIEW_PAGE_SIZE = 20;
    var UNTYPED_TYPE_VALUE = '__untyped__';

    function TypeReviewTab(props) {
      const React = require('react');
      const h = React.createElement;
      const isActive = props.isActive !== false;
      const [selectedType, setSelectedType] = React.useState(''); // '' = 全部题型
      const [statusFilter, setStatusFilter] = React.useState('all'); // 'all' | 'open'
      const [searchInput, setSearchInput] = React.useState('');
      const [appliedQuery, setAppliedQuery] = React.useState('');
      const [page, setPage] = React.useState(1);
      const [data, setData] = React.useState(null);
      const [loading, setLoading] = React.useState(false);
      const [err, setErr] = React.useState('');
      const [selectedKey, setSelectedKey] = React.useState('');
      const [typeModalOpen, setTypeModalOpen] = React.useState(false);
      const [typeModalFilter, setTypeModalFilter] = React.useState('');
      const [taxonomyTypes, setTaxonomyTypes] = React.useState([]);
      const listScrollRef = React.useRef(null);
      const seqRef = React.useRef(0);
      const abortRef = React.useRef(null);
      const clampedPageRef = React.useRef(null);
      const prevItemsRef = React.useRef([]);
      const pendingSelectEdgeRef = React.useRef('first');
      const pageFlipPendingRef = React.useRef(false);

      const itemKey = (it) => (it ? (String(it.batch_name || '') + '\0' + String(it.unit_id || it.number || '')) : '');

      React.useEffect(() => {
        if (!isActive || taxonomyTypes.length) return;
        let cancelled = false;
        jget('/taxonomy').then((tx) => {
          if (!cancelled && tx && Array.isArray(tx.types)) setTaxonomyTypes(tx.types);
        }).catch(() => {});
        return () => { cancelled = true; };
      }, [isActive, taxonomyTypes.length]);

      // 快速搜索框防抖：输入后自动触发分页查询并回到第 1 页
      React.useEffect(() => {
        const trimmed = String(searchInput || '').trim();
        if (trimmed === appliedQuery) return undefined;
        const timer = setTimeout(() => {
          pendingSelectEdgeRef.current = 'first';
          setAppliedQuery(trimmed);
          setPage(1);
        }, 220);
        return () => clearTimeout(timer);
      }, [searchInput, appliedQuery]);

      const loadPage = React.useCallback(async (opts) => {
        if (!isActive) {
          pageFlipPendingRef.current = false;
          return;
        }
        const silent = !!(opts && opts.silent);
        const seq = ++seqRef.current;
        if (abortRef.current) {
          try { abortRef.current.abort(); } catch (_) {}
        }
        const controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
        abortRef.current = controller;
        if (!silent) {
          setLoading(true);
          setErr('');
        }
        const qs = new URLSearchParams({
          page: String(Math.max(1, page)),
          page_size: String(TYPE_REVIEW_PAGE_SIZE),
          status: statusFilter,
        });
        if (selectedType) qs.set('type', selectedType);
        if (appliedQuery) qs.set('q', appliedQuery);
        try {
          const res = await jget('/type-review/units?' + qs.toString(), controller ? controller.signal : undefined);
          if (seq !== seqRef.current) return;
          const pageItems = Array.isArray(res && res.items) ? res.items : [];
          const prevItems = prevItemsRef.current || [];
          prevItemsRef.current = pageItems;
          setData(res || { items: [], total: 0, page: 1, page_size: TYPE_REVIEW_PAGE_SIZE, total_pages: 1, types: [] });
          if (res && typeof res.page === 'number' && res.page !== page) {
            clampedPageRef.current = res.page;
            setPage(res.page);
          }
          const edge = pendingSelectEdgeRef.current;
          pendingSelectEdgeRef.current = null;
          setSelectedKey((cur) => {
            if (!pageItems.length) return '';
            if (edge === 'first') return itemKey(pageItems[0]);
            if (edge === 'last') return itemKey(pageItems[pageItems.length - 1]);
            if (cur && pageItems.some((it) => itemKey(it) === cur)) return cur;
            // 当前题在审完后被移出列表（例如待审模式下点通过/驳回），顺移到原位置的下一题而非跳回第 1 题。
            if (cur && prevItems.length) {
              const prevIdx = prevItems.findIndex((it) => itemKey(it) === cur);
              if (prevIdx >= 0) return itemKey(pageItems[Math.min(prevIdx, pageItems.length - 1)]);
            }
            return itemKey(pageItems[0]);
          });
          setErr('');
        } catch (e) {
          if (seq !== seqRef.current) return;
          if (e && e.name === 'AbortError') return;
          if (!silent) setErr(String((e && e.message) || e || '加载失败'));
        } finally {
          if (abortRef.current === controller) abortRef.current = null;
          if (seq === seqRef.current) {
            pageFlipPendingRef.current = false;
            if (!silent) setLoading(false);
          }
        }
      }, [isActive, page, statusFilter, selectedType, appliedQuery]);

      React.useEffect(() => {
        if (!isActive) return;
        if (clampedPageRef.current !== null && clampedPageRef.current === page) {
          clampedPageRef.current = null;
          return;
        }
        loadPage();
      }, [isActive, loadPage, page, props.uploadedTick]);

      React.useEffect(() => () => {
        if (abortRef.current) {
          try { abortRef.current.abort(); } catch (_) {}
          abortRef.current = null;
        }
      }, []);

      const items = (data && Array.isArray(data.items)) ? data.items.slice(0, TYPE_REVIEW_PAGE_SIZE) : [];
      const total = Number((data && data.total) || 0);
      const totalPages = Math.max(1, Number((data && data.total_pages) || 1));
      const currentPage = Math.max(1, Math.min(totalPages, Number((data && data.page) || page || 1)));
      const allTotal = Number((data && data.all_total) || 0);
      const allPending = Number((data && data.all_pending) || 0);

      const typeEntries = React.useMemo(() => {
        if (data && Array.isArray(data.types) && data.types.length) return data.types;
        return taxonomyTypes.map((name) => ({ name, value: name, count: 0, pending: 0 }));
      }, [data, taxonomyTypes]);

      const selectedTypeLabel = React.useMemo(() => {
        if (!selectedType) return '全部题型';
        if (selectedType === UNTYPED_TYPE_VALUE) return '未判定';
        const hit = typeEntries.find((t) => t && t.value === selectedType);
        return (hit && hit.name) ? hit.name : selectedType;
      }, [selectedType, typeEntries]);

      const activeItem = React.useMemo(() => {
        if (!items.length) return null;
        return items.find((it) => itemKey(it) === selectedKey) || items[0];
      }, [items, selectedKey]);

      const sameBatchRows = React.useMemo(() => {
        if (!activeItem) return [];
        return items.filter((it) => it && it.batch_name === activeItem.batch_name);
      }, [items, activeItem]);

      // 切页后列表滚动回到顶部
      React.useEffect(() => {
        if (listScrollRef.current) listScrollRef.current.scrollTop = 0;
      }, [currentPage, selectedType, appliedQuery, statusFilter]);

      // 选中行滚进可视区
      React.useEffect(() => {
        const box = listScrollRef.current;
        if (!box || !activeItem) return;
        const targetKey = itemKey(activeItem);
        let rowEl = null;
        for (const el of box.querySelectorAll('[data-type-unit]')) {
          if (el.getAttribute('data-type-unit') === targetKey) { rowEl = el; break; }
        }
        if (!rowEl) return;
        const br = rowEl.getBoundingClientRect();
        const bc = box.getBoundingClientRect();
        if (br.top < bc.top + 4) box.scrollTop -= (bc.top - br.top) + 8;
        else if (br.bottom > bc.bottom - 4) box.scrollTop += (br.bottom - bc.bottom) + 8;
      }, [activeItem, items]);

      const flipToPage = React.useCallback((targetPage, edge) => {
        if (loading || pageFlipPendingRef.current) return false;
        const nextPage = Math.max(1, Math.min(totalPages, targetPage));
        if (nextPage === currentPage || nextPage === page) return false;
        pendingSelectEdgeRef.current = edge || 'first';
        pageFlipPendingRef.current = true;
        setPage(nextPage);
        return true;
      }, [loading, totalPages, currentPage, page]);

      // 键盘 ↑/↓ 或 j/k 切题（首尾单按跨页：向下到底后再按跳下一页第一题，长按不连翻），←/→ 翻页；任意模态弹窗打开时不拦截。
      React.useEffect(() => {
        if (!isActive || typeModalOpen || !items.length) return undefined;
        function onKey(e) {
          if (e.metaKey || e.ctrlKey || e.altKey) return;
          const tag = (e.target && e.target.tagName || '').toLowerCase();
          if (tag === 'input' || tag === 'textarea' || tag === 'select' || (e.target && e.target.isContentEditable)) return;
          if (typeof document !== 'undefined' && document.querySelector && document.querySelector('[role="dialog"][aria-modal="true"]')) return;
          if (e.key === 'ArrowRight') {
            if (e.repeat) { e.preventDefault(); return; }
            if (currentPage < totalPages) {
              e.preventDefault();
              flipToPage(currentPage + 1, 'first');
            }
            return;
          }
          if (e.key === 'ArrowLeft') {
            if (e.repeat) { e.preventDefault(); return; }
            if (currentPage > 1) {
              e.preventDefault();
              flipToPage(currentPage - 1, 'first');
            }
            return;
          }
          if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'j' && e.key !== 'k') return;
          const curKey = activeItem ? itemKey(activeItem) : '';
          let idx = items.findIndex((it) => itemKey(it) === curKey);
          if (idx < 0) idx = 0;
          const delta = (e.key === 'ArrowDown' || e.key === 'j') ? 1 : -1;
          const nextIdx = idx + delta;
          if (nextIdx >= items.length) {
            if (e.repeat) { e.preventDefault(); return; }
            if (currentPage < totalPages) {
              e.preventDefault();
              flipToPage(currentPage + 1, 'first');
            }
            return;
          }
          if (nextIdx < 0) {
            if (e.repeat) { e.preventDefault(); return; }
            if (currentPage > 1) {
              e.preventDefault();
              flipToPage(currentPage - 1, 'last');
            }
            return;
          }
          const next = items[nextIdx];
          if (next) {
            e.preventDefault();
            setSelectedKey(itemKey(next));
          }
        }
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
      }, [isActive, typeModalOpen, items, activeItem, currentPage, totalPages, flipToPage]);

      function chooseType(value) {
        pendingSelectEdgeRef.current = 'first';
        setSelectedType(value || '');
        setPage(1);
        setTypeModalOpen(false);
        setTypeModalFilter('');
      }

      const startIndex = total === 0 ? 0 : ((currentPage - 1) * TYPE_REVIEW_PAGE_SIZE + 1);
      const endIndex = total === 0 ? 0 : Math.min(total, startIndex + items.length - 1);

      const modalFilteredTypes = React.useMemo(() => {
        const kw = String(typeModalFilter || '').trim().toLowerCase();
        const base = kw
          ? typeEntries.filter((t) => String((t && t.name) || '').toLowerCase().includes(kw))
          : typeEntries.slice();
        // 弹窗优先展示有待审、其次有题目的题型，0 题题型沉底，组内保持词表原序。
        return base.map((t, idx) => ({ t, idx })).sort((a, b) => {
          const pa = Number((a.t && a.t.pending) || 0);
          const pb = Number((b.t && b.t.pending) || 0);
          const ca = Number((a.t && a.t.count) || 0);
          const cb = Number((b.t && b.t.count) || 0);
          const ga = pa > 0 ? 0 : (ca > 0 ? 1 : 2);
          const gb = pb > 0 ? 0 : (cb > 0 ? 1 : 2);
          if (ga !== gb) return ga - gb;
          if (pa !== pb) return pb - pa;
          if (ca !== cb) return cb - ca;
          return a.idx - b.idx;
        }).map((x) => x.t);
      }, [typeEntries, typeModalFilter]);

      const filterChip = (id, label, count) => {
        const activeChip = statusFilter === id;
        return h('button', {
          key: id,
          type: 'button',
          className: 'wf-filter-chip' + (activeChip ? ' wf-filter-chip-active' : ''),
          'aria-pressed': activeChip,
          onClick: () => { pendingSelectEdgeRef.current = 'first'; setStatusFilter(id); setPage(1); },
          style: {
            display: 'inline-flex', alignItems: 'center', gap: 5,
            padding: '5px 11px', fontSize: 12.5, fontWeight: activeChip ? 600 : 500, lineHeight: 1.2,
            borderRadius: 999,
            border: '1px solid ' + (activeChip ? 'var(--wf-sel-border)' : 'var(--dsw-alias-border-l2)'),
            background: activeChip ? 'var(--wf-sel-bg)' : 'var(--dsw-alias-bg-layer-1)',
            color: 'var(--dsw-alias-label-primary)',
            whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums',
          },
        },
          h('span', null, label),
          h('span', { style: { fontSize: 11, color: 'var(--dsw-alias-label-secondary)' } }, String(count)),
        );
      };

      const typeFilterBar = h('div', {
        role: 'region',
        'aria-label': '题型审核筛选',
        style: {
          display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '8px 10px',
          padding: '10px 18px', borderBottom: '1px solid var(--dsw-alias-border-l1)',
          background: 'var(--dsw-alias-bg-base)', flexShrink: 0,
        },
      },
        filterChip('all', '全部', allTotal),
        filterChip('open', '待审', allPending),
        h('button', {
          type: 'button',
          className: 'wf-btn',
          'aria-haspopup': 'dialog',
          'aria-expanded': typeModalOpen,
          onClick: () => { setTypeModalFilter(''); setTypeModalOpen(true); },
          style: {
            ...T.btn,
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '5px 12px', fontWeight: selectedType ? 600 : 500,
            background: selectedType ? 'var(--wf-sel-bg)' : 'var(--dsw-alias-bg-layer-1)',
            borderColor: selectedType ? 'var(--wf-sel-border)' : 'var(--dsw-alias-border-l2)',
          },
          title: '打开弹窗查看并选择全部题型',
        },
          h('span', null, '选择题型：' + selectedTypeLabel),
          h('span', { style: { fontSize: 10.5, opacity: 0.7 } }, '▾'),
        ),
        h('div', { style: { position: 'relative', display: 'flex', alignItems: 'center', flex: '1 1 220px', maxWidth: 340, minWidth: 160 } },
          h('input', {
            className: 'wf-input',
            type: 'search',
            'aria-label': '快速搜索题型或题目',
            value: searchInput,
            placeholder: '快速搜索题型 / 题号 / 试卷 / 知识点…',
            style: { ...T.input, width: '100%', padding: '5px 28px 5px 10px' },
            onChange: (e) => setSearchInput(e.target.value),
            onKeyDown: (e) => {
              if (e.key === 'Enter') {
                setAppliedQuery(String(searchInput || '').trim());
                setPage(1);
              }
            },
          }),
          searchInput ? h('button', {
            type: 'button',
            'aria-label': '清空搜索',
            title: '清空搜索',
            onClick: () => { setSearchInput(''); setAppliedQuery(''); setPage(1); },
            style: {
              position: 'absolute', right: 6, border: 'none', background: 'transparent',
              color: 'var(--dsw-alias-label-secondary)', cursor: 'pointer', fontSize: 13, lineHeight: 1, padding: '2px 4px',
            },
          }, '×') : null,
        ),
        h('span', { style: { flex: 1 } }),
        h('button', { className: 'wf-btn', style: T.btn, disabled: loading, onClick: () => loadPage() }, loading ? '加载中…' : TEXT.reload),
      );

      const listCol = h('div', {
        className: 'wf-review-list-col',
        style: {
          flex: '0 1 220px', minWidth: 140,
          borderRight: '1px solid var(--dsw-alias-border-l1)',
          display: 'flex', flexDirection: 'column', minHeight: 0,
          background: 'var(--dsw-alias-bg-layer-1)',
        },
      },
        h('div', { style: { padding: '8px 10px', borderBottom: '1px solid var(--dsw-alias-border-l1)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6, fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', flexShrink: 0 } },
          h('span', { style: { fontWeight: 600, color: 'var(--dsw-alias-label-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: selectedTypeLabel }, selectedTypeLabel),
          h('span', { style: { fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' } },
            total ? (startIndex + '–' + endIndex + ' / ' + total) : '0 题'),
        ),
        h('div', { className: 'wf-scroll', ref: listScrollRef, style: { flex: 1, overflow: 'auto', padding: '7px 7px 3px' } },
          loading && !items.length
            ? h('div', { style: { ...T.hint, padding: 14 } }, TEXT.loading)
            : err && !items.length
              ? h('div', { style: { ...T.statusErr, margin: 8, padding: 10, borderRadius: 8 } }, '加载失败：' + err)
              : items.length === 0
                ? h('div', { style: { ...T.hint, padding: 14 } }, '当前筛选条件下没有符合的题目')
                : items.map((r) => {
                    const key = itemKey(r);
                    const active = activeItem && itemKey(activeItem) === key;
                    const hasIssue = r.needs_human || r.state === 'attention' || r.state === 'blocked';
                    return h('div', {
                      key,
                      'data-type-unit': key,
                      className: 'wf-paper' + (active ? ' wf-active' : ''),
                      style: T.paperRow(active),
                      onClick: () => setSelectedKey(key),
                    },
                      h('span', {
                        style: { width: 44, flexShrink: 0, fontWeight: 600, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
                        title: Array.isArray(r.printed_numbers) && r.printed_numbers.length === 0 ? String((r.source_section || {}).title || '') : '',
                      }, Array.isArray(r.printed_numbers) && r.printed_numbers.length === 0 ? '—' : String(r.label || r.number || '')),
                      h('span', {
                        title: r.source_section ? [r.source_section.context, r.source_section.title].filter(Boolean).join(' · ') : '',
                        style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 11.5, opacity: .85 },
                      }, String(r.type || '').trim() || '未判定'),
                      r.excluded
                        ? h('span', { style: { ...T.badge(''), flexShrink: 0, fontSize: 10.5 } }, '听力跳过')
                        : h('span', {
                            className: 'wf-review-row-dot', role: 'img',
                            'aria-label': hasIssue ? '有审核问题' : (r.state === 'unverified' ? '尚未核对' : '无待审问题'),
                            title: hasIssue ? (r.open_summary || '有审核问题，请查看详情') : undefined,
                            style: {
                              width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                              background: hasIssue ? 'var(--dsw-alias-state-warn-primary)' : 'var(--dsw-alias-label-primary)',
                            },
                          }),
                    );
                  }),
        ),
        h('div', {
          role: 'navigation',
          'aria-label': '题型审核分页',
          style: {
            display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6,
            padding: '8px 10px', borderTop: '1px solid var(--dsw-alias-border-l1)',
            background: 'var(--dsw-alias-bg-base)', flexShrink: 0,
          },
        },
          h('button', {
            type: 'button', className: 'wf-btn',
            style: { ...T.miniBtn, padding: '4px 10px' },
            disabled: currentPage <= 1 || loading,
            onClick: () => flipToPage(currentPage - 1, 'first'),
          }, '上一页'),
          h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' } },
            currentPage + ' / ' + totalPages + ' 页'),
          h('button', {
            type: 'button', className: 'wf-btn',
            style: { ...T.miniBtn, padding: '4px 10px' },
            disabled: currentPage >= totalPages || loading,
            onClick: () => flipToPage(currentPage + 1, 'first'),
          }, '下一页'),
        ),
      );

      let rightPane;
      if (loading && !activeItem) {
        rightPane = h(EmptyState, { flex: true, title: '正在加载题型审核列表…' });
      } else if (err && !activeItem) {
        rightPane = h(EmptyState, {
          flex: true, tone: 'error', title: '加载失败：' + err,
          action: h('button', { className: 'wf-btn', style: { ...T.btn, marginTop: 12 }, onClick: () => loadPage() }, '重试'),
        });
      } else if (!activeItem) {
        rightPane = h(EmptyState, {
          flex: true,
          title: '没有符合条件的题目',
          hint: '可以调整搜索词、点击「选择题型」切换其他题型，或切回「全部」状态查看。',
          action: (selectedType || appliedQuery || statusFilter !== 'all')
            ? h('button', {
                className: 'wf-btn',
                style: { ...T.btn, marginTop: 12 },
                onClick: () => { setSelectedType(''); setSearchInput(''); setAppliedQuery(''); setStatusFilter('all'); setPage(1); },
              }, '重置筛选')
            : null,
        });
      } else {
        rightPane = h(ReviewView, {
          key: 'type-review:' + activeItem.batch_name,
          isActive,
          batchName: activeItem.batch_name,
          unitId: activeItem.unit_id,
          rows: sameBatchRows,
          hideListCol: true,
          disableBatchBundle: true,
          disableKeyNav: true,
          hidePaperPreview: true,
          hideAiSupplement: true,
          contextPaper: {
            title: activeItem.paper_title,
            batchName: activeItem.batch_name,
            uploaded: activeItem.uploaded,
          },
          onUnitMutated: () => loadPage({ silent: true }),
        });
      }

      const typePickerModal = typeModalOpen ? h(ModalShell, {
        title: '选择题型',
        tag: selectedTypeLabel,
        width: 680,
        onClose: () => setTypeModalOpen(false),
        footer: [
          selectedType ? h('button', { key: 'all', type: 'button', className: 'wf-btn', style: T.btn, onClick: () => chooseType('') }, '选择全部题型') : null,
          h('button', { key: 'close', type: 'button', className: 'wf-btn', style: T.btn, onClick: () => setTypeModalOpen(false) }, TEXT.close),
        ].filter(Boolean),
      },
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 12 } },
          h('input', {
            className: 'wf-input',
            type: 'search',
            autoFocus: true,
            value: typeModalFilter,
            placeholder: '筛选题型名称…',
            style: { ...T.input, width: '100%' },
            onChange: (e) => setTypeModalFilter(e.target.value),
          }),
          h('div', {
            role: 'listbox',
            'aria-label': '全部题型',
            style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 8 },
          },
            ...(!String(typeModalFilter || '').trim() || '全部题型'.includes(String(typeModalFilter || '').trim().toLowerCase()) ? [
              h('button', {
                key: '__all__',
                type: 'button',
                role: 'option',
                'aria-selected': selectedType === '',
                className: 'wf-type-grid-item',
                onClick: () => chooseType(''),
                style: {
                  display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4,
                  padding: '10px 12px', borderRadius: 10, textAlign: 'left',
                  border: '1px solid ' + (selectedType === '' ? 'var(--wf-sel-border)' : 'var(--dsw-alias-border-l2)'),
                  background: selectedType === '' ? 'var(--wf-sel-bg)' : 'var(--dsw-alias-bg-base)',
                  color: 'var(--dsw-alias-label-primary)',
                },
              },
                h('span', { style: { fontWeight: 600, fontSize: 13 } }, '全部题型'),
                h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', fontVariantNumeric: 'tabular-nums' } },
                  allTotal + ' 题' + (allPending > 0 ? (' · ' + allPending + ' 待审') : '')),
              ),
            ] : []),
            ...modalFilteredTypes.map((t) => {
              const isSelected = selectedType === t.value;
              return h('button', {
                key: t.value,
                type: 'button',
                role: 'option',
                'aria-selected': isSelected,
                className: 'wf-type-grid-item',
                onClick: () => chooseType(t.value),
                style: {
                  display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 4,
                  padding: '10px 12px', borderRadius: 10, textAlign: 'left',
                  border: '1px solid ' + (isSelected ? 'var(--wf-sel-border)' : 'var(--dsw-alias-border-l2)'),
                  background: isSelected ? 'var(--wf-sel-bg)' : 'var(--dsw-alias-bg-base)',
                  color: 'var(--dsw-alias-label-primary)',
                },
              },
                h('span', { style: { fontWeight: isSelected ? 600 : 500, fontSize: 13 } }, t.name),
                h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', fontVariantNumeric: 'tabular-nums' } },
                  Number(t.count || 0) + ' 题' + (Number(t.pending || 0) > 0 ? (' · ' + Number(t.pending) + ' 待审') : '')),
              );
            }),
          ),
          modalFilteredTypes.length === 0 ? h('div', { style: { ...T.hint, textAlign: 'center', padding: 12 } }, '没有匹配的题型') : null,
        ),
      ) : null;

      return h(React.Fragment, null,
        h('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 } },
          typeFilterBar,
          h('div', { style: { flex: 1, display: 'flex', minHeight: 0, minWidth: 0 } },
            listCol,
            rightPane,
          ),
        ),
        typePickerModal,
      );
    }

