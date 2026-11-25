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
            detail: hasOcr ? ('已配置 OCR 服务（' + (ocrCfg.endpoint || '') + '）。') : '未填 OCR Token。扫描件/图片型试卷和上传的 PDF（整卷识别产出 Word 版面参考）都需要 OCR；只传纯 Word 卷可跳过。',
            action: 'goto-ocr' },
          { id: 'model', title: '大模型', required: true, ready: hasModel,
            detail: hasModel ? ((router.default && router.default.model ? '默认模型：' : '跟随 Harness 当前模型：') + effectiveModel.model + (router.default && router.default.model ? '' : '（无需填写 API Key，凭据由 Harness 提供）')) : '未检测到可用模型。请在「模型与设置 → 模型设置」选择本机 Harness 已配置的模型；插件不需要额外填写 API Key。',
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
        h('p', { className: 'wf-settings-description' }, '上传使用具备暂存权限的 API Key，不需要题库账号密码。'),
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
            field('account', '上传账号（可选）', { type: 'text', placeholder: '例如 001 或主管理员账号', autoComplete: 'off', spellCheck: false }, '旧版人工会话配置，不参与上传暂存。'),
            h('label', { style: { display: 'grid', gap: 6, marginBottom: 0 } },
              h('span', { style: T.fieldLabel }, '登录密码（可选）'),
              h('input', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, type: 'password', value: loginPassword, autoComplete: 'new-password', spellCheck: false, disabled: !!busy, placeholder: config.has_password ? '已保存登录密码；留空保持不变' : '填写该账号的后台登录密码', onChange: (event) => { setLoginPassword(event.target.value); setDirty(true); setNotice(null); } }),
              h('span', { style: T.hint }, config.has_password ? '密码已保存，不回显；仅保存在本机 0600 私有文件。' : '仅保留旧版人工会话配置，不用于上传。'))),
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
          h('div', { style: T.hint }, '暂存权限以 API Key 的能力检查结果为准；确认、审核和发布仍在题库工作台完成。'),
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
        const list = await (await window.fetch('/wf-plugin/api/batches?lite=1', { headers: { accept: 'application/json' } })).json();
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

    function SettingsTab(props = {}) {
      const React = require('react');
      const h = React.createElement;
      // 默认停在「初始化引导」：新装用户第一眼就看到要装/要配什么。
      const [activeSection, setActiveSection] = React.useState('onboarding');
      const [visited, setVisited] = React.useState({ onboarding: true });
      const sections = [['onboarding', '初始化引导'], ['models', '模型设置'], ['api', '上传设置'], ['ocr', 'OCR 识别'], ['audio', '听力切分']];
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
        let alive = true;
        (async () => {
          try {
            const [mRes, cRes] = await Promise.all([
              window.fetch('/wf-plugin/models', { headers: { accept: 'application/json' } }),
              window.fetch('/wf-plugin/config', { headers: { accept: 'application/json' } }),
            ]);
            const m = await mRes.json().catch(() => null);
            const cfg = await cRes.json().catch(() => null);
            if (!alive) return;
            const list = (m && Array.isArray(m.models)) ? m.models.filter((x) => x && typeof x === 'object') : [];
            setModels(list);
            const rawRouter = (cfg && cfg.router && typeof cfg.router === 'object' && !Array.isArray(cfg.router)) ? cfg.router : {};
            const harnessDef = (cfg && cfg.harnessDefault && typeof cfg.harnessDefault === 'object')
              ? cfg.harnessDefault
              : ((rawRouter.harnessDefault && typeof rawRouter.harnessDefault === 'object') ? rawRouter.harnessDefault : null);
            setRouter(harnessDef && !rawRouter.harnessDefault ? { ...rawRouter, harnessDefault: harnessDef } : rawRouter);
            if (!mRes.ok || !cRes.ok) {
              setErr(String((m && m.error) || (cfg && cfg.error) || '读取模型配置失败'));
            }
          } catch (e) {
            if (alive) {
              setRouter((cur) => cur || {});
              setErr(String((e && e.message) || e));
            }
          }
        })();
        return () => { alive = false; };
      }, []);

      const safeModels = Array.isArray(models) ? models : [];
      const byKey = React.useMemo(() => { const map = {}; for (const m of safeModels) { if (m && typeof m === 'object') map[modelKey(m)] = m; } return map; }, [safeModels]);
      const defaultSel = router?.default?.model ? router.default : (router?.harnessDefault || null);

      async function persist(next) {
        const previous = router;
        const harnessDef = previous?.harnessDefault || next?.harnessDefault || null;
        const { harnessDefault: _omitHarnessDefault, ...payload } = (next && typeof next === 'object') ? next : {};
        setRouter(harnessDef ? { ...payload, harnessDefault: harnessDef } : payload); setStatus('saving'); setErr('');
        try {
          const r = await window.fetch('/wf-plugin/router', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) });
          if (!r.ok) throw new Error(await r.text());
          const res = await r.json().catch(() => null);
          const savedRouter = (res && res.router && typeof res.router === 'object' && !Array.isArray(res.router)) ? res.router : payload;
          setRouter(harnessDef && !savedRouter.harnessDefault ? { ...savedRouter, harnessDefault: harnessDef } : savedRouter);
          setStatus('saved');
          setTimeout(() => setStatus((s) => s === 'saved' ? 'idle' : s), 1500);
        } catch (e) { setRouter(previous); setErr(String((e && e.message) || e)); setStatus('idle'); }
      }
      const inferProfile = () => 'wf-deepseek-fast';
      const modelId = (entry) => (entry && entry.provider && entry.model ? entry.provider + '|' + entry.model : '');
      const isPlainMap = (value) => !!value && typeof value === 'object' && !Array.isArray(value);
      // 换全局默认时，凡是仍沿用旧默认值的子 Agent / 审核轮次也必须一起跟随，
      // 否则它们会保留旧模型，界面上看起来“下面的没跟着改”。
      // 损坏配置（数组/字符串等非对象）保持原样、不在此处改写，交由读取处的 || {} 兜底。
      function setDefault(sel) {
        const previous = router?.default || {};
        const nextDefault = sel && sel.provider && sel.model ? { ...previous, ...sel, profile: inferProfile() } : {};
        const changed = modelId(previous) !== modelId(nextDefault);
        const next = { ...router, default: nextDefault };
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
      function clearStage(stage) {
        const roles = { ...(router?.roles || {}) };
        delete roles[stage];
        if (stage === 'types') delete roles.preassemble;
        const next = { ...router, roles };
        if (isPlainMap(router?.stages) && (stage in router.stages || (stage === 'types' && 'preassemble' in router.stages))) {
          const stages = { ...router.stages };
          delete stages[stage];
          if (stage === 'types') delete stages.preassemble;
          next.stages = stages;
        }
        persist(next);
      }
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
          safeModels.map((m) => h('option', { key: modelKey(m), value: modelKey(m) }, m.label + ' · ' + m.provider)));
      }

      const stages = [['answers', '答案提取'], ['audio', '听力切分'], ['types', '题型判定'], ['explanations', '解析与大意'], ['knowledge', '知识点标注'], ['assemble', '填充汇总'], ['review', '审核'], ['operations', 'AI运维']];
      const supplementRoles = [['ai_summary', '文章大意'], ['ai_explanation', '答案解析']];
      const rounds = [['labels', '一审']];

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
          h('h2', { id: 'wf-model-settings-heading', style: T.cardTitle }, '模型设置'),
          h('p', { className: 'wf-settings-description' }, '默认跟随本机 Harness 当前模型，无需填写 API Key；也可显式指定全局默认、各处理阶段、文章大意/答案解析与一审审核模型。改动自动保存。'),
          err ? h('div', { role: 'alert', style: { ...T.statusErr, marginBottom: 8 } }, err) : null,
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.defaultModel),
            safeModels.length === 0 ? h('div', { style: T.hint }, '正在读取模型…') :
              h('div', { style: { display: 'grid', gap: 8, minWidth: 0 } },
                h('select', { className: 'wf-input', style: { ...T.input, width: '100%', minWidth: 0 }, disabled: !router || status === 'saving' || status === 'testing', value: (router?.default?.model ? modelKey(router.default) : ''), onChange: (e) => { if (e.target.value === '') { setDefault(null); return; } const m = byKey[e.target.value]; if (m) setDefault({ provider: m.provider, model: m.model }); } },
                  router?.harnessDefault?.model ? h('option', { value: '' }, '跟随本机 Harness（' + (byKey[modelKey(router.harnessDefault)]?.label || router.harnessDefault.model) + '）') : null,
                  safeModels.map((m) => h('option', { key: modelKey(m), value: modelKey(m) }, m.label + ' · ' + m.provider))),
                h('span', { style: T.hint }, '当前：' + (router?.default?.model ? '（插件默认）' : '（跟随 Harness）') + (byKey[router?.default?.model ? modelKey(router.default) : (router?.harnessDefault ? modelKey(router.harnessDefault) : '')]?.label || router?.default?.model || router?.harnessDefault?.model || '—')),
        h('div', { style: { ...T.hint, marginTop: 6 } }, '修改全局默认时，仍跟随默认的子 Agent 与审核轮次会一起切换；已单独指定的保持不变。')),
          ),
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.stageModels),
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '内置 8 个子 Agent：前置组装题型判定（共用「题型判定」）、答案提取、听力切段（常规走本地 WhisperX/ffmpeg，仅兼容分支调模型）、题型兼容校验、解析抄录、知识点标注、装配、审核；AI运维为附加能力。材料分类/元信息/结构归类跟随默认模型。默认全部「跟随默认」，需要单独指定时才选。'),
            h('div', { className: 'wf-model-fields', style: { display: 'grid', gridTemplateColumns: '120px minmax(0,1fr)', gap: '10px 14px', alignItems: 'center' } },
              stages.map(([s, lbl]) => h('div', { key: s, style: { display: 'contents' } },
                h('span', { style: T.label }, lbl),
                h(ModelSelect, { value: router?.roles?.[s] || router?.stages?.[s] || (s === 'types' ? (router?.roles?.preassemble || router?.stages?.preassemble) : null) || null, onChange: (sel) => setStage(s, sel), onClear: () => clearStage(s) })))),
          ),
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.aiSupplementModels),
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '用于工作台手动点击「AI文章大意」与「AI解析」的独立生成任务，不与试卷上传流程冲突。默认跟随全局默认模型，也可分别指定更快的模型。'),
            h('div', { className: 'wf-model-fields', style: { display: 'grid', gridTemplateColumns: '120px minmax(0,1fr)', gap: '10px 14px', alignItems: 'center' } },
              supplementRoles.map(([s, lbl]) => h('div', { key: s, style: { display: 'contents' } },
                h('span', { style: T.label }, lbl),
                h(ModelSelect, { value: router?.roles?.[s] || router?.stages?.ai_supplement || null, onChange: (sel) => setStage(s, sel), onClear: () => clearStage(s) })))),
          ),
          h('div', { style: { ...T.card, boxShadow: 'none' } },
            h('h3', { style: T.cardTitle }, TEXT.reviewModels),
            h('div', { style: { ...T.hint, marginBottom: 10 } }, '默认「跟随默认」；可单独指定一审模型（已取消二审抽审，改为自动「标签合并」）。'),
            h('div', { className: 'wf-model-fields', style: { display: 'grid', gridTemplateColumns: '120px minmax(0,1fr)', gap: '10px 14px', alignItems: 'center' } },
              rounds.map(([r, lbl]) => h('div', { key: r, style: { display: 'contents' } },
                h('span', { style: T.label }, lbl),
                h(ModelSelect, { value: router?.review?.rounds?.[r] || null, onChange: (sel) => setRound(r, sel), onClear: () => clearRound(r) })))),
            h('div', { style: { marginTop: 14, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' } },
              h('button', { className: 'wf-btn', style: T.btn, disabled: status === 'saving' || status === 'testing' || !router, onClick: writeEffective }, status === 'testing' ? '正在写入…' : TEXT.writeEffective),
              h(BackendRestartControl, { isActive: (!props || props.isActive !== false) && activeSection === 'models' }),
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

