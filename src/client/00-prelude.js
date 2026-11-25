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
//   · 模型与设置：左侧四项导航（模型设置 / 上传设置 / OCR 识别 / 听力切分）
// ---------------------------------------------------------------------------
window.__ModuleLoader__.load({
  id: 'md-upload-tiku',
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;

    var API = '/wf-plugin/api';
    // Embed the supplied WebP so the host can render it without a separate asset route.
    var BRAND_LOGO = 'data:image/webp;base64,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';

    var TEXT = {
      title: '录题系统',
      brandTitle: '木牍题库系统',
      review: '试卷审核',
      typeReview: '题型审核',
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
      contentPreview: '文章大意 · 解析',
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
      aiSupplementModels: '文章大意、答案解析模型',
      reviewModels: '审核模型',
      follow: '跟随默认',
      writeEffective: '写入生效配置',
      deleteConfirm: '彻底删除批次',
    };

    // 统一视觉：全部用 Harness 主题 token，随浅/深色主题自动适配。
    // 刻度（全站共用，改一处即可整体协调）：圆角 10/8/6/999，间距 4/6/8/10/12/14/16，
    // 字号 11.5/12/12.5/13/13.5/14/14.5，字重 500/600/700。
    var T = {
      root: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-base)', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.55 },
      tabbar: {
        display: 'flex', alignItems: 'center', gap: 12, height: 54,
        // Harness supplies the space needed for window controls and the sidebar opener.
        paddingInlineStart: 'max(18px, var(--dsh-frame-leading-clearance, 0px))',
        paddingInlineEnd: 18,
        borderBottom: '1px solid var(--dsw-alias-border-l2)',
        background: 'color-mix(in srgb, var(--dsw-alias-label-primary) 4.5%, var(--dsw-alias-bg-base))',
        flexShrink: 0,
      },
      brand: { display: 'flex', alignItems: 'center', gap: 9, marginRight: 8, flexShrink: 0 },
      brandMark: { width: 26, height: 26, objectFit: 'contain', flexShrink: 0 },
      brandName: { fontSize: 16, fontWeight: 700, letterSpacing: '.01em', whiteSpace: 'nowrap' },
      segWrap: {
        display: 'inline-flex', alignItems: 'center', gap: 4, padding: 3, borderRadius: 11,
        background: 'var(--dsw-alias-bg-base)',
        border: '1px solid var(--dsw-alias-border-l2)',
        boxShadow: '0 1px 2px rgba(15, 23, 42, 0.05)',
      },
      tab: (active) => ({
        padding: '6px 16px', borderRadius: 8, cursor: 'pointer',
        border: '1px solid ' + (active ? '#2563eb' : 'transparent'),
        fontSize: 13, fontWeight: active ? 600 : 500, whiteSpace: 'nowrap', letterSpacing: '.01em',
        color: active ? '#ffffff' : 'var(--dsw-alias-label-primary)',
        background: active ? '#2563eb' : 'transparent',
        boxShadow: active ? '0 2px 8px rgba(37, 99, 235, 0.28)' : 'none',
        transition: 'color .16s ease, background .16s ease, border-color .16s ease, box-shadow .16s ease',
      }),
      iconBtn: { width: 30, height: 30, padding: 0, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderRadius: 8, cursor: 'pointer', fontSize: 15, lineHeight: 1, color: 'var(--dsw-alias-label-secondary)', background: 'transparent', border: '1px solid transparent', transition: 'background .18s, color .18s' },
      body: { flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden', background: 'var(--dsw-alias-bg-base)' },
      toolbar: { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', minHeight: 48, borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0, flexWrap: 'wrap' },
      toolbarTitle: { fontWeight: 600, fontSize: 13.5 },
      paperRow: (active) => ({
        margin: '0 2px 6px', padding: '7px 9px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
        borderRadius: 8, transition: 'background .16s, border-color .16s',
        // 列表保留 Harness 默认字体，题目正文在内容区域使用统一排版。
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
        background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 4px 16px rgba(15, 23, 42, 0.08), 0 1px 3px rgba(15, 23, 42, 0.06)',
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
        background: kind === 'warn' ? 'rgba(230,162,60,.14)' : kind === 'info' ? 'rgba(59,130,246,.12)' : 'var(--dsw-alias-bg-layer-2)',
        border: kind === 'warn' ? '1px solid rgba(230,162,60,.35)' : kind === 'info' ? '1px solid rgba(59,130,246,.30)' : '1px solid var(--dsw-alias-border-l1)',
        color: kind === 'warn' ? 'var(--dsw-alias-state-warn-primary)' : kind === 'info' ? '#2563eb' : 'var(--dsw-alias-label-secondary)',
      }),
      empty: { padding: '32px 18px', color: 'var(--dsw-alias-label-secondary)', textAlign: 'center', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', minHeight: 140 },
      emptyIcon: { width: 40, height: 40, borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--dsw-alias-bg-layer-2)', border: '1px solid var(--dsw-alias-border-l1)', marginBottom: 12 },
      emptyTitle: { fontSize: 13, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' },
      emptyHint: { fontSize: 12, marginTop: 5, maxWidth: 340, lineHeight: 1.7 },
      cardClickable: {
        border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 12, padding: '10px 12px', marginBottom: 8,
        background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 4px 14px rgba(15, 23, 42, 0.07), 0 1px 3px rgba(15, 23, 42, 0.05)', cursor: 'pointer',
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
      const { title, tag, dirty, width, cardStyle, bodyStyle, headerExtra, onClose, footer, children } = props;
      return h('div', { onClick: onClose, style: T.modalOverlay },
        h('div', { onClick: (e) => e.stopPropagation(), style: { ...T.modalCard(width), ...(cardStyle || {}) }, role: 'dialog', 'aria-modal': 'true', 'aria-label': title },
          h('div', { style: T.modalHead },
            h('span', { style: T.modalTitle }, title),
            tag ? h('span', { title: '当前正在修改的题目', style: T.tagPill }, tag) : null,
            dirty ? h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-state-warn-primary)' } }, '未保存') : null,
            headerExtra || null,
            h('span', { style: { flex: 1 } }),
            h('button', { className: 'wf-icon-btn', title: TEXT.close, 'aria-label': TEXT.close, onClick: onClose, style: T.iconBtn }, closeIcon()),
          ),
          h('div', { className: 'wf-scroll', style: { ...T.modalBody, ...(bodyStyle || {}) } }, children),
          h('div', { style: T.modalFoot }, ...(Array.isArray(footer) ? footer : [footer])),
        ),
      );
    }

