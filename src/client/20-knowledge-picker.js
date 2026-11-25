    // ======================================================================
    // 知识点目录树选择组件：搜索框 + 已选知识点 + 按题型过滤的多选树状目录
    // ======================================================================
    const KNOWLEDGE_TREE_CACHE = new Map();

    function indexKnowledgeTree(items) {
      const selectableSet = new Set();
      const parentMap = new Map();
      const countMap = new Map();
      function walk(list, parentPath) {
        let total = 0;
        for (const node of list || []) {
          if (!node || !node.path) continue;
          if (parentPath) parentMap.set(node.path, parentPath);
          const isSel = Boolean(node.selectable != null ? node.selectable : node.leaf);
          if (isSel) selectableSet.add(node.path);
          const sub = walk(node.children || [], node.path);
          const own = (isSel ? 1 : 0) + sub;
          countMap.set(node.path, own);
          total += own;
        }
        return total;
      }
      walk(items, '');
      return { selectableSet, parentMap, countMap };
    }

    function filterKnowledgeTree(items, keyword) {
      const kw = String(keyword || '').trim().toLowerCase();
      if (!kw) return { items: items || [], expandPaths: new Set(), matchCount: 0 };
      const expandPaths = new Set();
      let matchCount = 0;
      function countSelectable(node) {
        const isSel = Boolean(node.selectable != null ? node.selectable : node.leaf);
        let n = isSel ? 1 : 0;
        for (const c of node.children || []) n += countSelectable(c);
        return n;
      }
      function collectBranchPaths(node) {
        if ((node.children || []).length) {
          expandPaths.add(node.path);
          for (const c of node.children) collectBranchPaths(c);
        }
      }
      function walk(list) {
        const out = [];
        for (const node of list || []) {
          if (!node || !node.path) continue;
          const nameHit = String(node.name || '').toLowerCase().includes(kw);
          const pathHit = String(node.path || '').toLowerCase().includes(kw);
          const isSel = Boolean(node.selectable != null ? node.selectable : node.leaf);
          if (nameHit || (isSel && pathHit)) {
            out.push(node);
            matchCount += countSelectable(node);
            collectBranchPaths(node);
            continue;
          }
          const kids = walk(node.children || []);
          if (kids.length) {
            expandPaths.add(node.path);
            out.push({ ...node, children: kids });
          }
        }
        return out;
      }
      return { items: walk(items), expandPaths, matchCount };
    }

    function KnowledgePicker(props) {
      const React = require('react');
      const h = React.createElement;
      const paths = Array.isArray(props.paths) ? props.paths : [];
      const mainType = String(props.mainType || '').trim();
      const onChange = props.onChange || (() => {});
      const fetchTree = props.fetchTree || ((t, signal) =>
        jget('/knowledge/tree' + (t ? '?type=' + encodeURIComponent(t) : ''), signal));
      const cacheKey = mainType || '__ALL__';
      const [query, setQuery] = React.useState('');
      const [treeData, setTreeData] = React.useState(() => KNOWLEDGE_TREE_CACHE.get(cacheKey) || null);
      const [loading, setLoading] = React.useState(() => !KNOWLEDGE_TREE_CACHE.has(cacheKey));
      const [loadError, setLoadError] = React.useState('');
      const [expanded, setExpanded] = React.useState({});
      const [locateReq, setLocateReq] = React.useState(null);
      const [locatedPath, setLocatedPath] = React.useState('');
      const treeBoxRef = React.useRef(null);

      React.useEffect(() => {
        const cached = KNOWLEDGE_TREE_CACHE.get(cacheKey);
        if (cached) {
          setTreeData(cached);
          setLoading(false);
          setLoadError('');
          return;
        }
        const ac = new AbortController();
        let alive = true;
        setLoading(true);
        setLoadError('');
        Promise.resolve(fetchTree(mainType, ac.signal))
          .then((data) => {
            if (!alive) return;
            const normalized = data && Array.isArray(data.items) ? data : { items: [], scoped: false, type: mainType, selectable_total: 0 };
            KNOWLEDGE_TREE_CACHE.set(cacheKey, normalized);
            setTreeData(normalized);
            setLoading(false);
          })
          .catch((err) => {
            if (!alive || (err && err.name === 'AbortError')) return;
            setLoadError(String((err && err.message) || err || '加载知识点目录失败'));
            setLoading(false);
          });
        return () => { alive = false; ac.abort(); };
      }, [cacheKey]);

      const treeItems = (treeData && Array.isArray(treeData.items)) ? treeData.items : [];
      const { selectableSet, parentMap, countMap } = React.useMemo(
        () => indexKnowledgeTree(treeItems),
        [treeItems],
      );

      React.useEffect(() => {
        const next = {};
        // 顶层维度不多（如完形填空只有 语篇体裁 / 主题）时默认展开一级，方便直接点选。
        if (treeItems.length > 0 && treeItems.length <= 3) {
          for (const root of treeItems) {
            if (root && root.path && (root.children || []).length) next[root.path] = true;
          }
        }
        // 自动展开当前已选知识点的所有祖先目录。
        for (const p of paths) {
          let cur = parentMap.get(p);
          while (cur) {
            next[cur] = true;
            cur = parentMap.get(cur);
          }
        }
        setExpanded((prev) => ({ ...next, ...prev }));
      }, [treeItems, paths.join('|')]);

      const searching = Boolean(query.trim());
      const filtered = React.useMemo(
        () => filterKnowledgeTree(treeItems, query),
        [treeItems, query],
      );
      const visibleItems = searching ? filtered.items : treeItems;
      const selectedSet = React.useMemo(() => new Set(paths), [paths]);
      const scoped = Boolean(treeData && treeData.scoped);

      function togglePath(p) {
        if (!p) return;
        if (selectedSet.has(p)) onChange(paths.filter((x) => x !== p));
        else onChange([...paths, p]);
      }
      function removePath(p) {
        onChange(paths.filter((x) => x !== p));
      }
      function toggleExpand(p) {
        setExpanded((prev) => ({ ...prev, [p]: !prev[p] }));
      }
      function locatePath(p) {
        if (!p || (!selectableSet.has(p) && !parentMap.has(p) && !countMap.has(p))) return;
        const next = {};
        let cur = parentMap.get(p);
        while (cur) {
          next[cur] = true;
          cur = parentMap.get(cur);
        }
        if (countMap.has(p)) next[p] = true;
        setExpanded((prev) => ({ ...prev, ...next }));
        if (query) setQuery('');
        setLocateReq({ path: p, seq: Date.now() });
      }

      React.useEffect(() => {
        if (!locateReq || !locateReq.path) return;
        const target = locateReq.path;
        const box = treeBoxRef.current;
        if (box && typeof box.querySelectorAll === 'function') {
          const rows = box.querySelectorAll('[data-path]');
          for (const el of rows) {
            if (el.getAttribute('data-path') === target) {
              if (typeof el.scrollIntoView === 'function') {
                el.scrollIntoView({ behavior: 'smooth', block: 'center' });
              }
              break;
            }
          }
        }
        setLocatedPath(target);
        const timer = setTimeout(() => {
          setLocatedPath((cur) => (cur === target ? '' : cur));
        }, 1500);
        return () => clearTimeout(timer);
      }, [locateReq]);

      function renderNodes(nodes, depth) {
        const rows = [];
        for (const node of nodes || []) {
          if (!node || !node.path) continue;
          const kids = Array.isArray(node.children) ? node.children : [];
          const hasKids = kids.length > 0;
          const isSel = Boolean(node.selectable != null ? node.selectable : node.leaf);
          const isOpen = hasKids && (searching ? filtered.expandPaths.has(node.path) : Boolean(expanded[node.path]));
          const isChecked = isSel && selectedSet.has(node.path);
          const count = countMap.get(node.path) || 0;
          rows.push(h('button', {
            key: node.path,
            type: 'button',
            className: 'wf-tree-row' + (isChecked ? ' is-selected' : '') + (isSel ? ' is-selectable' : ' is-branch') + (locatedPath === node.path ? ' is-located' : ''),
            'data-path': node.path,
            'data-selectable': isSel ? '1' : '0',
            'aria-expanded': hasKids ? (isOpen ? 'true' : 'false') : undefined,
            'aria-pressed': isSel ? (isChecked ? 'true' : 'false') : undefined,
            title: node.path,
            style: { paddingLeft: 8 + depth * 16 },
            onClick: () => {
              if (isSel) togglePath(node.path);
              else if (hasKids) toggleExpand(node.path);
            },
          },
            hasKids
              ? h('span', { className: 'wf-tree-caret', 'aria-hidden': 'true' }, isOpen ? '▾' : '▸')
              : h('span', { className: 'wf-tree-check', 'aria-hidden': 'true' }, isChecked ? '✓' : ''),
            h('span', { className: 'wf-tree-label' }, node.name || node.path),
            hasKids ? h('span', { className: 'wf-tree-count' }, String(count)) : null,
          ));
          if (hasKids && isOpen) {
            rows.push(...renderNodes(kids, depth + 1));
          }
        }
        return rows;
      }

      const totalSelectable = (treeData && typeof treeData.selectable_total === 'number')
        ? treeData.selectable_total : selectableSet.size;

      return h('div', { className: 'wf-label-left' },
        // 1. 搜索框（只搜当前题型范围内的知识点）
        h('div', { className: 'wf-kp-search-wrap' },
          h('input', {
            className: 'wf-input',
            style: { ...T.input, width: '100%', paddingRight: query ? 28 : 10 },
            value: query,
            'aria-label': '搜索知识点',
            placeholder: mainType ? '搜索「' + mainType + '」可用知识点…' : '输入关键词搜索知识点…',
            onChange: (e) => setQuery(e.target.value),
          }),
          query ? h('button', {
            type: 'button',
            className: 'wf-kp-clear',
            title: '清空搜索',
            'aria-label': '清空搜索',
            onClick: () => setQuery(''),
          }, '✕') : null,
        ),
        // 2. 已选知识点列表
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 5 } },
          h('div', { className: 'wf-kp-section-head' },
            h('span', { style: T.fieldLabel }, '已选知识点（' + paths.length + '）'),
            paths.length > 0 ? h('button', {
              type: 'button',
              onClick: () => onChange([]),
              style: { border: 'none', background: 'transparent', padding: 0, cursor: 'pointer', fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)' },
            }, '清空') : null,
          ),
          h('div', { className: 'wf-kp-chips' },
            paths.length === 0
              ? h('span', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, '未选择知识点，请在下方目录树点击添加')
              : paths.map((p) => {
                  const outOfScope = scoped && !loading && selectableSet.size > 0 && !selectableSet.has(p);
                  const canLocate = !loading && (selectableSet.has(p) || parentMap.has(p) || countMap.has(p));
                  return h('span', {
                    key: p,
                    className: 'wf-kp-chip' + (outOfScope ? ' is-outside' : '') + (canLocate ? ' is-locatable' : ''),
                    title: outOfScope
                      ? p + '（不在「' + mainType + '」允许范围内）'
                      : (canLocate ? p + '（点击快速定位到下方目录树）' : p),
                    onClick: canLocate ? () => locatePath(p) : undefined,
                  },
                    h('span', { className: 'wf-kp-chip-text' }, p),
                    outOfScope ? h('span', { className: 'wf-kp-chip-warn' }, '超出当前题型') : null,
                    h('button', {
                      type: 'button',
                      className: 'wf-kp-chip-rm',
                      title: '移除',
                      'aria-label': '移除 ' + p,
                      onClick: (e) => {
                        if (e && typeof e.stopPropagation === 'function') e.stopPropagation();
                        removePath(p);
                      },
                    }, '✕'),
                  );
                }),
          ),
        ),
        // 3. 知识点目录树（按当前题型过滤）
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 5 } },
          h('div', { className: 'wf-kp-section-head' },
            h('span', { style: T.fieldLabel }, mainType ? '「' + mainType + '」知识点目录' : '知识点目录'),
            h('span', null, loading ? '加载中…' : (searching ? '匹配 ' + filtered.matchCount + ' 项' : '可选 ' + totalSelectable + ' 项')),
          ),
          h('div', { ref: treeBoxRef, className: 'wf-kp-tree-box wf-scroll', role: 'tree', 'aria-label': '知识点目录树' },
            loading
              ? h('div', { style: { padding: '18px 12px', textAlign: 'center', fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, '正在加载知识点目录…')
              : loadError
                ? h('div', { style: { padding: '14px 12px', fontSize: 12, color: 'var(--dsw-alias-state-error-primary)' } }, loadError)
                : visibleItems.length === 0
                  ? h('div', { style: { padding: '18px 12px', textAlign: 'center', fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, searching ? '当前题型下未找到匹配知识点' : '该题型暂无可选知识点')
                  : renderNodes(visibleItems, 0),
          ),
        ),
      );
    }

