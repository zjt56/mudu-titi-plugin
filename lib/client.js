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
//   · 模型与设置：左侧四项导航（大模型配置 / 上传设置 / OCR 识别 / 听力切分）
// ---------------------------------------------------------------------------
window.__ModuleLoader__.load({
  id: 'md-upload-tiku',
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;

    var API = '/wf-plugin/api';

    var TEXT = {
      title: '录题系统',
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
      archive: '彻底删除批次',
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
      uploading: '上传中…',
      defaultModel: '默认模型（全局）',
      stageModels: '各子 Agent 模型',
      reviewModels: '两轮审核模型',
      follow: '跟随默认',
      writeEffective: '写入生效配置',
      deleteConfirm: '彻底删除批次',
    };

    // 统一视觉：全部用 Harness 主题 token，随浅/深色主题自动适配。
    // 刻度（全站共用，改一处即可整体协调）：圆角 10/8/6/999，间距 4/6/8/10/12/14/16，
    // 字号 11.5/12/12.5/13/13.5/14/14.5，字重 500/600/700。
    var T = {
      root: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-base)', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.55 },
      tabbar: { display: 'flex', alignItems: 'center', gap: 12, padding: '0 18px', height: 52, borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0 },
      brand: { display: 'flex', alignItems: 'center', gap: 9, marginRight: 6, flexShrink: 0 },
      brandMark: { width: 24, height: 24, borderRadius: 8, background: 'var(--wf-brand-soft)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--wf-brand)', flexShrink: 0 },
      brandName: { fontSize: 13, fontWeight: 600, letterSpacing: '.01em', whiteSpace: 'nowrap' },
      segWrap: { display: 'inline-flex', alignItems: 'center', gap: 3, padding: 3, borderRadius: 10, background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)' },
      tab: (active) => ({
        padding: '6px 14px', borderRadius: 7, cursor: 'pointer', border: 'none', fontSize: 12.5,
        fontWeight: active ? 600 : 500, whiteSpace: 'nowrap', letterSpacing: '.01em',
        color: active ? 'var(--wf-brand)' : 'var(--dsw-alias-label-secondary)',
        background: active ? 'var(--dsw-alias-bg-base)' : 'transparent',
        boxShadow: active ? '0 1px 3px rgba(16,24,40,.1)' : 'none',
        transition: 'color .18s cubic-bezier(.2,.8,.2,1), background .18s cubic-bezier(.2,.8,.2,1)',
      }),
      iconBtn: { width: 30, height: 30, padding: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderRadius: 8, cursor: 'pointer', fontSize: 15, lineHeight: 1, color: 'var(--dsw-alias-label-secondary)', background: 'transparent', border: '1px solid transparent', transition: 'background .18s, color .18s' },
      body: { flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden', background: 'var(--dsw-alias-bg-base)' },
      toolbar: { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', minHeight: 48, borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0, flexWrap: 'wrap' },
      toolbarTitle: { fontWeight: 600, fontSize: 13.5 },
      paperRow: (active) => ({
        margin: '0 2px 6px', padding: '7px 9px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
        borderRadius: 8, transition: 'background .16s, border-color .16s',
        // 列表用系统/Harness 默认字体（.wf-paper 是宋体，这里不是原卷正文）。
        fontFamily: 'inherit',
        // 每道题都是独立的一行卡片：细边框把题与题分开，不再像连成一片。
        // 选中态用加深的中性底色 + 左侧强调条，浅色/深色主题都看得清。
        background: active ? 'var(--wf-sel-bg)' : 'var(--dsw-alias-bg-base)',
        border: '1px solid ' + (active ? 'var(--wf-sel-border)' : 'var(--dsw-alias-border-l2)'),
        color: 'var(--dsw-alias-label-primary)',
        boxShadow: active ? 'inset 2px 0 0 var(--dsw-alias-label-primary)' : 'none',
      }),
      main: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
      scroll: { flex: 1, overflow: 'auto', padding: 18 },
      card: {
        border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 12, padding: 16, marginBottom: 14,
        background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 1px 2px rgba(16,24,40,.04), 0 8px 24px rgba(16,24,40,.035)',
      },
      cardTitle: { fontSize: 14, fontWeight: 600, letterSpacing: '-.01em', margin: '0 0 12px', color: 'var(--dsw-alias-label-primary)', display: 'flex', alignItems: 'center', gap: 8 },
      label: { minWidth: 88, fontSize: 12.5, color: 'var(--dsw-alias-label-secondary)' },
      fieldLabel: { fontSize: 12, fontWeight: 500, letterSpacing: '.01em', color: 'var(--dsw-alias-label-secondary)' },
      input: {
        padding: '8px 11px', borderRadius: 8, fontSize: 13, lineHeight: 1.5, color: 'var(--dsw-alias-label-primary)',
        background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)',
        outline: 'none', boxSizing: 'border-box', transition: 'border-color .15s, box-shadow .15s',
      },
      btn: {
        padding: '7px 14px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5,
        color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)',
        border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s, border-color .16s',
      },
      btnPrimary: {
        padding: '8px 18px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5, letterSpacing: '.01em',
        color: 'var(--wf-brand)', background: 'var(--wf-brand-soft)', border: '1px solid var(--wf-brand-border)',
        transition: 'background .16s, border-color .16s, box-shadow .16s',
      },
      btnDanger: {
        padding: '7px 14px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5,
        color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)',
        border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s, border-color .16s',
      },
      hint: { fontSize: 12, lineHeight: 1.65, color: 'var(--dsw-alias-label-secondary)' },
      // 成功态同样走中性色：用 ✓ 与主文字色表达「通过」，不再用绿色。
      statusOk: { fontSize: 12, color: 'var(--dsw-alias-label-primary)' },
      statusErr: { fontSize: 12, color: 'var(--dsw-alias-state-error-primary)' },
      badge: (kind) => ({
        fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 999, lineHeight: 1.5,
        background: kind === 'warn' ? 'rgba(230,162,60,.14)' : 'var(--dsw-alias-bg-layer-2)',
        border: kind === 'warn' ? '1px solid rgba(230,162,60,.35)' : '1px solid var(--dsw-alias-border-l1)',
        color: kind === 'warn' ? 'var(--dsw-alias-state-warn-primary)' : 'var(--dsw-alias-label-secondary)',
      }),
      empty: { padding: '32px 18px', color: 'var(--dsw-alias-label-secondary)', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: 140 },
      emptyIcon: { width: 40, height: 40, borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)', marginBottom: 12 },
      emptyTitle: { fontSize: 13, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' },
      emptyHint: { fontSize: 12, marginTop: 5, maxWidth: 340, lineHeight: 1.7 },
      cardClickable: {
        border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 10, padding: '10px 12px', marginBottom: 8,
        background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 1px 2px rgba(0,0,0,.05)', cursor: 'pointer',
        transition: 'border-color .16s, box-shadow .16s',
      },
      paperCardTitle: { fontWeight: 600, fontSize: 13, lineHeight: 1.35, color: 'var(--dsw-alias-label-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
      paperCardMeta: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
      pill: { display: 'inline-flex', alignItems: 'baseline', gap: 3, fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 999, padding: '2px 9px', lineHeight: 1.6, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' },
      pillNum: { fontWeight: 700, fontSize: 12.5, color: 'var(--dsw-alias-label-primary)', fontVariantNumeric: 'tabular-nums' },
      pillWarn: { color: 'var(--dsw-alias-state-warn-primary)', borderColor: 'rgba(230,162,60,.4)', background: 'rgba(230,162,60,.1)' },
      miniBtn: { padding: '3px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 12, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s, border-color .16s' },
      miniBtnPrimary: { padding: '3px 12px', borderRadius: 6, cursor: 'pointer', fontSize: 12, color: 'var(--wf-brand)', background: 'var(--wf-brand-soft)', border: '1px solid var(--wf-brand-border)', transition: 'background .16s, border-color .16s' },
      miniBtnDanger: { padding: '3px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 12, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .16s, border-color .16s' },
      // 自适应按钮：在按钮组内均分可用宽度，随容器伸缩，不写死宽度。
      adaptiveBtn: { flex: '1 1 0', minWidth: 0, whiteSpace: 'nowrap', textAlign: 'center', padding: '3px 2px', overflow: 'hidden', textOverflow: 'ellipsis' },
      // 弹窗外壳：两个弹窗共用，统一宽度、层级、圆角与阴影（带偏移+模糊）。
      modalOverlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,.42)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3000, padding: 16 },
      modalCard: (width) => ({ width, maxWidth: '94vw', maxHeight: '90vh', display: 'flex', flexDirection: 'column', background: 'var(--dsw-alias-bg-layer-1)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 12, boxShadow: '0 18px 50px rgba(0,0,0,.24), 0 2px 8px rgba(0,0,0,.08)', overflow: 'hidden' }),
      modalHead: { display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px', borderBottom: '1px solid var(--dsw-alias-border-l1)', flexShrink: 0 },
      modalBody: { padding: 16, overflow: 'auto', flex: 1 },
      modalFoot: { display: 'flex', justifyContent: 'flex-end', gap: 8, padding: '10px 16px', borderTop: '1px solid var(--dsw-alias-border-l1)', flexShrink: 0 },
      modalTitle: { fontWeight: 600, fontSize: 14.5 },
      tagPill: { fontSize: 12, fontWeight: 600, color: 'var(--wf-brand)', background: 'var(--wf-brand-soft)', borderRadius: 999, padding: '2px 9px', whiteSpace: 'nowrap' },
      // 上传页：分区标题统一字阶与留白（页头标题与流程图图例已移除）。
      sectionTitle: { fontSize: 12, fontWeight: 600, letterSpacing: '.02em', color: 'var(--dsw-alias-label-primary)' },
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
    // 上传入口图标：与 docIcon 同一笔画/线宽，不用 emoji。
    function uploadIcon(size) {
      const h = require('react').createElement;
      return h('svg', { width: size, height: size, viewBox: '0 0 18 18', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': 'true' },
        h('path', { d: 'M9 11.5V3.5M9 3.5L5.5 7M9 3.5L12.5 7' }),
        h('path', { d: 'M3.5 11.5v2.25a.75.75 0 0 0 .75.75h9.5a.75.75 0 0 0 .75-.75V11.5' }));
    }

    // 全站共用的空/加载/错误态：同一套图标、字阶与留白；文案由调用方逐字传入。
    function EmptyState(props) {
      const h = require('react').createElement;
      const { title, hint, action, flex, tone, compact } = props;
      const iconColor = tone === 'error' ? 'var(--dsw-alias-state-error-primary)' : 'var(--dsw-alias-label-secondary)';
      return h('div', { style: { ...T.empty, ...(flex ? { flex: 1 } : {}), ...(compact ? { minHeight: 0, padding: '10px 12px' } : {}), color: iconColor } },
        compact ? null : h('span', { style: { ...T.emptyIcon, color: iconColor } }, docIcon(18)),
        h('span', { style: compact ? { ...T.emptyTitle, fontSize: 12.5 } : T.emptyTitle }, title),
        hint ? h('span', { style: compact ? { ...T.emptyHint, fontSize: 11.5, marginTop: 3, lineHeight: 1.5 } : T.emptyHint }, hint) : null,
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
        // 中性色板：不用蓝色。按钮浅白底 + 中性灰文字，选中态用「加深」的中性底色，
        // 与 Harness 其他按钮/标签一致；浅色主题的 bg-layer-2 本身接近纯白，
        // 直接拿它当选中底色会看不出选择，所以按主题色与底色混合出一个更深的底。
        + '.wf-root{--wf-brand:var(--dsw-alias-label-primary);--wf-brand-strong:var(--dsw-alias-label-primary);--wf-brand-soft:var(--dsw-alias-bg-layer-2);--wf-brand-soft-2:var(--dsw-alias-bg-layer-1);--wf-brand-border:var(--dsw-alias-border-l2);--wf-sel-bg:color-mix(in srgb, var(--dsw-alias-label-primary) 10%, var(--dsw-alias-bg-base));--wf-sel-hover:color-mix(in srgb, var(--dsw-alias-label-primary) 5%, var(--dsw-alias-bg-base));--wf-sel-border:var(--dsw-alias-label-secondary);--wf-flow-line:var(--dsw-alias-border-l2);--wf-flow-line-done:color-mix(in srgb, var(--dsw-alias-label-secondary) 40%, transparent);--wf-flow-ring:var(--dsw-alias-border-l2);--wf-flow-ring-done:color-mix(in srgb, var(--dsw-alias-label-secondary) 42%, transparent);--wf-flow-ring-cur:color-mix(in srgb, var(--dsw-alias-label-secondary) 58%, transparent);--wf-flow-line-live:color-mix(in srgb, var(--dsw-alias-label-secondary) 55%, transparent);--wf-flow-node-idle:var(--dsw-alias-bg-layer-1);--wf-flow-node-done:color-mix(in srgb, var(--dsw-alias-label-secondary) 14%, var(--dsw-alias-bg-base));--wf-flow-node-alert:color-mix(in srgb, var(--dsw-alias-label-secondary) 8%, var(--dsw-alias-bg-base));--wf-flow-node-ring:var(--dsw-alias-border-l2);--wf-flow-node-done-ring:color-mix(in srgb, var(--dsw-alias-label-secondary) 45%, transparent);--wf-flow-node-cur-ring:color-mix(in srgb, var(--dsw-alias-label-secondary) 62%, transparent);--wf-flow-node-done-mark:var(--dsw-alias-label-secondary);--wf-flow-node-cur-mark:var(--dsw-alias-label-primary);--wf-flow-node-warn:var(--dsw-alias-state-warn-primary);--wf-flow-node-error:var(--dsw-alias-state-error-primary)}'
        + '.wf-root button,.wf-root input,.wf-root textarea,.wf-root select{font-family:inherit}'
        + '.wf-root ::selection{background:var(--dsw-alias-bg-layer-2);color:inherit}'
        + '.wf-root input,.wf-root textarea{caret-color:var(--wf-brand)}'
        + '.wf-root button:focus-visible,.wf-root input:focus-visible,.wf-root textarea:focus-visible,.wf-root select:focus-visible,.wf-root a:focus-visible{outline:2px solid var(--wf-brand-border);outline-offset:2px}'
        + '.wf-root button:disabled{opacity:.5;cursor:not-allowed!important;filter:none!important}'
        + '.wf-hoverable{cursor:pointer}'
        + '.wf-hoverable:hover{background:var(--dsw-alias-bg-layer-2)!important}'
        // 只让「题目列表行」响应 hover；原卷正文 .wf-paper 不跟着变色。
        // 选中行不再被 hover 的浅底盖掉，只加深边框。
        + '.wf-paper[data-unit]:not(.wf-active):hover{background:var(--wf-sel-hover)!important;border-color:var(--wf-brand-border)!important;color:var(--dsw-alias-label-primary)!important}'
        + '.wf-paper[data-unit].wf-active:hover{border-color:var(--wf-sel-border)!important}'
        + '.wf-card-hover:hover{border-color:var(--wf-brand-border)!important;box-shadow:0 4px 16px rgba(0,0,0,.09)!important}'
        // 按钮状态：内联 style 的 background/border 优先级更高，hover 必须 !important 才生效。
        + '.wf-btn:not(:disabled):hover{background:var(--dsw-alias-bg-layer-2)!important;border-color:var(--wf-brand-border)!important;color:var(--wf-brand)!important}'
        + '.wf-btn:not(:disabled):active{background:var(--dsw-alias-bg-layer-2)!important}'
        + '.wf-btn-primary:not(:disabled):hover{background:var(--wf-brand-soft-2)!important}'
        + '.wf-btn-primary:not(:disabled):active{background:var(--wf-brand-soft)!important}'
        + '.wf-btn-danger:not(:disabled):hover{background:var(--dsw-alias-bg-layer-1)!important;border-color:var(--dsw-alias-border-l2)!important;color:var(--dsw-alias-label-primary)!important}'
        + '.wf-icon-btn:hover{background:var(--dsw-alias-bg-layer-2)!important;color:var(--dsw-alias-label-primary)!important}'
        + '.wf-icon-btn:active{background:var(--dsw-alias-border-l1)!important}'
        + '.wf-input:focus{border-color:var(--wf-brand-border)!important;box-shadow:0 0 0 3px rgba(0,0,0,.06)!important}'
        + '.wf-scroll::-webkit-scrollbar{width:6px;height:6px}'
        + '.wf-scroll::-webkit-scrollbar-thumb{background:var(--dsw-alias-border-l2);border-radius:999px}'
        + '.wf-scroll::-webkit-scrollbar-thumb:hover{background:var(--dsw-alias-label-secondary)}'
        + '.wf-scroll::-webkit-scrollbar-track{background:transparent}'
        + '.wf-prose{white-space:pre-wrap;line-height:1.85;font-size:13.5px}'
        + '.wf-prose p{margin:.3em 0}'
        + '.wf-img{max-width:100%;margin:8px 0;border-radius:8px;border:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-img:hover{border-color:var(--wf-brand-border)}'
        + '.wf-paper{font-family:"Times New Roman","SimSun","Songti SC",serif;font-size:14.5px;line-height:1.95;color:var(--dsw-alias-label-primary);word-break:break-word}'
        + '.wf-line{margin:0 0 4px}'
        + '.wf-heading{font-weight:700;margin:10px 0 6px}'
        // 填空：试卷标准实线下划线，不用高亮底色
        + '.wf-blank{border-bottom:1.5px solid var(--dsw-alias-label-primary);padding:0 8px;color:inherit;font-weight:600;white-space:nowrap}'
        // 表格：试卷黑白线框，表头不加底色只加粗
        + '.wf-table{width:100%;max-width:100%;border-collapse:collapse;table-layout:fixed;font-size:13px;margin:8px 0;font-family:"Times New Roman","SimSun",serif}'
        + '.wf-table td,.wf-table th{border:1px solid var(--dsw-alias-label-primary);padding:5px 10px;vertical-align:top;line-height:1.7;overflow-wrap:anywhere;word-break:break-word;white-space:normal}'
        + '.wf-table th{font-weight:700;background:transparent;text-align:left}'
        + '.wf-table u{text-decoration:none;border-bottom:1.5px solid var(--dsw-alias-label-primary);padding:0 6px}'
        + '.wf-find{display:block;padding:10px 0;border-top:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-find:first-child{border-top:none}'
        + '.wf-settings-page{container-type:inline-size;display:flex;flex:1;min-width:0;min-height:0}'
        + '.wf-settings-layout{display:grid;grid-template-columns:176px minmax(0,1fr);flex:1;min-width:0;min-height:0}'
        + '.wf-settings-nav{display:flex;flex-direction:column;gap:4px;padding:20px 12px;border-right:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-layer-1);overflow-y:auto}'
        + '.wf-settings-nav-title{font-size:12px;color:var(--dsw-alias-label-secondary);padding:0 12px 12px;margin:0}'
        + '.wf-settings-nav button{display:flex;align-items:center;gap:10px;width:100%;padding:11px 12px;border:0;border-radius:8px;text-align:left;font-size:13px;line-height:1.5;white-space:nowrap;cursor:pointer;background:transparent;color:var(--dsw-alias-label-secondary)}'
        + '.wf-settings-nav button:hover{background:var(--wf-sel-hover)}'
        + '.wf-settings-nav button[aria-selected=true]{background:var(--wf-sel-bg);color:var(--dsw-alias-label-primary);font-weight:600}'
        + '.wf-settings-content{min-width:0;overflow:auto;padding:24px 28px;scrollbar-gutter:stable}'
        + '.wf-settings-section{max-width:800px;margin:0 auto;min-width:0}'
        + '.wf-settings-section[hidden]{display:none}'
        + '.wf-settings-section select{min-width:0;max-width:100%;text-overflow:ellipsis}'
        + '.wf-settings-description{margin:0 0 22px;font-size:12px;line-height:1.7;color:var(--dsw-alias-label-secondary)}'
        + '.wf-settings-section h2{font-size:18px!important;margin-bottom:8px!important}'
        + '.wf-settings-section h3{font-size:13px;font-weight:600;margin:0 0 12px}'
        + '.wf-settings-section .wf-settings-card{box-shadow:none}'
        + '.wf-switch{position:relative;width:42px;height:24px;flex:0 0 auto;padding:0;border-radius:999px;border:1px solid var(--dsw-alias-border-l2);background:var(--dsw-alias-bg-layer-2);cursor:pointer;transition:background .16s,border-color .16s}'
        + '.wf-switch::after{content:"";position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:999px;background:var(--dsw-alias-label-secondary);transition:transform .18s cubic-bezier(.2,.8,.2,1),background .16s}'
        + '.wf-switch[aria-checked=true]{background:var(--wf-sel-bg);border-color:var(--wf-sel-border)}'
        + '.wf-switch[aria-checked=true]::after{transform:translateX(18px);background:var(--dsw-alias-label-primary)}'
        + '.wf-switch:disabled{opacity:.5;cursor:not-allowed}'
        + '@container (max-width:640px){.wf-settings-layout{grid-template-columns:144px minmax(0,1fr)}.wf-settings-content{padding:20px 16px}.wf-settings-nav{padding:16px 8px}.wf-settings-nav button{gap:8px;padding:10px 8px}}'
        + '@container (max-width:560px){.wf-settings-section .wf-model-fields{grid-template-columns:minmax(0,1fr)!important;gap:6px!important}.wf-settings-section .wf-model-fields .wf-input{margin-bottom:6px}}'
        + '@container (max-width:440px){.wf-settings-layout{grid-template-columns:112px minmax(0,1fr)}.wf-settings-content{padding:16px 12px}.wf-settings-nav button{font-size:12px;gap:6px;padding:10px 6px}.wf-settings-nav button svg{display:none}.wf-settings-nav-title{padding-left:6px}}'
        // 审核两栏：左（原卷预览）与右（待人工处理）各自独立纵向滚动，不整体滚动。
        //   外层只在面板过窄时横向滚动；grid 限高为一行，两栏各自 overflow-y:auto。
        + '.wf-review-container{flex:1;min-height:0;overflow-x:auto;overflow-y:hidden;display:flex;flex-direction:column}'
        + '.wf-review-layout{display:grid;grid-template-columns:minmax(300px,1fr) minmax(240px,285px);grid-template-rows:minmax(0,1fr);align-items:stretch;gap:12px;padding:12px;min-width:564px;flex:1;min-height:0}'
        + '.wf-review-layout .wf-img{display:block;max-width:100%;max-height:260px;width:auto;height:auto;object-fit:contain;margin:4px 0}'
        // 选项配图：A/B/C/D 固定四列；每张图放入等比例图框，不因原图尺寸改变位置。
        + '.wf-opt-row{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));grid-auto-rows:auto;align-items:start;gap:10px 8px;width:100%;max-width:504px;margin:6px 0}'
        + '.wf-opt-cell{display:flex;flex-direction:column;align-items:stretch;gap:4px;min-width:0;width:100%}'
        + '.wf-opt-cell .wf-opt-label{display:block;min-height:18px;font-weight:600;font-size:13px;line-height:18px;white-space:nowrap}'
        + '.wf-opt-frame{position:relative;display:flex;align-items:center;justify-content:center;box-sizing:border-box;width:100%;height:72px;overflow:hidden;border:1px solid var(--dsw-alias-border-l1);border-radius:6px;background:var(--dsw-alias-bg-base)}'
        + '.wf-opt-frame .wf-img{display:block;width:100%;height:100%;max-width:100%;max-height:100%;object-fit:contain;margin:0;border:0;border-radius:0}'
        + '.wf-opt-frame [role=status]{max-width:100%;max-height:100%;overflow:hidden;overflow-wrap:anywhere;text-align:center}'
        // 题干/阅读插图：居中并限制在统一的自适应矩形中，object-fit:contain 保留整张原图。
        + '.wf-body-image-frame{display:flex;align-items:center;justify-content:center;width:100%;max-width:200px;aspect-ratio:4/3;margin:8px auto;overflow:hidden;border:1px solid var(--dsw-alias-border-l1);border-radius:6px;background:var(--dsw-alias-bg-base)}'
        + '.wf-table-imgcell{vertical-align:top;text-align:left;padding:5px 10px;white-space:normal;overflow-wrap:anywhere;word-break:break-word}'
        + '.wf-table-thumb{display:inline-block;width:36px;height:28px;margin:0 4px 2px 0;vertical-align:middle;overflow:hidden;border:1px solid var(--dsw-alias-border-l1);border-radius:3px;background:var(--dsw-alias-bg-base)}'
        + '.wf-table-thumb .wf-img{display:block;width:100%;height:100%;max-width:100%;max-height:100%;object-fit:contain;margin:0;border:0;border-radius:0}'
        + '.wf-table-thumb [role=status]{max-width:100%;max-height:100%;overflow:hidden;font-size:9px;overflow-wrap:anywhere;text-align:center}'
        + '.wf-body-image-frame .wf-img{display:block;width:100%;height:100%;max-width:100%;max-height:100%;object-fit:contain;margin:0;border:0;border-radius:0}'
        + '.wf-body-image-frame [role=status]{max-width:100%;max-height:100%;overflow:hidden;overflow-wrap:anywhere;text-align:center}'
        + '.wf-review-layout .wf-paper{font-size:13.5px;line-height:1.75}'
        + '.wf-review-layout .wf-line{font-size:13px!important;line-height:1.75!important}'
        + '.wf-review-layout .wf-review-main{min-width:0;max-width:100%;width:auto;min-height:0;max-height:100%;overflow-y:auto;overflow-x:hidden}'
        // 右栏审核内容与左侧预览各自独立纵向滚动。
        + '.wf-review-layout .wf-review-notes{min-width:0;max-width:100%;width:auto;min-height:0;max-height:100%;overflow-y:auto;overflow-x:hidden;align-self:stretch}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar,.wf-review-layout .wf-review-notes::-webkit-scrollbar{width:6px;height:6px}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar-thumb,.wf-review-layout .wf-review-notes::-webkit-scrollbar-thumb{background:var(--dsw-alias-border-l2);border-radius:999px}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar-thumb:hover,.wf-review-layout .wf-review-notes::-webkit-scrollbar-thumb:hover{background:var(--dsw-alias-label-secondary)}'
        + '.wf-review-layout .wf-review-main::-webkit-scrollbar-track,.wf-review-layout .wf-review-notes::-webkit-scrollbar-track{background:transparent}'
        + '.wf-preitem{white-space:nowrap;flex:0 0 auto}'
        + '.wf-group-head{display:flex;align-items:center;gap:4px;width:100%;padding:8px 10px;margin:4px 0;cursor:pointer;background:var(--dsw-alias-bg-base);border:1px solid var(--dsw-alias-border-l1);border-radius:8px;text-align:left;color:var(--dsw-alias-label-primary);transition:background .16s,border-color .16s}'
        + '.wf-group-head:hover{background:var(--dsw-alias-bg-layer-2);border-color:var(--wf-brand-border)}'
        // 表单：占位符用浅灰（不用浏览器默认深灰），下拉框统一手型与聚焦反馈。
        + '.wf-root input::placeholder,.wf-root textarea::placeholder{color:var(--dsw-alias-label-secondary);opacity:.72}'
        + '.wf-root select{cursor:pointer}'
        // 上传文件区：原生 input 只保留可聚焦性，视觉全部交给自定义浅灰控件。
        + '.wf-file-input{position:absolute;width:1px;height:1px;opacity:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}'
        + '.wf-file-drop{position:relative;display:flex;align-items:center;gap:10px;width:100%;padding:7px 12px;border:1px dashed var(--dsw-alias-border-l2);border-radius:10px;background:transparent;cursor:pointer;transition:border-color .18s,background .18s,box-shadow .18s}'
        + '.wf-file-drop:hover{border-color:var(--wf-brand-border);background:var(--dsw-alias-bg-layer-1)}'
        + '.wf-file-drop:focus-within{border-color:var(--wf-brand-border);box-shadow:0 0 0 3px rgba(16,24,40,.06)}'
        + '.wf-file-drop.wf-dragover{border-color:var(--wf-brand);background:var(--wf-brand-soft)}'
        + '.wf-file-label{font-size:12.5;font-weight:500;letter-spacing:.01em;color:var(--dsw-alias-label-secondary);transition:color .18s}'
        + '.wf-file-drop:hover .wf-file-label,.wf-file-drop:focus-within .wf-file-label{color:var(--wf-brand)}'
        + '.wf-file-chip{display:inline-flex;align-items:center;gap:6px;max-width:100%;padding:4px 6px 4px 10px;border:1px solid var(--dsw-alias-border-l1);border-radius:999px;background:var(--dsw-alias-bg-layer-1);font-size:12;color:var(--dsw-alias-label-primary);line-height:1.4}'
        + '.wf-file-chip button{display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;padding:0;border:none;border-radius:50%;background:transparent;color:var(--dsw-alias-label-secondary);cursor:pointer;transition:background .16s,color .16s}'
        + '.wf-file-chip button:hover{background:var(--dsw-alias-bg-layer-2);color:var(--dsw-alias-label-primary)}'
        // 上传页布局：左列（上传 + 排队区）/ 右列（流程图），窄面板自动堆叠。
        + '.wf-upload-layout{flex:1;min-height:0;display:flex;flex-direction:column;padding:8px 20px 16px;overflow:auto;container-type:inline-size}'
        + '.wf-upload-columns{flex:1;min-height:0;display:grid;grid-template-columns:minmax(320px,.92fr) minmax(0,1.08fr);grid-template-rows:minmax(0,1fr);gap:20px}'
        + '.wf-upload-columns>*{min-height:0}'
        + '.wf-upload-left{min-width:0;display:flex;flex-direction:column;min-height:0;overflow:auto;padding-right:20px;border-right:1px solid var(--dsw-alias-border-l1)}'
        + '.wf-diag-banner summary::-webkit-details-marker{display:none}'
        + '.wf-diag-banner summary:hover{opacity:.85}'
        + '.wf-handoff button:hover{filter:brightness(.97)}'
        + '.wf-handoff button:focus-visible{outline:2px solid var(--wf-brand);outline-offset:2px}'
        // 运行中节点动效：外圈脉冲放大淡出 + 中心点呼吸明暗，表示该阶段正在跑。
        + '@keyframes wf-node-pulse{0%{transform:scale(1);opacity:.75}70%{transform:scale(1.6);opacity:0}100%{transform:scale(1.6);opacity:0}}'
        + '@keyframes wf-node-breathe{0%,100%{opacity:.3}50%{opacity:1}}'
        + '@keyframes wf-node-glow{0%,100%{opacity:.35}50%{opacity:.9}}'
        + '.wf-node-pulse{transform-box:fill-box;transform-origin:center;animation:wf-node-pulse 1.5s ease-out infinite}'
        + '.wf-node-dot{transform-box:fill-box;transform-origin:center;animation:wf-node-breathe 1.2s ease-in-out infinite}'
        // 运行中连线的流动虚线：沿路径偏移动画，示意数据正流入该阶段。
        + '@keyframes wf-flow-dash{to{stroke-dashoffset:-22}}'
        + '.wf-flow-live{animation:wf-flow-dash 1s linear infinite}'
        + '@media (prefers-reduced-motion: reduce){.wf-node-pulse,.wf-node-dot,.wf-flow-live{animation:none}}'
        + '@container (max-width:840px){.wf-upload-columns{flex:0 0 auto;grid-template-columns:minmax(0,1fr);grid-template-rows:none;gap:18px}.wf-upload-left{padding-right:0;border-right:none;overflow:visible}.wf-flow-card{min-height:480px}}'
        // 唯一一处入场动效：切到页面时轻微上浮淡入，不逐个元素重复播放。
        + '@keyframes wf-page-in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}'
        + '.wf-upload-layout{animation:wf-page-in .22s cubic-bezier(.2,.8,.2,1)}';
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
      if (!r.ok) {
        const text = await r.text();
        let message = text || String(r.status);
        try { const data = JSON.parse(text); message = data.error || data.message || message; } catch (_) {}
        const error = new Error(message);
        error.httpStatus = r.status;
        throw error;
      }
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
      const [form, setForm] = React.useState({ title: '', year: '', region: '安徽', subject: 'english', source_type: '', grade: '' });
      const [files, setFiles] = React.useState([]);
      const [taxonomy, setTaxonomy] = React.useState({ sources: [], years: [], regions: [] });
      const [busy, setBusy] = React.useState(false);
      const [err, setErr] = React.useState('');
      const [notice, setNotice] = React.useState('');
      const [batches, setBatches] = React.useState([]);
      const [sel, setSel] = React.useState('');
      const [tick, setTick] = React.useState(0);
      const [dragging, setDragging] = React.useState(false);
      const [advancing, setAdvancing] = React.useState('');
      const fileInputRef = React.useRef(null);
      const aliveRef = React.useRef(true);
      const refreshSequence = React.useRef(0);
      const [refreshError, setRefreshError] = React.useState('');
      const [lastRefresh, setLastRefresh] = React.useState('');
      React.useEffect(() => () => { aliveRef.current = false; }, []);

      // 轮询批次进度（排队区 + 流程图共用）。上传后也会立即拉一次，让新试卷
      // 立刻进入排队区并开始它自己的流程。
      const refresh = React.useCallback(async () => {
        const sequence = ++refreshSequence.current;
        try {
          const d = await jget('/batches');
          if (!aliveRef.current || sequence !== refreshSequence.current) return null;
          setRefreshError(''); setLastRefresh(new Date().toLocaleTimeString());
          const list = d.batches || [];
          setBatches(list);
          // 排队区只展示「处理中」的试卷（内容审核与上传格式检查结束的已流转到试卷审核）。
          // 当前选中的那份一旦结束，自动切到下一份未结束的，让右侧流程图
          // 立即开始新试卷的流程，而不是停在已完成的旧卷上。
          setSel((cur) => {
            const pending = list.filter((b) => !b.flow_done && !b.uploaded);
            if (!pending.length) return '';
            return pending.some((b) => b.name === cur) ? cur : pending[0].name;
          });
          return list;
        } catch (e) {
          if (aliveRef.current && sequence === refreshSequence.current) setRefreshError('进度刷新失败，当前为上次结果：' + String(e.message || e));
          return null;
        }
      }, []);
      React.useEffect(() => {
        refresh();
        (async () => {
          try { const d = await jget('/taxonomy'); if (aliveRef.current) setTaxonomy({ sources: d.sources || [], years: d.years || [], regions: d.regions || [] }); }
          catch (e) { /* taxonomy is optional for the upload shell */ }
        })();
        let stopped = false, timer;
        async function poll() {
          await refresh();
          if (!stopped) timer = setTimeout(poll, 2000);
        }
        timer = setTimeout(poll, 2000);
        const onFocus = () => { if (!document.hidden) refresh(); };
        window.addEventListener('focus', onFocus);
        document.addEventListener('visibilitychange', onFocus);
        return () => { stopped = true; refreshSequence.current++; clearTimeout(timer);
          window.removeEventListener('focus', onFocus); document.removeEventListener('visibilitychange', onFocus); };
      }, [refresh]);

      // 选择 / 拖放统一入口：原生 input 只保留可聚焦性与 accept，视觉完全交给自定义控件。
      function addFiles(list) {
        const incoming = Array.from(list || []);
        if (!incoming.length) return;
        const supported = /\.(docx|pdf|mp3|wav|m4a|ogg|flac|aac)$/i;
        const bad = incoming.filter((f) => !supported.test(f.name));
        // Keep every selected item visible, even equal-sized same-name files.
        // Unsupported items block submission until removed rather than vanishing.
        setFiles((prev) => prev.concat(incoming));
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
      function removeFile(idx) { setFiles((prev) => prev.filter((_, i) => i !== idx)); }
      // 从当前阶段继续推进：宿主重启会丢掉后台推进线程，必须有一个可手动续跑
      // 的入口，否则批次会永远停在中间阶段。
      async function advance(name) {
        if (!name) return;
        setAdvancing(name);
        setErr('');
        try {
          await jpost('/batches/' + encodeURIComponent(name) + '/advance', {});
          for (let i = 0; i < 600; i++) {
            await new Promise((r) => setTimeout(r, 2000));
            if (!aliveRef.current) return;
            try {
              const st = await jget('/batches/' + encodeURIComponent(name) + '/pipeline');
              if (!st.running) break;
            } catch (e) { /* 单次查询失败不终止轮询 */ }
          }
        } catch (e) {
          if (aliveRef.current) setErr('批次「' + name + '」处理失败：' + String(e.message || e));
        } finally {
          if (aliveRef.current) setAdvancing('');
        }
      }
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
          for (const f of files) fd.append('files', f, f.name);
          const r = await window.fetch(API + '/uploads', { method: 'POST', body: fd });
          if (!r.ok) throw new Error((await r.text()) || r.status);
          const data = await r.json();
          setFiles([]);
          if (fileInputRef.current) { try { fileInputRef.current.value = ''; } catch (_) {} }
          setForm({ ...form, title: '', year: '' });
          setTick((x) => x + 1);
          // 留在上传页：立刻把新试卷拉进排队区，并让右侧流程图从它的第一步开始。
          await refresh();
          // 已上传的重复批次不在列表展示，不要强行选中一个看不到的批次。
          if (data && data.name && !(data.duplicate && data.uploaded)) setSel(data.name);
          // 重复上传不会新建任务：明确告知，避免“批次任务不见了”的错觉。
          if (data && (data.duplicate || data.reused)) setNotice(data.message || '该试卷已存在，已定位到已有任务。');
          if (props.onUploaded) props.onUploaded(data);
        } catch (e) { setErr(String(e.message || e)); }
        finally { setBusy(false); }
      }

      const inp = { ...T.input, width: '100%', padding: '6px 10px', fontSize: 12.5 };
      // 「处理中」= 内容审核与上传格式检查未结束且未上传；「已完成」= 内容审核与上传格式检查结束、等待人工确认。
      const pendingBatches = batches.filter((b) => !b.flow_done && !b.uploaded);
      const finishedBatches = batches.filter((b) => b.flow_done && !b.uploaded);
      const goReview = () => { if (props.onGoReview) props.onGoReview(); };
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
          h('label', {
            className: 'wf-file-drop' + (dragging ? ' wf-dragover' : ''),
            onDragOver: (e) => { e.preventDefault(); setDragging(true); },
            onDragLeave: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false); },
            onDrop,
          },
            h('input', { ref: fileInputRef, className: 'wf-file-input', type: 'file', multiple: true, accept: '.docx,.pdf,.mp3,.wav,.m4a,.ogg,.flac,.aac', onChange: onFiles, 'aria-label': '选择上传文件' }),
            h('span', { style: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, color: 'var(--dsw-alias-label-secondary)' } }, uploadIcon(18)),
            h('span', { style: { display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 } },
              h('span', { className: 'wf-file-label' }, '选择上传文件')),
          ),
          files.length ? h('div', { style: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6, marginTop: 10 } },
            files.map((f, i) => h('span', { key: f.name + ':' + i, className: 'wf-file-chip' },
              h('span', { title: f.name, style: { maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, f.name),
              h('button', { type: 'button', title: '移除', 'aria-label': '移除 ' + f.name, onClick: () => removeFile(i) }, closeIcon()),
            ))) : null,
          err ? h('div', { style: { ...T.statusErr, marginTop: 10, display: 'flex', alignItems: 'flex-start', gap: 7, lineHeight: 1.6 } },
            h('span', { style: { display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 15, height: 15, borderRadius: '50%', flexShrink: 0, marginTop: 2, background: 'var(--dsw-alias-state-error-primary)', color: '#fff', fontSize: 10, fontWeight: 700 } }, '!'),
            h('span', null, err)) : null,
          notice ? h('div', { role: 'status', 'aria-live': 'polite', style: { ...T.statusOk, marginTop: 10, lineHeight: 1.6 } }, notice) : null,
          h('div', { style: { marginTop: 10, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' } },
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, disabled: busy, onClick: submit }, busy ? TEXT.uploading : '上传'),
          ),
        ),
      );

      // 左下：排队区常驻显示（即使没有处理中的试卷也保留这块区域和空状态）。
      // 只列「处理中」的试卷：内容审核与上传格式检查结束的已从队列移除，流转到试卷审核等待人工确认，
      // 但它们不再占用排队区，也不改变排队区本身始终存在。
      const queue = h('div', { style: { flex: 1, minHeight: 190, display: 'flex', flexDirection: 'column', marginTop: 12 } },
        h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 } },
          h('span', { style: { fontSize: 14, fontWeight: 600 } }, '排队区'),
          h('span', { style: T.pill }, h('span', { style: T.pillNum }, String(pendingBatches.length)), ' 份处理中')),
        h('div', { className: 'wf-scroll', style: { flex: 1, overflow: 'auto', minHeight: 0, border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 12, background: 'var(--dsw-alias-bg-layer-1)', padding: 6, display: pendingBatches.length === 0 ? 'flex' : 'block', flexDirection: 'column' } },
          pendingBatches.length === 0
            ? h(EmptyState, {
                title: finishedBatches.length ? '暂无处理中的试卷' : '暂无试卷',
                hint: finishedBatches.length ? '上传新试卷会开始一轮新流程。' : '上传试卷后，会在这里排队。',
                flex: true, compact: true,
              })
            : pendingBatches.map((b, i) => {
                const st = b.stage || {};
                const cur = st.current || {};
                const progress = compactProgress(st);
                const done = progress.done, total = progress.total;
                const pct = total ? Math.round(done / total * 100) : 0;
                const active = b.name === sel;
                return h('div', {
                  key: b.name, className: 'wf-queue-row', onClick: () => setSel(b.name),
                  style: {
                    display: 'flex', alignItems: 'center', gap: 10, padding: '9px 10px', borderRadius: 9, cursor: 'pointer',
                    background: active ? 'var(--wf-sel-bg)' : 'transparent',
                    marginBottom: 3,
                    transition: 'background .16s',
                  },
                },
                  h('span', { style: { flex: '0 0 auto', width: 22, height: 22, borderRadius: 7, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 11.5, fontWeight: 700, background: 'var(--dsw-alias-bg-layer-2)', color: active ? 'var(--wf-brand)' : 'var(--dsw-alias-label-secondary)' } }, String(i + 1)),
                  h('div', { style: { flex: 1, minWidth: 0 } },
                    h('div', { style: { fontSize: 12.5, fontWeight: 600, color: active ? 'var(--wf-brand)' : 'var(--dsw-alias-label-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, (b.paper && b.paper.title) || b.name),
                    h('div', { style: { ...T.hint, fontSize: 11, marginTop: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, (cur.id ? shortStageName(cur) : (st.hint || '等待')) + (total ? ' · ' + done + '/' + total : '')),
                    h('div', { style: { height: 3, borderRadius: 999, background: 'var(--dsw-alias-bg-layer-2)', marginTop: 6, overflow: 'hidden' } },
                      h('div', { style: { width: pct + '%', height: '100%', borderRadius: 999, background: 'var(--wf-brand)', opacity: .7, transition: 'width .3s ease' } })),
                  ),
                  h('span', { style: { flex: '0 0 auto', fontSize: 11, color: 'var(--dsw-alias-label-secondary)', fontVariantNumeric: 'tabular-nums' } }, pct + '%'),
                );
              }),
        ),
      );

      // 醒目入口：已结束二审的试卷不占排队区，流转到「试卷审核」做人工确认。
      // 只有点这个按钮才切标签，不打断继续看下一份试卷的流程。
      const handoff = finishedBatches.length === 0 ? null : h('div', {
        className: 'wf-handoff',
        style: {
          marginTop: 14, flex: '0 0 auto', display: 'flex', alignItems: 'center', gap: 12,
          padding: '12px 14px', borderRadius: 12,
          background: 'var(--wf-brand-soft)', border: '1px solid var(--wf-brand-border)',
        },
      },
        h('span', {
          style: {
            flex: '0 0 auto', width: 30, height: 30, borderRadius: '50%',
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            // 完成标记也用中性色：浅底 + 细边框，不用绿色。
            background: 'var(--wf-brand-soft)', border: '1px solid var(--dsw-alias-border-l2)',
            color: 'var(--dsw-alias-label-secondary)', fontSize: 15, fontWeight: 700, lineHeight: 1,
          },
        }, '✓'),
        h('div', { style: { flex: 1, minWidth: 0 } },
          h('div', { style: { fontSize: 13, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' } },
            '已完成 ' + finishedBatches.length + ' 份 · 待人工确认')),
        h('button', {
          className: 'wf-btn-primary', style: { ...T.miniBtnPrimary, padding: '7px 14px', fontSize: 12.5, fontWeight: 600, flexShrink: 0, whiteSpace: 'nowrap' },
          onClick: goReview,
        }, '前往人工确认 →'),
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
      const sourceCurId = (stage.current && stage.current.id) || '';
      const curGroup = items.find((it) => it.stage_ids.includes(sourceCurId));
      const curId = curGroup ? curGroup.id : '';
      const DONE = { completed: true, approved: true, skipped: true, completed_with_findings: true };
      const doneN = items.filter((it) => DONE[it.status]).length;
      const pctAll = items.length ? Math.round(doneN / items.length * 100) : 0;
      const curStage = items.find((it) => it.id === curId) || null;
      // 四行依赖布局：前三步 / 四路并行 / 文字汇总与体检 / 审核至上传。
      // 听力独立沿左侧通向内容审核，不经过文字汇总和本地体检。
      // 原图按宽度等比缩放，行距偏紧显得拥挤；加大行距与半径让纵向更舒展，
      // 底部预留两行节点名（名称在节点下方 + 两行文字）。
      const R = 13, CW = 100, ROW_H = 108, PADX = 46, PADY = 30;
      const positions = {
        extract: [0, 0], structure_gate: [1.5, 0], preassemble: [3, 0],
        audio: [0, 1], answers: [1, 1], explanations: [2, 1], knowledge: [3, 1],
        assemble: [1, 2], validate: [3, 2],
        review: [0, 3], format_gate: [1.5, 3], upload: [3, 3],
      };
      const nodes = items.map((it) => {
        const [col, row] = positions[it.id];
        return { it, x: PADX + col * CW, y: PADY + row * ROW_H, row, col };
      });
      const svgW = PADX * 2 + 3 * CW;
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
      const segs = [], arrows = [];
      const nodeById = new Map(nodes.map((node) => [node.it.id, node]));
      flowDependencies().forEach(([from, to]) => {
        const a = nodeById.get(from), b = nodeById.get(to);
        if (!a || !b) return;
        const reached = !!DONE[a.it.status];
        // 连接线分三态：两端都到 = 已走过的实线；到达运行中节点 = 流动虚线；
        // 其余 = 未走的浅色虚线。箭头沿用同线颜色，避免阅读歧义。
        const live = !!a.it.inProgress || !!b.it.inProgress;
        const segLine = reached ? doneLine : live ? 'var(--wf-flow-line-live)' : todoLine;
        let points;
        if (a.row === b.row) {
          points = [[a.x + R, a.y], [b.x - R, b.y]];
        } else if (from === 'preassemble') {
          // 共用分叉横线放在上排标签下方，四路各自向下进入节点。
          const busY = b.y - 24;
          points = [[a.x + R, a.y], [svgW - 18, a.y], [svgW - 18, busY], [b.x, busY], [b.x, b.y - R]];
        } else if (to === 'assemble') {
          const busY = b.y - 22;
          // 从文字节点侧面出线，绕过其下方名称，三路在汇总前合流。
          points = [[a.x + R, a.y], [a.x + 40, a.y], [a.x + 40, busY], [b.x, busY], [b.x, b.y - R]];
        } else if (from === 'audio') {
          points = [[a.x - R, a.y], [18, a.y], [18, b.y], [b.x - R, b.y]];
        } else {
          // 体检完成沿行间通道回到内容审核，与听力单独汇合。
          const busY = b.y - 22;
          points = [[a.x + R, a.y], [svgW - 18, a.y], [svgW - 18, busY], [b.x, busY], [b.x, b.y - R]];
        }
        segs.push({ from, to, d: points.map(([x, y], i) => (i ? ' L' : 'M') + x + ',' + y).join(''), reached, live, line: segLine });
        const last = points[points.length - 1], prev = points[points.length - 2];
        arrows.push({ x: last[0], y: last[1], dir: last[1] > prev[1] ? 'down' : 'right', reached, live, line: segLine });
      });
      const flowHead = h('div', { style: { flexShrink: 0 } },
        h('div', { style: { display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' } },
          h('div', { style: { minWidth: 0, flex: 1 } },
            h('div', { style: { fontSize: 14, fontWeight: 600, letterSpacing: '-.01em' } }, isFlowOverview ? '流程总览' : '流程进度'),
            selBatch
              ? h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-label-primary)', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, (selBatch.paper && selBatch.paper.title) || selBatch.name)
              : null,
            !isFlowOverview && items.length ? h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginTop: 7 } },
              h('div', { style: { width: 132, height: 4, borderRadius: 999, background: 'var(--dsw-alias-bg-layer-2)', overflow: 'hidden', flexShrink: 0 } },
                h('div', { style: { width: pctAll + '%', height: '100%', borderRadius: 999, background: 'var(--dsw-alias-label-secondary)', opacity: .45, transition: 'width .4s ease' } })),
              h('span', { style: { ...T.hint, fontSize: 11.5, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' } }, doneN + ' / ' + items.length),
              curStage ? h('span', { style: { fontSize: 11.5, fontWeight: 600, color: 'var(--wf-brand)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, '· ' + shortStageName(curStage)) : null) : null),
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, flexWrap: 'wrap', justifyContent: 'flex-end', paddingTop: 2 } },
            selBatch ? (() => { const queued = (selBatch.advance || {}).queued || (selBatch.advance || {}).waiting; const busy = advancing === selBatch.name || (selBatch.advance || {}).running || queued; const label = advancing === selBatch.name ? '推进中…' : ((selBatch.advance || {}).running ? '推进中…' : (queued ? ((selBatch.advance || {}).waiting ? '等待前一份…' : '排队中…') : '继续处理')); return h('button', { className: 'wf-btn-primary', style: { ...T.miniBtnPrimary, padding: '4px 12px' }, disabled: busy, title: '从当前阶段继续推进', onClick: () => advance(selBatch.name) }, label); })() : null),
        ),
        recoveryProgressText(selBatch && selBatch.advance) ? h('p', { role: 'status', 'aria-live': 'polite', style: { ...T.hint, margin: '8px 0 0' } }, recoveryProgressText(selBatch.advance)) : null,
      );
      // 整图是一个自带 viewBox 的 SVG：随右栏宽度等比缩放，绝不会顶出卡片边界。
      // 悬停浮层里的子步骤状态：只显示“名称·状态”，保持极短，不显示长描述。
      function compactStatusLabel(status) {
        const map = { completed: '已完成', completed_with_findings: '有差异', approved: '已批准',
          skipped: '已跳过', failed: '失败', blocked: '受阻', needs_review: '待核验',
          invalidated: '需重跑', pending: '待处理', running: '处理中', warning: '提示' };
        return map[status] || statusText(status);
      }
      const flowDiagram = h('div', { style: { width: '100%', display: 'flex', justifyContent: 'center' } },
        h('svg', {
          viewBox: '0 0 ' + svgW + ' ' + svgH,
          role: 'group',
          // 完全自适应容器：不设固定宽高，随列宽拉伸，高度按 viewBox 比例自动计算，
          // 面板变宽变窄都重新排布；空间不足时外层容器滚动兜底。
          preserveAspectRatio: 'xMidYMid meet',
          style: { display: 'block', width: '100%', height: 'auto', aspectRatio: svgW + ' / ' + svgH },
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
            const nodeLabel = isFlowOverview ? shortStageName(it) : [shortStageName(it) + ' · ' + statusText(it.status)]
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
              done && !warn ? h('text', { x: n.x, y: n.y + 4.5, textAnchor: 'middle', fontSize: 12.5, fontWeight: 700, fill: 'var(--wf-flow-node-done-mark)' }, '✓')
                : failed ? h('text', { x: n.x, y: n.y + 4.5, textAnchor: 'middle', fontSize: 12.5, fontWeight: 700, fill: 'var(--wf-flow-node-error)' }, '!')
                  : warn ? h('text', { x: n.x, y: n.y + 4.5, textAnchor: 'middle', fontSize: 12.5, fontWeight: 700, fill: 'var(--wf-flow-node-warn)' }, '!')
                    : isCur ? h('circle', { className: 'wf-node-dot', cx: n.x, cy: n.y, r: 4, fill: 'var(--wf-flow-node-cur-mark)' }) : null,
              // 节点名：两行居中；文字随 viewBox 一起缩放，不再溢出到卡片外。
              lines.map((line, li) => h('text', {
                key: 'l' + li,
                x: n.x, y: n.y + R + 17 + li * 15,
                textAnchor: 'middle', fontSize: 12,
                fontWeight: isCur ? 700 : 500,
                fill: isCur ? 'var(--wf-brand)' : (done ? 'var(--dsw-alias-label-primary)' : 'var(--dsw-alias-label-secondary)'),
              }, line)),
            );
          }),
        ));
      const flow = h('div', { className: 'wf-flow-card', style: { ...T.card, flex: 1, minWidth: 0, minHeight: 0, display: 'flex', flexDirection: 'column', marginBottom: 0 } },
        flowHead,
        refreshError || !isFlowOverview ? h('p', { role: 'status', style: { ...T.hint, margin: '5px 0' } }, refreshError || (lastRefresh ? '进度更新：' + lastRefresh + ' · 自动刷新' : '正在读取进度…')) : null,
        h('div', { className: 'wf-scroll', style: { flex: 1, overflow: 'auto', minHeight: 0, display: 'flex', justifyContent: 'center', alignItems: 'flex-start', paddingTop: 12 } },
          items.length === 0
            ? h('div', { style: { width: '100%', display: 'flex', flex: 1 } }, h(EmptyState, { title: '暂无流程', flex: true }))
            : flowDiagram),
        diagBanner(selBatch, stage),
        h(OperationsPanel, { key: 'operations:' + (selBatch ? selBatch.name : ''), batchName: selBatch ? selBatch.name : '' }),
      );

      return h('div', { className: 'wf-upload-layout' },
        h('div', { className: 'wf-upload-columns' },
          // 左列：上（上传）+ 中（排队区，只列处理中）+ 下（已完成 → 人工确认入口）
          h('div', { className: 'wf-upload-left wf-scroll' }, uptop, queue, handoff),
          // 右列：流程进度
          flow,
        ),
      );
    }
    // AI 运维是附加观察面板；读取记录不启动接手，也不替代原有自愈。
    function operationsText(value) {
      if (value == null) return '';
      if (typeof value !== 'object') return String(value);
      try { return JSON.stringify(value); } catch (_) { return '无法读取详情'; }
    }
    function operationsView(incident, context = {}) {
      const item = incident && typeof incident === 'object' && !Array.isArray(incident) ? incident : {};
      const events = Array.isArray(item.events) ? item.events.filter((e) => e && typeof e === 'object') : [];
      const timeOf = (e) => e.timestamp || e.created_at || e.at || e.time || '';
      const latest = events.reduce((last, event) => {
        if (!last) return event;
        const a = Date.parse(timeOf(last)), b = Date.parse(timeOf(event));
        return Number.isFinite(a) && Number.isFinite(b) && b < a ? last : event;
      }, null) || {};
      // Heartbeats prove liveness; keep the last diagnostic or repair action visible.
      const substantive = events.filter(e => !['heartbeat', 'progress'].includes(e.action));
      const business = substantive.reduce((last, event) => {
        if (!last) return event;
        const a = Date.parse(timeOf(last)), b = Date.parse(timeOf(event));
        return Number.isFinite(a) && Number.isFinite(b) && b < a ? last : event;
      }, null) || latest;
      const verification = item.verification || latest.verification || null;
      // Only the backend's structured verification may establish recovery; prose cannot.
      const verified = !!(verification && typeof verification === 'object'
        && (verification.verified === true || verification.passed === true)
        && typeof verification.evidence_id === 'string' && verification.evidence_id.trim());
      const raw = operationsText(item.status).toLowerCase();
      const labels = {
        unclaimed: '未接手', idle: '未接手', pending: '排队', queued: '排队',
        investigating: '调查', investigation: '调查', diagnosing: '调查',
        repairing: '修复', repair: '修复', verifying: '复验', verification: '复验',
        recovered: '已恢复', resolved: '已恢复', waiting_external: '待外部条件', awaiting_external: '待外部条件',
        awaiting_execution: '排队', needs_deep_repair: '需深入修复', needs_human: '需人工处理', claimed: '调查', working: '调查',
        blocked: '待外部条件', cancelled: '已取消', canceled: '已取消',
      };
      let label = !incident ? '未接手' : (labels[raw] || '状态待确认');
      if (raw === 'claimed' || raw === 'working') {
        if (latest.action === 'apply_repair' || latest.action === 'preview_repair') label = '修复';
        if (latest.action === 'verify') label = '复验';
      }
      if ((raw === 'recovered' || raw === 'resolved') && !verified) label = '复验待确认';
      const unknown = (context.receipts || []).filter(r => r && r.status === 'unknown').length;
      const now = context.now || Date.now();
      const hostAttempts = ((context.host || {}).attempts || []).filter(r =>
        r.batch_name === context.batchName && r.incident_id === item.incident_id);
      const hostAttempt = hostAttempts.slice().reverse().find(r => r.active) || hostAttempts[hostAttempts.length - 1];
      if (raw === 'claimed' || raw === 'working') {
        if (['apply_repair', 'preview_repair', 'regenerate_units', 'request_code_repair', 'code_repair', 'install_code_repair'].includes(business.action)) label = '修复';
        if (business.action === 'verify') label = '复验';
        if (hostAttempt && hostAttempt.active) {
          const phases = { starting: '启动会话', investigating: '调查', awaiting_execution: '等待原流程续跑',
            awaiting_restart: '等待重启复验', session_failed: '会话失败', backend_disconnected: '连接中断',
            lease_lost: '租约失效', budget_exceeded: '本次预算已用尽', outcome_unknown: '待核对动作回执' };
          label = phases[hostAttempt.state] || label;
        }
        if (item.lease_expires_at && item.lease_expires_at * 1000 <= now) label = '接手已过期';
      }
      if (raw === 'awaiting_execution') label = '等待原流程续跑';
      if (raw === 'queued' && item.not_before * 1000 > now) label = '冷却等待';
      if (unknown) label = '待核对动作回执';
      if (hostAttempt && hostAttempt.state === 'configuration_failed') label = '模型配置受阻';
      return { label, raw, verified, unknown,
        heartbeat: operationsText(timeOf(latest)), retryAt: item.not_before ? new Date(item.not_before * 1000).toLocaleString() : '',
        error: hostAttempt && hostAttempt.error || '', sessionId: typeof item.session_id === 'string' ? item.session_id : '',
        time: operationsText(timeOf(business) || item.updated_at),
        action: operationsText(business.action || business.type || business.event || business.kind),
        outcome: [operationsText(business.outcome || business.result || business.message), operationsText(business.detail)].filter(Boolean).join(' · '),
        verification: operationsText(verification),
      };
    }
    async function operationsRequest(path, body, signal) {
      const controller = new AbortController();
      const abort = () => controller.abort();
      if (signal && signal.aborted) abort();
      else if (signal) signal.addEventListener('abort', abort, { once: true });
      const timer = setTimeout(abort, 15000);
      try {
        const response = await window.fetch('/wf-plugin/operations/' + path, {
          method: body === undefined ? 'GET' : 'POST', signal: controller.signal,
          headers: { accept: 'application/json', ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        });
        const text = await response.text();
        let data;
        try { data = JSON.parse(text); } catch (_) { throw new Error('运维接口返回了无法读取的内容（HTTP ' + response.status + '）'); }
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('运维接口返回格式不完整');
        if (!response.ok || (path !== 'status' && data.error)) throw new Error(operationsText(data.error || data.message) || '运维请求失败（HTTP ' + response.status + '）');
        return data;
      } catch (e) {
        if (e.name === 'AbortError') throw new Error('运维请求已取消或超过15秒，请刷新核实');
        throw e;
      } finally {
        clearTimeout(timer);
        if (signal) signal.removeEventListener('abort', abort);
      }
    }
    function OperationsStatus({ incident, context }) {
      const h = require('react').createElement;
      const view = operationsView(incident, context);
      return h('span', { role: 'status', style: T.badge(), title: view.raw || '尚无接手记录' }, view.label);
    }
    function OperationsPanel({ batchName }) {
      const React = require('react'), h = React.createElement;
      const [host, setHost] = React.useState({ enabled: true, available: false, loaded: false });
      const [record, setRecord] = React.useState(null);
      const [hostError, setHostError] = React.useState('');
      const [recordError, setRecordError] = React.useState('');
      const [busy, setBusy] = React.useState(false);
      const [copyMessage, setCopyMessage] = React.useState('');
      const [refresh, setRefresh] = React.useState(0);
      const generation = React.useRef(0);
      const saving = React.useRef(false);
      const configVersion = React.useRef(0);
      React.useEffect(() => {
        const seq = ++generation.current;
        const controller = new AbortController();
        let timer;
        setRecord(null); setRecordError(''); setCopyMessage('');
        async function readStatus() {
          // A config write owns host state until its acknowledgement arrives.
          if (!saving.current) {
            const version = configVersion.current;
            try {
              const data = await operationsRequest('status', undefined, controller.signal);
              if (seq === generation.current && !saving.current && version === configVersion.current) {
                if (typeof data.enabled !== 'boolean' || typeof data.available !== 'boolean') throw new Error('运维状态字段不完整');
                setHost({ ...data, loaded: true }); setHostError(operationsText(data.error));
              }
            } catch (e) {
              if (seq === generation.current && version === configVersion.current) { setHostError(operationsText(e.message)); setHost((old) => ({ ...old, loaded: false, available: false })); }
            }
          }
          if (batchName) {
            try {
              const data = await jget('/batches/' + encodeURIComponent(batchName) + '/operations', controller.signal);
              if (!data || !Object.prototype.hasOwnProperty.call(data, 'incident') || (data.incident !== null && (typeof data.incident !== 'object' || Array.isArray(data.incident)))) throw new Error('运维记录格式不完整');
              if (seq === generation.current) { setRecord({ batchName, incident: data.incident, receipts: data.receipts || [] }); setRecordError(''); }
            } catch (e) {
              if (seq === generation.current) setRecordError(operationsText(e.message));
            }
          }
          if (seq === generation.current) timer = setTimeout(readStatus, 3000);
        }
        readStatus();
        return () => { generation.current++; controller.abort(); clearTimeout(timer); };
      }, [batchName, refresh]);
      async function toggle() {
        if (saving.current || !host.loaded) return;
        saving.current = true; configVersion.current++; setBusy(true); setHostError('');
        const seq = generation.current;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 15000);
        try {
          const data = await operationsRequest('config', { enabled: !host.enabled }, controller.signal);
          if (typeof data.enabled !== 'boolean') throw new Error('接口未确认启停结果，请刷新核实');
          if (seq === generation.current) setHost((old) => ({ ...old, enabled: data.enabled }));
        } catch (e) {
          if (seq === generation.current) { setHostError('启停结果未确认：' + operationsText(e.message) + '。请刷新核实。'); setHost((old) => ({ ...old, loaded: false })); }
        } finally {
          clearTimeout(timeout); saving.current = false;
          if (seq === generation.current) setBusy(false);
        }
      }
      const current = record && record.batchName === batchName ? record : null;
      const context = { host, batchName, receipts: current && current.receipts || [] };
      const view = operationsView(current && current.incident, context);
      const service = !host.loaded ? '状态读取中' : !host.enabled ? '已关闭' : !host.available ? '服务不可用'
        : hostError || host.configError ? '配置受阻' : host.lastPollError ? '轮询异常'
        : host.running === false ? '服务未运行' : host.activeCount > 0 ? '正在接手 ' + host.activeCount + ' 个故障'
        : host.incidentCount > 0 ? '有故障待接手'
        : host.healingCount > 0 ? '流程推进中（' + host.healingCount + ' 个批次，自愈进行中）' : '已启用 · 待命';
      const line = (label, value) => h('div', { style: { overflowWrap: 'anywhere', whiteSpace: 'pre-wrap', marginTop: 4 } }, h('strong', null, label + '：'), value || '暂无记录');
      return h('section', { 'aria-label': 'AI运维', style: { borderTop: '1px solid var(--dsw-alias-border-l1)', marginTop: 10, paddingTop: 10, minWidth: 0, flexShrink: 0 } },
        h('div', { className: 'wf-operations-toolbar', style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'nowrap', minWidth: 0 } },
          h('strong', { style: { ...T.sectionTitle, flexShrink: 0, whiteSpace: 'nowrap' } }, 'AI运维'),
          h('span', { style: { flex: 1, minWidth: 0 } }),
          h('button', { type: 'button', className: 'wf-btn', role: 'switch', 'aria-label': 'AI运维', 'aria-checked': host.enabled, disabled: busy || !host.loaded, style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, onClick: toggle }, busy ? '保存中…' : host.enabled ? '关闭运维' : '启动运维'),
          h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: () => setRefresh((n) => n + 1) }, '刷新')),
        h('div', { className: 'wf-operations-status', style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginTop: 8, minWidth: 0, overflowWrap: 'anywhere' } },
          h('span', { role: 'status', style: T.hint }, service),
          current && !recordError ? h(OperationsStatus, { incident: current.incident, context }) : h('span', { role: 'status', style: T.hint }, recordError ? '记录不可用' : batchName ? '读取中…' : '未接手')),
        h('p', { style: { ...T.hint, margin: '5px 0 0' } },
          !host.loaded ? '后台接手状态待确认；原流程继续，原有自愈不受影响。' : !host.enabled ? '后台接手已关闭；原流程继续，原有自愈不受影响。' : !host.available ? '后台接手暂不可用；原流程继续，原有自愈不受影响。' : '后台接手已开启，仅处理异常；原有自愈保持运行。'),
        hostError ? h('p', { role: 'alert', style: { ...T.statusErr, margin: '5px 0 0', overflowWrap: 'anywhere' } }, '运维服务：' + hostError) : null,
        host.lastPollAt ? h('p', { style: { ...T.hint, margin: '5px 0 0' } }, '最近扫描：' + new Date(host.lastPollAt).toLocaleTimeString() + ' · 自动巡检每5秒；仅在普通自愈失败后接手，审核差异不触发。') : null,
        host.lastPollError ? h('p', { role: 'alert', style: T.statusErr }, '运维扫描失败：' + host.lastPollError + '（如已恢复，将在下次成功扫描后自动消失）') : null,
        host.loopAlive === false && host.enabled ? h('p', { role: 'status', style: T.hint }, '自动巡检循环已停止，正在自动恢复；如持续不恢复，请重载插件。') : null,
        host.lastPollAt && host.enabled && (host.pollStale || Date.now() - Date.parse(host.lastPollAt) > 20000) ? h('p', { role: 'status', style: T.hint }, '自动巡检已超过 20 秒无成功扫描，可能后端重启中，正在重试。') : null,
        !batchName && Array.isArray(host.attempts) ? host.attempts.map((attempt) => h('p', { key: attempt.incident_id, role: 'status', style: { ...T.hint, margin: '5px 0' } },
          '批次「' + attempt.batch_name + '」（workflows/' + attempt.batch_name + '）：' + (attempt.active ? '正在接手' : '本次接手已结束')
          + ' · ' + attempt.state + (attempt.error ? ' · ' + attempt.error : '')
          + (attempt.updated_at ? ' · ' + new Date(attempt.updated_at).toLocaleTimeString() : ''))) : null,
        view.unknown ? h('p', { role: 'status', style: T.hint }, '有 ' + view.unknown + ' 个动作结果尚未确认，后台已停止重复执行，等待核对回执；不代表已修复。') : null,
        view.retryAt && view.label === '冷却等待' ? h('p', { style: T.hint }, '下次可接手时间：' + view.retryAt) : null,
        view.error ? h('p', { role: 'alert', style: T.statusErr }, view.error) : null,
        recordError ? h('p', { role: 'alert', style: { ...T.statusErr, margin: '5px 0 0', overflowWrap: 'anywhere' } }, '批次「' + batchName + '」（workflows/' + batchName + '）运维记录读取失败：' + recordError + '。可刷新重试。') : null,
        batchName ? h('details', { style: { ...T.hint, marginTop: 5 } },
          h('summary', { style: { cursor: 'pointer' } }, '查看最近运维事件'),
          h('div', { style: { maxHeight: 180, overflow: 'auto' } },
            line('批次', batchName + ' · workflows/' + batchName),
            recordError && current ? h('div', { role: 'status' }, '以下为上次读取记录，当前状态未确认。') : null,
            line('最近动作时间', view.time), line('最近心跳或事件', view.heartbeat), line('action', view.action), line('outcome', view.outcome), line('verification', view.verification),
            h('div', { style: { marginTop: 5 } }, '恢复仅以后台复验记录为准，模型自述不代表成功。'),
            view.sessionId ? h('div', { style: { marginTop: 6, display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 6 } },
              h('label', { style: { display: 'flex', alignItems: 'center', flex: '1 1 180px', minWidth: 0, gap: 6 } }, '会话 ID', h('input', { className: 'wf-input', style: { ...T.input, padding: '3px 6px', flex: 1, minWidth: 0 }, readOnly: true, value: view.sessionId, onFocus: (e) => e.target.select() })),
              h('button', { type: 'button', className: 'wf-btn', style: T.miniBtn, onClick: async () => {
                try {
                  if (!window.navigator || !window.navigator.clipboard) throw new Error('剪贴板不可用');
                  await window.navigator.clipboard.writeText(view.sessionId); setCopyMessage('已复制会话 ID');
                } catch (_) { setCopyMessage('复制失败，可选中会话 ID 复制'); }
              } }, '复制 ID'), h('span', { role: 'status' }, copyMessage)) : null)) : null);
    }

    function recoveryProgressText(advance) {
      if (!advance) return '';
      // 严格串行：前一份未完成且未移出排队区前，后面的批次不开始。
      if (advance.waiting) return '排队等待：前一份试卷全部完成并移出排队区后再开始';
      // 全局串行队列：前一份没跑完时，其它批次显示“排队等待”，不并跑。
      if (advance.queued && !advance.running) return '排队等待：前一份试卷跑完后自动开始';
      const healing = advance.self_heal || {};
      const phase = healing.status || '';
      if (phase === 'diagnosing') return advance.running ? 'AI 自动修复：正在判断卡点原因' : 'AI 自动修复：已接管，等待恢复检测';
      // 诊断调用被后端/插件重启打断（不是题目内容失败）：状态已自动收尾并退还预算。
      if (phase === 'diagnostic_interrupted') return 'AI 自动修复：上次诊断被重启中断，不是题目问题，已自动重新判断';
      if (phase === 'repaired') return 'AI 自动修复：已执行局部修复，继续校验';
      if (phase === 'requeued') return healing.has_feedback ? 'AI 自动修复：已安排受影响题目带反馈重试' : 'AI 自动修复：已安排当前阶段重试';
      if (phase === 'verified') return 'AI 自动修复：重试后校验已通过';
      if (['failed', 'retry_failed', 'recorded'].includes(phase)) return 'AI 自动修复：本轮尚未修复，已安排再次判断';
      if (phase === 'waiting_external') return 'AI 自动修复：等待所需输入或服务恢复，将自动重试';
      const status = (advance.supervisor || {}).status;
      if (['cooldown', 'waiting_external', 'exhausted'].includes(status)) return '自动恢复：已安排稍后重试，无需反复点击继续';
      return '';
    }
    // 仅表达分类间的真实依赖；类内交接与处理步骤只在悬浮层展开。
    function flowDependencies() {
      return [
        ['extract', 'structure_gate'], ['structure_gate', 'preassemble'],
        ['preassemble', 'audio'], ['preassemble', 'answers'],
        ['preassemble', 'explanations'], ['preassemble', 'knowledge'],
        ['answers', 'assemble'], ['explanations', 'assemble'], ['knowledge', 'assemble'],
        ['assemble', 'validate'], ['validate', 'review'], ['audio', 'review'],
        ['review', 'format_gate'], ['format_gate', 'upload'],
      ];
    }
    // 主图固定 12 类；状态归并真实 stage，不改变后台执行阶段。
    function compactFlowStages(sourceItems, overview = false) {
      const groups = [
        { id: 'extract', title: '内容提取', ids: ['extract'] },
        { id: 'structure_gate', title: '结构确认', ids: ['structure_gate'] },
        { id: 'preassemble', title: '组装判型', ids: ['preassemble', 'types'] },
        { id: 'audio', title: '听力处理', ids: ['audio'] },
        { id: 'answers', title: '答案填充', ids: ['answers'] },
        { id: 'explanations', title: '解析填充', ids: ['explanations'] },
        { id: 'knowledge', title: '知识标注', ids: ['knowledge'] },
        { id: 'assemble', title: '汇总整理', ids: ['assemble', 'meta', 'underlines'] },
        { id: 'validate', title: '本地体检', ids: ['validate'] },
        { id: 'review', title: '内容审核', ids: ['review', 'review_gate'] },
        { id: 'format_gate', title: '格式检查', ids: ['format_gate'] },
        { id: 'upload', title: '审核上传', ids: ['upload_gate', 'upload', 'publish_gate'] },
      ].map((group) => ({ ...group, members: [] }));
      // 无任务时复用同一分类与依赖图，仅展示结构，不构造任何批次或执行状态。
      if (overview) return groups.map((group) => ({
        id: group.id, title: group.title, stage_ids: group.ids,
        inProgress: false, members: [], substeps: [],
      }));
      const lookup = new Map();
      groups.forEach((group) => group.ids.forEach((id) => lookup.set(id, group)));
      let preceding = groups[0];
      (sourceItems || []).forEach((item) => {
        // 未知的兼容阶段保留在前一分类的悬浮层，不增加主图分类。
        const group = lookup.get(item.id) || preceding;
        group.members.push(item);
        preceding = group;
      });
      const done = { completed: true, approved: true, skipped: true };
      return groups.filter((group) => group.members.length).map((group) => {
        const members = group.members;
        const executionAccepted = (item) => item.status === 'needs_review'
          && item.execution_completed === true && item.continue_unqualified === true && !item.release_ready;
        const states = members.map((item) => executionAccepted(item) ? 'completed' : item.status || 'pending');
        const hasFindings = members.some(executionAccepted);
        let status = 'pending';
        if (states.every((state) => done[state])) status = hasFindings ? 'completed_with_findings' : states.every((state) => state === 'skipped') ? 'skipped' : 'completed';
        else status = ['failed', 'blocked', 'running', 'invalidated', 'needs_review'].find((state) => states.includes(state)) || 'pending';
        // 显式 active 覆盖兼容 inProgress（包括 active=false）；仅缺 active 时回退。
        const inProgress = members.some((item) => typeof item.active === 'boolean'
          ? item.active : typeof item.inProgress === 'boolean' ? item.inProgress : item.status === 'running');
        const description = members.map((item) => shortStageName(item) + '：' + (executionAccepted(item) ? '已执行，有质量差异（未放行）' : statusText(item.status))
          + (item.description ? '\n' + item.description : '') + (item.notes ? '\n' + item.notes : '')).join('\n');
        const displayMember = (item) => ({ id: item.id, title: shortStageName(item),
          status: executionAccepted(item) ? 'completed_with_findings' : (item.status || 'pending') });
        return { id: group.id, title: group.title, status, inProgress,
          stage_ids: members.map((item) => item.id),
          description, done: states.filter((state) => done[state]).length, total: members.length,
          members: members.map(displayMember),
          // 只展开后台证据，不由父阶段状态推断内部步骤完成。旧响应缺步骤时回退 stage。
          substeps: members.flatMap((item) => {
            const steps = Array.isArray(item.substeps) ? item.substeps.filter((step) => step && typeof step === 'object') : [];
            return steps.length ? steps.map((step) => ({ id: step.id, title: shortStageName(step), status: step.status || 'pending' })) : [displayMember(item)];
          }) };
      });
    }
    function statusText(s) {
      return ({ completed: '已完成', completed_with_findings: '已执行，有差异', approved: '已批准', skipped: '已跳过', failed: '失败', blocked: '受阻', needs_review: '待核验', invalidated: '需重跑', pending: '待处理', running: '处理中' })[s] || (s || '待处理');
    }
    // 标题以服务端阶段契约为准；仅在旧响应缺标题时兜底，不在客户端维护阶段顺序。
    // 排队区的“已完成/总数”必须用流程图的实际 12 类，不能用内部 18 个
    // 阶段的台账数字，否则列表会出现用户在图上看不到的“十几/18”。
    function compactProgress(stage) {
      const items = (stage && stage.items) || [];
      if (!items.length) return { done: (stage && stage.done) || 0, total: (stage && stage.total) || 0 };
      const finished = { completed: true, completed_with_findings: true, skipped: true, approved: true };
      const groups = compactFlowStages(items);
      return { done: groups.filter((group) => finished[group.status]).length, total: groups.length };
    }

    // 卡片时间：精确到「月-日 时:分」，按浏览器本地时区展示。
    function fmtMonthMinute(iso) {
      if (!iso) return '';
      const d = new Date(iso);
      if (isNaN(d.getTime())) return '';
      const p = (n) => String(n).padStart(2, '0');
      return (d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
    }
    function flowTimingText(b) {
      const finished = fmtMonthMinute(b && b.flow_finished_at);
      if (!finished) return '';
      const mins = b && b.flow_minutes;
      const minutesText = (typeof mins === 'number' && mins >= 0) ? ('流程耗时 ' + mins + ' 分钟') : '';
      if (b.flow_done) return '完成时间：' + finished + (minutesText ? ' · ' + minutesText : '');
      return '进行中 · 最近进度 ' + finished;
    }
    function shortStageName(it) {
      const title = String((it && it.title) || '').trim();
      if (title) return title;
      const id = String((it && it.id) || '');
      const MAP = {
        extract: '原卷提取', structure_gate: '结构确认', preassemble: '前置批次组装',
        answers: '答案填充', audio: '音频填充', types: '题型填充',
        explanations: '解析与大意填充', knowledge: '知识点填充', assemble: '填充汇总',
        meta: '元数据补全', underlines: '画线补全', validate: '源文与内容体检',
        review: '内容与知识审核', review_gate: '内容审核放行', format_gate: '上传格式检查',
        upload_gate: '上传人工确认', upload: '上传', publish_gate: '发布人工确认',
      };
      return MAP[id] || id;
    }

    // 卡点诊断条：默认只显示一行摘要，点开才展开原因 / 怎么做 / 修复命令。
    // 默认收起，避免一大段红字把右侧流程图挤得只剩一半。
    function diagBanner(selBatch, stage) {
      const h = require('react').createElement;
      if (!selBatch || !stage) return null;
      const items = stage.items || [];
      const blocking = items.filter((it) => it.blocking);
      const diagnostics = stage.diagnostics || null;
      const shown = blocking.length
        ? blocking
        : (diagnostics && diagnostics.blocking_count ? [] : []);
      // 没有自身卡点、也没有诊断信息时，只在存在当前未完成阶段时给一句等待提示。
      if (!shown.length) return null;
      const copy = (text) => {
        try { navigator.clipboard && navigator.clipboard.writeText(text); } catch (_) {}
      };
      const first = shown[0] || {};
      const batchLabel = String(selBatch.name || (selBatch.paper && selBatch.paper.title) || '').trim();
      return h('details', {
        className: 'wf-diag-banner',
        style: { marginTop: 10, flexShrink: 0, border: '1px solid var(--dsw-alias-state-error-primary)', borderRadius: 10, background: 'var(--dsw-alias-bg-l2, rgba(255,120,120,0.06))', padding: '8px 12px' },
      },
        h('summary', { style: { display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', listStyle: 'none', minWidth: 0 } },
          h('span', { style: { fontWeight: 600, fontSize: 12.5, color: 'var(--dsw-alias-state-error-primary)', flexShrink: 0 } }, '卡点诊断'),
          h('span', { style: { ...T.hint, fontSize: 11.5, flexShrink: 0, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: batchLabel }, '批次：' + batchLabel),
          h('span', { style: { ...T.hint, fontSize: 11.5, flexShrink: 0 } }, blocking.length + ' 处需处理'),
          h('span', { style: { flex: 1, minWidth: 0, fontSize: 12, color: 'var(--dsw-alias-label-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } },
            (shortStageName(first) || first.id || '') + (first.reason ? '：' + first.reason : '')),
          h('span', { style: { flexShrink: 0, fontSize: 11, color: 'var(--dsw-alias-label-secondary)' } }, '详情')),
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 } },
          shown.slice(0, 3).map((it, i) => h('div', { key: 'd' + i, style: { display: 'flex', flexDirection: 'column', gap: 3, paddingTop: i ? 6 : 0, borderTop: i ? '1px dashed var(--dsw-alias-border-l2)' : 'none' } },
            h('div', { style: { fontSize: 12.5, fontWeight: 600 } }, (shortStageName(it) || it.id) + '：' + (it.reason || statusText(it.status))),
            (it.detail || it.notes)
              ? h('div', { style: { ...T.hint, fontSize: 11.5, wordBreak: 'break-word' } }, '原因：' + String(it.detail || it.notes).slice(0, 220))
              : null,
            it.hint ? h('div', { style: { ...T.hint, fontSize: 11.5 } }, '怎么办：' + it.hint) : null,
            it.command
              ? h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 } },
                  h('code', { style: { flex: 1, fontSize: 11, fontFamily: 'monospace', background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 6, padding: '4px 8px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: it.command }, it.command),
                  h('button', { className: 'wf-btn-ghost', style: { ...T.miniBtn, flexShrink: 0 }, onClick: () => copy(it.command) }, '复制命令'))
              : null,
          )),
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
            formField(TEXT.sourceType, h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, 'aria-label': TEXT.sourceType, value: labels?.source_type || '', onChange: (e) => setL('source_type', e.target.value) },
              h('option', { value: '' }, '未选择'),
              labels?.source_type && !(tax.sources || []).includes(labels.source_type)
                ? h('option', { value: labels.source_type, disabled: true }, labels.source_type + '（当前值，请从目录选择）') : null,
              (tax.sources || []).map((source) => h('option', { key: source, value: source }, source)))),
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
                  h('span', { style: { fontSize: 12, fontWeight: 700, color: 'var(--wf-brand)', background: 'var(--wf-brand-soft)', borderRadius: 6, padding: '1px 7px' } }, s.number),
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
      const listScrollRef = React.useRef(null);
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
      // 后台/命令行完成审核后及时读回结果；编辑期间暂停，避免覆盖未保存内容。
      React.useEffect(() => {
        if (contentDirty || labelDlg || contentDlg || busy) return;
        const refresh = () => {
          if (typeof document === 'undefined' || document.visibilityState !== 'hidden') load();
        };
        const timer = setInterval(refresh, 8000);
        if (typeof window !== 'undefined') window.addEventListener('focus', refresh);
        return () => {
          clearInterval(timer);
          if (typeof window !== 'undefined') window.removeEventListener('focus', refresh);
        };
      }, [load, contentDirty, labelDlg, contentDlg, busy]);
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
          if (q) {
            const hay = (String(r.label || '') + String(r.number || '') + String(r.type || '') + String((r.knowledge || []).join(' '))).toLowerCase();
            if (!hay.includes(q.toLowerCase())) return false;
          }
          return true;
        });
      }, [allRows, filter, q]);

      // 切到新题时显式清掉上一题的状态：组件不再按 unitId 重挂载，
      // 否则新题会短暂显示旧题详情，或把上一题的未保存编辑/弹窗带过去。
      React.useEffect(() => {
        setDetail(null);
        setLabels(null);
        setContent(null);
        setContentDirty(false);
        setLabelDlg(false);
        setContentDlg(false);
        setBusy('');
        setErr('');
      }, [unitId]);

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
        const t = String(text == null ? '' : text).replace(/<[^>]*>/g, '').replace(/\*/g, '').replace(/[\s\u3000]+/g, '');
        return /^[A-Ha-h][.．、)）]?$/.test(t) || /^[（(][A-Ha-h][)）]$/.test(t) ? t : null;
      }
      function optionLetter(label) {
        const match = String(label || '').match(/[A-H]/i);
        return match ? match[0].toUpperCase() : '';
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
              const label = optionLabel(list[j].text);
              cells.push({ label, letter: optionLetter(label), block: list[j + 1] });
              j += 2;
            }
            if (cells.length >= 1) {
              out.push(h('div', { key: 'optrow:' + i, className: 'wf-opt-row' },
                cells.map((c, ci) => {
                  const optionIndex = Math.max(0, (c.letter || 'A').charCodeAt(0) - 65);
                  return h('div', {
                    key: c.letter || ci,
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
            out.push(h('div', { key: 'body-image:' + String(b.url || b.name || '') + ':' + i, className: 'wf-body-image-frame' },
              h(PreviewImage, { url: b.url, name: b.name })));
            continue;
          }
          if (b.type === 'table') {
            const cellImages = b.cellImages || [];
            out.push(h('div', { key: i, style: { margin: '8px 0', maxWidth: '100%', minWidth: 0 } },
              h('table', { className: 'wf-table' },
                h('tbody', null, (b.rows || []).map((row, ri) => {
                  const head = isHeaderRow(row);
                  const rowCells = cellImages[ri] || [];
                  return h('tr', { key: ri }, (row || []).map((cell, ci) => {
                    // 单元格内的图片与该单元格文字一起显示，图片在前、文字在后。
                    const imgs = (rowCells[ci] || []).filter((im) => im && im.url);
                    const children = [];
                    imgs.forEach((im, k) => children.push(
                      h('span', { key: 'img' + k, className: 'wf-table-thumb' },
                        h(PreviewImage, { url: im.url, name: im.name }))));
                    if (cell !== '' && cell != null) children.push(
                      h('span', { key: 'txt', dangerouslySetInnerHTML: { __html: markLine(cell) } }));
                    const props = { key: ci, className: imgs.length ? 'wf-table-imgcell' : undefined };
                    return head ? h('th', props, children) : h('td', props, children);
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
        const checks = (r.checks || []).filter((c) => c && c.assessment && Object.keys(c.assessment).length);
        // 二审按小题记录，不能过滤带 number 的 check，也不能把一条抽样结论当作整组结论。
        const unit = checks.find((c) => c.number == null);
        return { status: r.status, assessment: unit ? unit.assessment : null, checks };
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
        const sampleChecks = (r2 && r2.checks) || [];
        const kpRows = h('div', { style: { padding: '2px 0 8px' } },
          h('div', { style: { fontSize: 12.5, fontWeight: 600, margin: '2px 0 6px' } }, '知识点标签'),
          cmpRow('原标签', kpOrig, {}),
          cmpRow('一审标签', kp1, { pending: !r1 || !r1.assessment, mismatch: !!(kp1 && kp1.length && !kpSame(kp1, kpOrig)) }),
          sampleChecks.length ? sampleChecks.map((check) => {
            const kp = kpShort(check.assessment.knowledge);
            return h('div', { key: String(check.number ?? 'unit') },
              cmpRow('二审标签', kp, { mismatch: !!(kp.length && !kpSame(kp, kpOrig)) }),
              h('div', { style: T.hint }, check.number != null ? ('抽查第 ' + check.number + ' 题') : '本单元审核'));
          }) : cmpRow('二审标签', r2 && r2.status === '未覆盖' ? '未抽中，未做二审' : null, { pending: !r2 || r2.status !== '未覆盖' }),
        );

        // 只展示「知识点标签」三列差异；难度/来源/分类/题型等差异统一放到下方「审核说明」文字里，
        // 不再单独开一个卡片/区块（用户 2026-11 要求）。
        return [h('div', null, kpRows)];
      }

      // 生成审核说明：知识点差异在上方「知识点标签」三列展示；这里放
      // 题型/难度等基础差异 + 来源/分类/词数等说明 + 审核理由与原文核对。
      // 审核说明：越短越好，只一句话说清差异。审核范畴只有「知识点标签」，
      // 难度/来源/分类等基本标签不参与审核，也不在这里罗列。
      function auditNotes(detail) {
        const comp = detail.comparison || {};
        const orig = (comp.original && comp.original.labels) || {};
        const cur = detail.labels || {};
        const r1 = roundAssessment(comp, 'labels');
        const r2 = roundAssessment(comp, 'sample');
        const baseType = String(orig.type || cur.type || '').trim();
        const joinList = (v) => (Array.isArray(v) ? v.filter(Boolean).join('、') : (v == null ? '' : String(v)));
        const baseSub = joinList(orig.subtypes || cur.subtypes);
        const typeDiffs = [];
        const checkType = (name, a) => {
          if (!a) return;
          const t = String(a.type || '').trim();
          if (t && baseType && t !== baseType) typeDiffs.push(name + '「' + t + '」');
          const st = joinList(a.subtypes);
          if (st && baseSub && st !== baseSub) typeDiffs.push(name + '子类「' + st + '」');
        };
        checkType('一审', r1 && r1.assessment);
        for (const check of (r2 && r2.checks) || []) checkType('二审', check.assessment);
        if (typeDiffs.length) return ['题型差异：' + typeDiffs.join('、') + '（原「' + baseType + '」）'];
        // 有知识点差异时，用一句简短原因说明为什么会不一致（有就写，没有就空）。
        const kpDiff = (typeof hasKnowledgeDiff === 'function') ? hasKnowledgeDiff(detail) : false;
        if (kpDiff) {
          const reason = ((r2 && r2.checks) || []).map((c) => String(c.rationale || '').trim()).find(Boolean)
            || ((r1 && r1.assessment && r1.rationale) ? String(r1.rationale).trim() : '');
          const short = reason ? reason.slice(0, 60) : '一审/二审与原标签判断不同';
          return ['差异原因：' + short];
        }
        return [];
      }
      // 是否有一审/二审与原标签不一致的知识点（仅用于审核说明的空态文案）。
      function hasKnowledgeDiff(detail) {
        const comp = detail.comparison || {};
        const orig = (comp.original && comp.original.labels) || {};
        const cur = detail.labels || {};
        const short = (list) => (Array.isArray(list) ? list.map((x) => {
          const s = String(x == null ? '' : x).trim();
          const parts = s.split(/[\/>＞]/).map((p) => p.trim()).filter(Boolean);
          return parts.length > 1 ? parts[parts.length - 1] : s;
        }).filter(Boolean) : []);
        const same = (a, b) => JSON.stringify(short(a || [])) === JSON.stringify(short(b || []));
        const base = short(orig.knowledge || cur.knowledge);
        const r1 = roundAssessment(comp, 'labels');
        if (r1 && r1.assessment && !same(short(r1.assessment.knowledge), base)) return true;
        const r2 = roundAssessment(comp, 'sample');
        for (const check of (r2 && r2.checks) || []) {
          if (!same(short(check.assessment.knowledge), base)) return true;
        }
        return false;
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
        const rounds = (comparison && comparison.rounds) || [];
        if (!rounds.length) return h('div', { style: T.hint }, '无审核轮次记录');
        const states = rounds.map((r) => ({ r, st: nodeState(r) }));
        const allOk = states.every((x) => x.st.kind === 'ok');
        const box = { background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 8, padding: '7px 10px', marginBottom: 10, fontSize: 12 };
        if (allOk) {
          return h('div', { style: box },
            h('span', { style: { color: 'var(--dsw-alias-label-primary)', fontWeight: 600 } }, '✓ 一审 / 二审 全通过'));
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
          return h('div', { style: { ...T.hint, marginTop: 4, color: 'var(--dsw-alias-label-primary)' } }, '✓ 一审 / 二审 与该问题一致');
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
            return h('div', {
              key: uid, className: 'wf-paper' + (active ? ' wf-active' : ''),
              'data-unit': uid,
              style: T.paperRow(active),
              onClick: () => onSelectUnit(uid),
            },
              h('span', { style: { width: 44, flexShrink: 0, fontWeight: 600, fontSize: 12 } }, String(r.label || r.number)),
              h('span', { style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 11.5, opacity: .85 } }, String(r.type || '')),
              r.excluded
                ? h('span', { style: { ...T.badge(''), flexShrink: 0, fontSize: 10.5 } }, '听力跳过')
                : (r.needs_human
                  ? h('span', { style: { ...T.badge('warn'), flexShrink: 0 } }, String(r.open_count || ''))
                  : h('span', { style: { width: 7, height: 7, borderRadius: '50%', flexShrink: 0, background: r.state === 'blocked' ? 'var(--dsw-alias-state-error-primary)' : (r.state === 'unverified' ? 'var(--dsw-alias-border-l2)' : 'var(--dsw-alias-label-secondary)') } })),
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
            detail.row && detail.row.excluded
              ? h('span', { style: T.badge('') }, '听力跳过')
              : (detail.needs_human ? h('span', { style: T.badge('warn') }, '待审标签') : h('span', { style: T.badge('') }, '无待审')),
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
            // 右：标签审核（对比）+ 审核说明（差异说明 + 修改/通过/驳回按钮）
            h('div', { className: 'wf-review-notes' },
              h('div', { style: T.card },
                h('div', { style: { ...T.cardTitle, flexWrap: 'wrap' } }, '标签审核', h('span', { style: { flex: 1 } }), detail.comparison?.stale ? h('span', { style: { ...T.hint, fontWeight: 400, color: 'var(--dsw-alias-state-warn-primary)' } }, '上一版审核快照，需重新审核') : null, h('span', { style: { ...T.hint, fontWeight: 400 } }, '原标签 / 一审 / 二审')),
                ...labelAuditPanels(detail),
              ),
              // 审核说明：只描述一审/二审与原标签（及各轮之间）的差异；按钮放在说明下面
              (() => {
                const lines = auditNotes(detail);
                const kpDiff = hasKnowledgeDiff(detail);
                const decideAll = async (status) => {
                  setBusy(status);
                  try {
                    const ids = [];
                    (groupedFindings || []).forEach((g) => { (g.ids && g.ids.length ? g.ids : [g.finding_id]).forEach((id) => { if (id) ids.push(id); }); });
                    (fmtFindings || []).forEach((f) => { if (f.finding_id) ids.push(f.finding_id); });
                    for (const id of ids) await jpost('/batches/' + encodeURIComponent(batchName) + '/decisions', { item: { finding_id: id, status, note: '' } });
                    setBusy(''); load();
                  } catch (e) { setErr(String(e.message || e)); setBusy(''); }
                };
                return h('div', { style: T.card },
                  h('div', { style: { ...T.cardTitle } }, '审核说明'),
                  lines.length
                    ? lines.map((t, i) => h('div', { key: i, style: { fontSize: 12, color: 'var(--dsw-alias-label-primary)', padding: '3px 0', lineHeight: 1.6, borderTop: i ? '1px dashed var(--dsw-alias-border-l1)' : 'none' } }, '· ' + t))
                    : h('div', { style: T.hint }, kpDiff ? '差异已在上方「知识点标签」中标出。' : '各轮与原标签一致，无差异。'),
                  h('div', { style: { marginTop: 12, display: 'flex', gap: 8, justifyContent: 'stretch', borderTop: '1px solid var(--dsw-alias-border-l1)', paddingTop: 10 } },
                    h('button', { className: 'wf-btn', style: { ...T.btn, flex: 1, textAlign: 'center' }, onClick: () => setLabelDlg(true) }, '修改'),
                    h('button', { className: 'wf-btn', style: { ...T.btn, flex: 1, textAlign: 'center' }, disabled: busy === 'accepted', onClick: () => decideAll('accepted') }, busy === 'accepted' ? '处理中…' : '通过'),
                    h('button', { className: 'wf-btn-danger', style: { ...T.btnDanger, flex: 1, textAlign: 'center' }, disabled: busy === 'rejected', onClick: () => decideAll('rejected') }, busy === 'rejected' ? '处理中…' : '驳回'),
                  ),
                );
              })(),
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
          const st = await jget('/batches/' + encodeURIComponent(name) + '/upload-status');
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
            setActive((current) => {
              if (!current) return current;
              const latest = (d.batches || []).find((b) => b.name === current.name);
              if (!latest) return current;
              // overview includes row status, findings and stale-stage state; revision clocks
              // can miss candidate edits, so always replace it while preserving selection.
              return { ...current, overview: latest };
            });
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
      React.useEffect(() => {
        loadBatches();
        const timer = setInterval(() => {
          if (typeof document === 'undefined' || document.visibilityState !== 'hidden') loadBatches();
        }, 8000);
        return () => { clearInterval(timer); loadSeq.current += 1; };
      }, [loadBatches]);
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

      // Reconnect and page re-entry always consult durable status before enabling upload.
      React.useEffect(() => {
        if (!active) return;
        const name = active.name;
        if (!uploadStatesRef.current[name]) setUploadState(name, { state: 'checking', message: '正在查询上传状态…' });
        checkUpload(name);
        const timer = setInterval(() => checkUpload(name), 3000);
        return () => { clearInterval(timer); uploadGeneration.current[name] = (uploadGeneration.current[name] || 0) + 1; };
      }, [active && active.name]);

      function openBatch(name) {
        const prior = uploadStatesRef.current[name];
        setUploadState(name, { state: 'checking', attempted: !!(prior && prior.attempted), message: '正在查询上传状态…' });
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
        try {
          await jpost('/batches/' + encodeURIComponent(name) + '/rerun', { mode });
          // 后端返回 started=true 后仍在后台执行，持续跟踪，避免立即显示旧状态/解锁重复操作。
          const deadline = Date.now() + 30 * 60 * 1000;
          let last;
          while (Date.now() < deadline) {
            await new Promise((r) => setTimeout(r, 2000));
            last = await jget('/batches/' + encodeURIComponent(name) + '/pipeline');
            if (!last.running) break;
          }
          if (last && last.running) setErr('重新执行仍在后台运行，请稍后刷新查看；当前页面已停止等待。');
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
          await jpost('/batches/' + encodeURIComponent(del.name) + '/purge', { confirm_name: del.name });
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
        const rows = (active.overview && active.overview.rows) || [];
        const upload = uploadStates[active.name] || { state: 'checking', message: '正在查询上传状态…' };
        return h(React.Fragment, null, h('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } },
          h('div', { style: T.toolbar },
            h('button', { className: 'wf-btn', style: T.btn, onClick: () => setActive(null) }, '← ' + TEXT.back),
            h('span', { style: T.toolbarTitle }, (active.overview?.paper?.title || active.name)),
            h('span', { style: { ...T.hint, marginLeft: 4 } }, '↑/↓ 或 k/j 切换题组'),
            h('span', { style: { flex: 1 } }),
            upload.state === 'idle' && typeof upload.requiresUnreviewedConfirmation !== 'boolean' ? h('span', { role: 'status', style: T.hint }, '无法确认审核状态，请更新后端并重新进入批次。') : null,
            upload.message && uploadNotice !== active.name && (upload.attempted || upload.state === 'failed' || upload.state === 'idle') ? h('button', { className: 'wf-btn', style: T.btn, onClick: () => setUploadNotice(active.name) }, '查看上传通知') : null,
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: () => doUpload(active.name), disabled: !['idle', 'failed'].includes(upload.state) || typeof upload.requiresUnreviewedConfirmation !== 'boolean' || advBusy === active.name }, upload.state === 'running' ? '上传中…' : '上传批次'),
            h('button', { className: 'wf-btn-danger', style: T.btnDanger, onClick: () => openDelete(active.name) }, TEXT.archive),
          ),
          uploadNotice === active.name && upload.message ? h('section', {
            role: 'dialog', 'aria-label': '上传通知',
            style: { position: 'fixed', right: 20, bottom: 20, zIndex: 2500, width: 'min(420px, calc(100vw - 40px))', boxSizing: 'border-box', padding: 16, borderRadius: 10, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 8px 24px rgba(0,0,0,.18)' },
          },
            h('div', { style: { display: 'flex', alignItems: 'center', gap: 12 } },
              h('strong', { style: { flex: 1 } }, '上传通知'),
              h('button', { className: 'wf-btn', style: T.miniBtn, onClick: () => setUploadNotice(''), 'aria-label': '关闭上传通知' }, '关闭')),
            h('div', { role: upload.state === 'failed' || upload.state === 'unknown' ? 'alert' : 'status', 'aria-live': upload.state === 'failed' || upload.state === 'unknown' ? 'assertive' : 'polite', style: { marginTop: 8, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' } }, upload.message),
          ) : null,
          h(ReviewView, {
            // 切题不重建组件：key 只随批次变化。若按 unitId 作 key，每次 ↑/↓
            // 都会重挂载，把题组列表与预览区的滚动条全部重置到顶端。
            key: 'review:' + active.name,
            batchName: active.name,
            rows,
            unitId: active.unitId,
            onSelectUnit: (uid) => setActive({ ...active, unitId: uid }),
          }),
        ), deleteDialog);
      }

      // 只保留右侧「试卷审核」卡片列表这一份，去掉左栏「需要审核的试卷」列表，
      // 避免同一批试卷在左右两列重复展示。
      // 已上传到云端（overview.uploaded）的批次视为完成，不再出现在审核列表。
      // 内容审核与上传格式检查结束（flow_done）的试卷排在最前并标「待人工确认」，与上传页的
      // 「已完成 N 份 → 前往人工确认」入口对应：处理中仍在队列，结束后流转到这里。
      const list = (batches || []).filter((b) => !b.uploaded)
        .sort((a, b) => (Number(!!b.flow_done) - Number(!!a.flow_done)));
      return h(React.Fragment, null, h('div', { style: { flex: 1, display: 'flex', minHeight: 0 } },
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
            listState === 'loading' && list.length === 0
              ? h(EmptyState, { title: '正在加载试卷列表…（首次进入需等待后端启动，请稍候）' })
              : listState === 'error' && list.length === 0
                ? h(EmptyState, { tone: 'error', title: '暂时连不上后端，未能取到试卷列表。点上方「重试」再试一次。' })
                : list.length === 0
                  ? h(EmptyState, { title: TEXT.noPaper })
                  : list.map((b) => h('div', { key: b.name, className: 'wf-card-hover', style: { ...T.cardClickable, width: '94%', marginLeft: 'auto', marginRight: 'auto' }, onClick: () => openBatch(b.name) },
              // 第一行：标题 + 细字体统计（无底色，仅“待审”带底色）
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 10 } },
                h('div', { style: { ...T.paperCardTitle, flex: 1, minWidth: 0 } }, b.paper?.title || b.name),
                b.flow_done
                  ? h('span', { style: { flexShrink: 0, fontSize: 11, fontWeight: 600, color: 'var(--dsw-alias-label-primary)', background: 'var(--wf-sel-bg)', border: '1px solid var(--wf-sel-border)', borderRadius: 999, padding: '2px 9px', whiteSpace: 'nowrap' } }, '待人工确认')
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
            )),
          ),
        ),
      ), deleteDialog);
    }

    // ======================================================================
    // 模型与设置
    // ======================================================================
    // 题库连接配置独立于模型路由；密钥仅提交给宿主，不保存在浏览器存储中。
    // 初始化引导：把「要装什么、要下什么、要配什么」收成一页，新用户照此走一遍即可。
    // 插件本体很小，听力运行时与模型按需下载，所以下载/配置都收成一个按钮。
    function OnboardingSection({ onNavigate }) {
      const React = require('react');
      const h = React.createElement;
      const [loading, setLoading] = React.useState(true);
      const [items, setItems] = React.useState([]);
      const [busy, setBusy] = React.useState('');
      const [msg, setMsg] = React.useState(null);
      const get = async (url) => {
        try {
          const r = await window.fetch(url, { headers: { accept: 'application/json' } });
          return await r.json();
        } catch (e) { return { error: String((e && e.message) || e) }; }
      };
      async function load() {
        setLoading(true);
        const [health, audio, ocr, cfg, bank] = await Promise.all([
          get('/wf-plugin/health'),
          get('/wf-plugin/api/audio/config'),
          get('/wf-plugin/ocr'),
          get('/wf-plugin/config'),
          get('/wf-plugin/question-bank'),
        ]);
        const ocrCfg = (ocr && ocr.config) || {};
        const router = (cfg && cfg.router) || {};
        const bankCfg = (bank && bank.config) || {};
        // OCR/题库的 endpoint/base_url 都有默认值，不能用它判断“配好了”；
        // 宿主只回 has_token / has_api_key（不回密钥本身），按这两个字段判定。
        const hasOcr = ocrCfg.has_token === true;
        const effectiveModel = (router.default && router.default.model) ? router.default : (router.harnessDefault || {});
        const hasModel = !!effectiveModel.model;
        const hasBank = bankCfg.has_api_key === true;
        setItems([
          { id: 'python', title: 'Python 运行时', required: true, ready: !!(health && health.python),
            detail: (health && health.python) ? health.python : '未找到可用 Python。插件通常自带便携版，缺失时请重新安装插件。',
            action: null },
          { id: 'ffmpeg', title: 'ffmpeg / ffprobe', required: true,
            ready: !!(audio && audio.ffmpeg && audio.ffprobe),
            detail: (audio && audio.ffmpeg) ? ('ffmpeg: ' + audio.ffmpeg) : '未检测到 ffmpeg。听力切段需要它（插件 vendor 目录或系统 PATH）。',
            action: null },
          { id: 'audio', title: '听力模型（WhisperX + 识别模型）', required: false,
            ready: !!(audio && audio.ready),
            detail: (audio && audio.ready) ? '已安装，可以直接切段。' : '未安装。插件本体不含模型；点下方按钮按需下载运行时与模型，首次约需数分钟。',
            action: 'install-audio' },
          { id: 'ocr', title: 'OCR 识别', required: false, ready: hasOcr,
            detail: hasOcr ? ('已配置 OCR 服务（' + (ocrCfg.endpoint || '') + '）。') : '未填 OCR Token。扫描件/图片型试卷需要 OCR；纯 Word/PDF 文字卷可跳过。',
            action: 'goto-ocr' },
          { id: 'model', title: '大模型', required: true, ready: hasModel,
            detail: hasModel ? ((router.default && router.default.model ? '默认模型：' : '跟随 Harness 当前模型：') + effectiveModel.model + (router.default && router.default.model ? '' : '（无需填写 API Key，凭据由 Harness 提供）')) : '未检测到可用模型。请在「模型与设置 → 大模型配置」选择本机 Harness 已配置的模型；插件不需要额外填写 API Key。',
            action: 'goto-models' },
          { id: 'bank', title: '题库连接', required: false, ready: hasBank,
            detail: hasBank ? ('题库：' + bankCfg.base_url) : '未填题库 API Key。上传到题库前需要填服务地址、账号与密钥。',
            action: 'goto-api' },
        ]);
        setLoading(false);
      }
      React.useEffect(() => { load(); }, []);
      async function installAudio(force) {
        setBusy('audio');
        setMsg(force ? '正在更新听力运行时与模型（强制重新下载）…' : '正在安装听力运行时与模型；首次下载较大，请保持窗口打开…');
        try {
          const r = await window.fetch('/wf-plugin/api/audio/bootstrap', {
            method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json' },
            body: JSON.stringify({ force: !!force }),
          });
          const data = await r.json();
          if (!r.ok || data.error) throw new Error(data.error || '安装任务启动失败');
          setMsg('安装任务已启动，可在「听力切分」页查看进度。');
          await load();
        } catch (e) { setMsg('安装失败：' + String((e && e.message) || e)); }
        finally { setBusy(''); }
      }
      function act(item) {
        if (!item.action) return;
        // 已就绪时按钮是“更新模型”，用 force 真正重新下载，而不是被 READY 标记跳过。
        if (item.action === 'install-audio') return installAudio(item.ready);
        if (item.action === 'goto-ocr') return onNavigate('ocr');
        if (item.action === 'goto-models') return onNavigate('models');
        if (item.action === 'goto-api') return onNavigate('api');
      }
      const readyCount = items.filter((i) => i.ready).length;
      const requiredMissing = items.filter((i) => i.required && !i.ready);
      const badge = (ready) => h('span', { style: T.pill },
        h('span', { style: T.pillNum }, ready ? '\u2713' : '!'), ready ? '就绪' : '待完成');
      return h('section', { 'aria-labelledby': 'wf-onboarding-heading', style: { minWidth: 0 } },
        h('h2', { id: 'wf-onboarding-heading', style: T.cardTitle }, '初始化引导'),
        h('p', { className: 'wf-settings-description' },
          '插件本体很小，听力运行时与模型按需下载。新装插件后照着下面走一遍即可：标「必须」的项完成后才能跑完整流程，其余按需。'),
        loading ? h('div', { style: T.hint }, '正在检查环境…') : null,
        !loading ? h('div', { style: { ...T.card, boxShadow: 'none', marginBottom: 12 } },
          h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' } },
            h('strong', null, '完成进度'),
            h('span', { style: T.pill }, h('span', { style: T.pillNum }, readyCount + '/' + items.length), '项就绪'),
            requiredMissing.length
              ? h('span', { style: { ...T.hint, marginLeft: 'auto' } }, '还需完成：' + requiredMissing.map((i) => i.title).join('、'))
              : h('span', { style: { ...T.statusOk, marginLeft: 'auto' } }, '\u2713 必需项已就绪，可以开始使用')),
          h('div', { style: { marginTop: 10 } },
            h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: load }, '重新检查')),
        ) : null,
        msg ? h('div', { role: 'status', style: { ...(String(msg).startsWith('安装失败') ? T.statusErr : T.statusOk), marginBottom: 12 } }, msg) : null,
        h('div', { style: { display: 'grid', gap: 10 } },
          items.map((item) => h('div', { key: item.id, style: { ...T.card, boxShadow: 'none', display: 'grid', gap: 8 } },
            h('div', { style: { display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' } },
              h('strong', { style: { fontSize: 13.5 } }, item.title),
              h('span', { style: { ...T.pill, fontSize: 10.5 } }, item.required ? '必须' : '按需'),
              h('span', { style: { marginLeft: 'auto' } }, badge(item.ready))),
            h('div', { style: T.hint }, item.detail),
            item.action ? h('div', null,
              h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => act(item) },
                item.action === 'install-audio' ? (item.ready ? '重新安装 / 更新模型' : '安装运行时与模型') : '去配置')) : null,
          ))),
      );
    }

    function QuestionBankSettings() {
      const React = require('react');
      const h = React.createElement;
      const [config, setConfig] = React.useState(null);
      const [apiKey, setApiKey] = React.useState('');
      const [loginPassword, setLoginPassword] = React.useState('');
      const [busy, setBusy] = React.useState('');
      const [dirty, setDirty] = React.useState(false);
      const [sshDirty, setSshDirty] = React.useState(false);
      const [notice, setNotice] = React.useState(null);
      React.useEffect(() => {
        let active = true;
        requestSettings('GET').then((data) => { if (active) setConfig(data.config); })
          .catch((error) => { if (active) setNotice({ error: true, text: error.message }); });
        return () => { active = false; };
      }, []);
      async function requestSettings(method, payload, test) {
        const response = await window.fetch('/wf-plugin/question-bank' + (test ? '/test' : ''), {
          method, headers: { accept: 'application/json', ...(method === 'POST' ? { 'content-type': 'application/json' } : {}) },
          ...(method === 'POST' ? { body: JSON.stringify(payload) } : {}),
        });
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.error || data.message || '题库连接操作失败，请检查设置后重试。');
        return data;
      }
      function change(name, value) {
        setConfig((current) => ({ ...current, [name]: value })); if (name.startsWith('ssh_')) setSshDirty(true); setDirty(true); setNotice(null);
      }
      async function perform(action) {
        if (!config || busy) return;
        setBusy(action); setNotice(null);
        const payload = { base_url: config.base_url, subject_id: config.subject_id, timeout_seconds: Number(config.timeout_seconds), knowledge_version_id: config.knowledge_version_id || '', account: config.account || '' };
        if (sshDirty) Object.assign(payload, { ssh_host: config.ssh_host || '', ssh_port: Number(config.ssh_port), ssh_identity: config.ssh_identity || '', ssh_remote_port: Number(config.ssh_remote_port) });
        if (apiKey) payload.api_key = apiKey;
        if (loginPassword) payload.password = loginPassword;
        if (action === 'clear') { delete payload.api_key; payload.clear_api_key = true; }
        if (action === 'clear-password') { delete payload.password; payload.clear_password = true; }
        try {
          const data = await requestSettings('POST', payload, action === 'test');
          if (action === 'test') {
            setNotice({ error: false, text: data.message || '目录读取成功。上传仍需 draft 权限，此测试不会提交题目。' });
          } else {
            setConfig(data.config); setApiKey(''); setLoginPassword(''); setDirty(false); setSshDirty(false);
            setNotice({ error: false, text: action === 'clear' ? '已清除保存的 API Key。' : action === 'clear-password' ? '已清除保存的登录密码。' : '题库连接设置已保存。' });
          }
        } catch (error) { setNotice({ error: true, text: error.message }); }
        finally { setBusy(''); }
      }
      const field = (name, label, options, help) => h('label', { style: { display: 'grid', gap: 6, marginBottom: 16 } },
        h('span', { style: T.fieldLabel }, label),
        h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, value: config?.[name] ?? '', disabled: !!busy, onChange: (event) => change(name, event.target.value), ...options }),
        help ? h('span', { style: T.hint }, help) : null);
      return h('section', { 'aria-labelledby': 'wf-question-bank-heading', style: { minWidth: 0 } },
        h('h2', { id: 'wf-question-bank-heading', style: T.cardTitle }, '上传设置'),
        h('p', { className: 'wf-settings-description' }, '只需 API Key 和上传账户；SSH 隧道已预填本机服务器与密钥，通常无需修改。'),
        !config ? h('div', { style: { ...T.card, boxShadow: 'none' } },
          h('div', { style: T.hint }, notice?.error ? '配置加载失败，重新进入此页可重试。' : '正在读取上传设置…')) :
        h('div', { style: { display: 'grid', gap: 16 } },
          h('div', { style: T.card },
            h('h3', { style: { ...T.cardTitle, fontSize: 16, marginBottom: 12 } }, 'API'),
            field('base_url', 'API Base 地址', { type: 'url', placeholder: 'http://127.0.0.1:8089/api', autoComplete: 'off', spellCheck: false }, '填写以 /api 结尾的接口地址。默认走本机 SSH 隧道。'),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 16 } },
              h('span', { style: T.fieldLabel }, 'API Key'),
              h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, type: 'password', value: apiKey, autoComplete: 'new-password', spellCheck: false, disabled: !!busy, placeholder: config.has_api_key ? '已保存密钥；留空保持不变' : '填写题库账号的 API Key', onChange: (event) => { setApiKey(event.target.value); setDirty(true); setNotice(null); } }),
              h('span', { style: T.hint }, config.has_api_key ? '密钥已保存，不回显。填入新值后保存即可替换。' : '使用 Bearer Token 认证。')),
            field('subject_id', '科目 ID', { type: 'text', placeholder: 'english', autoComplete: 'off' }, '应与 API Key 绑定账号的科目一致。英语默认 english。'),
            h('details', { style: { margin: '4px 0 0' } },
              h('summary', { style: { cursor: 'pointer', ...T.hint, padding: '4px 0', marginBottom: 12 } }, '高级参数'),
              field('timeout_seconds', '请求超时（秒）', { type: 'number', min: 1, max: 120, step: 1 }, '默认 30 秒。'),
              field('knowledge_version_id', '知识点版本 ID（可选）', { type: 'text', placeholder: '留空自动选择启用版本', autoComplete: 'off' }, '仅在有多个启用通用版本时填写。'))),
          h('div', { style: T.card },
            h('h3', { style: { ...T.cardTitle, fontSize: 16, marginBottom: 12 } }, '上传账户'),
            field('account', '上传账号（可选）', { type: 'text', placeholder: '例如 001 或主管理员账号', autoComplete: 'off', spellCheck: false }, 'API Key 不能创建上传暂存；填写人工账号和密码后，保存暂存会用人工会话提交。'),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 0 } },
              h('span', { style: T.fieldLabel }, '登录密码（可选）'),
              h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, type: 'password', value: loginPassword, autoComplete: 'new-password', spellCheck: false, disabled: !!busy, placeholder: config.has_password ? '已保存登录密码；留空保持不变' : '填写该账号的后台登录密码', onChange: (event) => { setLoginPassword(event.target.value); setDirty(true); setNotice(null); } }),
              h('span', { style: T.hint }, config.has_password ? '密码已保存，不回显；仅保存在本机 0600 私有文件。' : '仅用于人工会话提交上传暂存；不填则只能使用 API Key。'))),
          h('div', { style: T.card },
            h('h3', { style: { ...T.cardTitle, fontSize: 16, marginBottom: 12 } }, 'SSH 隧道'),
            h('div', { style: { ...T.hint, marginBottom: 14 } }, '已预填本机服务器与密钥路径；上传时自动建立隧道，不需要手工执行 ssh。'),
            field('ssh_host', 'SSH 登录地址', { type: 'text', placeholder: 'user@host', autoComplete: 'off', spellCheck: false }, '格式 user@host；仅更换服务器时修改。'),
            field('ssh_port', 'SSH 端口', { type: 'number', min: 1, max: 65535, step: 1 }, '默认 22。'),
            field('ssh_identity', '密钥文件路径', { type: 'text', placeholder: '本机私钥路径', autoComplete: 'off', spellCheck: false }, '填写本机私钥文件路径，不要粘贴私钥内容。'),
            field('ssh_remote_port', '远端端口', { type: 'number', min: 1, max: 65535, step: 1 }, '默认 89。保存后上传时自动建立。'))),
        h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 16 } },
          h('button', { type: 'button', className: 'wf-btn-primary', style: T.btnPrimary, disabled: !!busy, onClick: () => perform('save') }, busy === 'save' ? '正在保存…' : '保存设置'),
          h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => perform('test') }, busy === 'test' ? '正在测试…' : '测试连接'),
          config?.has_api_key ? h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => perform('clear') }, busy === 'clear' ? '正在清除…' : '清除密钥') : null,
          config?.has_password ? h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => perform('clear-password') }, busy === 'clear-password' ? '正在清除…' : '清除登录密码') : null),
        dirty && !busy ? h('div', { style: { ...T.hint, marginTop: 10 } }, '有未保存的改动；连接测试使用当前填写值。') : null,
        notice ? h('div', { role: notice.error ? 'alert' : 'status', 'aria-live': 'polite', style: { ...(notice.error ? T.statusErr : T.statusOk), marginTop: 12, overflowWrap: 'anywhere' } }, notice.text) : null,
        h('div', { style: { borderTop: '1px solid var(--dsw-alias-border-l1)', marginTop: 20, paddingTop: 16, display: 'grid', gap: 8 } },
          h('div', { style: T.hint }, 'API Key 用于目录读取与媒体核验；创建上传暂存需要上传账户会话。'),
          h('div', { style: T.hint }, 'SSH 隧道使用本机 ssh 与密钥，只连本机转发端口；密钥路径保存在本机私有配置中。'),
          h('div', { style: T.hint }, '公网 HTTP 文档地址仅供阅读，不能用于发送密钥。云端提交只写入上传暂存；人工确认入库和发布仍在题库后台完成。')));
    }

    function OCRSettings() {
      const React = require('react');
      const h = React.createElement;
      const [config, setConfig] = React.useState(null);
      const [token, setToken] = React.useState('');
      const [source, setSource] = React.useState('');
      const [sourceType, setSourceType] = React.useState('url');
      const [busy, setBusy] = React.useState('');
      const [switching, setSwitching] = React.useState(false);
      const [, setDirty] = React.useState(false);
      const [notice, setNotice] = React.useState(null);
      const [result, setResult] = React.useState(null);
      const [loading, setLoading] = React.useState(true);
      async function request(method, payload, test) {
        const response = await window.fetch('/wf-plugin/ocr' + (test ? '/test' : ''), {
          method, headers: { accept: 'application/json', ...(method === 'POST' ? { 'content-type': 'application/json' } : {}) },
          ...(method === 'POST' ? { body: JSON.stringify(payload) } : {}),
        });
        const data = await response.json();
        if (!response.ok || data.ok === false || data.error) throw new Error(data.error || data.message || 'OCR 操作失败，请检查配置后重试。');
        return data;
      }
      async function load() {
        setLoading(true); setNotice(null);
        try { const data = await request('GET'); setConfig(data.config); }
        catch (error) { setNotice({ error: true, text: error.message }); }
        finally { setLoading(false); }
      }
      React.useEffect(() => { load(); }, []);
      function change(name, value) { setConfig((current) => ({ ...current, [name]: value })); setDirty(true); setNotice(null); setResult(null); }
      async function setMode(next) {
        if (!config || busy || switching || config.mode === next) return;
        const previous = config.mode;
        setConfig({ ...config, mode: next }); setSwitching(true); setNotice(null);
        try {
          const data = await request('POST', { mode: next });
          setConfig(data.config);
          setNotice({ error: false, text: 'OCR 模式已更新。' });
        } catch (error) {
          setConfig({ ...config, mode: previous });
          setNotice({ error: true, text: error.message });
        } finally { setSwitching(false); }
      }
      async function perform(action) {
        if (!config || busy) return;
        if (action === 'test' && !source.trim()) { setNotice({ error: true, text: '请先填写用于测试的文件地址。' }); return; }
        setBusy(action); setNotice(null); setResult(null);
        const payload = { mode: config.mode || 'auto', enabled: (config.mode || 'auto') !== 'off', endpoint: config.endpoint, model: config.model, timeout_seconds: Number(config.timeout_seconds), poll_interval_seconds: Number(config.poll_interval_seconds),
          useDocOrientationClassify: !!config.useDocOrientationClassify, useDocUnwarping: !!config.useDocUnwarping, useChartRecognition: !!config.useChartRecognition };
        if (token) payload.token = token;
        if (action === 'clear') { delete payload.token; payload.clear_token = true; }
        if (action === 'test') payload[sourceType === 'url' ? 'file_url' : 'file_path'] = source.trim();
        try {
          const data = await request('POST', payload, action === 'test');
          if (action === 'test') { setResult(data); setNotice({ error: false, text: data.message || '识别完成，飞桨 OCR 接口可用。' }); }
          else { setConfig(data.config); setToken(''); setDirty(false); setNotice({ error: false, text: action === 'clear' ? '已清除 OCR 密钥。' : 'OCR 设置已保存。' }); }
        } catch (error) { setNotice({ error: true, text: error.message }); }
        finally { setBusy(''); }
      }
      const field = (name, label, options) => h('label', { style: { display: 'grid', gap: 6, marginBottom: 16 } },
        h('span', { style: T.fieldLabel }, label),
        h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, value: config?.[name] ?? '', disabled: !!busy, onChange: (event) => change(name, event.target.value), ...options }));
      return h('section', { 'aria-labelledby': 'wf-ocr-heading', style: { minWidth: 0 } },
        h('h2', { id: 'wf-ocr-heading', style: T.cardTitle }, 'OCR 识别'),
        !config ? h('div', { role: 'status', style: T.hint }, loading ? '正在读取 OCR 设置…' : h('button', { type: 'button', className: 'wf-btn', style: T.btn, onClick: load }, '重新加载设置')) : h('div', null,
        h('div', { style: { ...T.card, boxShadow: 'none' } },
          h('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' } },
            h('h3', { style: { ...T.cardTitle, margin: 0 } }, 'OCR 复核策略'),
            h('div', { role: 'radiogroup', 'aria-label': 'OCR 复核策略', style: { display: 'flex', gap: 6, flexWrap: 'wrap' } },
              [['auto', '自动（AI 判断）'], ['on', '始终开启'], ['off', '关闭']].map(([value, label]) =>
                h('button', { key: value, type: 'button', role: 'radio', 'aria-checked': (config.mode || 'auto') === value,
                  className: 'wf-btn', disabled: !!busy || switching, onClick: () => setMode(value),
                  style: { ...T.btn, ...(((config.mode || 'auto') === value) ? { outline: '2px solid var(--dsw-alias-brand-primary)', fontWeight: 600 } : {}) } }, label)))),
          (config.mode || 'auto') !== 'off' && !config.has_token ? h('div', { style: { ...T.statusErr, marginTop: 10 } }, '尚未保存 Token；需要 OCR 复核时会因缺少凭据失败。') : null),
        h('div', { className: 'wf-settings-card', style: { ...T.card, boxShadow: 'none' } },
          h('h3', null, '接口配置'),
          field('model', '识别模型', { type: 'text', placeholder: 'PaddleOCR-VL-1.6', spellCheck: false }),
          h('label', { style: { display: 'grid', gap: 6, marginBottom: 16 } },
            h('span', { style: T.fieldLabel }, '访问令牌 Token'),
            h('input', { className: 'wf-input', style: { ...T.input, width: '100%' }, type: 'password', autoComplete: 'new-password', spellCheck: false, value: token, disabled: !!busy, placeholder: config.has_token ? '已保存；留空保持不变' : '填写飞桨 API Token', onChange: (event) => { setToken(event.target.value); setDirty(true); setNotice(null); } })),
          h('div', { style: { display: 'grid', gap: 12, marginBottom: 18 } },
            [['useDocOrientationClassify', '自动判断文档方向'], ['useDocUnwarping', '文档去扭曲'], ['useChartRecognition', '图表识别']].map(([name, label]) =>
              h('label', { key: name, style: { display: 'flex', gap: 8, alignItems: 'center' } }, h('input', { type: 'checkbox', checked: !!config[name], disabled: !!busy, onChange: (event) => change(name, event.target.checked) }), h('span', { style: T.fieldLabel }, label)))),
          h('details', { style: { marginBottom: 18 } },
            h('summary', { style: { ...T.hint, cursor: 'pointer', padding: '4px 0', marginBottom: 12 } }, '高级接口参数'),
            field('endpoint', '任务接口地址', { type: 'url', placeholder: 'https://paddleocr.aistudio-app.com/api/v2/ocr/jobs', spellCheck: false, autoComplete: 'off' }),
            field('timeout_seconds', '识别任务超时（秒）', { type: 'number', min: 1, max: 1800, step: 1 }),
            field('poll_interval_seconds', '状态查询间隔（秒）', { type: 'number', min: 0.01, max: 60, step: 0.01 })),
          h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' } },
            h('button', { type: 'button', className: 'wf-btn-primary', style: T.btnPrimary, disabled: !!busy, onClick: () => perform('save') }, busy === 'save' ? '正在保存…' : '保存 OCR 设置'),
            config.has_token ? h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => perform('clear') }, busy === 'clear' ? '正在清除…' : '清除密钥') : null),
          h('div', { style: { borderTop: '1px solid var(--dsw-alias-border-l1)', marginTop: 24, paddingTop: 20 } },
            h('h3', null, '识别测试'),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 12 } },
              h('span', { style: T.fieldLabel }, '文件来源'),
              h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: sourceType, disabled: !!busy, onChange: (event) => { setSourceType(event.target.value); setSource(''); setResult(null); } },
                h('option', { value: 'url' }, '文件 URL'), h('option', { value: 'local' }, '本机文件路径'))),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 14 } },
              h('span', { style: T.fieldLabel }, sourceType === 'url' ? '公开可访问的文件 URL' : '插件运行电脑上的文件路径'),
              h('input', { className: 'wf-input', style: { ...T.input, width: '100%' }, type: sourceType === 'url' ? 'url' : 'text', value: source, disabled: !!busy, spellCheck: false, autoComplete: 'off', placeholder: sourceType === 'url' ? 'https://example.com/document.pdf' : '/path/to/document.pdf', onChange: (event) => setSource(event.target.value) })),
            h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy || !source.trim() || !(token || config.has_token), onClick: () => perform('test') }, busy === 'test' ? '识别中，正在等待飞桨返回…' : '测试 OCR 识别'),
            busy === 'test' ? h('div', { role: 'status', style: { ...T.hint, marginTop: 10 } }, '任务已发起；排队与识别可能需要一些时间，切换设置分类后仍会保留结果。') : null))),
        notice ? h('div', { role: notice.error ? 'alert' : 'status', 'aria-live': 'polite', style: { ...(notice.error ? T.statusErr : T.statusOk), marginTop: 12, overflowWrap: 'anywhere' } }, notice.text) : null,
        result ? h('div', { style: { marginTop: 16 } },
          h('h3', null, '识别结果'),
          h('div', { style: { ...T.hint, margin: '0 0 8px' } }, ['识别完成', result.pages ? result.pages + ' 页' : '', result.seconds ? result.seconds + ' 秒' : ''].filter(Boolean).join(' · ')),
          h('pre', { className: 'wf-scroll', style: { ...T.input, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 360, overflow: 'auto', fontFamily: 'inherit' } }, result.markdown || result.text || '识别任务已完成。')) : null);
    }

    // 各 ASR 模型的真实下载体积：faster-whisper 转换版 model.bin 的实际字节数（十进制 MB）。
    // 体积不是装饰——它决定下载时长、内存与精度；仅英语模型会把中文题号播报转成拼音。
    const ASR_MODELS = [
      { id: 'large-v3-turbo', mb: 1618, lang: '多语种大模型', tag: '默认', en: false,
        note: 'turbo 解码，约 large-v3 的 6–8 倍速；听力播报、中英混读都稳，切段首选' },
      { id: 'large-v3', mb: 3087, lang: '多语种大模型', tag: '', en: false,
        note: '精度最高档，最慢；turbo 出现漏词/错词时用它复算' },
      { id: 'large-v2', mb: 3087, lang: '多语种大模型', tag: '', en: false,
        note: '与 v3 同体积，中文略弱；只在已有缓存时考虑' },
      { id: 'large-v1', mb: 3087, lang: '多语种大模型', tag: '', en: false,
        note: '老版权重，没有理由优先选' },
      { id: 'large', mb: 3087, lang: '多语种大模型', tag: '= large-v3', en: false,
        note: '别名，实际加载 large-v3，二者等价' },
      { id: 'medium', mb: 1528, lang: '多语种', tag: '', en: false,
        note: '约 large 一半体积，精度明显低于 large 档；内存紧张时的折中' },
      { id: 'small', mb: 484, lang: '多语种小模型', tag: '', en: false,
        note: '快，但会漏词、中文题号识别差；必须明确接受精度损失才选' },
      { id: 'base', mb: 145, lang: '多语种小模型', tag: '', en: false,
        note: '只适合验证链路能否跑通' },
      { id: 'tiny', mb: 75, lang: '多语种小模型', tag: '', en: false,
        note: '最小，仅冒烟测试' },
      { id: 'medium.en', mb: 1528, lang: '仅英语', tag: '', en: true,
        note: '英语专用 medium，中文播报会输出乱码' },
      { id: 'small.en', mb: 484, lang: '仅英语', tag: '', en: true,
        note: '英语专用 small；实测把中文播报转成拼音，不适合本流程' },
      { id: 'base.en', mb: 145, lang: '仅英语', tag: '', en: true,
        note: '英语专用 base，仅冒烟测试' },
      { id: 'tiny.en', mb: 75, lang: '仅英语', tag: '', en: true,
        note: '英语专用 tiny，仅冒烟测试' },
    ];
    const asrLabel = (m) => m.id + '（' + [m.tag, m.lang, '≈' + m.mb + ' MB'].filter(Boolean).join(' · ') + '）';

    // 英语听力切段（WhisperX 可选能力）：适配器随插件分发，运行时/模型懒下载。
    function AudioSettings({ visible } = {}) {
      const React = require('react');
      const h = React.createElement;
      const [info, setInfo] = React.useState(null);
      const [status, setStatus] = React.useState(null);
      const [batches, setBatches] = React.useState([]);
      const [batch, setBatch] = React.useState('');
      const [draft, setDraft] = React.useState(null);
      const [busy, setBusy] = React.useState('');
      const [notice, setNotice] = React.useState(null);
      const [error, setError] = React.useState('');

      async function refreshInfo() {
        const cfg = await (await window.fetch('/wf-plugin/api/audio/config', { headers: { accept: 'application/json' } })).json();
        if (cfg.error) throw new Error(cfg.error);
        setInfo(cfg); setDraft((d) => d || cfg.config);
        return cfg;
      }
      async function refreshBatches() {
        const list = await (await window.fetch('/wf-plugin/api/batches', { headers: { accept: 'application/json' } })).json();
        const names = (list.batches || []).filter((b) => b.has_audio).map((b) => b.name);
        setBatches(names);
        setBatch((current) => (names.includes(current) ? current : ''));
        return names;
      }
      async function refreshStatus(name = batch) {
        if (!name) {
          // 没有选中批次时不读全局状态：全局状态可能属于别的试卷。
          const idle = { batch: '', job: '', running: false, busy: false, phase: 'idle', pct: 0, detail: '', error: '' };
          setStatus(idle); return idle;
        }
        const query = '?batch=' + encodeURIComponent(name);
        const st = await (await window.fetch('/wf-plugin/api/audio/status' + query, { headers: { accept: 'application/json' } })).json();
        setStatus(st);
        return st;
      }
      React.useEffect(() => {
        let active = true;
        (async () => {
          try {
            await refreshInfo();
            if (active) await refreshBatches();
          } catch (e) { if (active) setError(String(e.message || e)); }
        })();
        return () => { active = false; };
      }, []);
      // 设置页是常驻组件：批次列表和状态只在进入「听力切分」时刷新，
      // 避免新上传的批次看不到，也避免把上一个批次的状态当成当前批次。
      React.useEffect(() => {
        if (!visible) return;
        let active = true;
        (async () => {
          try {
            await refreshInfo();
            if (!active) return;
            const names = await refreshBatches();
            if (active) await refreshStatus(names.includes(batch) ? batch : '');
          } catch (e) { if (active) setError(String(e.message || e)); }
        })();
        return () => { active = false; };
      }, [visible]);
      React.useEffect(() => {
        if (!visible) return;
        refreshStatus(batch).catch(() => {});
      }, [batch]);
      React.useEffect(() => {
        if (!status?.busy && !status?.running) return;
        const timer = setInterval(() => { refreshStatus(batch).catch(() => {}); }, 2000);
        return () => clearInterval(timer);
      }, [status?.busy, status?.running, batch]);

      function change(name, value) { setDraft((d) => ({ ...d, [name]: value })); setNotice(null); }
      async function post(path, payload) {
        const r = await window.fetch('/wf-plugin/api/audio/' + path, {
          method: 'POST', headers: { 'content-type': 'application/json', accept: 'application/json' },
          body: JSON.stringify(payload || {}),
        });
        const data = await r.json();
        if (!r.ok || data.error) throw new Error(data.error || '请求失败');
        return data;
      }
      async function save() {
        setBusy('save'); setError('');
        try { const d = await post('config', { config: draft }); setDraft(d.config); setNotice('配置已保存。'); await refreshInfo(); }
        catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
      }
      async function install() {
        setBusy('install'); setError(''); setNotice('开始安装；首次会下载运行时与模型，可能需要几分钟。');
        try { await post('bootstrap', { force: false }); await refreshInfo(); }
        catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
      }
      async function segment() {
        if (!batch) { setError('请先选择批次。'); return; }
        setBusy('segment'); setError('');
        try { await post('segment', { name: batch }); setNotice('已启动「' + batch + '」的切段任务。'); await refreshStatus(batch); }
        catch (e) { setError(String(e.message || e)); } finally { setBusy(''); }
      }
      const field = (name, label, extra, help) => h('label', { style: { display: 'grid', gap: 6, marginBottom: 14 } },
        h('span', { style: T.fieldLabel }, label),
        h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, value: draft?.[name] ?? '', disabled: !!busy, onChange: (e) => change(name, e.target.value), ...(extra || {}) }),
        help ? h('span', { style: T.hint }, help) : null);
      const running = !!(status?.busy || status?.running);
      return h('section', { 'aria-labelledby': 'wf-audio-heading', style: { minWidth: 0 } },
        h('h2', { id: 'wf-audio-heading', style: T.cardTitle }, '听力切分'),
        h('p', { className: 'wf-settings-description' }, '配置转写模型与切分参数，为已确认结构的试卷生成听力片段。'),
        h('div', { style: { ...T.card, marginBottom: 0, boxShadow: 'none' } },
          h('div', { style: T.hint }, '结构闸门确认后会自动切段；也可在这里选择批次手动重跑。WhisperX 词级时间戳只用来找题号锚点，切点由 ffmpeg 长静音骨架决定。'),
          !info || !draft ? h('div', { style: { ...T.hint, marginTop: 10 } }, '正在读取设置…') : h('div', { style: { marginTop: 12 } },
            h('label', { style: { display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14 } },
              h('input', { type: 'checkbox', checked: !!draft.enabled, disabled: !!busy, onChange: (e) => change('enabled', e.target.checked) }),
              h('span', { style: T.fieldLabel }, '启用听力切段适配器')),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 14 } },
              h('span', { style: T.fieldLabel }, 'ASR 模型'),
              h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: draft.asr_model, disabled: !!busy, onChange: (e) => change('asr_model', e.target.value) },
                [false, true].map((isEn) => h('optgroup', { key: isEn ? 'en' : 'multi', label: isEn ? '仅英语（中文播报会转成拼音，不建议）' : '多语种（推荐）' },
                  ASR_MODELS.filter((m) => m.en === isEn).map((m) => h('option', { key: m.id, value: m.id }, asrLabel(m)))))),
              h('span', { style: T.hint }, '体积是 model.bin 的下载体积（十进制 MB，约数），不含 venv 的 4–6 GB。默认多语种大模型；安装失败不会自动换成小模型。英文词级对齐使用 facebook/wav2vec2-base-960h。')),
            h('details', { style: { margin: '0 0 16px' } },
              h('summary', { style: { cursor: 'pointer', ...T.hint, padding: '4px 0', marginBottom: 10 } }, '各模型体积与特点'),
              h('div', { style: { display: 'grid', gap: 6 } },
                h('div', { style: { display: 'grid', gridTemplateColumns: 'minmax(110px, 150px) minmax(64px, 84px) minmax(0, 1fr)', gap: 8, ...T.hint } },
                  h('span', null, '模型'), h('span', null, '下载体积'), h('span', null, '特点')),
                ASR_MODELS.map((m) => h('div', { key: m.id, style: { display: 'grid', gridTemplateColumns: 'minmax(110px, 150px) minmax(64px, 84px) minmax(0, 1fr)', gap: 8, alignItems: 'baseline', borderTop: '1px solid var(--dsw-alias-border-l1)', paddingTop: 6 } },
                  h('span', { style: T.fieldLabel }, m.id + (m.tag ? '（' + m.tag + '）' : '')),
                  h('span', { style: T.hint }, '≈' + m.mb + ' MB'),
                  h('span', { style: T.hint }, m.lang + '：' + m.note)))),
              h('div', { style: { ...T.hint, marginTop: 8 } }, '体积按 faster-whisper 转换版 model.bin 计，不同镜像或量化方式可能差几 MB。')),
            field('skeleton_silence', '骨架静音阈值（秒）', { type: 'number', min: 0.5, max: 20, step: 0.5 }, '≥3s 的长静音中点作为切点骨架。'),
            h('details', { style: { margin: '4px 0 18px' } },
              h('summary', { style: { cursor: 'pointer', ...T.hint, padding: '4px 0', marginBottom: 12 } }, '高级参数'),
              field('cut_silence', '静音探测最短时长（秒）', { type: 'number', min: 0.1, max: 5, step: 0.05 }),
              field('noise', '静音判定阈值', { type: 'text', placeholder: '-35dB' }),
              field('anchor_language', '播报语言（留空关闭辅助转写）', { type: 'text', placeholder: 'zh' }),
              field('cpu_threads', '每个转写任务的 CPU 线程数', { type: 'number', min: 1, max: 64, step: 1 }),
              field('batch_size', '转写批量大小', { type: 'number', min: 1, max: 64, step: 1 }),
              field('max_parallel_transcriptions', '同时驻留的转写模型数', { type: 'number', min: 1, max: 2, step: 1 },
                '大模型默认 1，避免两份模型争抢内存；资源充足时可设为 2。'),
              field('hf_endpoint', '模型下载镜像（留空自动探测）', { type: 'text', placeholder: 'https://hf-mirror.com' },
                'huggingface.co 不可达时自动用 hf-mirror.com；也可填公司内网镜像。'),
              h('label', { style: { display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14 } },
                h('input', { type: 'checkbox', checked: !!draft.cpu_torch, disabled: !!busy,
                  onChange: (e) => change('cpu_torch', e.target.checked) }),
                h('span', { style: T.fieldLabel }, 'Linux 上优先装 CPU 版 torch（避免数 GB 的 CUDA 依赖）')),
              field('venv', '复用已有 venv（留空则自建）', { type: 'text', placeholder: '例如 ~/.local/share/video-kit/venv' },
                '填已存在的虚拟环境目录；仍会自动补齐缺失依赖。'),
              field('data_dir', '运行时/模型目录（留空用数据根）', { type: 'text', placeholder: '~/.dsh/wf-plugin-data/audio' })),
            h('div', { style: { display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' } },
              h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: save }, busy === 'save' ? '正在保存…' : '保存设置'),
              h('button', { type: 'button', className: 'wf-btn-primary', style: T.btnPrimary, disabled: !!busy || running, onClick: install },
                info.ready ? '重新检查 / 强制更新' : '安装运行时与模型'),
              h('span', { style: T.hint }, info.ready ? '✓ 已就绪' : '未安装')),
            h('div', { style: { borderTop: '1px solid var(--dsw-alias-border-l1)', marginTop: 18, paddingTop: 16 } },
              h('label', { style: { display: 'grid', gap: 6, marginBottom: 12 } },
                h('span', { style: T.fieldLabel }, '为批次生成听力切段'),
                h('div', { style: { display: 'flex', gap: 8, alignItems: 'center', minWidth: 0 } },
                  h('select', { className: 'wf-input', style: { ...T.input, flex: 1, minWidth: 0 }, value: batch, disabled: !!busy, onChange: (e) => setBatch(e.target.value) },
                    h('option', { value: '' }, '选择批次…'),
                    batches.map((n) => h('option', { key: n, value: n }, n))),
                  h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy, onClick: () => refreshBatches().catch((e) => setError(String(e.message || e))) }, '刷新批次')),
                h('span', { style: T.hint }, '列表只显示已上传整卷听力音频的批次；新上传后点「刷新批次」或重新进入此页即可看到。')),
              h('button', { type: 'button', className: 'wf-btn', style: T.btn, disabled: !!busy || running || !info.ready || !batch, onClick: segment },
                busy === 'segment' ? '正在切段…' : '生成切段'),
              !info.ready ? h('span', { style: { ...T.hint, marginLeft: 8 } }, '请先安装运行时。') : null),
            info.ffmpeg ? null : h('div', { role: 'alert', style: { ...T.statusErr, marginTop: 12 } }, '未检测到 ffmpeg/ffprobe：插件 vendor/ 里没有本平台二进制，系统 PATH 也找不到。请联系打包者运行 fetch_vendor.py，或自行安装 ffmpeg。'),
            h('div', { style: { ...T.hint, marginTop: 12 } }, '数据目录：' + info.data_dir),
            h('div', { style: T.hint }, '模型镜像：' + (info.hf_endpoint || '直连 huggingface.co')),
          ),
          status && batch ? h('div', { style: { borderTop: '1px solid var(--dsw-alias-border-l1)', marginTop: 16, paddingTop: 14, display: 'grid', gap: 6 } },
            h('div', { style: T.fieldLabel }, '任务状态：' + (status.phase || 'idle') + (status.pct ? ' · ' + status.pct + '%' : '') + (status.batch ? ' · ' + status.batch : '')),
            status.detail ? h('div', { style: T.hint }, status.detail) : null,
            status.error ? h('div', { role: 'alert', style: T.statusErr }, status.error) : null) : null,
          notice ? h('div', { role: 'status', style: { ...T.statusOk, marginTop: 12 } }, notice) : null,
          error ? h('div', { role: 'alert', style: { ...T.statusErr, marginTop: 12, overflowWrap: 'anywhere' } }, error) : null));
    }

    function SettingsTab() {
      const React = require('react');
      const h = React.createElement;
      // 默认停在「初始化引导」：新装用户第一眼就看到要装/要配什么。
      const [activeSection, setActiveSection] = React.useState('onboarding');
      const [visited, setVisited] = React.useState({ onboarding: true });
      const sections = [['onboarding', '初始化引导'], ['models', '大模型配置'], ['api', '上传设置'], ['ocr', 'OCR 识别'], ['audio', '听力切分']];
      function navigate(id) {
        setVisited((current) => ({ ...current, [id]: true })); setActiveSection(id);
      }
      function onNavKey(event, index) {
        const delta = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? sections.length - 1 : delta ? (index + delta + sections.length) % sections.length : -1;
        if (next < 0) return;
        event.preventDefault(); navigate(sections[next][0]);
        event.currentTarget.parentElement.querySelectorAll('[role=tab]')[next]?.focus();
      }
      function navIcon(id) {
        const paths = { onboarding: 'M9 2l2.2 4.6L16 7.3l-3.5 3.4.8 4.8L9 13.2 4.7 15.5l.8-4.8L2 7.3l4.8-.7z', models: 'M4 3h10v12H4z M7 6h4 M7 9h4 M7 12h2', api: 'M6.5 5L3 9l3.5 4 M11.5 5L15 9l-3.5 4 M10 3L8 15', ocr: 'M6 3H3v3 M12 3h3v3 M15 12v3h-3 M6 15H3v-3 M6 6h6v6H6z', audio: 'M3 7v4 M6 4v10 M9 2v14 M12 5v8 M15 7v4' };
        return h('svg', { width: 16, height: 16, viewBox: '0 0 18 18', fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }, h('path', { d: paths[id] }));
      }
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
      const defaultSel = router?.default?.model ? router.default : (router?.harnessDefault || null);

      async function persist(next) {
        const previous = router;
        setRouter(next); setStatus('saving'); setErr('');
        try {
          const r = await window.fetch('/wf-plugin/router', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(next) });
          if (!r.ok) throw new Error(await r.text());
          const res = await r.json(); setRouter(res.router); setStatus('saved');
          setTimeout(() => setStatus((s) => s === 'saved' ? 'idle' : s), 1500);
        } catch (e) { setRouter(previous); setErr(String(e.message || e)); setStatus('idle'); }
      }
      const inferProfile = () => 'wf-deepseek-fast';
      const modelId = (entry) => (entry && entry.provider && entry.model ? entry.provider + '|' + entry.model : '');
      const isPlainMap = (value) => !!value && typeof value === 'object' && !Array.isArray(value);
      // 换全局默认时，凡是仍沿用旧默认值的子 Agent / 审核轮次也必须一起跟随，
      // 否则它们会保留旧模型，界面上看起来“下面的没跟着改”。
      // 损坏配置（数组/字符串等非对象）保持原样、不在此处改写，交由读取处的 || {} 兜底。
      function setDefault(sel) {
        const previous = router?.default || {};
        const changed = modelId(previous) !== modelId(sel);
        const next = { ...router, default: { ...previous, ...sel, profile: inferProfile() } };
        const stale = (entry) => changed && modelId(entry) === modelId(previous);
        for (const group of ['roles', 'stages']) {
          if (!isPlainMap(next[group])) continue;
          const map = { ...next[group] };
          for (const key of Object.keys(map)) if (stale(map[key])) delete map[key];
          next[group] = map;
        }
        if (isPlainMap(next.review) && isPlainMap(next.review.rounds)) {
          const rounds = { ...next.review.rounds };
          for (const key of Object.keys(rounds)) if (stale(rounds[key])) delete rounds[key];
          next.review = { ...next.review, rounds };
        }
        persist(next);
      }
      function setStage(stage, sel) { const roles = { ...(router?.roles || {}) }; roles[stage] = { ...sel, profile: inferProfile() }; persist({ ...router, roles }); }
      function clearStage(stage) { const roles = { ...(router?.roles || {}) }; delete roles[stage]; persist({ ...router, roles }); }
      function setRound(round, sel) { const review = { ...(router?.review || {}) }; const rounds = { ...(review.rounds || {}) }; rounds[round] = { ...sel, profile: inferProfile() }; review.rounds = rounds; persist({ ...router, review }); }
      function clearRound(round) { const review = { ...(router?.review || {}) }; const rounds = { ...(review.rounds || {}) }; delete rounds[round]; review.rounds = rounds; persist({ ...router, review }); }

      function ModelSelect({ value, onChange, onClear }) {
        const hasOwn = value && value.provider && value.model;
        const v = hasOwn ? modelKey(value) : '';
        const follow = defaultSel ? ('跟随默认（' + (byKey[modelKey(defaultSel)]?.label || defaultSel.model) + '）') : '跟随默认模型';
        return h('select', {
          className: 'wf-input', style: { ...T.input, width: '100%' }, value: v, disabled: !router || status === 'saving' || status === 'testing',
          onChange: (e) => { if (e.target.value === '') { onClear && onClear(); return; } const m = byKey[e.target.value]; if (m) onChange({ provider: m.provider, model: m.model }); },
        },
          h('option', { value: '' }, follow),
          models.map((m) => h('option', { key: modelKey(m), value: modelKey(m) }, m.label + ' · ' + m.provider)));
      }

      const stages = [['answers', '答案提取'], ['audio', '听力切分'], ['types', '题型判定'], ['explanations', '解析与大意'], ['knowledge', '知识点标注'], ['assemble', '填充汇总'], ['review', '审核'], ['operations', 'AI运维']];
      const rounds = [['labels', '一审'], ['sample', '二审（默认 20% 抽样）']];

      async function writeEffective() {
        setStatus('testing'); setErr('');
        try {
          const response = await window.fetch('/wf-plugin/materialize', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' });
          const data = await response.json();
          if (!response.ok || data.ok === false || data.error) throw new Error(data.error || '写入生效配置失败，请重试。');
          setStatus('tested');
        } catch (e) { setErr(String(e.message || e)); setStatus('idle'); }
      }

      return h('div', { className: 'wf-settings-page' },
        h('div', { className: 'wf-settings-layout' },
          h('nav', { className: 'wf-settings-nav wf-scroll', role: 'tablist', 'aria-label': '设置分类', 'aria-orientation': 'vertical' },
            h('p', { className: 'wf-settings-nav-title' }, '设置'),
            sections.map(([id, label], index) => h('button', { key: id, type: 'button', id: 'wf-settings-tab-' + id, role: 'tab', 'aria-selected': activeSection === id, 'aria-controls': 'wf-settings-panel-' + id, tabIndex: activeSection === id ? 0 : -1, onClick: () => navigate(id), onKeyDown: (event) => onNavKey(event, index) }, navIcon(id), label))),
          h('div', { className: 'wf-settings-content wf-scroll' },
          h('div', { className: 'wf-settings-section', role: 'tabpanel', id: 'wf-settings-panel-onboarding', 'aria-labelledby': 'wf-settings-tab-onboarding', hidden: activeSection !== 'onboarding', tabIndex: 0 },
            visited.onboarding ? h(OnboardingSection, { onNavigate: navigate }) : null),
          h('div', { className: 'wf-settings-section', role: 'tabpanel', id: 'wf-settings-panel-models', 'aria-labelledby': 'wf-settings-tab-models', hidden: activeSection !== 'models', tabIndex: 0 },
          h('section', { 'aria-labelledby': 'wf-model-settings-heading', style: { minWidth: 0 } },
          h('h2', { id: 'wf-model-settings-heading', style: T.cardTitle }, '大模型配置'),
          h('p', { className: 'wf-settings-description' }, '默认跟随本机 Harness 当前模型，无需填写 API Key；也可显式指定全局默认、各处理阶段与两轮审核模型。改动自动保存。'),
          err ? h('div', { role: 'alert', style: { ...T.statusErr, marginBottom: 8 } }, err) : null,
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.defaultModel),
            models.length === 0 ? h('div', { style: T.hint }, '正在读取模型…') :
              h('div', { style: { display: 'grid', gap: 8, minWidth: 0 } },
                h('select', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, disabled: !router || status === 'saving' || status === 'testing', value: (router?.default?.model ? modelKey(router.default) : (router?.harnessDefault ? modelKey(router.harnessDefault) : '')), onChange: (e) => { const m = byKey[e.target.value]; if (m) setDefault({ provider: m.provider, model: m.model }); } },
                  models.map((m) => h('option', { key: modelKey(m), value: modelKey(m) }, m.label + ' · ' + m.provider))),
                h('span', { style: T.hint }, '当前：' + (router?.default?.model ? '（插件默认）' : '（跟随 Harness）') + (byKey[router?.default?.model ? modelKey(router.default) : (router?.harnessDefault ? modelKey(router.harnessDefault) : '')]?.label || router?.default?.model || router?.harnessDefault?.model || '—')),
        h('div', { style: { ...T.hint, marginTop: 6 } }, '修改全局默认时，仍跟随默认的子 Agent 与审核轮次会一起切换；已单独指定的保持不变。')),
          ),
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.stageModels),
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '内置 8 个子 Agent：前置组装题型判定、答案提取、听力切段、题型兼容校验、解析抄录、知识点标注、装配、审核；AI运维为附加能力，可为它单独选模型（未选时跟随默认/Harness 当前模型）。默认全部「跟随默认」，需要单独指定时才选。'),
            h('div', { className: 'wf-model-fields', style: { display: 'grid', gridTemplateColumns: '120px minmax(0,1fr)', gap: '10px 14px', alignItems: 'center' } },
              stages.map(([s, lbl]) => h('div', { key: s, style: { display: 'contents' } },
                h('span', { style: T.label }, lbl),
                h(ModelSelect, { value: router?.roles?.[s] || router?.stages?.[s] || null, onChange: (sel) => setStage(s, sel), onClear: () => clearStage(s) })))),
          ),
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.reviewModels),
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '默认两轮都「跟随默认」；可分别指定一审/二审模型。'),
            h('div', { className: 'wf-model-fields', style: { display: 'grid', gridTemplateColumns: '120px minmax(0,1fr)', gap: '10px 14px', alignItems: 'center' } },
              rounds.map(([r, lbl]) => h('div', { key: r, style: { display: 'contents' } },
                h('span', { style: T.label }, lbl),
                h(ModelSelect, { value: router?.review?.rounds?.[r] || null, onChange: (sel) => setRound(r, sel), onClear: () => clearRound(r) })))),
            h('div', { style: { marginTop: 14, display: 'flex', alignItems: 'center', gap: 12 } },
              h('button', { className: 'wf-btn', style: T.btn, disabled: status === 'saving' || status === 'testing' || !router, onClick: writeEffective }, status === 'testing' ? '正在写入…' : TEXT.writeEffective),
              status === 'saving' ? h('span', { style: T.hint }, '正在保存…')
                : status === 'saved' ? h('span', { style: T.statusOk }, '✓ 已保存，下次进入会记住')
                : status === 'tested' ? h('span', { style: T.statusOk }, '✓ 已写入生效配置')
                : h('span', { style: T.hint }, '改动会自动保存')),
          )),
          ),
          [['api', QuestionBankSettings], ['ocr', OCRSettings], ['audio', AudioSettings]].map(([id, Component]) =>
            h('div', { key: id, className: 'wf-settings-section', role: 'tabpanel', id: 'wf-settings-panel-' + id, 'aria-labelledby': 'wf-settings-tab-' + id, hidden: activeSection !== id, tabIndex: 0 }, visited[id] ? h(Component, id === 'audio' ? { visible: activeSection === id } : null) : null)),
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
      const [tab, setTab] = React.useState('upload');
      const [uploadedTick, setUploadedTick] = React.useState(0);
      const tabBtn = (id, label) => h('button', { key: id, onClick: () => setTab(id), style: T.tab(tab === id), 'aria-current': tab === id ? 'page' : undefined }, label);
      return h('div', { className: 'wf-root', style: T.root },
        h('div', { style: T.tabbar },
          h('div', { style: T.brand },
            h('span', { style: T.brandMark }, docIcon(13)),
            h('span', { style: T.brandName }, TEXT.title)),
          h('div', { style: T.segWrap, role: 'tablist' },
            tabBtn('upload', TEXT.upload),
            tabBtn('review', TEXT.review),
            tabBtn('settings', TEXT.settings)),
          h('span', { style: { flex: 1 } }),
          props.closePanel ? h('button', { className: 'wf-icon-btn', title: TEXT.close, 'aria-label': TEXT.close, onClick: props.closePanel, style: T.iconBtn }, closeIcon()) : null,
        ),
        h('div', { style: T.body },
          tab === 'review' ? h(WorkbenchTab, { key: uploadedTick }) :
          tab === 'upload' ? h(UploadView, {
            // 上传后留在上传页：继续看新试卷从第一步跑起的流程，不被自动跳走打断。
            // 内容审核与上传格式检查结束的试卷由上传页的醒目入口按需切到「试卷审核」人工确认。
            onUploaded: () => { setUploadedTick((x) => x + 1); },
            onGoReview: () => { setUploadedTick((x) => x + 1); setTab('review'); },
          }) :
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

