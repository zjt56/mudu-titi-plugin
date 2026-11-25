    // ======================================================================
    // 面板外壳：三个标签页
    // ======================================================================
    function WfPanel(props) {
      const React = require('react');
      const h = React.createElement;
      const [tab, setTab] = React.useState('upload');
      const [uploadedTick, setUploadedTick] = React.useState(0);
      const [visited, setVisited] = React.useState({ upload: true, review: false, typeReview: false, settings: false });
      const switchTab = (id) => {
        setTab(id);
        setVisited((v) => (v[id] ? v : { ...v, [id]: true }));
      };
      const tabBtn = (id, label) => h('button', { key: id, className: 'wf-nav-tab' + (tab === id ? ' wf-nav-tab-active' : ''), onClick: () => switchTab(id), style: T.tab(tab === id), 'aria-current': tab === id ? 'page' : undefined }, label);
      const paneStyle = (id) => ({ display: tab === id ? 'flex' : 'none', flex: 1, minWidth: 0, minHeight: 0, flexDirection: 'column' });
      const wrapTab = (name, label, child) => (typeof TabBoundary === 'function' ? h(TabBoundary, { name, label }, child) : child);
      return h('div', { className: 'wf-root', style: T.root },
        // Harness turns this header into a native drag region and excludes its buttons.
        h('div', { style: T.tabbar, 'data-window-drag': '' },
          h('div', { style: T.brand },
            h('img', { style: T.brandMark, src: BRAND_LOGO, alt: '', draggable: false }),
            h('span', { style: T.brandName }, TEXT.brandTitle)),
          h('div', { style: T.segWrap, role: 'tablist' },
            tabBtn('upload', TEXT.upload),
            tabBtn('review', TEXT.review),
            tabBtn('typeReview', TEXT.typeReview),
            tabBtn('settings', TEXT.settings)),
          h('span', { style: { flex: 1 } }),
          ...(typeof BackendRestartControl === 'function' ? [h(BackendRestartControl, { prominent: true, onRestarted: () => setUploadedTick((x) => x + 1) })] : []),
          props.closePanel ? h('button', { className: 'wf-icon-btn', title: TEXT.close, 'aria-label': TEXT.close, onClick: props.closePanel, style: { ...T.iconBtn, background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)', boxShadow: '0 1px 2px rgba(15, 23, 42, 0.06)' } }, closeIcon()) : null,
        ),
        h('div', { style: T.body },
          h('div', { style: paneStyle('upload') },
            wrapTab('upload', TEXT.upload,
              h(UploadView, {
                isActive: tab === 'upload',
                // 上传后留在上传页：继续看新试卷从第一步跑起的流程，不被自动跳走打断。
                onUploaded: () => { setUploadedTick((x) => x + 1); },
                onGoReview: () => { setUploadedTick((x) => x + 1); switchTab('review'); },
              }),
            ),
          ),
          visited.review ? h('div', { style: paneStyle('review') },
            wrapTab('review', TEXT.review,
              h(WorkbenchTab, { isActive: tab === 'review', uploadedTick }),
            ),
          ) : null,
          visited.typeReview ? h('div', { style: paneStyle('typeReview') },
            wrapTab('typeReview', TEXT.typeReview,
              h(TypeReviewTab, { isActive: tab === 'typeReview', uploadedTick }),
            ),
          ) : null,
          visited.settings ? h('div', { style: paneStyle('settings') },
            wrapTab('settings', TEXT.settings,
              h(SettingsTab, { isActive: tab === 'settings' }),
            ),
          ) : null,
        ),
      );
    }

    function SidebarIcon() {
      const React = require('react');
      const h = React.createElement;
      return h('span', { title: TEXT.title, 'aria-label': TEXT.title, style: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 20, height: 20, color: 'var(--dsw-alias-label-secondary)' } }, docIcon(17));
    }

    function apply(ctx) {
      try {
        if (!ctx || typeof ctx.inject !== 'function') { console.error('[wf-plugin] ctx.inject unavailable'); return; }
        try { injectStyles(); } catch (e) { /* 非浏览器环境忽略 */ }
        ctx.inject(['slots'], (scope) => {
          scope.slots.inject('main', () => scope.slots.register({ name: 'main', key: 'wf-plugin', inject: (p) => ({ ...(p || {}), presentation: 'panel' }) }, WfPanel));
          scope.slots.inject('sidebar.panellist', () => scope.slots.register({ name: 'sidebar.panellist', id: 'wf-plugin', order: 41, label: TEXT.title }, SidebarIcon));
        });
      } catch (e) { console.error('[wf-plugin] client apply failed:', e); }
    }

    exports.apply = apply;
    exports.inject = ['slots'];
    exports.__panel = { WfPanel, UploadView, ReviewView, WorkbenchTab, TypeReviewTab, SettingsTab, TabBoundary, ContentModal, LabelModal, BackendRestartControl, __cache: { IMG_CACHE, IMG_RESOLVED, UNIT_CACHE, syncCachedImage, collectBlockImageUrls, prefetchImages, getCachedUnit, setCachedUnit, invalidateBatchUnits, loadBatchUnitsBundle, batchCacheSignature, isCachedUnitStale, PreviewImage } };
    return module.exports;
  },
});
