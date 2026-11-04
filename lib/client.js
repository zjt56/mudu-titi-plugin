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
//   · 模型与设置：默认/阶段/三审模型
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
      reviewModels: '三审模型',
      follow: '跟随默认',
      writeEffective: '写入生效配置',
      deleteConfirm: '删除批次',
    };

    // 统一视觉：全部用 Harness 主题 token，随浅/深色主题自动适配。
    var T = {
      root: { display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-base)', fontFamily: 'inherit', fontSize: 13, lineHeight: 1.5 },
      tabbar: { display: 'flex', alignItems: 'center', gap: 4, padding: '0 14px', height: 46, borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0 },
      tab: (active) => ({
        position: 'relative', padding: '8px 14px', cursor: 'pointer', border: 'none', background: 'transparent',
        color: active ? 'var(--dsw-alias-brand-primary)' : 'var(--dsw-alias-label-secondary)',
        borderBottom: active ? '2px solid var(--dsw-alias-brand-primary)' : '2px solid transparent',
        fontWeight: active ? 600 : 400, fontSize: 13.5, transition: 'color .15s',
      }),
      body: { flex: 1, minHeight: 0, display: 'flex', overflow: 'hidden', background: 'var(--dsw-alias-bg-base)' },
      toolbar: { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', borderBottom: '1px solid var(--dsw-alias-border-l1)', background: 'var(--dsw-alias-bg-layer-1)', flexShrink: 0, flexWrap: 'wrap' },
      side: { width: 248, flexShrink: 0, borderRight: '1px solid var(--dsw-alias-border-l1)', overflow: 'auto', background: 'var(--dsw-alias-bg-layer-1)', padding: '4px 0' },
      sideTitle: { padding: '12px 14px 8px', fontSize: 11.5, fontWeight: 600, letterSpacing: '.02em', color: 'var(--dsw-alias-label-secondary)', textTransform: 'uppercase' },
      paperRow: (active) => ({
        margin: '2px 8px', padding: '8px 10px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
        borderRadius: 8, transition: 'background .12s',
        background: active ? 'var(--dsw-alias-brand-primary)' : 'transparent',
        color: active ? '#fff' : 'var(--dsw-alias-label-primary)',
      }),
      paperName: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12.5 },
      statGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, padding: '8px 12px', borderTop: '1px solid var(--dsw-alias-border-l1)' },
      stat: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)' },
      statNum: { fontSize: 15, fontWeight: 700, color: 'var(--dsw-alias-label-primary)', marginRight: 4 },
      main: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' },
      scroll: { flex: 1, overflow: 'auto', padding: 16 },
      split: { display: 'flex', gap: 16, flex: 1, minHeight: 0 },
      col: { flex: 1, minWidth: 0, overflow: 'auto' },
      card: {
        border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 12, padding: 16, marginBottom: 14,
        background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 1px 2px rgba(0,0,0,.04)',
      },
      cardTitle: { fontSize: 13.5, fontWeight: 650, margin: '0 0 12px', color: 'var(--dsw-alias-label-primary)', display: 'flex', alignItems: 'center', gap: 8 },
      row: { display: 'flex', alignItems: 'center', gap: 10, margin: '8px 0', flexWrap: 'wrap' },
      label: { minWidth: 88, fontSize: 12.5, color: 'var(--dsw-alias-label-secondary)' },
      input: {
        padding: '7px 11px', borderRadius: 9, fontSize: 13, color: 'var(--dsw-alias-label-primary)',
        background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)',
        outline: 'none', boxSizing: 'border-box', transition: 'border-color .15s, box-shadow .15s',
      },
      btn: {
        padding: '6px 14px', borderRadius: 9, cursor: 'pointer', fontSize: 13,
        color: 'var(--dsw-alias-label-primary)', background: 'var(--dsw-alias-bg-layer-2)',
        border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .12s, border-color .12s',
      },
      btnPrimary: {
        padding: '6px 16px', borderRadius: 9, cursor: 'pointer', fontSize: 13, fontWeight: 600,
        color: '#fff', background: 'var(--dsw-alias-brand-primary)', border: '1px solid var(--dsw-alias-brand-primary)',
        transition: 'filter .12s',
      },
      btnDanger: {
        padding: '6px 14px', borderRadius: 9, cursor: 'pointer', fontSize: 13,
        color: 'var(--dsw-alias-state-error-primary)', background: 'transparent',
        border: '1px solid var(--dsw-alias-border-l2)', transition: 'background .12s',
      },
      hint: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)' },
      statusOk: { fontSize: 12, color: 'var(--dsw-alias-state-success-primary)' },
      statusErr: { fontSize: 12, color: 'var(--dsw-alias-state-error-primary)' },
      badge: (kind) => ({
        fontSize: 11, fontWeight: 600, padding: '1px 7px', borderRadius: 10, lineHeight: 1.5,
        background: kind === 'warn' ? 'rgba(230,162,60,.14)' : 'var(--dsw-alias-bg-layer-2)',
        border: kind === 'warn' ? '1px solid rgba(230,162,60,.35)' : '1px solid var(--dsw-alias-border-l1)',
        color: kind === 'warn' ? 'var(--dsw-alias-state-warn-primary)' : 'var(--dsw-alias-label-secondary)',
      }),
      empty: { padding: 32, fontSize: 13, color: 'var(--dsw-alias-label-secondary)', textAlign: 'center' },
      cardClickable: {
        border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 12, padding: 16, marginBottom: 12,
        background: 'var(--dsw-alias-bg-layer-1)', boxShadow: '0 1px 2px rgba(0,0,0,.04)',
        transition: 'border-color .12s, box-shadow .12s',
      },
    };

    // 注入一次全局样式：hover/focus/滚动条/块级排版（内联样式表达不了）。
    var STYLE_ID = 'wf-plugin-styles';
    function injectStyles() {
      if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) return;
      var css = ''
        + '.wf-root *{box-sizing:border-box}'
        + '.wf-hoverable{cursor:pointer}'
        + '.wf-hoverable:hover{background:var(--dsw-alias-bg-layer-2)!important}'
        + '.wf-paper:hover{background:var(--dsw-alias-bg-layer-2)!important;color:var(--dsw-alias-label-primary)!important}'
        + '.wf-paper.wf-active:hover{filter:brightness(1.06)}'
        + '.wf-card-hover:hover{border-color:var(--dsw-alias-brand-primary)!important;box-shadow:0 4px 14px rgba(0,0,0,.08)!important}'
        + '.wf-btn:hover{background:var(--dsw-alias-bg-layer-2);border-color:var(--dsw-alias-brand-primary)}'
        + '.wf-btn-primary:hover{filter:brightness(1.08)}'
        + '.wf-btn-danger:hover{background:rgba(245,108,108,.1)}'
        + '.wf-input:focus{border-color:var(--dsw-alias-brand-primary)!important;box-shadow:0 0 0 3px rgba(47,91,234,.12)!important}'
        + '.wf-scroll::-webkit-scrollbar{width:8px;height:8px}'
        + '.wf-scroll::-webkit-scrollbar-thumb{background:var(--dsw-alias-border-l2);border-radius:4px}'
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
        + '.wf-find{display:block;padding:10px 0;border-top:1px dashed var(--dsw-alias-border-l1)}'
        + '.wf-find:first-child{border-top:none}'
        + '.wf-stat{flex:1;min-width:70px;background:var(--dsw-alias-bg-base);border:1px solid var(--dsw-alias-border-l1);border-radius:10px;padding:8px 10px;text-align:center}'
        + '.wf-stat b{display:block;font-size:17px;font-weight:700;line-height:1.2}'
        + '.wf-stat span{font-size:11px;color:var(--dsw-alias-label-secondary)}'
        + '.wf-stat.warn b{color:var(--dsw-alias-state-warn-primary)}';
      var el = document.createElement('style');
      el.id = STYLE_ID;
      el.textContent = css;
      document.head.appendChild(el);
    }


    // ---- API helpers（全部走 /wf-plugin/api/*）----
    async function jget(path, signal) {
      const r = await window.fetch(API + path, { headers: { accept: 'application/json' }, signal });
      if (!r.ok) throw new Error((await r.text()) || r.status);
      return r.json();
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
    async function fetchPreviewImage(value, signal) {
      const response = await window.fetch(imageRequestUrl(value), { headers: { accept: 'image/*' }, signal });
      if (!response.ok) throw new Error('图片请求失败（' + response.status + '）');
      const blob = await response.blob();
      if (signal.aborted) throw new DOMException('已取消图片加载', 'AbortError');
      if (!/^image\//i.test(blob.type)) throw new Error('图片接口未返回图片');
      return URL.createObjectURL(blob);
    }

    function PreviewImage({ url, name }) {
      const React = require('react');
      const h = React.createElement;
      const [attempt, setAttempt] = React.useState(0);
      const [image, setImage] = React.useState(null);
      React.useEffect(() => {
        const controller = new AbortController();
        let active = true;
        let objectUrl = '';
        setImage(null);
        fetchPreviewImage(url, controller.signal).then((src) => {
          if (!active) { URL.revokeObjectURL(src); return; }
          objectUrl = src;
          setImage({ url, src });
        }).catch((error) => {
          if (active && error.name !== 'AbortError') setImage({ url, error: String(error.message || error) });
        });
        return () => {
          active = false;
          controller.abort();
          if (objectUrl) URL.revokeObjectURL(objectUrl);
        };
      }, [url, attempt]);
      if (!image || image.url !== url) return h('span', { role: 'status', style: T.hint }, '图片加载中…');
      if (image.error) return h('div', { role: 'status', style: { ...T.hint, margin: '8px 0' } },
        '图片未显示：' + (name || '') + '（' + image.error + '） ',
        h('button', { className: 'wf-btn', style: T.btn, onClick: () => setAttempt((n) => n + 1) }, '重试图片'));
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
        return BoundaryReact.createElement('div', { role: 'alert', style: { ...T.empty, flex: 1 } },
          '本题显示失败：' + this.state.error + '。可以继续切换其他题目。 ',
          BoundaryReact.createElement('button', { className: 'wf-btn', style: T.btn,
            onClick: () => this.setState((s) => ({ error: null, retry: s.retry + 1 })) }, '重新显示本题'));
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
      return h('div', { className: 'wf-scroll', style: { flex: 1, overflow: 'auto', padding: 24 } },
        h('div', { style: { maxWidth: 640, margin: '0 auto' } },
          h('div', { style: { fontSize: 18, fontWeight: 700, marginBottom: 4 } }, '上传试卷'),
          h('div', { style: { ...T.hint, marginBottom: 16 } }, '选原卷（docx / pdf，可带答案与听力音频），提交后自动建批次并推进流程。'),
          h('div', { style: T.card },
            h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px 16px' } },
              h('label', { style: { gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: 5 } },
                h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)' } }, TEXT.uploadTitle),
                h('input', { className: 'wf-input', style: inp, value: form.title, placeholder: '如 2025年安徽省初中学业水平考试英语', onChange: (e) => setForm({ ...form, title: e.target.value }) })),
              h('label', { style: { display: 'flex', flexDirection: 'column', gap: 5 } },
                h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)' } }, TEXT.uploadYear),
                h('input', { className: 'wf-input', style: inp, value: form.year, placeholder: '2025', onChange: (e) => setForm({ ...form, year: e.target.value }) })),
              h('label', { style: { display: 'flex', flexDirection: 'column', gap: 5 } },
                h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)' } }, TEXT.uploadRegion),
                h('input', { className: 'wf-input', style: inp, value: form.region, onChange: (e) => setForm({ ...form, region: e.target.value }) })),
            ),
            h('label', { style: { display: 'flex', flexDirection: 'column', gap: 5, marginTop: 14 } },
              h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)' } }, TEXT.uploadFiles),
              h('input', { type: 'file', multiple: true, accept: '.docx,.pdf,.mp3,.wav,.m4a,.ogg,.flac', onChange: onFiles, style: { fontSize: 12.5, padding: '8px', border: '1px dashed var(--dsw-alias-border-l2)', borderRadius: 9, background: 'var(--dsw-alias-bg-base)' } }),
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
      const { tax, labels, setL, knowledgeStr, warnings, busy, onSave, onClose } = props;
      function field(label, control) {
        return h('label', { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
          h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)' } }, label), control);
      }
      const subs = (tax.subtypes || {})[labels?.type] || [];
      return h('div', { onClick: onClose, style: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 3000 } },
        h('div', { onClick: (e) => e.stopPropagation(), style: { width: 560, maxWidth: '92vw', maxHeight: '88vh', overflow: 'auto', background: 'var(--dsw-alias-bg-layer-1)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 14, boxShadow: '0 12px 40px rgba(0,0,0,.3)' } },
          h('div', { style: { display: 'flex', alignItems: 'center', padding: '14px 18px', borderBottom: '1px solid var(--dsw-alias-border-l1)' } },
            h('span', { style: { fontWeight: 700, fontSize: 15 } }, '修改标签'),
            h('span', { style: { flex: 1 } }),
            h('button', { onClick: onClose, style: { border: 'none', background: 'transparent', cursor: 'pointer', fontSize: 18, color: 'var(--dsw-alias-label-secondary)' } }, '✕'),
          ),
          h('div', { style: { padding: 18 } },
            h('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px 14px' } },
              field(TEXT.type, h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: labels?.type || '', onChange: (e) => { setL('type', e.target.value); setL('subtypes', []); } },
                h('option', { value: '' }, '—'), (tax.types || []).map((t) => h('option', { key: t, value: t }, t)))),
              field(TEXT.subtype, h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: (labels?.subtypes || [])[0] || '', onChange: (e) => setL('subtypes', e.target.value ? [e.target.value] : []) },
                h('option', { value: '' }, '—'), subs.map((s) => h('option', { key: s, value: s }, s)))),
              field(TEXT.difficulty, h('select', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: labels?.difficulty || '', onChange: (e) => setL('difficulty', e.target.value) },
                h('option', { value: '' }, '—'), (tax.difficulties || []).map((d) => h('option', { key: d, value: d }, d)))),
              field(TEXT.sourceType, h('input', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: labels?.source_type || '', onChange: (e) => setL('source_type', e.target.value) })),
              field(TEXT.source, h('input', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: labels?.source || '', onChange: (e) => setL('source', e.target.value) })),
            ),
            h('div', { style: { marginTop: 12 } }, field(TEXT.knowledge,
              h(KnowledgePicker, { paths: labels?.knowledge || [], onChange: (arr) => setL('knowledge', arr) }))),
            h('div', { style: { marginTop: 12 } }, field(TEXT.categories,
              h('input', { className: 'wf-input', style: { ...T.input, width: '100%' }, value: (labels?.categories || []).join(', '), placeholder: '多值用逗号分隔', onChange: (e) => setL('categories', e.target.value.split(',').map((x) => x.trim()).filter(Boolean)) }))),
            (warnings || []).length ? h('div', { style: { ...T.hint, color: 'var(--dsw-alias-state-warn-primary)', marginTop: 10 } }, '提示：' + (warnings || []).join('；')) : null,
          ),
          h('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 10, padding: '12px 18px', borderTop: '1px solid var(--dsw-alias-border-l1)' } },
            h('button', { className: 'wf-btn', style: T.btn, onClick: onClose }, '取消'),
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: onSave, disabled: busy === 'save' }, busy === 'save' ? TEXT.saving : TEXT.saveLabels),
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

      const requestGate = React.useRef(null);
      if (!requestGate.current) requestGate.current = createRequestGate();
      const mounted = React.useRef(false);
      const load = React.useCallback(async () => {
        if (!mounted.current) return;
        const request = requestGate.current.begin();
        setErr('');
        try {
          const d = await jget('/batches/' + encodeURIComponent(batchName) + '/units/' + encodeURIComponent(unitId), request.signal);
          if (!request.current()) return;
          setDetail(d);
          setLabels(d.labels || {});
        } catch (e) {
          if (request.current()) setErr(String(e.message || e));
        }
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
      async function decide(f, status) {
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/decisions', {
            item: { finding_id: f.finding_id, status, note: f.resolution_note || '' },
          });
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
      function renderBlocks(blocks) {
        return (blocks || []).map((b, i) => {
          if (b.type === 'text') {
            const heading = isHeading(b.text);
            return h('div', {
              key: i,
              className: 'wf-line' + (heading ? ' wf-heading' : ''),
              style: { whiteSpace: 'pre-wrap', lineHeight: 1.85, fontSize: 13.5 },
              dangerouslySetInnerHTML: { __html: markLine(b.text) },
            });
          }
          if (b.type === 'image') return h(PreviewImage, {
            key: 'image:' + String(b.url || b.name || '') + ':' + i, url: b.url, name: b.name,
          });
          if (b.type === 'table') return h('div', { key: i, style: { overflowX: 'auto', margin: '8px 0' } },
            h('table', { className: 'wf-table' },
              h('tbody', null, (b.rows || []).map((row, ri) => {
                const head = isHeaderRow(row);
                return h('tr', { key: ri }, (row || []).map((cell, ci) => {
                  const props = { key: ci, dangerouslySetInnerHTML: { __html: markLine(cell) } };
                  return head ? h('th', props) : h('td', props);
                }));
              }))));
          return null;
        });
      }

      // 表单字段：上标签下控件，统一排版。
      function field(label, control) {
        return h('label', { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
          h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)' } }, label),
          control);
      }
      // 预览小字标签：灰色小标 + 值
      function plItem(k, v) {
        if (v === null || v === undefined || v === '') return null;
        return h('span', null,
          h('span', { style: { color: 'var(--dsw-alias-label-secondary)', opacity: .8, marginRight: 3 } }, k),
          h('span', { style: { color: 'var(--dsw-alias-label-primary)' } }, String(v)));
      }

      // 三轮整体状态标注（一审/二审/三审是否已执行）
      function roundStatusColor(status) {
        if (status === '已提供' || status === '已覆盖') return 'var(--dsw-alias-state-success-primary)';
        if (status === '未覆盖' || status === '未提供') return 'var(--dsw-alias-label-secondary)';
        return 'var(--dsw-alias-label-secondary)';
      }
      function roundsRow(rounds, renderBody) {
        if (!rounds || !rounds.length) return h('div', { style: T.hint }, '无审核轮次记录');
        return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 6 } },
          rounds.map((r) => h('div', { key: r.round_id, style: { fontSize: 12 } },
            h('span', { style: { fontWeight: 600, marginRight: 6 } }, (r.name || r.round_id) + '：'),
            h('span', { style: { color: roundStatusColor(r.status) } }, r.status || '未提供'),
            renderBody ? renderBody(r) : null,
          )));
      }
      // 单元级：每轮第一条判定的裁决与理由
      function renderRounds(comparison) {
        const rounds = (comparison && comparison.rounds) || [];
        return h('div', { style: { background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 8, padding: '8px 10px', marginBottom: 10 } },
          roundsRow(rounds, (r) => {
            const ch = (r.checks || [])[0];
            if (!ch) return null;
            const verdict = ch.verdict === 'ok' ? '无异常' : (ch.verdict === 'error' ? '有差异' : (ch.verdict === 'uncertain' ? '有疑点' : ch.verdict || ''));
            return h('div', { style: { ...T.hint, marginTop: 2, paddingLeft: 10 } },
              h('span', { style: { color: ch.verdict === 'ok' ? 'var(--dsw-alias-state-success-primary)' : 'var(--dsw-alias-state-warn-primary)' } }, verdict),
              ch.rationale ? (' — ' + String(ch.rationale).slice(0, 120)) : '');
          }),
        );
      }
      // 单条问题的三轮判断（值/差异）
      function renderFindingRounds(finding) {
        const comp = finding && finding.comparison;
        if (!comp || !comp.rounds) return null;
        return h('div', { style: { marginTop: 5, paddingLeft: 2 } },
          roundsRow(comp.rounds, (r) => {
            const chs = r.checks || [];
            if (!chs.length) return null;
            return h('div', { style: { ...T.hint, paddingLeft: 10, marginTop: 1 } },
              chs.map((ch) => {
                const diff = (ch.differences || [])[0];
                return h('div', { key: Math.random() },
                  h('span', { style: { color: ch.verdict === 'ok' ? 'var(--dsw-alias-state-success-primary)' : 'var(--dsw-alias-state-warn-primary)' } },
                    ch.verdict === 'ok' ? '无异常' : (ch.verdict === 'error' ? '有差异' : (ch.verdict === 'uncertain' ? '有疑点' : (ch.verdict || '')))),
                  ch.value !== undefined && ch.value !== null ? (' — 判为：' + (Array.isArray(ch.value) ? ch.value.join('；') : String(ch.value))) : '',
                  diff ? ('（原：' + (Array.isArray(diff.original) ? diff.original.join('；') : String(diff.original || '—')) + '）') : '',
                  ch.rationale ? (' · ' + String(ch.rationale).slice(0, 100)) : '');
              }));
          }));
      }

      // 知识点：多选（简化为逗号输入 + 快捷树）
      const knowledgeStr = (labels?.knowledge || []).join(', ');

      // ---- 中栏：题组列表（过滤 + 搜索 + 键盘高亮）----
      const filterTabs = [['all', '全部'], ['open', '待审'], ['blocked', '阻断'], ['unverified', '未核对']];
      const listCol = h('div', { style: { width: 236, flexShrink: 0, borderRight: '1px solid var(--dsw-alias-border-l1)', display: 'flex', flexDirection: 'column', minHeight: 0, background: 'var(--dsw-alias-bg-layer-1)' } },
        h('div', { style: { padding: '10px 10px 8px', borderBottom: '1px solid var(--dsw-alias-border-l1)' } },
          h('div', { style: { display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 } },
            filterTabs.map(([id, lbl]) => h('button', {
              key: id, onClick: () => setFilter(id),
              style: {
                padding: '3px 9px', borderRadius: 7, fontSize: 12, cursor: 'pointer',
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
        workCol = h('div', { style: T.empty }, '加载失败：' + err);
      } else if (!detail) {
        workCol = h('div', { style: T.empty }, TEXT.loading);
      } else {
        workCol = h('div', { style: { flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 } },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 10, padding: '10px 16px', borderBottom: '1px solid var(--dsw-alias-border-l1)', flexWrap: 'wrap' } },
            h('span', { style: { fontWeight: 700, fontSize: 15 } }, '第 ' + (detail.label || '') + ' 题'),
            detail.needs_human ? h('span', { style: T.badge('warn') }, '待审标签') : h('span', { style: T.badge('') }, '无待审'),
            h('span', { style: T.hint }, (detail.type || '') + ' · ' + (detail.numbers || []).length + ' 小题'),
            h('span', { style: { flex: 1 } }),
            h('button', { className: 'wf-btn', style: T.btn, onClick: load }, TEXT.reload),
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: () => setLabelDlg(true) }, '修改标签'),
          ),
          h('div', { className: 'wf-scroll', style: { ...T.split, padding: 16, overflow: 'auto' } },
            // 左：原卷预览 + 小题
            h('div', { style: { flex: 1.4, minWidth: 0 } },
              h('div', { style: T.card },
                h('div', { style: { display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 10 } },
                  h('span', { style: { fontSize: 13.5, fontWeight: 650 } }, TEXT.sourcePreview),
                  detail.preview_labels ? h('span', { style: { fontSize: 11.5, color: 'var(--dsw-alias-label-secondary)', display: 'flex', gap: 12, flexWrap: 'wrap' } },
                    plItem('来源', detail.preview_labels.source_type),
                    plItem('难度', detail.preview_labels.difficulty),
                    plItem('词数', detail.preview_labels.word_count),
                    plItem('知识点', (detail.preview_labels.knowledge || []).join('；') || '未标注'),
                  ) : null,
                ),
                (detail.audio || []).map((a, i) => h('audio', { key: i, controls: true, src: mediaUrl(a.url), style: { width: '100%', margin: '4px 0 8px' } })),
                h('div', { className: 'wf-paper' }, renderBlocks(detail.preview_blocks)),
              ),
              h('div', { style: T.card },
                h('div', { style: T.cardTitle }, TEXT.subquestions + '（' + subs.length + '）'),
                subs.length === 0 ? h('div', { style: T.hint }, TEXT.empty) : subs.map((s, i) =>
                  h('div', { key: i, style: { borderTop: i ? '1px solid var(--dsw-alias-border-l1)' : 'none', padding: '10px 0' } },
                    h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 } },
                      h('span', { style: { fontSize: 12, fontWeight: 700, color: '#fff', background: 'var(--dsw-alias-brand-primary)', borderRadius: 6, padding: '1px 7px' } }, s.number),
                      s.state ? h('span', { style: T.badge(s.state === 'ok' ? '' : 'warn') }, s.state) : null,
                    ),
                    s.stem ? h('div', { style: { whiteSpace: 'pre-wrap', marginBottom: 5 } }, s.stem) : null,
                    (s.options || []).length ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 3, marginBottom: 6 } },
                      (s.options || []).map((o, oi) => h('div', { key: oi, style: { fontSize: 13 } }, typeof o === 'object' ? ((o.label || '') + '. ' + (o.text || '')) : String(o)))) : null,
                    h('div', { style: { fontSize: 12.5 } }, h('b', null, TEXT.answer + '：'), h('span', { style: { color: 'var(--dsw-alias-state-success-primary)', fontWeight: 600 } }, s.answer || TEXT.empty)),
                  )),
              ),
            ),
            // 右：待人工处理（含一/二/三审意见）在上，格式疑点在下
            h('div', { style: { flex: 1, minWidth: 320 } },
              h('div', { style: T.card },
                h('div', { style: T.cardTitle }, TEXT.findings + '（' + findings.length + '）', h('span', { style: { flex: 1 } }), h('span', { style: { ...T.hint, fontWeight: 400 } }, '只含一/二/三审结论')),
                renderRounds(detail.comparison),
                findings.length === 0 ? h('div', { style: { ...T.hint, marginTop: 6 } }, TEXT.noFindings) : findings.map((f) =>
                  h('div', { key: f.finding_id, className: 'wf-find' },
                    h('div', { style: { fontSize: 12.5, whiteSpace: 'pre-wrap', marginBottom: 3 } }, f.question || f.message || f.field || '（待人工确认）'),
                    f.current ? h('div', { style: T.hint }, '当前：' + (typeof f.current === 'string' ? f.current : JSON.stringify(f.current))) : null,
                    renderFindingRounds(f),
                    h('div', { style: { marginTop: 8, display: 'flex', gap: 6 } },
                      h('button', { className: 'wf-btn', style: T.btn, onClick: () => decide(f, 'accepted') }, TEXT.accept),
                      h('button', { className: 'wf-btn', style: T.btn, onClick: () => decide(f, 'fixed') }, TEXT.fixed),
                      h('button', { className: 'wf-btn-danger', style: T.btnDanger, onClick: () => decide(f, 'rejected') }, TEXT.reject),
                    ),
                  )),
              ),
              fmtFindings.length ? h('div', { style: T.card },
                h('div', { style: T.cardTitle }, '格式/结构疑点（' + fmtFindings.length + '）'),
                fmtFindings.map((f) => h('div', { key: f.finding_id, className: 'wf-find', style: { fontSize: 12.5 } },
                  (f.question || f.message || '') + ' ',
                  h('button', { className: 'wf-btn', style: T.btn, onClick: () => decide(f, 'fixed') }, TEXT.fixed),
                )),
              ) : null,
            ),
          ),
          labelDlg ? h(LabelModal, {
            tax, labels, setL, knowledgeStr, warnings: detail.label_warnings || [],
            busy, onSave: async () => { await saveLabels(); setLabelDlg(false); }, onClose: () => setLabelDlg(false),
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

      const loadBatches = React.useCallback(async () => {
        try {
          const d = await jget('/batches');
          setBatches(d.batches || []);
        } catch (e) { setErr(String(e.message || e)); }
      }, []);
      React.useEffect(() => { loadBatches(); }, [loadBatches]);

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
            h('span', { style: { fontWeight: 650, fontSize: 14 } }, (active.overview?.paper?.title || active.name)),
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
            h('span', { style: { fontWeight: 650, fontSize: 14 } }, TEXT.review),
            h('span', { style: { flex: 1 } }),
            h('button', { className: 'wf-btn', style: T.btn, onClick: loadBatches }, TEXT.reload),
          ),
          err ? h('div', { style: { padding: 14, ...T.statusErr } }, err) : null,
          h('div', { className: 'wf-scroll', style: T.scroll },
            h('div', { style: { ...T.hint, marginBottom: 12 } }, '选择一份试卷进入审核；也可「继续处理」或「删除批次」。'),
            list.length === 0 ? h('div', { style: T.empty }, TEXT.noPaper) : list.map((b) => h('div', { key: b.name, className: 'wf-card-hover', style: T.cardClickable, onClick: () => openBatch(b.name) },
              h('div', { style: { display: 'flex', alignItems: 'flex-start', gap: 12 } },
                h('div', { style: { flex: 1, minWidth: 0 } },
                  h('div', { style: { fontWeight: 650, fontSize: 14.5, marginBottom: 3 } }, b.paper?.title || b.name),
                  h('div', { style: T.hint }, '批次：' + b.name + ' · ' + (b.stage?.hint || '')),
                ),
                h('div', { style: { display: 'flex', gap: 6, flexShrink: 0 } },
                  h('div', { className: 'wf-stat' }, h('b', null, (b.unit_total ?? 0)), h('span', null, '题组')),
                  h('div', { className: 'wf-stat' }, h('b', null, (b.question_total ?? 0)), h('span', null, '小题')),
                  h('div', { className: 'wf-stat' + ((b.needs_human ?? 0) > 0 ? ' warn' : '') }, h('b', null, (b.needs_human ?? 0)), h('span', null, '待审')),
                ),
              ),
              h('div', { style: { marginTop: 12, display: 'flex', gap: 8 } },
                h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: (e) => { e.stopPropagation(); openBatch(b.name); } }, '进入审核'),
                h('button', { className: 'wf-btn', style: T.btn, onClick: (e) => { e.stopPropagation(); doAdvance(b.name); }, disabled: advBusy === b.name }, TEXT.advance),
                h('button', { className: 'wf-btn-danger', style: T.btnDanger, onClick: (e) => { e.stopPropagation(); doArchive(b.name); } }, TEXT.archive),
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
      const rounds = [['labels', '一审'], ['knowledge', '二审'], ['sample', '三审（默认 20% 抽样）']];

      async function writeEffective() { try { await fetch('/wf-plugin/materialize', { method: 'POST', headers: { 'content-type': 'application/json' }, body: '{}' }); setStatus('tested'); } catch (e) { setErr(String(e.message || e)); } }

      return h('div', { className: 'wf-scroll', style: { flex: 1, overflow: 'auto', padding: '24px' } },
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
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '默认三轮都「跟随默认」；可分别指定一审/二审/三审模型。'),
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
      const tabBtn = (id, label) => h('button', { key: id, onClick: () => setTab(id), style: T.tab(tab === id) }, label);
      return h('div', { className: 'wf-root', style: T.root },
        h('div', { style: T.tabbar },
          tabBtn('review', TEXT.review),
          tabBtn('upload', TEXT.upload),
          tabBtn('settings', TEXT.settings),
          h('span', { style: { flex: 1 } }),
          props.closePanel ? h('button', { className: 'wf-btn', onClick: props.closePanel, style: { ...T.btn, padding: '4px 12px' } }, TEXT.close) : null,
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
      return h('span', { title: TEXT.title, 'aria-label': TEXT.title, style: { fontSize: 18 } }, '📝');
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
    exports.__panel = { WfPanel, UploadView, ReviewView, WorkbenchTab, SettingsTab };
    return module.exports;
  },
});
