// ---------------------------------------------------------------------------
// 录题工作流 插件版 · Client 半边（原生 Harness 面板，方案 A）
//
// 全部功能以插件原生 React 面板实现，不再 iframe 嵌入旧页面。
// 数据经宿主 /wf-plugin/api/* 透明代理到 Python 后端（与原工作台同一套接口），
// 因此数据逻辑完全复用既有实现，只重写前端，保证高效、准确、风格统一。
//
// 注册：main（keyed：wf-plugin，整页面板）、sidebar.panellist（侧边栏入口）。
// 视图：
//   · 试卷审核（默认）：左栏试卷列表 + 统计；右栏进入某批次审阅
//   · 审阅：原文预览（文字/图片/表格/音频）、小题列表、标签编辑、待人工处理
//   · 上传：新建批次并推进
//   · 模型与设置：默认/阶段/两轮审核模型
// ---------------------------------------------------------------------------
window.__ModuleLoader__.load({
  id: 'dsh-wf-plugin',
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;

    var API = '/wf-plugin/api';

    var TEXT = {
      title: '录题工作流',
      review: '试卷审核',
      upload: '上传试卷',
      settings: '模型与设置',
      paperList: '需要审核的试卷',
      noPaper: '还没有试卷批次，去「上传试卷」新建一份。',
      unitGroup: '题组',
      unitQ: '小题',
      unitHuman: '待审标签',
      unitOk: '无误',
      loading: '加载中…',
      back: '返回列表',
      reload: '刷新',
      archive: '删除批次',
      advance: '继续处理',
      saveLabels: '保存标签',
      saved: '已保存',
      saving: '保存中…',
      sourcePreview: '原卷预览',
      subquestions: '小题',
      labelEditor: '标签与属性',
      findings: '待人工处理',
      noFindings: '没有待人工处理的疑点。',
      type: '题型',
      subtype: '子类',
      difficulty: '难度',
      knowledge: '知识点',
      sourceType: '来源类型',
      source: '来源',
      categories: '分类',
      answer: '答案',
      stem: '题干',
      options: '选项',
      accept: '通过',
      reject: '驳回',
      fixed: '已修改',
      manual: '手动修改',
      close: '关闭',
      empty: '（无）',
      uploadTitle: '试卷名',
      uploadYear: '年份',
      uploadRegion: '地区',
      uploadSubject: '科目',
      uploadFiles: '文件',
      uploadSubmit: '上传并开始处理',
      uploading: '上传中…',
      uploadHint: '文件名含“答案/解析”的会自动识别为答案卷。',
      defaultModel: '默认模型（全局）',
      stageModels: '各阶段模型',
      reviewModels: '两轮审核模型',
      follow: '跟随默认',
      writeEffective: '写入生效配置',
      deleteConfirm: '删除批次',
    };

    // 统一视觉：全部用 Harness 主题 token，随浅/深色主题自动适配。
    // 刻度（全站共用，改一处即可整体协调）：圆角 10/8/6/999，间距 4/6/8/10/12/14/16，
    // 字号 11.5/12/12.5/13/13.5/14/14.5，字重 500/600/700。
    var T = {
      root: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-base)', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.5 },
      tabbar: { display: 'flex', alignItems: 'center', gap: 10, padding: '0 14px', height: 48, borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0 },
      brand: { display: 'flex', alignItems: 'center', gap: 8, marginRight: 4, flexShrink: 0 },
      brandMark: { width: 22, height: 22, borderRadius: 7, background: 'var(--dsw-alias-brand-primary)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', flexShrink: 0 },
      brandName: { fontSize: 12.5, fontWeight: 600, whiteSpace: 'nowrap' },
      segWrap: { display: 'inline-flex', alignItems: 'center', gap: 2, padding: 3, borderRadius: 9, background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)' },
      tab: (active) => ({
        padding: '5px 13px', borderRadius: 6, cursor: 'pointer', border: 'none', fontSize: 12.5,
        fontWeight: active ? 600 : 500, whiteSpace: 'nowrap',
        color: active ? 'var(--dsw-alias-brand-primary)' : 'var(--dsw-alias-label-secondary)',
        background: active ? 'var(--dsw-alias-bg-base)' : 'transparent',
        boxShadow: active ? '0 1px 3px rgba(0,0,0,.08)' : 'none',
        transition: 'color .18s cubic-bezier(.2,.8,.2,1), background .18s cubic-bezier(.2,.8,.2,1)',
      }),
      iconBtn: { width: 28, height: 28, padding: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderRadius: 7, cursor: 'pointer', fontSize: 15, lineHeight: 1, color: 'var(--dsw-alias-label-secondary)', background: 'transparent', border: '1px solid transparent', transition: 'background .18s, color .18s' },
      body: { flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden', background: 'var(--dsw-alias-bg-base)' },
      toolbar: { display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', minHeight: 44, borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0, flexWrap: 'wrap' },
      toolbarTitle: { fontWeight: 600, fontSize: 13.5 },
      paperRow: (active) => ({
        margin: '2px 6px', padding: '6px 8px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
        borderRadius: 7, transition: 'background .16s',
        background: active ? 'var(--dsw-alias-brand-primary)' : 'transparent',
        color: active ? '#fff' : 'var(--dsw-alias-label-primary)',
      }),
      main: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
      scroll: { flex: 1, overflow: 'auto', padding: 14 },
      card: {
        border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 10, padding: 14, marginBottom: 12,
        background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 1px 2px rgba(0,0,0,.05), 0 2px 10px rgba(0,0,0,.025)',
      },
      cardTitle: { fontSize: 13.5, fontWeight: 600, margin: '0 0 10px', color: 'var(--dsw-alias-label-primary)', display: 'flex', alignItems: 'center', gap: 8 },
      label: { minWidth: 88, fontSize: 12, color: 'var(--dsw-alias-label-secondary)' },
      fieldLabel: { fontSize: 11.5, fontWeight: 500, color: 'var(--dsw-alias-label-secondary)' },
      input: {
        padding: '7px 10px', borderRadius: 8, fontSize: 13, color: 'var(--dsw-alias-label-primary)',
        background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)',
        outline: 'none', boxSizing: 'border-box', transition: 'border-color .15s, box-shadow .15s',
      },
      btn: {
        padding: '5px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5,
        color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)',
        border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s, border-color .16s',
      },
      btnPrimary: {
        padding: '5px 14px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5, fontWeight: 600,
        color: '#fff', background: 'var(--dsw-alias-brand-primary)', border: '1px solid var(--dsw-alias-brand-primary)',
        transition: 'filter .16s',
      },
      btnDanger: {
        padding: '5px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5,
        color: 'var(--dsw-alias-state-error-primary)', background: 'transparent',
        border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s',
      },
      hint: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)' },
      statusOk: { fontSize: 12, color: 'var(--dsw-alias-state-success-primary)' },
      statusErr: { fontSize: 12, color: 'var(--dsw-alias-state-error-primary)' },
      badge: (kind) => ({
        fontSize: 11, fontWeight: 600, padding: '1px 7px', borderRadius: 999, lineHeight: 1.5,
        background: kind === 'warn' ? 'rgba(230,162,60,.14)' : 'var(--dsw-alias-bg-layer-2)',
        border: kind === 'warn' ? '1px solid rgba(230,162,60,.35)' : '1px solid var(--dsw-alias-border-l1)',
        color: kind === 'warn' ? 'var(--dsw-alias-state-warn-primary)' : 'var(--dsw-alias-label-secondary)',
      }),
      empty: { padding: '28px 16px', color: 'var(--dsw-alias-label-secondary)', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: 120 },
      emptyIcon: { width: 36, height: 36, borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)', marginBottom: 10 },
      emptyTitle: { fontSize: 13, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' },
      emptyHint: { fontSize: 12, marginTop: 4, maxWidth: 340, lineHeight: 1.6 },
      cardClickable: {
        border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 10, padding: '10px 12px', marginBottom: 8,
        background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 1px 2px rgba(0,0,0,.05)', cursor: 'pointer',
        transition: 'border-color .16s, box-shadow .16s',
      },
      paperCardTitle: { fontWeight: 600, fontSize: 13, lineHeight: 1.35, color: 'var(--dsw-alias-label-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
      paperCardMeta: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
      pill: { display: 'inline-flex', alignItems: 'baseline', gap: 3, fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 999, padding: '1px 8px', lineHeight: 1.6, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' },
      pillNum: { fontWeight: 700, fontSize: 12.5, color: 'var(--dsw-alias-label-primary)', fontVariantNumeric: 'tabular-nums' },
      pillWarn: { color: 'var(--dsw-alias-state-warn-primary)', borderColor: 'rgba(230,162,60,.4)', background: 'rgba(230,162,60,.1)' },
      miniBtn: { padding: '3px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 12, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s, border-color .16s' },
      miniBtnPrimary: { padding: '3px 12px', borderRadius: 6, cursor: 'pointer', fontSize: 12, fontWeight: 600, color: '#fff', background: 'var(--dsw-alias-brand-primary)', border: '1px solid var(--dsw-alias-brand-primary)', transition: 'filter .16s' },
      miniBtnDanger: { padding: '3px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 12, color: 'var(--dsw-alias-state-error-primary)', background: 'transparent', border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s' },
      // 自适应按钮：在按钮组内均分可用宽度，随容器伸缩，不写死宽度。
      adaptiveBtn: { flex: '1 1 0', minWidth: 0, whiteSpace: 'nowrap', textAlign: 'center', padding: '3px 2px', overflow: 'hidden', textOverflow: 'ellipsis' },
      // 弹窗外壳：两个弹窗共用，统一宽度、层级、圆角与阴影（带偏移+模糊）。
      modalOverlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,.42)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3000, padding: 16 },
      modalCard: (width) => ({ width, maxWidth: '94vw', maxHeight: '90vh', display: 'flex', flexDirection: 'column', background: 'var(--dsw-alias-bg-layer-1)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 12, boxShadow: '0 18px 50px rgba(0,0,0,.24), 0 2px 8px rgba(0,0,0,.08)', overflow: 'hidden' }),
      modalHead: { display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px', borderBottom: '1px solid var(--dsw-alias-border-l1)', flexShrink: 0 },
      modalBody: { padding: 16, overflow: 'auto', flex: 1 },
      modalFoot: { display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '10px 16px', borderTop: '1px solid var(--dsw-alias-border-l1)', flexShrink: 0 },
      modalTitle: { fontWeight: 600, fontSize: 14.5 },
      tagPill: { fontSize: 12, fontWeight: 600, color: '#fff', background: 'var(--dsw-alias-brand-primary)', borderRadius: 999, padding: '2px 9px', whiteSpace: 'nowrap' },
    };

    // 统一的线性图标：品牌标识与侧边栏入口共用同一笔画/线宽，不用 emoji 充当图标。
    function docIcon(size) {
      const h = require('react').createElement;
      return h('svg', { width: size, height: size, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': 'true' },
        h('path', { d: 'M4 2.5h5.5L12 5v8.5H4z' }),
        h('path', { d: 'M9.5 2.5V5H12' }),
        h('path', { d: 'M6 8h4M6 10.5h4' }));
    }
    function closeIcon() {
      const h = require('react').createElement;
      return h('svg', { width: 12, height: 12, viewBox: '0 0 12 12', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', 'aria-hidden': 'true' },
        h('path', { d: 'M2.5 2.5l7 7M9.5 2.5l-7 7' }));
    }

    // 全站共用的空/加载/错误态：同一套图标、字阶与留白；文案由调用方逐字传入。
    function EmptyState(props) {
      const h = require('react').createElement;
      const { title, hint, action, flex, tone } = props;
      const iconColor = tone === 'error' ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-secondary)';
      return h('div', { style: { ...T.empty, ...(flex ? { flex: 1 } : {}), color: iconColor } },
        h('span', { style: { ...T.emptyIcon, color: iconColor } }, docIcon(18)),
        h('span', { style: T.emptyTitle }, title),
        hint ? h('span', { style: T.emptyHint }, hint) : null,
        action || null,
      );
    }
    // 全站共用的表单字段：上标签下控件；标签与控件之间的间距统一。
    function formField(label, control) {
      const h = require('react').createElement;
      return h('label', { style: { display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 } },
        h('span', { style: T.fieldLabel }, label), control);
    }
    // 全站共用的弹窗外壳：标签编辑与原卷修改共用同一套层级、圆角、阴影与头部/底部。
    function ModalShell(props) {
      const h = require('react').createElement;
      const { title, tag, dirty, width, onClose, footer, children } = props;
      return h('div', { onClick: onClose, style: T.modalOverlay },
        h('div', { onClick: (e) => e.stopPropagation(), style: T.modalCard(width), role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
          h('div', { style: T.modalHead },
            h('span', { style: T.modalTitle }, title),
            tag ? h('span', { title: '当前正在修改的题目', style: T.tagPill }, tag) : null,
            dirty ? h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-state-warn-primary)' } }, '未保存') : null,
            h('span', { style: { flex: 1 } }),
            h('button', { className: 'wf-icon-btn', title: TEXT.close, 'aria-label': TEXT.close, onClick: onClose, style: T.iconBtn }, closeIcon()),
          ),
          h('div', { className: 'wf-scroll', style: T.modalBody }, children),
          h('div', { style: T.modalFoot }, ...(Array.isArray(footer) ? footer : [footer])),
        ),
      );
    }

    // 注入一次全局样式：hover/focus/滚动条/块级排版（内联样式表达不了）。
    var STYLE_ID = 'wf-plugin-styles';
    function injectStyles() {
      if (typeof document === 'undefined') return;
      // 热加载（HMR）时模块会重新执行，但旧的 <style> 元素仍留在 <head> 里。
      // 之前“已存在就 return”会导致一直用第一次注入的旧 CSS——改样式后看不到效果。
      // 现在始终用最新 CSS 覆盖：存在则替换其内容，不存在才新建。
      var existing = document.getElementById(STYLE_ID);
      var css = ''
        + '.wf-root *{box-sizing:border-box}'
        + '.wf-root button,.wf-root input,.wf-root textarea,.wf-root select{font-family:inherit}'
        + '.wf-root ::selection{background:rgba(47,91,234,.18);color:inherit}'
        + '.wf-root input,.wf-root textarea{caret-color:var(--dsw-alias-brand-primary)}'
        + '.wf-root button:focus-visible,.wf-root input:focus-visible,.wf-root textarea:focus-visible,.wf-root select:focus-visible,.wf-root a:focus-visible{outline:2px solid var(--dsw-alias-brand-primary);outline-offset:2px}'
        + '.wf-root button:disabled{opacity:.5;cursor:not-allowed!important;filter:none!important}'
        + '.wf-hoverable{cursor:pointer}'
        + '.wf-hoverable:hover{background:var(--dsw-alias-bg-layer-2)!important}'
        + '.wf-paper:hover{background:var(--dsw-alias-bg-layer-2)!important;color:var(--dsw-alias-label-primary)!important}'
        + '.wf-paper.wf-active:hover{filter:brightness(1.06)}'
        + '.wf-card-hover:hover{border-color:var(--dsw-alias-brand-primary)!important;box-shadow:0 4px 16px rgba(0,0,0,.09)!important}'
        // 按钮状态：内联 style 的 background/border 优先级更高，hover 必须 !important 才生效。
        + '.wf-btn:not(:disabled):hover{background:var(--dsw-alias-bg-layer-1)!important;border-color:var(--dsw-alias-brand-primary)!important;color:var(--dsw-alias-brand-primary)!important}'
        + '.wf-btn:not(:disabled):active{background:var(--dsw-alias-bg-layer-2)!important}'
        + '.wf-btn-primary:not(:disabled):hover{filter:brightness(1.08)}'
        + '.wf-btn-primary:not(:disabled):active{filter:brightness(.94)}'
        + '.wf-btn-danger:not(:disabled):hover{background:rgba(245,108,108,.1)!important;border-color:var(--dsw-alias-state-error-primary)!important}'
        + '.wf-icon-btn:hover{background:var(--dsw-alias-bg-layer-2)!important;color:var(--dsw-alias-label-primary)!important}'
        + '.wf-icon-btn:active{background:var(--dsw-alias-border-l1)!important}'
        + '.wf-input:focus{border-color:var(--dsw-alias-brand-primary)!important;box-shadow:0 0 0 3px rgba(47,91,234,.12)!important}'
        + '.wf-scroll::-webkit-scrollbar{width:6px;height:6px}'
        + '.wf-scroll::-webkit-scrollbar-thumb{background:var(--dsw-alias-border-l2);border-radius:999px}'
        + '.wf-scroll::-webkit-scrollbar-thumb:hover{background:var(--dsw-alias-label-secondary)}'
        + '.wf-scroll::-webkit-scrollbar-track{background:transparent}'
        + '.wf-prose{white-space:pre-wrap;line-height:1.85;font-size:13.5px}'
        + '.wf-prose p{margin:.3em 0}'
        + '.wf-img{max-width:100%;margin:8px 0;border-radius:8px;border:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-img:hover{border-color:var(--dsw-alias-brand-primary)}'
        + '.wf-paper{font-family:"Times New Roman","SimSun","Songti SC",serif;font-size:14.5px;line-height:1.95;color:var(--dsw-alias-label-primary);word-break:break-word}'
        + '.wf-line{margin:0 0 4px}'
        + '.wf-heading{font-weight:700;margin:10px 0 6px}'
        // 填空：试卷标准实线下划线，不用高亮底色
        + '.wf-blank{border-bottom:1.5px solid var(--dsw-alias-label-primary);padding:0 8px;color:inherit;font-weight:600;white-space:nowrap}'
        // 表格：试卷黑白线框，表头不加底色只加粗
        + '.wf-table{border-collapse:collapse;font-size:13px;margin:8px 0;font-family:"Times New Roman","SimSun",serif}'
        + '.wf-table td,.wf-table th{border:1px solid var(--dsw-alias-label-primary);padding:5px 10px;vertical-align:top;line-height:1.7}'
        + '.wf-table th{font-weight:700;background:transparent;text-align:left}'
        + '.wf-table u{text-decoration:none;border-bottom:1.5px solid var(--dsw-alias-label-primary);padding:0 6px}'
        + '.wf-find{display:block;padding:10px 0;border-top:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-find:first-child{border-top:none}'
        // 审核两栏：左（原卷预览）与右（待人工处理）各自独立纵向滚动，不整体滚动。
        //   外层只在面板过窄时横向滚动；grid 限高为一行，两栏各自 overflow-y:auto。
        + '.wf-review-container{flex:1;min-height:0;overflow-x:auto;overflow-y:hidden;display:flex;flex-direction:column}'
        + '.wf-review-layout{display:grid;grid-template-columns:minmax(300px,1fr) minmax(240px,285px);grid-template-rows:minmax(0,1fr);align-items:stretch;gap:12px;padding:12px;min-width:564px;flex:1;min-height:0}'
        + '.wf-review-layout .wf-img{display:block;max-width:100%;max-height:260px;width:auto;height:auto;object-fit:contain;margin:4px 0}'
        // 选项配图：把「A. 图 B. 图 C. 图」这类选项横向一行排列，接近试卷。
        + '.wf-opt-row{display:flex;flex-wrap:wrap;align-items:flex-end;gap:14px;margin:6px 0}'
        + '.wf-opt-cell{display:flex;flex-direction:column;align-items:flex-start;gap:2px;flex:0 1 auto;max-width:32%}'
        + '.wf-opt-cell .wf-opt-label{font-weight:600;font-size:13px;line-height:1.2}'
        + '.wf-opt-cell .wf-img{margin:0;max-height:200px;max-width:100%}'
        + '.wf-review-layout .wf-paper{font-size:13.5px;line-height:1.75}'
        + '.wf-review-layout .wf-line{font-size:13px!important;line-height:1.75!important}'
        + '.wf-review-layout .wf-review-main{min-width:0;max-width:100%;width:auto;min-height:0;max-height:100%;overflow-y:auto;overflow-x:hidden}'
        // 右栏「标签审核」卡片固定不滑动：不设内部滚动，高度随内容（交给整块容器的纵向滚动/按需）。
        + '.wf-review-layout .wf-review-notes{min-width:0;max-width:100%;width:auto;min-height:0;max-height:none;overflow:visible;align-self:start}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar,.wf-review-layout .wf-review-notes::-webkit-scrollbar{width:6px;height:6px}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar-thumb,.wf-review-layout .wf-review-notes::-webkit-scrollbar-thumb{background:var(--dsw-alias-border-l2);border-radius:999px}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar-thumb:hover,.wf-review-layout .wf-review-notes::-webkit-scrollbar-thumb:hover{background:var(--dsw-alias-label-secondary)}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar-track,.wf-review-layout .wf-review-notes::-webkit-scrollbar-track{background:transparent}'
        + '.wf-preitem{white-space:nowrap;flex:0 0 auto}'
        + '.wf-group-head{display:flex;align-items:center;gap:4px;width:100%;padding:8px 10px;margin:4px 0;cursor:pointer;background:var(--dsw-alias-bg-base);border:1px solid var(--dsw-alias-border-l1);border-radius:8px;text-align:left;color:var(--dsw-alias-label-primary);transition:background .16s,border-color .16s}'
        + '.wf-group-head:hover{background:var(--dsw-alias-bg-layer-2);border-color:var(--dsw-alias-brand-primary)}';
      var el = existing || document.createElement('style');
      el.id = STYLE_ID;
      el.textContent = css;
      if (!existing) document.head.appendChild(el);
    }


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
    async function jpost(path, body) {
      const r = await window.fetch(API + path, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body || {}),
      });
      if (!r.ok) throw new Error((await r.text()) || r.status);
      return r.json();
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
    // ---- 图片缓存 + 并发去重 + 预取 --------------------------------------------------
    // 同一 URL 的题图只下载/解码一次：来回切题直接命中缓存，消除重复下载造成的卡顿。
    // Map<url, Promise<string>>：Promise 值兼作“正在下载”占位，避免同一张图并发多取。
    var IMG_CACHE = new Map();
    var IMG_CACHE_ORDER = [];      // LRU 次序（先入先出）
    var IMG_CACHE_MAX = 400;       // 最多缓存 400 张（题图很小，约几 MB 量级）
    function imgCacheRemember(url, promise) {
      IMG_CACHE.set(url, promise);
      IMG_CACHE_ORDER.push(url);
      while (IMG_CACHE_ORDER.length > IMG_CACHE_MAX) {
        const old = IMG_CACHE_ORDER.shift();
        if (IMG_CACHE.has(old) && old !== url) IMG_CACHE.delete(old);
      }
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
      if (cached) return cached;
      const p = fetchImageOnce(url).then(
        (src) => src,
        (err) => { IMG_CACHE.delete(url); const i = IMG_CACHE_ORDER.indexOf(url); if (i >= 0) IMG_CACHE_ORDER.splice(i, 1); throw err; }
      );
      imgCacheRemember(url, p);
      return p;
    }
    // 带缓存的取图：命中缓存直接返回；否则共享下载。同时尊重【调用方】的取消与超时——
    // 调用方 abort 只取消它自己的等待（抛 AbortError），不会打断共享下载/污染缓存。
    function fetchPreviewImage(value, signal) {
      const url = imageRequestUrl(value);
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
    // 预取：把（可能很快切换到的）图片在后台提前放进缓存，忽略错误。
    function prefetchImages(values) {
      (values || []).forEach((v) => {
        try {
          const url = imageRequestUrl(v);
          if (!/^(blob:|data:image\/)/i.test(url) && !IMG_CACHE.has(url)) fetchPreviewImage(url).catch(() => {});
        } catch {}
      });
    }

    function PreviewImage({ url, name }) {
      const React = require('react');
      const h = React.createElement;
      const [attempt, setAttempt] = React.useState(0);
      const [image, setImage] = React.useState(null);
      React.useEffect(() => {
        let active = true;
        // 命中缓存时立即渲染，避免“图片加载中…”闪烁造成的跳动感。
        const cached = (() => { try { return IMG_CACHE.get(imageRequestUrl(url)); } catch { return null; } })();
        if (cached) {
          cached.then((src) => { if (active) setImage({ url, src }); }).catch(() => {});
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
      if (!image || image.url !== url) return h('span', { role: 'status', style: T.hint }, '图片加载中…');
      if (image.error) return h('div', { role: 'status', style: { ...T.hint, margin: '8px 0' } },
        '图片未显示：' + (name || '') + '（' + image.error + '） ',
        h('button', { className: 'wf-btn', style: T.btn, onClick: () => { try { const u = imageRequestUrl(url); IMG_CACHE.delete(u); const i = IMG_CACHE_ORDER.indexOf(u); if (i >= 0) IMG_CACHE_ORDER.splice(i, 1); } catch {} setAttempt((n) => n + 1); } }, '重试图片'));
      return h('img', {
        src: image.src, alt: name || '试题配图', className: 'wf-img',
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

    function modelKey(m) { return m ? m.provider + '|' + m.model : ''; }

    // 试卷正文渲染：先转义，再只放行 <u><b><i><strong><em> 与 <br>，把 **x**→粗体、
    // __x__→下划线、全角空格包裹的题号 →下划线填空样式。与原工作台 markLine 口径一致。
    function escHtml(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    function markLine(text) {
      let out = escHtml(text);
      out = out.replace(/&lt;(\/?)(u|b|i|strong|em|br)&gt;/gi, '<$1$2>'); // 放行白名单标签
      out = out.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/__(.+?)__/g, '<u>$1</u>');
      out = out.replace(/\u3000(\d{1,3})\u3000/g, '<span class="wf-blank">$1</span>');
      return out;
    }
    function isHeaderRow(row) {
      return Array.isArray(row) && row.length > 0 && row.every((c) => String(c == null ? '' : c).length <= 10);
    }
    const SECTION_RE = /^(?:第[一二三四五六七八九十]+部分|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.|[IVX]{1,4}\.)/;
    function isHeading(text) { return SECTION_RE.test(String(text || '').trim()); }

    // ======================================================================
    // 上传试卷视图
    // ======================================================================
    function UploadView(props) {
      const React = require('react');
      const h = React.createElement;
      const [form, setForm] = React.useState({ title: '', year: '', region: '安徽', subject: 'english' });
      const [files, setFiles] = React.useState([]);
      const [busy, setBusy] = React.useState(false);
      const [err, setErr] = React.useState('');

      function onFiles(e) {
        setFiles(Array.from(e.target.files || []));
      }
      async function submit() {
        if (!files.length) { setErr('请选择原卷文件（docx/pdf，可带答案与音频）'); return; }
        setBusy(true); setErr('');
        try {
          const fd = new FormData();
          fd.append('title', form.title || '');
          fd.append('year', form.year || '');
          fd.append('region', form.region || '');
          fd.append('subject', form.subject || 'english');
          for (const f of files) fd.append('files', f, f.name);
          const r = await window.fetch(API + '/uploads', { method: 'POST', body: fd });
          if (!r.ok) throw new Error((await r.text()) || r.status);
          const data = await r.json();
          setFiles([]);
          if (props.onUploaded) props.onUploaded(data);
        } catch (e) { setErr(String(e.message || e)); }
        finally { setBusy(false); }
      }

      const inp = { ...T.input, width: '100%' };
      return h('div', { className: 'wf-scroll', style: { flex: 1, overflow: 'auto', padding: 16 } },
        h('div', { style: { maxWidth: 640, margin: '0 auto' } },
          h('div', { style: { fontSize: 16, fontWeight: 600, marginBottom: 4 } }, '上传试卷'),
          h('div', { style: { ...T.hint, marginBottom: 16 } }, '选原卷（docx / pdf，可带答案与听力音频），提交后自动建批次并推进流程。'),
          h('div', { style: T.card },
            h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px 16px' } },
              h('label', { style: { gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: 5 } },
                h('span', { style: T.fieldLabel }, TEXT.uploadTitle),
                h('input', { className: 'wf-input', style: inp, value: form.title, placeholder: '如 2025年安徽省初中学业水平考试英语', onChange: (e) => setForm({ ...form, title: e.target.value }) })),
              h('label', { style: { display: 'flex', flexDirection: 'column', gap: 5 } },
                h('span', { style: T.fieldLabel }, TEXT.uploadYear),
                h('input', { className: 'wf-input', style: inp, value: form.year, placeholder: '2025', onChange: (e) => setForm({ ...form, year: e.target.value }) })),
              h('label', { style: { display: 'flex', flexDirection: 'column', gap: 5 } },
                h('span', { style: T.fieldLabel }, TEXT.uploadRegion),
                h('input', { className: 'wf-input', style: inp, value: form.region, onChange: (e) => setForm({ ...form, region: e.target.value }) })),
            ),
            h('label', { style: { display: 'flex', flexDirection: 'column', gap: 5, marginTop: 14 } },
              h('span', { style: T.fieldLabel }, TEXT.uploadFiles),
              h('input', { type: 'file', multiple: true, accept: '.docx,.pdf,.mp3,.wav,.m4a,.ogg,.flac', onChange: onFiles, style: { fontSize: 12.5, padding: '10px', border: '1px dashed var(--dsw-alias-border-l2)', borderRadius: 8, background: 'var(--dsw-alias-bg-base)', width: '100%', boxSizing: 'border-box', cursor: 'pointer' } }),
            ),
            files.length ? h('div', { style: { ...T.hint, marginTop: 8 } }, '已选 ' + files.length + ' 个文件：' + files.map((f) => f.name).join('、')) : null,
            err ? h('div', { style: { ...T.statusErr, marginTop: 10 } }, err) : null,
            h('div', { style: { marginTop: 18, display: 'flex', gap: 12, alignItems: 'center' } },
              h('button', { className: 'wf-btn-primary', style: T.btnPrimary, disabled: busy, onClick: submit }, busy ? TEXT.uploading : TEXT.uploadSubmit),
              h('span', { style: T.hint }, TEXT.uploadHint),
            ),
          ),
        ),
      );
    }

    // ======================================================================
    // 知识点搜索添加组件：输入关键词 → 调 /api/knowledge → 选合法叶子加入
    // ======================================================================
    function KnowledgePicker(props) {
      const React = require('react');
      const h = React.createElement;
      const paths = props.paths || [];
      const onChange = props.onChange || (() => {});
      const [query, setQuery] = React.useState('');
      const [items, setItems] = React.useState([]);
      const [open, setOpen] = React.useState(false);
      const [loading, setLoading] = React.useState(false);
      const [timer, setTimer] = React.useState(null);

      function search(q) {
        setQuery(q);
        if (timer) clearTimeout(timer);
        if (!q.trim()) { setItems([]); setOpen(false); return; }
        setLoading(true);
        const t = setTimeout(async () => {
          try {
            const r = await window.fetch('/wf-plugin/api/knowledge?q=' + encodeURIComponent(q.trim()) + '&limit=30', { headers: { accept: 'application/json' } });
            const d = await r.json();
            setItems((d.items || []).map((it) => it.path));
            setOpen(true);
          } catch (e) { setItems([]); }
          finally { setLoading(false); }
        }, 220);
        setTimer(t);
      }
      function add(p) {
        if (!p || paths.includes(p)) return;
        onChange([...paths, p]);
        setQuery(''); setItems([]); setOpen(false);
      }
      function remove(p) { onChange(paths.filter((x) => x !== p)); }

      return h('div', null,
        h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: paths.length ? 8 : 0 } },
          paths.map((p) => h('span', { key: p, style: { display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, padding: '2px 8px', borderRadius: 8, background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)' } },
            p,
            h('button', { onClick: () => remove(p), title: '移除', style: { border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--dsw-alias-label-secondary)', fontSize: 13, padding: 0 } }, '✕'),
          )),
          paths.length === 0 ? h('span', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, '未选择知识点') : null,
        ),
        h('div', { style: { position: 'relative' } },
          h('input', {
            className: 'wf-input', style: { ...T.input, width: '100%' },
            value: query, placeholder: '输入关键词搜索知识点（如 语音 / 词义 / 定语从句），点击添加',
            onChange: (e) => search(e.target.value),
            onFocus: () => { if (items.length) setOpen(true); },
          }),
          open && items.length ? h('div', { className: 'wf-scroll', style: { position: 'absolute', zIndex: 10, left: 0, right: 0, top: '100%', marginTop: 4, maxHeight: 240, overflow: 'auto', background: 'var(--dsw-alias-bg-overlay)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 10, boxShadow: '0 8px 24px rgba(0,0,0,.18)' } },
            items.filter((p) => !paths.includes(p)).map((p) => h('div', {
              key: p, onClick: () => add(p), className: 'wf-hoverable',
              style: { padding: '7px 11px', fontSize: 12.5, cursor: 'pointer', borderBottom: '1px solid var(--dsw-alias-border-l1)' },
            }, p)),
            items.filter((p) => !paths.includes(p)).length === 0 ? h('div', { style: { padding: '8px 11px', fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, '（都已添加）') : null,
          ) : null,
          loading ? h('div', { style: { position: 'absolute', right: 10, top: 9, fontSize: 11, color: 'var(--dsw-alias-label-secondary)' } }, '搜索中…') : null,
        ),
      );
    }

    // ======================================================================
    // 修改标签弹窗
    // ======================================================================
    function LabelModal(props) {
      const React = require('react');
      const h = React.createElement;
      const { tax, labels, setL, knowledgeStr, warnings, busy, onSave, onClose,
        unitLabel, unitKind, numbers } = props;
      // 当前正在修改哪一题：单题显示「第 X 题」，题组显示「第 X 题（题组，共 N 小题）」
      // 或题号区间，避免在弹窗里改标签时不知道改的是第几题。
      const nums = (numbers || []).map((n) => String(n));
      const labelText = String(unitLabel == null ? '' : unitLabel).trim();
      const isGroup = unitKind === 'group' || nums.length > 1;
      let rangeText = '';
      if (nums.length > 1) {
        const sorted = nums.slice().sort((a, b) => (Number(a) || 0) - (Number(b) || 0));
        rangeText = sorted[0] + '–' + sorted[sorted.length - 1];
      }
      let qTitle = '';
      if (isGroup && rangeText) qTitle = '第 ' + rangeText + ' 题';      // 题组优先显示真实覆盖区间
      else if (labelText) qTitle = '第 ' + labelText + ' 题';
      else if (nums.length === 1) qTitle = '第 ' + nums[0] + ' 题';
      const qTag = isGroup
        ? qTitle + '（题组·' + nums.length + ' 小题）' : qTitle;
      const subs = (tax.subtypes || {})[labels?.type] || [];
      return h(ModalShell, {
        title: '修改标签', tag: qTag, width: 560, onClose,
        footer: [
          h('button', { key: 'cancel', className: 'wf-btn', style: T.btn, onClick: onClose }, '取消'),
          h('button', { key: 'save', className: 'wf-btn-primary', style: T.btnPrimary, onClick: onSave, disabled: busy === 'save' }, busy === 'save' ? TEXT.saving : TEXT.saveLabels),
        ],
      },
        h(React.Fragment, null,
          h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 14px' } },
            formField(TEXT.type, h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: labels?.type || '', onChange: (e) => { setL('type', e.target.value); setL('subtypes', []); } },
              h('option', { value: '' }, '—'), (tax.types || []).map((t) => h('option', { key: t, value: t }, t)))),
            formField(TEXT.subtype, h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: (labels?.subtypes || [])[0] || '', onChange: (e) => setL('subtypes', e.target.value ? [e.target.value] : []) },
              h('option', { value: '' }, '—'), subs.map((s) => h('option', { key: s, value: s }, s)))),
            formField(TEXT.difficulty, h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: labels?.difficulty || '', onChange: (e) => setL('difficulty', e.target.value) },
              h('option', { value: '' }, '—'), (tax.difficulties || []).map((d) => h('option', { key: d, value: d }, d)))),
            formField(TEXT.sourceType, h('input', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: labels?.source_type || '', onChange: (e) => setL('source_type', e.target.value) })),
            formField(TEXT.source, h('input', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: labels?.source || '', onChange: (e) => setL('source', e.target.value) })),
          ),
          h('div', { style: { marginTop: 12 } }, formField(TEXT.knowledge,
            h(KnowledgePicker, { paths: labels?.knowledge || [], onChange: (arr) => setL('knowledge', arr) }))),
          h('div', { style: { marginTop: 12 } }, formField(TEXT.categories,
            h('input', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: (labels?.categories || []).join(', '), placeholder: '多值用逗号分隔', onChange: (e) => setL('categories', e.target.value.split(',').map((x) => x.trim()).filter(Boolean)) }))),
          (warnings || []).length ? h('div', { style: { ...T.hint, color: 'var(--dsw-alias-state-warn-primary)', marginTop: 10 } }, '提示：' + (warnings || []).join('；')) : null,
        ),
      );
    }

    // 原卷修改弹窗：正文/材料、解析（整组共用）+ 逐小题题干/选项/答案。
    // 组题按每个小题单独折叠（点一题开一题），不一次性全部展开。
    function ContentModal(props) {
      const React = require('react');
      const h = React.createElement;
      const { content, detail, onSub, onUnit, onSave, onClose, busy, dirty } = props;
      const subs = (content && content.subs) || [];
      const isGroup = (detail && (detail.unit_kind === 'group' || (detail.numbers || []).length > 1));
      const [openSubs, setOpenSubs] = React.useState(() => isGroup ? {} : subs.reduce((m, s) => { m[String(s.number)] = true; return m; }, {}));
      const toggleSub = (num) => setOpenSubs((x) => ({ ...x, [String(num)]: !x[String(num)] }));
      const nums = ((detail && detail.numbers) || []).map(String);
      const range = nums.length > 1 ? (nums.slice().sort((a, b) => (Number(a) || 0) - (Number(b) || 0)).reduce((acc, n, i, arr) => i === 0 ? n : (i === arr.length - 1 ? acc + '–' + n : acc), '')) : (nums[0] || '');
      const qTag = range ? ('第 ' + range + ' 题' + (isGroup ? '（题组·' + nums.length + ' 小题）' : '')) : '';
      return h(ModalShell, {
        title: '原卷修改', tag: qTag, dirty, width: 720, onClose,
        footer: [
          h('button', { key: 'cancel', className: 'wf-btn', style: T.btn, onClick: onClose }, '取消'),
          h('button', { key: 'save', className: 'wf-btn-primary', style: T.btnPrimary, onClick: onSave, disabled: busy === 'content' }, busy === 'content' ? '保存中…' : '保存内容'),
        ],
      },
        h(React.Fragment, null,
          h('div', { style: { display: 'flex', flexDirection: 'column', gap: 12, marginBottom: subs.length ? 14 : 0 } },
            formField('正文 / 材料（整组共用，含听力原文、阅读原文）',
              h('textarea', { className: 'wf-input', value: content.material, rows: isGroup ? 10 : 6,
                style: { ...T.input, width: '100%', resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.7, minHeight: isGroup ? 180 : 110 },
                onChange: (e) => onUnit('material', e.target.value) })),
            formField('解析',
              h('textarea', { className: 'wf-input', value: content.explanation, rows: 3,
                style: { ...T.input, width: '100%', resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.7 },
                onChange: (e) => onUnit('explanation', e.target.value) })),
          ),
          subs.length === 0 ? null : h('div', { style: { borderTop: '1px solid var(--dsw-alias-border-l1)', paddingTop: 6 } },
            subs.map((s, i) => {
              const open = !!openSubs[String(s.number)];
              return h('div', { key: String(s.number), style: { borderBottom: '1px solid var(--dsw-alias-border-l1)' } },
                h('button', { className: 'wf-group-head', onClick: () => toggleSub(s.number) },
                  h('span', { style: { fontSize: 12, fontWeight: 700, color: '#fff', background: 'var(--dsw-alias-brand-primary)', borderRadius: 6, padding: '1px 7px' } }, s.number),
                  h('span', { style: { fontSize: 13, fontWeight: 600, marginLeft: 6 } }, '第 ' + s.number + ' 题'),
                  h('span', { style: { ...T.hint, marginLeft: 8, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, (s.stem || '').slice(0, 40)),
                  h('span', { style: { color: 'var(--dsw-alias-label-secondary)', fontSize: 12 } }, open ? '▾' : '▸'),
                ),
                open ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 8, padding: '4px 2px 12px' } },
                  formField('题干', h('textarea', { className: 'wf-input', value: s.stem, rows: 2, style: { ...T.input, width: '100%', resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.6 }, onChange: (e) => onSub(i, 'stem', e.target.value) })),
                  formField('选项（每行一项）', h('textarea', { className: 'wf-input', value: s.optionsText, rows: 4, placeholder: '每行一项，如 A. xxx', style: { ...T.input, width: '100%', resize: 'vertical', fontFamily: 'inherit', lineHeight: 1.6 }, onChange: (e) => onSub(i, 'optionsText', e.target.value) })),
                  formField('答案', h('input', { className: 'wf-input', value: s.answer, style: { ...T.input, width: '100%' }, onChange: (e) => onSub(i, 'answer', e.target.value) })),
                ) : null,
              );
            }),
          ),
        ),
      );
    }

    // ======================================================================
    // 审阅视图（原工作台三栏：单元列表 + 工作区原文/小题/标签/待人工）
    // ======================================================================
    function ReviewView(props) {
      const React = require('react');
      const h = React.createElement;
      const { batchName, unitId, rows } = props;
      const onSelectUnit = props.onSelectUnit || (() => {});
      const [detail, setDetail] = React.useState(null);
      const [labels, setLabels] = React.useState(null);
      const [busy, setBusy] = React.useState('');
      const [err, setErr] = React.useState('');
      const [filter, setFilter] = React.useState('all'); // all | open | blocked | unverified
      const [q, setQ] = React.useState('');
      const [labelDlg, setLabelDlg] = React.useState(false);
      const [content, setContent] = React.useState(null); // 可编辑内容：{ material, explanation, subs:[{number,stem,optionsText,answer}] }
      const [contentDirty, setContentDirty] = React.useState(false);
      const [contentDlg, setContentDlg] = React.useState(false); // 「原卷修改」弹窗

      const requestGate = React.useRef(null);
      if (!requestGate.current) requestGate.current = createRequestGate();
      const mounted = React.useRef(false);
      const load = React.useCallback(async () => {
        if (!mounted.current) return;
        const request = requestGate.current.begin();
        setErr('');
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
            setDetail(d);
            setLabels(d.labels || {});
            initContent(d);
            setErr('');
            return;
          } catch (e) {
            if (!request.current()) return;
            lastErr = e;
            const msg = String((e && e.message) || e);
            if (/\b(400|401|403|404)\b/.test(msg)) break; // 非瞬时错误，不重试
          }
        }
        if (request.current()) setErr(String((lastErr && lastErr.message) || lastErr || '加载失败'));
      }, [batchName, unitId]);
      React.useEffect(() => {
        mounted.current = true;
        load();
        return () => { mounted.current = false; requestGate.current.cancel(); };
      }, [load]);
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
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/units/' + encodeURIComponent(unitId), { labels });
          setBusy('saved');
          setTimeout(() => setBusy(''), 1500);
          load();
        } catch (e) { setErr(String(e.message || e)); setBusy(''); }
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
      // 从 detail 生成可编辑内容快照；load 成功后调用。
      function initContent(d) {
        if (!d) { setContent(null); return; }
        const item = d.item || {};
        setContent({
          material: item.material != null ? String(item.material) : '',
          explanation: item.explanation != null ? String(item.explanation) : '',
          subs: (d.subquestions || []).map((s) => ({
            number: s.number,
            stem: s.stem != null ? String(s.stem) : '',
            optionsText: optionsToText(s.options),
            options: s.options,
            answer: s.answer != null ? String(s.answer) : '',
          })),
        });
        setContentDirty(false);
      }
      function setSubField(idx, key, val) {
        setContent((c) => {
          if (!c) return c;
          const subs = c.subs.slice();
          subs[idx] = { ...subs[idx], [key]: val };
          return { ...c, subs };
        });
        setContentDirty(true);
      }
      function setUnitField(key, val) {
        setContent((c) => (c ? { ...c, [key]: val } : c));
        setContentDirty(true);
      }
      async function saveContent() {
        if (!content) return;
        setBusy('content');
        try {
          const items = (content.subs || []).map((s) => ({
            number: s.number, stem: s.stem,
            options: textToOptions(s.optionsText, s.options),
            answer: s.answer,
          }));
          const unit = { material: content.material ?? '', explanation: content.explanation ?? '' };
          await jpost('/batches/' + encodeURIComponent(batchName) + '/units/' + encodeURIComponent(unitId), { items, unit });
          setContentDirty(false);
          setBusy('');
          setContentDlg(false); // 保存成功关闭弹窗
          load();
        } catch (e) { setErr(String(e.message || e)); setBusy(''); }
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
          load();
        } catch (e) { setErr(String(e.message || e)); }
      }

      // ---- 单元列表：过滤 + 搜索 ----
      const allRows = rows || [];
      const filteredRows = React.useMemo(() => {
        return allRows.filter((r) => {
          if (filter === 'open' && !r.needs_human) return false;
          if (filter === 'blocked' && r.state !== 'blocked') return false;
          if (filter === 'unverified' && r.state !== 'unverified') return false;
          if (q) {
            const hay = (String(r.label || '') + String(r.number || '') + String(r.type || '') + String((r.knowledge || []).join(' '))).toLowerCase();
            if (!hay.includes(q.toLowerCase())) return false;
          }
          return true;
        });
      }, [allRows, filter, q]);

      // 预取图片：当前题全部图立即进缓存，并顺手预取上一/下一题的图，
      // 切换题组时命中缓存、几乎瞬开，消除“切换卡顿”。
      React.useEffect(() => {
        if (!detail) return;
        const curImgs = (detail.preview_blocks || []).filter((b) => b && b.url).map((b) => b.url);
        prefetchImages(curImgs);
        const list = filteredRows || [];
        let idx = list.findIndex((r) => (r.unit_id || r.number) === unitId);
        if (idx < 0) return;
        const neigh = [list[idx - 1], list[idx + 1]].filter(Boolean);
        let cancelled = false;
        (async () => {
          for (const r of neigh) {
            if (cancelled) return;
            try {
              const nid = r.unit_id || r.number;
              const nd = await jget('/batches/' + encodeURIComponent(batchName) + '/units/' + encodeURIComponent(nid));
              if (cancelled) return;
              prefetchImages((nd.preview_blocks || []).filter((b) => b && b.url).map((b) => b.url));
            } catch {}
          }
        })();
        return () => { cancelled = true; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
      }, [detail, unitId]);

      // ---- 键盘 ↑/↓（或 j/k）切换题组；输入框内不拦截 ----
      React.useEffect(() => {
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
      }, [filteredRows, unitId, onSelectUnit]);

      const sel = (v) => ({ value: v || '', onChange: (e) => { /* handled by caller */ } });

      // 原文预览块 -> React
      // 选项标签：形如 "A." / "\tB." / "（C）" 的短文本。
      function optionLabel(text) {
        const t = String(text == null ? '' : text).replace(/[\s\u3000]+/g, '');
        return /^[A-Ha-h][.．、)）]?$/.test(t) || /^[（(][A-Ha-h][)）]$/.test(t) ? t : null;
      }
      function renderBlocks(blocks) {
        const list = blocks || [];
        const out = [];
        const isOptLabel = (b) => b && b.type === 'text' && optionLabel(b.text) !== null;
        const isImg = (b) => b && b.type === 'image';
        for (let i = 0; i < list.length; i++) {
          const b = list[i];
          // 连续「选项标签 + 图片」成组横排（试卷选项 A/B/C 一行）。
          if (isOptLabel(b) && isImg(list[i + 1])) {
            const cells = [];
            let j = i;
            while (j < list.length && isOptLabel(list[j]) && isImg(list[j + 1])) {
              cells.push({ label: optionLabel(list[j].text), block: list[j + 1] });
              j += 2;
            }
            if (cells.length >= 2) {
              out.push(h('div', { key: 'optrow:' + i, className: 'wf-opt-row' },
                cells.map((c, ci) => h('div', { key: ci, className: 'wf-opt-cell' },
                  h('span', { className: 'wf-opt-label' }, c.label),
                  h(PreviewImage, { url: c.block.url, name: c.block.name })))));
              i = j - 1;
              continue;
            }
          }
          if (b.type === 'text') {
            const heading = isHeading(b.text);
            out.push(h('div', {
              key: i,
              className: 'wf-line' + (heading ? ' wf-heading' : ''),
              style: { whiteSpace: 'pre-wrap', lineHeight: 1.85, fontSize: 13.5 },
              dangerouslySetInnerHTML: { __html: markLine(b.text) },
            }));
            continue;
          }
          if (b.type === 'image') {
            out.push(h(PreviewImage, { key: 'image:' + String(b.url || b.name || '') + ':' + i, url: b.url, name: b.name }));
            continue;
          }
          if (b.type === 'table') {
            out.push(h('div', { key: i, style: { overflowX: 'auto', margin: '8px 0' } },
              h('table', { className: 'wf-table' },
                h('tbody', null, (b.rows || []).map((row, ri) => {
                  const head = isHeaderRow(row);
                  return h('tr', { key: ri }, (row || []).map((cell, ci) => {
                    const props = { key: ci, dangerouslySetInnerHTML: { __html: markLine(cell) } };
                    return head ? h('th', props) : h('td', props);
                  }));
                })))));
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
        const chs = (r.checks || []).filter((c) => !c.number); // 单元级 check
        const withA = chs.find((c) => c.assessment && Object.keys(c.assessment).length);
        if (withA) return { status: r.status, assessment: withA.assessment };
        return { status: r.status, assessment: null };
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
      // 一行：板块标题 + 「值」；不一致时标红。
      function cmpRow(label, value, opts) {
        const o = opts || {};
        const empty = value == null || value === '' || (Array.isArray(value) && !value.length);
        const text = empty ? (o.pending ? '待审核' : '—') : (Array.isArray(value) ? value.join('、') : String(value));
        const color = empty
          ? 'var(--dsw-alias-label-secondary)'
          : (o.mismatch ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-primary)');
        return h('div', { style: { display: 'flex', gap: 8, alignItems: 'baseline', padding: '3px 0' } },
          h('span', { style: { flex: '0 0 auto', width: 64, fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, label),
          h('span', { style: { flex: 1, minWidth: 0, fontSize: 12.5, color, fontWeight: o.mismatch ? 600 : 400, wordBreak: 'break-word' } }, text),
          o.mismatch ? h('span', { style: { flex: '0 0 auto', fontSize: 11, color: 'var(--dsw-alias-state-error-primary)' } }, '不一致') : null,
        );
      }
      function labelAuditPanels(detail) {
        const comp = detail.comparison || {};
        const orig = (comp.original && comp.original.labels) || {};
        const cur = detail.labels || {};
        const r1 = roundAssessment(comp, 'labels');   // 一审
        const r2 = roundAssessment(comp, 'sample');   // 二审
        const meta = detail.meta || {};
        const kpShort = (list) => (Array.isArray(list) ? list.map((x) => {
          const s = String(x == null ? '' : x).trim();
          const parts = s.split(/[\/>＞]/).map((p) => p.trim()).filter(Boolean);
          return parts.length > 1 ? parts[parts.length - 1] : s;
        }).filter(Boolean) : []);
        const kpSame = (a, b) => JSON.stringify(kpShort(a || [])) === JSON.stringify(kpShort(b || []));

        // ===== 板块一：知识点标签（原 / 一审 / 二审 三行，承载审核结果给人看）=====
        const kpOrig = kpShort(orig.knowledge || cur.knowledge);
        const kp1 = r1 && r1.assessment ? kpShort(r1.assessment.knowledge) : null;
        const kp2 = r2 && r2.assessment ? kpShort(r2.assessment.knowledge) : null;
        const kpRows = h('div', { style: { padding: '2px 0 8px' } },
          h('div', { style: { fontSize: 12.5, fontWeight: 600, margin: '2px 0 6px' } }, '知识点标签'),
          cmpRow('原标签', kpOrig, {}),
          cmpRow('一审标签', kp1, { pending: !r1 || !r1.assessment, mismatch: !!(kp1 && kp1.length && !kpSame(kp1, kpOrig)) }),
          cmpRow('二审标签', kp2, { pending: !r2 || !r2.assessment, mismatch: !!(kp2 && kp2.length && !kpSame(kp2, kpOrig)) }),
        );

        // ===== 板块二：难度 / 来源 / 分类（平铺，不分一审二审）=====
        const firstDiff = (a) => {
          if (!a) return null;
          if (Array.isArray(a)) { const it = a.find((x) => x && x.value); return it ? it.value : null; }
          return a.value || (typeof a === 'string' ? a : null);
        };
        const diffOrig = cur.difficulty || (detail.preview_labels && detail.preview_labels.difficulty) || null;
        const diff1 = r1 && r1.assessment ? firstDiff(r1.assessment.difficulty) : null;
        const diff2 = r2 && r2.assessment ? firstDiff(r2.assessment.difficulty) : null;
        const source = meta.source || meta.paper_title || cur.source || null;
        const catList = (arr) => (Array.isArray(arr) ? arr : (arr ? [arr] : []));
        const cat = catList(cur.categories).length ? catList(cur.categories)
          : (cur.source_type ? [cur.source_type] : (meta.source_type ? [meta.source_type] : []));
        const flatRow = (label, value) => h('div', { style: { display: 'flex', gap: 8, alignItems: 'baseline', padding: '3px 0' } },
          h('span', { style: { flex: '0 0 auto', width: 44, fontSize: 12, color: 'var(--dsw-alias-label-secondary)' } }, label),
          h('span', { style: { flex: 1, minWidth: 0, fontSize: 12.5, wordBreak: 'break-word' } },
            (value == null || value === '' || (Array.isArray(value) && !value.length)) ? '—' : (Array.isArray(value) ? value.join('、') : String(value))),
        );
        const metaRows = h('div', { style: { padding: '8px 0 4px', borderTop: '1px solid var(--dsw-alias-border-l1)' } },
          flatRow('难度', diffOrig),
          flatRow('来源', source),
          flatRow('分类', cat),
        );

        // ===== 审核说明：点名哪一审与原标签结果不同 =====
        const notes = [];
        if (r1 && r1.assessment) {
          if (!kpSame(kp1, kpOrig)) notes.push('一审知识点与原标签不同：原「' + (kpOrig.join('、') || '—') + '」→ 一审「' + (kp1.join('、') || '—') + '」');
          if (diffOrig && diff1 && diff1 !== diffOrig) notes.push('一审难度与原标签不同：原「' + diffOrig + '」→ 一审「' + diff1 + '」');
        }
        if (r2 && r2.assessment) {
          if (!kpSame(kp2, kpOrig)) notes.push('二审知识点与原标签不同：原「' + (kpOrig.join('、') || '—') + '」→ 二审「' + (kp2.join('、') || '—') + '」');
          if (diffOrig && diff2 && diff2 !== diffOrig) notes.push('二审难度与原标签不同：原「' + diffOrig + '」→ 二审「' + diff2 + '」');
        }
        const noteBlock = notes.length
          ? h('div', { style: { marginTop: 8, padding: '8px 10px', background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 8 } },
              h('div', { style: { fontSize: 12, fontWeight: 600, color: 'var(--dsw-alias-state-warn-primary)', marginBottom: 4 } }, '审核说明'),
              notes.map((t, i) => h('div', { key: i, style: { fontSize: 12, color: 'var(--dsw-alias-label-primary)', padding: '2px 0', lineHeight: 1.6 } }, '· ' + t)))
          : null;

        return [h('div', null, kpRows, metaRows, noteBlock)];
      }

      // 两轮审核（一审全量 / 二审抽 20%）统一、紧凑的展示。
      // 原则：只显示一次；正常即"全通过"；有问题的在对应审核节点上点名；
      // 只讲差异，不堆完整层级，标签保持短小。
      const ROUND_SHORT = { labels: '一审', sample: '二审' };
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
          ok: ['✓', 'var(--dsw-alias-state-success-primary)', '通过'],
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
        const rounds = (comparison && comparison.rounds) || [];
        if (!rounds.length) return h('div', { style: T.hint }, '无审核轮次记录');
        const states = rounds.map((r) => ({ r, st: nodeState(r) }));
        const allOk = states.every((x) => x.st.kind === 'ok');
        const box = { background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 8, padding: '7px 10px', marginBottom: 10, fontSize: 12 };
        if (allOk) {
          return h('div', { style: box },
            h('span', { style: { color: 'var(--dsw-alias-state-success-primary)', fontWeight: 600 } }, '✓ 一审 / 二审 全通过'));
        }
        return h('div', { style: box },
          h('div', { style: { display: 'flex', flexDirection: 'column', gap: 3 } },
            states.map(({ r, st }) => roundNodeRow(r, st))));
      }
      // 单条问题：只显示"这一字段"上每轮的结果，且跳过通过/未涉及的节点，
      // 避免与上方单元级汇总重复；若该字段两轮都通过 → 一句"两轮一致"。
      function renderFindingRounds(finding) {
        const comp = finding && finding.comparison;
        const rounds = (comp && comp.rounds) || [];
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
          return h('div', { style: { ...T.hint, marginTop: 4, color: 'var(--dsw-alias-state-success-primary)' } }, '✓ 一审 / 二审 与该问题一致');
        }
        return h('div', { style: { marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2, fontSize: 12 } },
          items.map(({ r, st }) => roundNodeRow(r, st)));
      }

      // 知识点：多选（简化为逗号输入 + 快捷树）
      const knowledgeStr = (labels?.knowledge || []).join(', ');

      // ---- 中栏：题组列表（过滤 + 搜索 + 键盘高亮）----
      const filterTabs = [['all', '全部'], ['open', '待审'], ['blocked', '阻断'], ['unverified', '未核对']];
      const listCol = h('div', { className: 'wf-review-list-col', style: { flex: '0 1 220px', minWidth: 140, borderRight: '1px solid var(--dsw-alias-border-l1)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--dsw-alias-bg-layer-1)' } },
        h('div', { style: { padding: '10px 10px 8px', borderBottom: '1px solid var(--dsw-alias-border-l1)' } },
          h('div', { style: { display: 'flex', gap: 4, flexWrap: 'nowrap', marginBottom: 8 } },
            filterTabs.map(([id, lbl]) => h('button', {
              key: id, onClick: () => setFilter(id),
              style: {
                flex: '1 1 0', minWidth: 0, padding: '3px 3px', borderRadius: 7, fontSize: 11.5, cursor: 'pointer',
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', textAlign: 'center',
                border: '1px solid var(--dsw-alias-border-l2)',
                background: filter === id ? 'var(--dsw-alias-brand-primary)' : 'transparent',
                color: filter === id ? '#fff' : 'var(--dsw-alias-label-secondary)',
              },
            }, lbl))),
          h('input', { className: 'wf-input', style: { ...T.input, width: '100%', padding: '5px 9px' }, value: q, placeholder: '题号 / 题型 / 知识点', onChange: (e) => setQ(e.target.value) }),
        ),
        h('div', { className: 'wf-scroll', style: { flex: 1, overflow: 'auto', padding: '4px 6px' } },
          filteredRows.length === 0 ? h('div', { style: { ...T.hint, padding: 12 } }, '没有符合条件的题组') : filteredRows.map((r) => {
            const uid = r.unit_id || r.number;
            const active = uid === unitId;
            return h('div', {
              key: uid, className: 'wf-paper' + (active ? ' wf-active' : ''),
              style: T.paperRow(active),
              onClick: () => onSelectUnit(uid),
            },
              h('span', { style: { width: 44, flexShrink: 0, fontWeight: 600, fontSize: 12 } }, String(r.label || r.number)),
              h('span', { style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 11.5, opacity: .85 } }, String(r.type || '')),
              r.needs_human
                ? h('span', { style: { ...T.badge('warn'), flexShrink: 0 } }, String(r.open_count || ''))
                : h('span', { style: { width: 7, height: 7, borderRadius: '50%', flexShrink: 0, background: r.state === 'blocked' ? 'var(--dsw-alias-state-error-primary)' : (r.state === 'unverified' ? 'var(--dsw-alias-border-l2)' : 'var(--dsw-alias-state-success-primary)') } }),
            );
          })),
      );

      // ---- 右栏：工作区 ----
      let workCol;
      if (err) {
        workCol = h(EmptyState, {
          flex: true, tone: 'error', title: '加载失败：' + err,
          action: h('button', { className: 'wf-btn', style: { ...T.btn, marginTop: 12 }, onClick: load }, '重试本题'),
        });
      } else if (!detail) {
        workCol = h(EmptyState, { flex: true, title: TEXT.loading });
      } else {
        workCol = h('div', { style: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 } },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', minHeight: 44, borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0, flexWrap: 'wrap' } },
            h('span', { style: { fontWeight: 600, fontSize: 14.5 } }, '第 ' + (detail.label || '') + ' 题'),
            detail.needs_human ? h('span', { style: T.badge('warn') }, '待审标签') : h('span', { style: T.badge('') }, '无待审'),
            h('span', { style: T.hint }, (detail.type || '') + ' · ' + (detail.numbers || []).length + ' 小题'),
            h('span', { style: { flex: 1 } }),
            h('button', { className: 'wf-btn', style: T.btn, onClick: load }, TEXT.reload),
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: () => setLabelDlg(true) }, '修改标签'),
          ),
          h('div', { className: 'wf-scroll wf-review-container' },
           h('div', { className: 'wf-review-layout' },
            // 左：原卷预览 + 小题
            h('div', { className: 'wf-review-main', style: { minWidth: 0 } },
              h('div', { style: T.card },
                h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 10 } },
                  h('span', { style: { fontSize: 13.5, fontWeight: 600, whiteSpace: 'nowrap' } }, TEXT.sourcePreview),
                  detail.preview_labels ? h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', display: 'flex', gap: 10, flexWrap: 'wrap', minWidth: 0 } },
                    plItem('来源', detail.preview_labels.source_type),
                    plItem('难度', detail.preview_labels.difficulty),
                    plItem('词数', detail.preview_labels.word_count),
                    plItem('知识点', (detail.preview_labels.knowledge || []).join('；') || '未标注'),
                  ) : null,
                ),
                (detail.audio || []).map((a, i) => h('audio', { key: (a.url || i) + '|' + (typeof MEDIA_ORIGIN === 'string' ? MEDIA_ORIGIN : ''), controls: true, preload: 'metadata', src: mediaUrl(a.url), style: { width: '100%', margin: '4px 0 8px' } })),
                h('div', { className: 'wf-paper' }, renderBlocks(detail.preview_blocks)),
                // 「原卷修改」入口：放在原卷预览卡片右下角，点击弹出修改弹窗。
                h('div', { style: { display: 'flex', justifyContent: 'flex-end', marginTop: 12 } },
                  h('button', {
                    className: 'wf-btn-primary', style: T.btnPrimary, disabled: !content,
                    onClick: () => setContentDlg(true),
                  }, '原卷修改'),
                ),
              ),
            ),
            // 右：标签审核（单张卡片，固定不滑动）：知识点标签 + 难度/来源/分类 + 审核说明
            h('div', { className: 'wf-review-notes' },
              h('div', { style: T.card },
                h('div', { style: { ...T.cardTitle, flexWrap: 'wrap' } }, '标签审核', h('span', { style: { flex: 1 } }), h('span', { style: { ...T.hint, fontWeight: 400 } }, '原标签 / 一审 / 二审')),
                ...labelAuditPanels(detail),
              ),
            ),
           ),
          ),
          labelDlg ? h(LabelModal, {
            tax, labels, setL, knowledgeStr, warnings: detail.label_warnings || [],
            unitLabel: detail.label, unitKind: detail.unit_kind, numbers: detail.numbers,
            busy, onSave: async () => { await saveLabels(); setLabelDlg(false); }, onClose: () => setLabelDlg(false),
          }) : null,
          (contentDlg && content) ? h(ContentModal, {
            content, detail, busy, dirty: contentDirty,
            onSub: setSubField, onUnit: setUnitField,
            onSave: saveContent, onClose: () => setContentDlg(false),
          }) : null,
        );
      }

      return h('div', { style: { flex: 1, display: 'flex', minHeight: 0 } }, listCol, workCol);
    }

    // ======================================================================
    // 试卷审核视图：列表 + 统计（进入后切换到审阅）
    // ======================================================================
    function WorkbenchTab(props) {
      const React = require('react');
      const h = React.createElement;
      const [batches, setBatches] = React.useState(null);
      const [active, setActive] = React.useState(null); // {name, unitId}
      const [err, setErr] = React.useState('');
      const [advBusy, setAdvBusy] = React.useState('');
      // 列表加载态：'idle' | 'loading' | 'ready' | 'error'。用于区分“在后端冷启动/网络抖动”
      // 与“确实没有试卷”，避免单次请求失败后一片空白、只能手动刷新。
      const [listState, setListState] = React.useState('idle');
      const loadSeq = React.useRef(0);

      const loadBatches = React.useCallback(async () => {
        // 每次加载递增序号，只让最新一次的结果落地（防止重试/手动刷新交叉覆盖）。
        const seq = ++loadSeq.current;
        setListState((s) => (s === 'ready' ? s : 'loading'));
        setErr('');
        // 冷启动/后端重启时首包可能 5xx 或短暂连不上：指数退避重试几次，
        // 让面板自动“缓过来”，而不是把空列表丢给用户。
        const delays = [0, 600, 1200, 2000, 3000];
        let lastErr = null;
        for (let attempt = 0; attempt < delays.length; attempt++) {
          if (seq !== loadSeq.current) return; // 已被更新的请求取代
          if (delays[attempt]) await new Promise((r) => setTimeout(r, delays[attempt]));
          try {
            const d = await jget('/batches');
            if (seq !== loadSeq.current) return;
            setBatches(d.batches || []);
            setListState('ready');
            setErr('');
            return;
          } catch (e) {
            lastErr = e;
            if (seq !== loadSeq.current) return;
            // 只有可重试的错误（网络/超时/5xx）才继续退避；4xx（如 404）直接放弃。
            const msg = String((e && e.message) || e);
            const retriable = !/\b(400|401|403|404)\b/.test(msg);
            if (!retriable) break;
          }
        }
        if (seq !== loadSeq.current) return;
        setListState('error');
        setErr(String((lastErr && lastErr.message) || lastErr || '加载失败'));
      }, []);
      React.useEffect(() => { loadBatches(); }, [loadBatches]);
      // 面板标签切走再切回、或窗口重新获得焦点时自动刷新一次，
      // 覆盖“后台处理完了但列表停在旧快照”的情况。
      React.useEffect(() => {
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
      }, [loadBatches]);

      function openBatch(name) {
        // 列表 /api/batches 已含每个批次的完整 rows（overview），直接取第一个单元进入。
        const b = (batches || []).find((x) => x.name === name);
        const rows = (b && b.rows) || [];
        const firstRow = rows[0];
        const unitId = firstRow ? (firstRow.unit_id || firstRow.number) : '_';
        setActive({ name, unitId, overview: b || null });
      }
      async function doAdvance(name) {
        setAdvBusy(name);
        try { await jpost('/batches/' + encodeURIComponent(name) + '/advance', {}); await loadBatches(); }
        catch (e) { setErr(String(e.message || e)); }
        finally { setAdvBusy(''); }
      }
      async function doArchive(name) {
        if (!window.confirm('确定删除批次「' + name + '」？将移入可恢复归档。')) return;
        try { await jpost('/batches/' + encodeURIComponent(name) + '/archive', { confirm_name: name }); await loadBatches(); setActive(null); }
        catch (e) { setErr(String(e.message || e)); }
      }

      // 进入审阅：原工作台三栏布局（单元列表 + 工作区），键盘 ↑/↓ 切换题组。
      if (active) {
        const rows = (active.overview && active.overview.rows) || [];
        return h('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } },
          h('div', { style: T.toolbar },
            h('button', { className: 'wf-btn', style: T.btn, onClick: () => setActive(null) }, '← ' + TEXT.back),
            h('span', { style: T.toolbarTitle }, (active.overview?.paper?.title || active.name)),
            h('span', { style: { ...T.hint, marginLeft: 4 } }, '↑/↓ 或 k/j 切换题组'),
            h('span', { style: { flex: 1 } }),
            h('button', { className: 'wf-btn', style: T.btn, onClick: () => doAdvance(active.name), disabled: advBusy === active.name }, TEXT.advance),
            h('button', { className: 'wf-btn-danger', style: T.btnDanger, onClick: () => doArchive(active.name) }, TEXT.archive),
          ),
          h(ReviewView, {
            key: active.unitId,
            batchName: active.name,
            rows,
            unitId: active.unitId,
            onSelectUnit: (uid) => setActive({ ...active, unitId: uid }),
          }),
        );
      }

      // 只保留右侧「试卷审核」卡片列表这一份，去掉左栏「需要审核的试卷」列表，
      // 避免同一批试卷在左右两列重复展示。
      const list = batches || [];
      return h('div', { style: { flex: 1, display: 'flex', minHeight: 0 } },
        h('div', { style: T.main },
          h('div', { style: T.toolbar },
            h('span', { style: T.toolbarTitle }, TEXT.review),
            h('span', { style: { flex: 1 } }),
            h('button', { className: 'wf-btn', style: T.btn, onClick: loadBatches }, TEXT.reload),
          ),
          err ? h('div', { style: { padding: 14, ...T.statusErr } },
            '试卷列表加载失败：' + err + ' ',
            h('button', { className: 'wf-btn', style: { ...T.btn, marginLeft: 10 }, onClick: loadBatches }, '重试')) : null,
          h('div', { className: 'wf-scroll', style: T.scroll },
            h('div', { style: { ...T.hint, marginBottom: 12 } }, '选择一份试卷进入审核；也可「继续处理」或「删除批次」。'),
            listState === 'loading' && list.length === 0
              ? h(EmptyState, { title: '正在加载试卷列表…（首次进入需等待后端启动，请稍候）' })
              : listState === 'error' && list.length === 0
                ? h(EmptyState, { tone: 'error', title: '暂时连不上后端，未能取到试卷列表。点上方「重试」再试一次。' })
                : list.length === 0
                  ? h(EmptyState, { title: TEXT.noPaper })
                  : list.map((b) => h('div', { key: b.name, className: 'wf-card-hover', style: T.cardClickable, onClick: () => openBatch(b.name) },
              // 第一行：标题 + 紧凑统计胶囊
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 10 } },
                h('div', { style: { ...T.paperCardTitle, flex: 1, minWidth: 0 } }, b.paper?.title || b.name),
                h('div', { style: { display: 'flex', gap: 6, flexShrink: 0 } },
                  h('span', { style: T.pill }, h('b', { style: T.pillNum }, (b.unit_total ?? 0)), '题组'),
                  h('span', { style: T.pill }, h('b', { style: T.pillNum }, (b.question_total ?? 0)), '小题'),
                  h('span', { style: { ...T.pill, ...((b.needs_human ?? 0) > 0 ? T.pillWarn : {}) } }, h('b', { style: { ...T.pillNum, color: 'inherit' } }, (b.needs_human ?? 0)), '待审'),
                ),
              ),
              // 第二行：批次/阶段 + 紧凑操作按钮
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 10, marginTop: 6 } },
                h('div', { style: { ...T.paperCardMeta, flex: 1, minWidth: 0 } }, '批次：' + b.name + (b.stage?.hint ? ' · ' + b.stage.hint : '')),
                h('div', { style: { display: 'flex', gap: 6, flexShrink: 0 } },
                  h('button', { className: 'wf-btn-primary', style: T.miniBtnPrimary, onClick: (e) => { e.stopPropagation(); openBatch(b.name); } }, '进入审核'),
                  h('button', { className: 'wf-btn', style: T.miniBtn, onClick: (e) => { e.stopPropagation(); doAdvance(b.name); }, disabled: advBusy === b.name }, TEXT.advance),
                  h('button', { className: 'wf-btn-danger', style: T.miniBtnDanger, onClick: (e) => { e.stopPropagation(); doArchive(b.name); } }, TEXT.archive),
                ),
              ),
            )),
          ),
        ),
      );
    }

    // ======================================================================
    // 模型与设置
    // ======================================================================
    function SettingsTab() {
      const React = require('react');
      const h = React.createElement;
      const [models, setModels] = React.useState([]);
      const [router, setRouter] = React.useState(null);
      const [status, setStatus] = React.useState('idle');
      const [err, setErr] = React.useState('');

      React.useEffect(() => {
        (async () => {
          try {
            const m = await (await window.fetch('/wf-plugin/models', { headers: { accept: 'application/json' } })).json();
            setModels(m.models || []);
            const cfg = await (await window.fetch('/wf-plugin/config', { headers: { accept: 'application/json' } })).json();
            setRouter(cfg.router || {});
          } catch (e) { setErr(String(e.message || e)); }
        })();
      }, []);

      const byKey = React.useMemo(() => { const map = {}; for (const m of models) map[modelKey(m)] = m; return map; }, [models]);
      const defaultSel = router?.default?.model ? router.default : null;

      async function persist(next) {
        setRouter(next); setStatus('saving');
        try {
          const r = await window.fetch('/wf-plugin/router', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(next) });
          if (!r.ok) throw new Error(await r.text());
          const res = await r.json(); setRouter(res.router); setStatus('saved');
          setTimeout(() => setStatus((s) => s === 'saved' ? 'idle' : s), 1500);
        } catch (e) { setErr(String(e.message || e)); setStatus('idle'); }
      }
      const inferProfile = () => 'wf-deepseek-fast';
      function setDefault(sel) { persist({ ...router, default: { ...(router?.default || {}), ...sel, profile: inferProfile() } }); }
      function setStage(stage, sel) { const stages = { ...(router?.stages || {}) }; stages[stage] = { ...sel, profile: inferProfile() }; persist({ ...router, stages }); }
      function clearStage(stage) { const stages = { ...(router?.stages || {}) }; delete stages[stage]; persist({ ...router, stages }); }
      function setRound(round, sel) { const review = { ...(router?.review || {}) }; const rounds = { ...(review.rounds || {}) }; rounds[round] = { ...sel, profile: inferProfile() }; review.rounds = rounds; persist({ ...router, review }); }
      function clearRound(round) { const review = { ...(router?.review || {}) }; const rounds = { ...(review.rounds || {}) }; delete rounds[round]; review.rounds = rounds; persist({ ...router, review }); }

      function ModelSelect({ value, onChange, onClear }) {
        const hasOwn = value && value.provider && value.model;
        const v = hasOwn ? modelKey(value) : '';
        const follow = defaultSel ? ('跟随默认（' + (byKey[modelKey(defaultSel)]?.label || defaultSel.model) + '）') : '跟随默认模型';
        return h('select', {
          className: 'wf-input', style: { ...T.input, width: '100%' }, value: v,
          onChange: (e) => { if (e.target.value === '') { onClear && onClear(); return; } const m = byKey[e.target.value]; if (m) onChange({ provider: m.provider, model: m.model }); },
        },
          h('option', { value: '' }, follow),
          models.map((m) => h('option', { key: modelKey(m), value: modelKey(m) }, m.label + ' · ' + m.provider)));
      }

      const stages = [['answers', '答案提取'], ['audio', '听力切分'], ['types', '题型分类'], ['explanations', '解析与大意'], ['knowledge', '知识点标注'], ['assemble', '装配成批'], ['review', '审核']];
      const rounds = [['labels', '一审'], ['sample', '二审（默认 20% 抽样）']];

      async function writeEffective() { try { await fetch('/wf-plugin/materialize', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }); setStatus('tested'); } catch (e) { setErr(String(e.message || e)); } }

      return h('div', { className: 'wf-scroll', style: { flex: 1, overflow: 'auto', padding: 16 } },
        h('div', { style: { maxWidth: 760, margin: '0 auto' } },
          err ? h('div', { style: { ...T.statusErr, marginBottom: 8 } }, err) : null,
          h('div', { style: T.card },
            h('div', { style: T.cardTitle }, TEXT.defaultModel),
            models.length === 0 ? h('div', { style: T.hint }, '正在读取模型…') :
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 12 } },
                h('select', { className: 'wf-input', style: { ...T.input, minWidth: 300 }, value: router?.default ? modelKey(router.default) : '', onChange: (e) => { const m = byKey[e.target.value]; if (m) setDefault({ provider: m.provider, model: m.model }); } },
                  models.map((m) => h('option', { key: modelKey(m), value: modelKey(m) }, m.label + ' · ' + m.provider))),
                h('span', { style: T.hint }, '当前：' + (byKey[router?.default ? modelKey(router.default) : '']?.label || router?.default?.model || '—'))),
          ),
          h('div', { style: T.card },
            h('div', { style: T.cardTitle }, TEXT.stageModels),
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '默认全部「跟随默认」；需要单独指定某阶段才选具体模型。'),
            h('div', { style: { display: 'grid', gridTemplateColumns: '120px 1fr', gap: '10px 14px', alignItems: 'center' } },
              stages.map(([s, lbl]) => h('div', { key: s, style: { display: 'contents' } },
                h('span', { style: T.label }, lbl),
                h(ModelSelect, { value: router?.stages?.[s] || null, onChange: (sel) => setStage(s, sel), onClear: () => clearStage(s) })))),
          ),
          h('div', { style: T.card },
            h('div', { style: T.cardTitle }, TEXT.reviewModels),
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '默认两轮都「跟随默认」；可分别指定一审/二审模型。'),
            h('div', { style: { display: 'grid', gridTemplateColumns: '120px 1fr', gap: '10px 14px', alignItems: 'center' } },
              rounds.map(([r, lbl]) => h('div', { key: r, style: { display: 'contents' } },
                h('span', { style: T.label }, lbl),
                h(ModelSelect, { value: router?.review?.rounds?.[r] || null, onChange: (sel) => setRound(r, sel), onClear: () => clearRound(r) })))),
            h('div', { style: { marginTop: 14, display: 'flex', alignItems: 'center', gap: 12 } },
              h('button', { className: 'wf-btn', style: T.btn, onClick: writeEffective }, TEXT.writeEffective),
              status === 'saving' ? h('span', { style: T.hint }, '正在保存…')
                : status === 'saved' ? h('span', { style: T.statusOk }, '✓ 已保存，下次进入会记住')
                : status === 'tested' ? h('span', { style: T.statusOk }, '✓ 已写入生效配置')
                : h('span', { style: T.hint }, '改动会自动保存')),
          ),
        ),
      );
    }

    // ======================================================================
    // 面板外壳：三个标签页
    // ======================================================================
    function WfPanel(props) {
      const React = require('react');
      const h = React.createElement;
      const [tab, setTab] = React.useState('review');
      const [uploadedTick, setUploadedTick] = React.useState(0);
      const tabBtn = (id, label) => h('button', { key: id, onClick: () => setTab(id), style: T.tab(tab === id), 'aria-current': tab === id ? 'page' : undefined }, label);
      return h('div', { className: 'wf-root', style: T.root },
        h('div', { style: T.tabbar },
          h('div', { style: T.brand },
            h('span', { style: T.brandMark }, docIcon(13)),
            h('span', { style: T.brandName }, TEXT.title)),
          h('div', { style: T.segWrap, role: 'tablist' },
            tabBtn('review', TEXT.review),
            tabBtn('upload', TEXT.upload),
            tabBtn('settings', TEXT.settings)),
          h('span', { style: { flex: 1 } }),
          props.closePanel ? h('button', { className: 'wf-icon-btn', title: TEXT.close, 'aria-label': TEXT.close, onClick: props.closePanel, style: T.iconBtn }, closeIcon()) : null,
        ),
        h('div', { style: T.body },
          tab === 'review' ? h(WorkbenchTab, { key: uploadedTick }) :
          tab === 'upload' ? h(UploadView, { onUploaded: () => { setUploadedTick((x) => x + 1); setTab('review'); } }) :
          h(SettingsTab, {}),
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
    exports.__panel = { WfPanel, UploadView, ReviewView, WorkbenchTab, SettingsTab, ContentModal, LabelModal };
    return module.exports;
  },
});

