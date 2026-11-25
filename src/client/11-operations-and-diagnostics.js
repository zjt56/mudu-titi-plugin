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
      const pipelineProgress = item.resolution_source === 'pipeline_progress'
        || item.resolved_by_operations === false;
      const labels = {
        detected: '已触发', unclaimed: '已触发', idle: '已触发', pending: '已触发', queued: '已触发',
        investigating: '调查中', investigation: '调查中', diagnosing: '调查中',
        repairing: '修复中', repair: '修复中', verifying: '复验中', verification: '复验中',
        recovered: '已恢复', resolved: pipelineProgress ? '流水线已复验' : '已恢复', removed: '已移出排队区', waiting_external: '待外部条件', awaiting_external: '待外部条件',
        awaiting_execution: '排队', needs_deep_repair: '自动升级修复', needs_human: '自动升级修复', recovery_queued: '自动升级修复', waiting_evidence: '等待新证据', claimed: '调查中', working: '调查中',
        blocked: '待外部条件', cancelled: '已取消', canceled: '已取消',
      };
      let label = !incident ? '未接手' : (labels[raw] || '状态待确认');
      if (raw === 'claimed' || raw === 'working') {
        if (latest.action === 'apply_repair' || latest.action === 'preview_repair') label = '修复中';
        if (latest.action === 'verify') label = '复验中';
      }
      if ((raw === 'recovered' || raw === 'resolved') && pipelineProgress) label = '流水线已复验';
      else if ((raw === 'recovered' || raw === 'resolved') && !verified) label = '复验待确认';
      const unknown = (context.receipts || []).filter(r => r && r.status === 'unknown').length;
      const now = context.now || Date.now();
      const hostAttempts = ((context.host || {}).attempts || []).filter(r =>
        r.batch_name === context.batchName && r.incident_id === item.incident_id);
      const hostAttempt = hostAttempts.slice().reverse().find(r => r.active) || hostAttempts[hostAttempts.length - 1];
      if (raw === 'claimed' || raw === 'working') {
        if (['apply_repair', 'preview_repair', 'regenerate_units', 'request_code_repair', 'code_repair', 'install_code_repair'].includes(business.action)) label = '修复中';
        if (business.action === 'verify') label = '复验中';
        if (hostAttempt && hostAttempt.active) {
          const phases = { starting: '已触发', investigating: '调查中', diagnosing: '调查中', waiting_repair: '等待修复锁', repairing: '修复中', verifying: '复验中', awaiting_execution: '等待原流程续跑',
            awaiting_restart: '等待重启复验', session_failed: '会话失败', backend_disconnected: '连接中断',
            lease_lost: '租约失效', budget_exceeded: '本次预算已用尽', outcome_unknown: '待核对动作回执',
            waiting_worker: '等待批次执行锁释放', release_deferred: '释放待接续' };
          label = phases[hostAttempt.state] || label;
        }
        if (item.lease_expires_at && item.lease_expires_at * 1000 <= now) label = '接手已过期';
      }
      const mode = hostAttempt && hostAttempt.mode || item.mode;
      if (['claimed', 'working', 'investigating', 'diagnosing'].includes(raw) && mode === 'investigation' && ['调查中', '修复中', '复验中'].includes(label)) label = '调查中';
      if (['claimed', 'working'].includes(raw) && mode === 'repair') {
        const phases = { model_wait: '准备修复', evidence_read: '定位代码', code_write: '修改中',
          code_test: '测试中', code_prepare: '准备安装', code_install: '安装中',
          verifying: '复验中', no_substantive_progress: '修复无进展' };
        const phase = hostAttempt && hostAttempt.phase || (item.repair_progress || {}).phase;
        if (phases[phase] && ['调查中', '准备修复', '修复中', '复验中'].includes(label)) label = phases[phase];
        else if (label === '调查中') label = '准备修复';
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
    function operationsSummary(host, record, options = {}) {
      const batchName = options.batchName || '';
      const current = record && record.batchName === batchName ? record : null;
      const attempts = Array.isArray(host.attempts) ? host.attempts : [];
      const active = attempts.slice().reverse().find((a) => a && a.active && (!batchName || a.batch_name === batchName));
      const incident = current && current.incident || (!batchName && active ? { incident_id: active.incident_id, status: 'claimed', mode: active.mode } : null);
      const selectedBatch = batchName || active && active.batch_name || '';
      const view = operationsView(incident, { host, batchName: selectedBatch, receipts: current && current.receipts || [], now: options.now });
      const scope = selectedBatch ? '批次「' + selectedBatch + '」（workflows/' + selectedBatch + '）' : '';
      const sentence = (text) => {
        const detail = operationsText(text).replace(/\s+/g, ' ').trim();
        return detail ? [scope, detail].filter(Boolean).join('：') : '';
      };
      const fail = (label, text) => ({ label, hint: sentence(text), error: true });
      if (!host.loaded) return options.hostError ? fail('服务失联', options.hostError) : { label: '状态读取中', hint: '', error: false };
      if (!host.enabled) return { label: '已关闭', hint: '', error: false };
      if (!host.available) return fail('服务不可用', options.hostError || host.configError || 'Harness 运维服务不可用');
      if (options.hostError || host.configError) return fail('配置受阻', options.hostError || host.configError);
      if (host.lastPollError) return fail('扫描异常', host.lastPollError);
      if (host.running === false || host.loopAlive === false) return fail('监测已停止', '运维扫描循环未运行');
      const scannedAt = Date.parse(host.lastPollAt), now = options.now || Date.now();
      if (host.pollStale || Number.isFinite(scannedAt) && now - scannedAt > 20000) return fail('服务失联', '超过 20 秒未成功扫描，正在重试');
      if (options.recordError) return fail('记录不可用', '运维记录读取失败：' + options.recordError);
      if (batchName && !current) return { label: '记录读取中', hint: '', error: false };
      if (view.error) return fail(view.label, view.error);
      if (view.unknown) return fail(view.label, view.unknown + ' 个动作结果未确认，等待核对回执');
      if (incident) {
        const triggerLabels = {
          stale_running: '检测到僵死运行阶段',
          repeated_transient: '连续瞬态失败自动升级',
          manual_takeover: '人工触发接手',
          first_blocker: '首次阻断自动接手',
          failed_self_heal: '自愈失败升级接手',
        };
        const parts = [];
        const trig = triggerLabels[incident.trigger];
        if (trig) parts.push(trig);
        const waitSec = incident.status === 'queued' && Number.isFinite(Number(incident.not_before))
          ? Math.ceil(Number(incident.not_before) - now / 1000) : 0;
        if (waitSec > 0) parts.push('约 ' + waitSec + ' 秒后自动认领');
        const baseDetail = operationsText(incident.detail || incident.reason || '').replace(/\s+/g, ' ').trim();
        if (baseDetail) parts.push(baseDetail);
        return { label: view.label, hint: sentence(parts.join('；')), error: false };
      }
      if (!batchName && host.incidentCount > 0) return { label: '已触发', hint: host.incidentCount + ' 个故障待接手', error: false };
      const monitoring = host.running === true && host.loopAlive === true && Number.isFinite(scannedAt);
      return { label: monitoring ? '持续监测中' : '监测待确认', hint: '', error: false };
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
    function operationsStatusTone(summary) {
      if (!summary) return 'idle';
      if (summary.error) return 'error';
      const label = String(summary.label || '');
      if (['持续监测中', '已恢复', '流水线已复验', '已移出排队区'].includes(label)) return 'ok';
      if (['调查中', '修复中', '复验中', '准备修复', '定位代码', '修改中', '测试中', '准备安装', '安装中', '自动升级修复', '已触发'].includes(label)) return 'active';
      if (['等待新证据', '等待原流程续跑', '等待修复锁', '等待重启复验', '等待批次执行锁释放', '待外部条件', '冷却等待', '排队', '待核对动作回执', '复验待确认', '释放待接续', '修复无进展'].includes(label)) return 'waiting';
      return 'idle';
    }
    function operationsFeedItems(host, record, options = {}) {
      if (options.recordError || (host && host.enabled === false)) return [];
      const batchName = options.batchName || '';
      const current = record && (!batchName || record.batchName === batchName) ? record : null;
      const incident = current && current.incident && typeof current.incident === 'object' ? current.incident : null;
      const attempts = Array.isArray(host && host.attempts) ? host.attempts : [];
      const activeAttempt = attempts.slice().reverse().find((a) => a && a.active && (!batchName || a.batch_name === batchName));
      const fmtTime = (value) => {
        if (!value) return '';
        const d = typeof value === 'number' ? new Date(value > 1e12 ? value : value * 1000) : new Date(value);
        if (isNaN(d.getTime())) return '';
        const p = (n) => String(n).padStart(2, '0');
        return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
      };
      const actionLabels = {
        claim: '接手处理',
        diagnose: '诊断分析',
        read_evidence: '读取证据',
        recover: '恢复尝试',
        preview_repair: '预演修复',
        apply_repair: '应用修复',
        regenerate_units: '局部重生成',
        request_code_repair: '准备修复区',
        code_repair: '代码修复',
        install_code_repair: '安装修复',
        verify: '复验检查',
        resume: '触发续跑',
        finish: '完成本轮',
        unfreeze: '解除冻结',
        reconcile: '核对回执',
        recovery: '恢复调度',
      };
      const phaseLabels = {
        model_wait: '等待模型响应',
        evidence_read: '正在定位相关代码',
        code_write: '正在修改代码',
        code_test: '正在运行回归测试',
        code_prepare: '正在准备安装补丁',
        code_install: '正在安装代码修复',
        verifying: '正在复验检查',
        no_substantive_progress: '本轮未产生实质进展',
      };
      const items = [];
      const seen = new Set();
      const push = (time, tag, text) => {
        const clean = operationsText(text).replace(/\s+/g, ' ').trim();
        if (!clean) return;
        const key = tag + '|' + clean;
        if (seen.has(key)) return;
        seen.add(key);
        items.push({ time: fmtTime(time), tag, text: clean });
      };
      if (activeAttempt) {
        const phaseText = phaseLabels[activeAttempt.phase] || '';
        const detailText = operationsText(activeAttempt.detail || activeAttempt.summary || '');
        const liveText = [phaseText, detailText].filter(Boolean).join('：');
        if (liveText) push(activeAttempt.updated_at || activeAttempt.started_at, '当前动作', liveText);
      }
      if (incident && incident.repair_progress && typeof incident.repair_progress === 'object') {
        const rp = incident.repair_progress;
        const rpText = [phaseLabels[rp.phase] || operationsText(rp.phase), operationsText(rp.operation)].filter(Boolean).join(' · ');
        if (rpText) push(rp.at, '修复进度', rpText);
      }
      if (incident && incident.recovery && typeof incident.recovery === 'object') {
        const rec = incident.recovery;
        const recText = [operationsText(rec.reason), operationsText(rec.next_action)].filter(Boolean).join(' · ');
        if (recText) push(rec.at, '恢复调度', recText);
      }
      const events = incident && Array.isArray(incident.events) ? incident.events.filter((e) => e && typeof e === 'object') : [];
      const substantive = events.filter((e) => !['heartbeat', 'progress'].includes(e.action)).slice(-20).reverse();
      for (const ev of substantive) {
        const actionKey = operationsText(ev.action || ev.type || ev.event || ev.kind);
        const tag = actionLabels[actionKey] || actionKey || '处理动态';
        const outcome = operationsText(ev.outcome || ev.result || ev.message);
        const detail = operationsText(ev.detail || ev.summary);
        const body = [detail, outcome && outcome !== 'completed' ? outcome : ''].filter(Boolean).join(' · ') || outcome;
        push(ev.timestamp || ev.created_at || ev.at || ev.time, tag, body);
      }
      const findings = incident && Array.isArray(incident.findings) ? incident.findings.filter((f) => f && typeof f === 'object').slice(-5).reverse() : [];
      for (const f of findings) {
        push(f.at || f.timestamp, '诊断发现', f.summary || f.detail || '');
      }
      return items;
    }
    function OperationsPanel({ batchName, isActive = true }) {
      const React = require('react'), h = React.createElement;
      const [host, setHost] = React.useState({ enabled: true, available: false, loaded: false });
      const [record, setRecord] = React.useState(null);
      const [hostError, setHostError] = React.useState('');
      const [recordError, setRecordError] = React.useState('');
      const [busy, setBusy] = React.useState(false);
      const [refresh, setRefresh] = React.useState(0);
      const [showDetail, setShowDetail] = React.useState(false);
      const [detailData, setDetailData] = React.useState(null);
      const generation = React.useRef(0);
      const saving = React.useRef(false);
      const configVersion = React.useRef(0);
      React.useEffect(() => {
        if (isActive === false) return undefined;
        const seq = ++generation.current;
        const controller = new AbortController();
        let timer;
        setRecord(null); setRecordError('');
        async function readStatus() {
          if (typeof document !== 'undefined' && document.hidden) {
            if (seq === generation.current) timer = setTimeout(readStatus, 3000);
            return;
          }
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
        const onVisible = () => {
          if (seq === generation.current && (typeof document === 'undefined' || !document.hidden)) {
            clearTimeout(timer);
            readStatus();
          }
        };
        if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
          document.addEventListener('visibilitychange', onVisible);
        }
        return () => {
          generation.current++; controller.abort(); clearTimeout(timer);
          if (typeof document !== 'undefined' && typeof document.removeEventListener === 'function') {
            document.removeEventListener('visibilitychange', onVisible);
          }
        };
      }, [batchName, refresh, isActive]);
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
      const summary = operationsSummary(host, record, { batchName, hostError, recordError });
      const tone = operationsStatusTone(summary);
      const incident = record && record.incident && typeof record.incident === 'object' ? record.incident : null;
      const feedItems = operationsFeedItems(host, record, { batchName, recordError });
      const isBatchClaimed = Boolean(
        (incident && ['claimed', 'investigating', 'repairing', 'verifying'].includes(incident.status))
        || ['调查中', '修复中', '维护中', '诊断中', '验证中'].includes(summary.label)
        || (Array.isArray(host.attempts) && host.attempts.some((a) => a && a.active && a.batch_name === batchName))
      );
      const unknownCount = Array.isArray(record && record.receipts)
        ? record.receipts.filter((r) => r && r.status === 'unknown').length : 0;
      const canReconcileUnknown = Boolean(batchName && unknownCount > 0);
      const canTakeover = Boolean(batchName && host.enabled && host.loaded && !isBatchClaimed && !canReconcileUnknown);
      const canUnfreeze = Boolean(batchName && !canReconcileUnknown && incident && ['waiting_evidence', 'waiting_external'].includes(incident.status));
      const hasDetail = Boolean(incident && ((Array.isArray(incident.findings) && incident.findings.length > 0) || incident.recovery || incident.code_repair || canUnfreeze || canReconcileUnknown));
      async function reconcileUnknown() {
        if (!batchName || busy) return;
        setBusy(true); setRecordError('');
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/operations/reconcile-unknown', { note: 'ui_manual_reconcile' });
          setRefresh((n) => n + 1);
        } catch (e) {
          setRecordError('解除隔离失败：' + operationsText(e.message));
        } finally {
          setBusy(false);
        }
      }
      async function takeover() {
        if (!batchName || busy) return;
        setBusy(true); setRecordError('');
        try {
          const res = await jpost('/batches/' + encodeURIComponent(batchName) + '/operations/takeover', {});
          if (res && res.triggered === false && res.reason) {
            setRecordError(operationsText(res.reason));
          }
          setRefresh((n) => n + 1);
        } catch (e) {
          setRecordError('立即接手失败：' + operationsText(e.message));
        } finally {
          setBusy(false);
        }
      }
      async function unfreeze() {
        if (!batchName || busy) return;
        setBusy(true); setRecordError('');
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/operations/unfreeze', {});
          setRefresh((n) => n + 1);
        } catch (e) {
          setRecordError('解除冻结失败：' + operationsText(e.message));
        } finally {
          setBusy(false);
        }
      }
      async function openDetail() {
        const next = !showDetail;
        if (typeof setShowDetail === 'function') setShowDetail(next);
        if (next && batchName) {
          try {
            const [perf, diff] = await Promise.all([
              jget('/batches/' + encodeURIComponent(batchName) + '/operations/performance').catch(() => null),
              jget('/batches/' + encodeURIComponent(batchName) + '/operations/diff').catch(() => null),
            ]);
            if (typeof setDetailData === 'function') setDetailData({ perf, diff });
          } catch (_) {}
        }
      }
      const emptyFeedText = !host.enabled
        ? '开启后将自动监测并实时显示处理动态。'
        : (summary.label === '持续监测中' ? '当前运行平稳，处理动态将实时显示在这里。' : '实时处理动态将显示在这里。');
      return h('section', {
        'aria-label': 'AI运维',
        className: 'wf-operations-card',
        style: {
          marginTop: 0,
          marginBottom: 0,
          paddingTop: 8,
          paddingRight: 14,
          paddingBottom: 10,
          paddingLeft: 14,
          height: 176,
          minWidth: 0,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          border: '1px solid var(--dsw-alias-border-l1)',
          borderRadius: 12,
          background: 'var(--dsw-alias-bg-layer-1)',
          boxShadow: '0 1px 2px rgba(16,24,40,.04), 0 8px 24px rgba(16,24,40,.035)',
        },
      },
        h('div', { className: 'wf-operations-toolbar', style: { display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'nowrap', minWidth: 0, flexShrink: 0 } },
          h('strong', { style: { ...T.sectionTitle, flexShrink: 0, whiteSpace: 'nowrap' } }, 'AI运维'),
          h('span', { className: 'wf-operations-status', 'data-tone': tone, role: 'status', 'aria-live': 'polite', style: { marginRight: 'auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' } }, summary.label),
          canReconcileUnknown ? h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: reconcileUnknown }, '解除隔离') : null,
          canTakeover ? h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: takeover }, '立即接手') : null,
          canUnfreeze ? h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: unfreeze }, '解除冻结') : null,
          hasDetail ? h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: openDetail }, showDetail ? '收起详情' : '详情') : null,
          h('button', { type: 'button', className: 'wf-btn', role: 'switch', 'aria-label': 'AI运维', 'aria-checked': host.enabled, disabled: busy || !host.loaded, style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, onClick: toggle }, busy ? '保存中…' : host.enabled ? '关闭运维' : '启动运维'),
          h('button', { type: 'button', className: 'wf-btn', style: { ...T.miniBtn, flexShrink: 0, whiteSpace: 'nowrap' }, disabled: busy, onClick: () => setRefresh((n) => n + 1) }, '刷新')),
        h('div', {
          className: 'wf-operations-feed wf-scroll',
          style: {
            flex: 1,
            minHeight: 0,
            overflowY: 'auto',
            overflowX: 'hidden',
            marginTop: 6,
            paddingTop: 6,
            borderTop: '1px solid var(--dsw-alias-border-l1)',
            display: 'flex',
            flexDirection: 'column',
            gap: 5,
          },
        },
          summary.hint ? h('p', {
            className: 'wf-operations-hint',
            role: summary.error ? 'alert' : 'status',
            title: summary.hint,
            style: { ...(summary.error ? T.statusErr : T.hint), margin: 0, flexShrink: 0, overflowWrap: 'anywhere', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' },
          }, summary.hint) : null,
          feedItems.length > 0
            ? feedItems.map((item, idx) => h('div', { key: 'feed:' + idx, className: 'wf-operations-feed-item' },
                item.time ? h('span', { className: 'wf-operations-feed-time' }, item.time) : null,
                h('span', { className: 'wf-operations-feed-tag' }, item.tag),
                h('span', { className: 'wf-operations-feed-text' }, item.text)))
            : (!summary.hint ? h('div', { style: { ...T.hint, fontSize: 11.5, margin: 'auto 0', opacity: .82 } }, emptyFeedText) : null)),
        showDetail && hasDetail ? h('div', {
          className: 'wf-operations-detail-modal',
          role: 'dialog',
          'aria-label': 'AI运维详情',
          style: {
            position: 'fixed', right: 24, bottom: 24, zIndex: 2600,
            width: 'min(480px, calc(100vw - 32px))',
            maxHeight: 'min(420px, calc(100vh - 96px))',
            overflowY: 'auto',
            padding: 12,
            border: '1px solid var(--dsw-alias-border-l2)',
            borderRadius: 10,
            background: 'var(--dsw-alias-bg-layer-1)',
            boxShadow: '0 12px 36px rgba(0,0,0,.22)',
            fontSize: 12,
            lineHeight: 1.55,
          },
        },
          h('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6, paddingBottom: 6, borderBottom: '1px solid var(--dsw-alias-border-l1)' } },
            h('strong', null, `事件详情（${incident.stage || '-'} / ${incident.status || '-'}）`),
            h('button', { type: 'button', className: 'wf-btn', style: T.miniBtn, onClick: () => typeof setShowDetail === 'function' && setShowDetail(false) }, '关闭')),
          incident.recovery && incident.recovery.reason ? h('div', null, '当前阻塞/恢复说明：' + operationsText(incident.recovery.reason)) : null,
          Array.isArray(incident.findings) && incident.findings.length > 0 ? h('div', { style: { marginTop: 4 } }, '最近诊断发现：' + operationsText(incident.findings[incident.findings.length - 1].summary || '')) : null,
          detailData && detailData.perf && detailData.perf.budgets ? h('div', { style: { marginTop: 4 } }, `预算使用：尝试 ${detailData.perf.budgets.attempts}/${detailData.perf.budgets.attempts_limit} · 安装 ${detailData.perf.budgets.installs}/${detailData.perf.budgets.installs_limit} · 重生成 ${detailData.perf.budgets.regenerations}/${detailData.perf.budgets.regenerations_limit}`) : null,
          detailData && detailData.diff && detailData.diff.patch ? h('pre', { style: { marginTop: 6, maxHeight: 180, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', padding: 6, background: 'var(--dsw-alias-bg-base)', borderRadius: 6, border: '1px solid var(--dsw-alias-border-l1)' } }, detailData.diff.patch) : null) : null);
    }

    async function requestAdvance(name, post, get, timeoutMs = 15000) {
      const path = '/batches/' + encodeURIComponent(name);
      let timer;
      try {
        const receipt = await Promise.race([
          post(path + '/advance', {}),
          new Promise((resolve, reject) => { timer = setTimeout(() => reject(new Error('请求回执超时')), timeoutMs); }),
        ]);
        if (!receipt || typeof receipt !== 'object') throw new Error('后台未返回有效回执');
        return receipt;
      } catch (error) {
        // 请求可能已送达；先只读核对，绝不自动重发推进请求。
        if (error.httpStatus) throw error;
        try {
          const state = await get(path + '/pipeline');
          if (state && (state.running || state.queued || state.waiting || state.external)) return state;
        } catch (_) { /* 下方明确显示未知结果，不把网络失败当执行失败 */ }
        throw new Error('继续处理请求结果未确认：' + String(error.message || error) + '；请查看进度或刷新，系统未自动重复提交');
      } finally {
        clearTimeout(timer);
      }
    }
    function advanceReceiptFeedback(receipt) {
      const state = receipt || {};
      if (state.error) return { error: true, message: String(state.error) };
      const stageLabel = state.active_stage_label || state.next_stage_label || '';
      const isLive = Boolean((state.running || state.external) && !state.flow_done);
      const elapsed = (isLive && typeof state.elapsed_seconds === 'number') ? ('，已运行 ' + state.elapsed_seconds + 's') : '';
      const detail = state.active_detail ? ('（' + state.active_detail + elapsed + '）') : (elapsed ? ('（' + elapsed.slice(1) + '）') : '');
      const blockedNote = state.blocked_reason ? ('；当前阻断：' + state.blocked_reason) : '';
      if (state.waiting) {
        return { message: state.reason || ('等待前一份试卷' + (state.waiting_for ? '「' + state.waiting_for + '」' : '') + '完成后自动从' + (stageLabel ? '「' + stageLabel + '」' : '') + '继续') };
      }
      if (state.external || (state.running && !state.started)) {
        if (stageLabel) return { message: '当前已在执行「' + stageLabel + '」' + detail + '，无需重复点击' };
        return { message: state.external ? '该批次正由另一执行器处理，正在同步进度' : '该批次已在处理中，正在同步进度' };
      }
      if (state.started && state.queued) {
        const cleared = state.cleared_cooldown ? '（已清除退避倒计时）' : '';
        const pos = Number(state.queue_position) || 1;
        if (pos <= 1) {
          return { message: '已立即启动' + (stageLabel ? '「' + stageLabel + '」' : '下一阶段') + cleared + blockedNote };
        }
        return { message: '已加入队列（第 ' + pos + ' 位），轮到后从' + (stageLabel ? '「' + stageLabel + '」' : '当前阶段') + '继续' + cleared + blockedNote };
      }
      if (state.queued) {
        const pos = Number(state.queue_position);
        return { message: '已在处理队列中' + (pos > 0 ? '（第 ' + pos + ' 位）' : '') + (stageLabel ? '，待执行「' + stageLabel + '」' : '') };
      }
      if (state.started) return { message: '已启动' + (stageLabel ? '「' + stageLabel + '」' : '继续处理') + '，正在刷新进度' };
      return { error: true, message: state.reason || '后台未启动处理，且未返回排队或等待状态，请刷新查看诊断' };
    }
    function advanceProgressFeedback(state, receipt) {
      if (receipt && receipt.error) return receipt;
      if (state && (state.running || state.queued || state.waiting || state.external)) return advanceReceiptFeedback(state);
      if (state && state.result) {
        const result = state.result;
        if (result.error) return { error: true, message: '后台处理失败：' + String(result.error) };
        if (result.reason) return { error: ['failed', 'blocked'].includes(result.status), message: '本轮停在「' + (result.stopped_at || '当前阶段') + '」：' + result.reason };
        return { message: '本轮处理已结束，具体结果见流程及卡点诊断' };
      }
      return receipt || { message: '' };
    }
    function recoveryProgressText(advance) {
      if (!advance) return '';
      // 严格串行：前一份未完成且未移出排队区前，后面的批次不开始。
      if (advance.waiting) {
        return '排队等待：需等前一份「' + (advance.waiting_for || '试卷') + '」完成后自动开始';
      }
      // 全局串行队列：前一份没跑完时，其它批次显示“排队等待”，不并跑。
      if (advance.queued && !advance.running) return '排队等待：前一份试卷跑完后自动开始';
      if (advance.running && !advance.flow_done && advance.active_stage_label) {
        const elapsed = typeof advance.elapsed_seconds === 'number' ? ('（已运行 ' + advance.elapsed_seconds + 's）') : '';
        const detail = advance.active_detail ? (' · ' + advance.active_detail) : '';
        return '正在执行「' + advance.active_stage_label + '」' + detail + elapsed;
      }
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
    function batchLatestTime(b) {
      const values = [b && b.flow_finished_at, b && b.flow_started_at,
                      b && b.updated_at, b && b.created_at];
      let latest = 0;
      for (const value of values) {
        const at = Date.parse(String(value || ''));
        if (Number.isFinite(at) && at > latest) latest = at;
      }
      return latest;
    }
    function flowTimingText(b) {
      const finished = fmtMonthMinute(b && b.flow_finished_at);
      if (!finished) return '';
      const mins = b && b.flow_minutes;
      const minutesText = (typeof mins === 'number' && mins >= 0) ? ('流程耗时 ' + mins + ' 分钟') : '';
      if (b.flow_done || b.uploaded) return '完成时间：' + finished + (minutesText ? ' · ' + minutesText : '');
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

    function MaterialRoleFixer(props) {
      const React = require('react');
      const h = React.createElement;
      const { batchName, onRerunStarted } = props;
      const [docs, setDocs] = React.useState([]);
      const [draft, setDraft] = React.useState({});
      const [loading, setLoading] = React.useState(true);
      const [saving, setSaving] = React.useState(false);
      const [error, setError] = React.useState('');
      const [doneMsg, setDoneMsg] = React.useState('');

      React.useEffect(() => {
        let cancelled = false;
        setLoading(true); setError(''); setDoneMsg('');
        jget('/batches/' + encodeURIComponent(batchName) + '/material-roles')
          .then((d) => {
            if (cancelled) return;
            const list = (d && d.documents) || [];
            setDocs(list);
            const next = {};
            for (const row of list) {
              const eff = row.user_role || row.detected_role || 'other';
              next[row.file] = eff === 'explanations' ? 'answers' : eff;
            }
            setDraft(next);
          })
          .catch((e) => { if (!cancelled) setError('加载材料列表失败：' + String((e && e.message) || e)); })
          .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
      }, [batchName]);

      async function applyAndRerun() {
        if (saving || !docs.length) return;
        setSaving(true); setError(''); setDoneMsg('');
        try {
          await jpost('/batches/' + encodeURIComponent(batchName) + '/material-roles', { roles: draft, rerun: true });
          setDoneMsg('已保存材料角色并启动重跑提取…');
          if (onRerunStarted) onRerunStarted();
        } catch (e) {
          setError('保存失败：' + String((e && e.message) || e));
        } finally {
          setSaving(false);
        }
      }

      const selectable = MATERIAL_ROLE_OPTIONS.filter((o) => o.value !== 'auto');
      return h('div', { style: { marginTop: 6, padding: '8px 10px', borderRadius: 8, background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)', display: 'flex', flexDirection: 'column', gap: 6 } },
        h('div', { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 } },
          h('span', { style: { fontSize: 12, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' } }, '手动指定各材料角色并重跑提取'),
          h('button', {
            type: 'button', className: 'wf-btn-primary',
            style: { ...T.miniBtn, background: 'var(--wf-brand)', color: '#fff', border: 'none', padding: '3px 10px' },
            disabled: loading || saving || !docs.length,
            onClick: applyAndRerun,
          }, saving ? '正在重跑…' : '确认角色并重跑提取'),
        ),
        loading ? h('div', { style: { ...T.hint, fontSize: 11.5 } }, '正在读取材料列表…') : null,
        !loading && docs.length ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 5 } },
          docs.map((row) => h('div', { key: row.file, style: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 11.5 } },
            h('span', { title: (row.original_filename || row.file) + ' (' + row.file + ')', style: { flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--dsw-alias-label-primary)' } }, row.original_filename || row.file),
            row.detected_role ? h('span', { style: { ...T.hint, fontSize: 10.5, flexShrink: 0 } }, '原判：' + (MATERIAL_ROLE_LABELS[row.detected_role] || row.detected_role)) : null,
            h('select', {
              'aria-label': (row.original_filename || row.file) + ' 角色',
              value: draft[row.file] || 'other',
              disabled: saving,
              onChange: (e) => setDraft((prev) => ({ ...prev, [row.file]: e.target.value })),
              style: { fontSize: 11.5, padding: '2px 6px', borderRadius: 5, border: '1px solid var(--dsw-alias-border-l2)', background: 'var(--dsw-alias-bg-layer-1)', color: 'var(--dsw-alias-label-primary)', flexShrink: 0 },
            }, selectable.map((opt) => h('option', { key: opt.value, value: opt.value }, opt.label))),
          )),
        ) : null,
        error ? h('div', { style: { ...T.statusErr, fontSize: 11.5, margin: 0 } }, error) : null,
        doneMsg ? h('div', { style: { ...T.statusOk, fontSize: 11.5, margin: 0 } }, doneMsg) : null,
      );
    }

    function copyDiagText(text) {
      const str = String(text || '');
      const fallback = () => {
        try {
          if (typeof document === 'undefined') return false;
          const ta = document.createElement('textarea');
          ta.value = str;
          ta.setAttribute('readonly', '');
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          ta.style.left = '-9999px';
          document.body.appendChild(ta);
          ta.select();
          const ok = document.execCommand('copy');
          document.body.removeChild(ta);
          return Boolean(ok);
        } catch (_) {
          return false;
        }
      };
      if (typeof navigator !== 'undefined' && navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        return navigator.clipboard.writeText(str).then(() => true).catch(() => fallback());
      }
      return Promise.resolve(fallback());
    }

    function buildDiagLogText(selBatch, stage, shown, flowFeedback) {
      const lines = ['【卡点诊断日志】'];
      const batchName = String((selBatch && selBatch.name) || '').trim();
      const paperTitle = String((selBatch && selBatch.paper && selBatch.paper.title) || '').trim();
      if (batchName) lines.push('批次：' + batchName);
      if (paperTitle && paperTitle !== batchName) lines.push('试卷：' + paperTitle);
      if (stage && stage.current) {
        lines.push('当前阶段：' + (shortStageName(stage.current) || stage.current.id || '') + ' (' + (stage.current.id || '') + ' / ' + (stage.current.status || '') + ')');
      }
      if (stage && stage.hint) lines.push('流程提示：' + stage.hint);
      if (flowFeedback && flowFeedback.error && flowFeedback.message) {
        lines.push('推进提示：' + String(flowFeedback.message));
      }
      if (selBatch && selBatch.advance && selBatch.advance.error) {
        lines.push('推进报错：' + String(selBatch.advance.error));
      }
      lines.push('卡点数量：' + shown.length);
      shown.forEach((it, idx) => {
        lines.push('');
        lines.push('--- 卡点 ' + (idx + 1) + ' ---');
        lines.push('阶段：' + (shortStageName(it) || it.id || '') + (it.id ? ' (' + it.id + ')' : ''));
        lines.push('状态：' + statusText(it.status) + (it.status ? ' (' + it.status + ')' : ''));
        if (it.reason) lines.push('摘要：' + String(it.reason));
        if (it.detail) lines.push('详细原因：\n' + String(it.detail));
        if (it.notes && String(it.notes) !== String(it.detail || '')) lines.push('备注：\n' + String(it.notes));
        if (it.hint) lines.push('处理建议：' + String(it.hint));
        if (it.command) lines.push('修复命令：' + String(it.command));
        const badSubs = Array.isArray(it.substeps)
          ? it.substeps.filter((s) => s && (s.status === 'failed' || s.status === 'blocked' || s.status === 'needs_review'))
          : [];
        if (badSubs.length) {
          lines.push('异常子步骤：');
          badSubs.forEach((s) => {
            lines.push('  - ' + (s.title || s.id || '') + '：' + statusText(s.status) + (s.detail ? ' — ' + String(s.detail) : ''));
          });
        }
      });
      return lines.join('\n');
    }

    // 卡点诊断悬浮卡片：平时收起为流程图右下角的红色圆角矩形，点击放大查看详情并支持一键复制诊断日志，再点击缩小。
    function DiagFloatingWidget(props) {
      const React = require('react');
      const h = React.createElement;
      const { selBatch, stage, shown, flowFeedback, onRefresh } = props;
      const [expanded, setExpanded] = React.useState(false);
      const [copiedLog, setCopiedLog] = React.useState(false);
      const [copiedCmdIdx, setCopiedCmdIdx] = React.useState(-1);
      const timerRef = React.useRef(null);
      React.useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);

      const batchLabel = String((selBatch && selBatch.name) || (selBatch && selBatch.paper && selBatch.paper.title) || '').trim();
      const needsRoleFix = shown.some((it) => /MATERIAL_MULTIPLE_PAPERS|MATERIAL_PAPER_UNIDENTIFIED|多份不同正文|未识别到试卷正文/.test(String(it.detail || it.notes || it.reason || '')));
      const hasAdvanceErr = Boolean(flowFeedback && flowFeedback.error && flowFeedback.message);
      const issueCount = shown.length || (hasAdvanceErr ? 1 : 0);

      const handleCopyLog = (e) => {
        if (e && e.stopPropagation) e.stopPropagation();
        const text = buildDiagLogText(selBatch, stage, shown, flowFeedback);
        copyDiagText(text).then((ok) => {
          if (!ok) return;
          setCopiedLog(true);
          if (timerRef.current) clearTimeout(timerRef.current);
          timerRef.current = setTimeout(() => setCopiedLog(false), 1800);
        });
      };

      const handleCopyCmd = (cmd, idx, e) => {
        if (e && e.stopPropagation) e.stopPropagation();
        copyDiagText(cmd).then((ok) => {
          if (!ok) return;
          setCopiedCmdIdx(idx);
          setTimeout(() => setCopiedCmdIdx((cur) => (cur === idx ? -1 : cur)), 1600);
        });
      };

      if (!expanded) {
        return h('button', {
          type: 'button',
          className: 'wf-diag-fab',
          'aria-expanded': false,
          title: '点击展开卡点诊断详情',
          onClick: () => setExpanded(true),
          style: {
            position: 'absolute', right: 12, bottom: 12, zIndex: 20,
            display: 'inline-flex', alignItems: 'center', gap: 7,
            padding: '7px 12px', borderRadius: 10, cursor: 'pointer',
            border: '1px solid var(--dsw-alias-state-error-primary)',
            background: 'var(--dsw-alias-bg-layer-1, var(--dsw-alias-bg-l1, #fff))',
            color: 'var(--dsw-alias-state-error-primary)',
            boxShadow: '0 4px 12px rgba(217,48,37,.12)',
            fontSize: 12.5, fontWeight: 600, lineHeight: 1.2,
          },
        },
          h('span', { style: { width: 7, height: 7, borderRadius: '50%', background: 'var(--dsw-alias-state-error-primary)', flexShrink: 0 } }),
          h('span', null, '卡点诊断'),
          issueCount > 0 ? h('span', {
            style: {
              fontSize: 11, fontWeight: 600, padding: '1px 6px', borderRadius: 999,
              background: 'color-mix(in srgb, var(--dsw-alias-state-error-primary) 12%, transparent)',
              color: 'var(--dsw-alias-state-error-primary)',
            },
          }, String(issueCount)) : null,
        );
      }

      return h('div', {
        className: 'wf-diag-popover',
        role: 'dialog',
        'aria-label': '卡点诊断',
        style: {
          position: 'absolute', right: 12, bottom: 12, zIndex: 30,
          width: 'min(460px, calc(100% - 24px))',
          maxHeight: 'min(420px, calc(100% - 24px))',
          display: 'flex', flexDirection: 'column', overflow: 'hidden',
          borderRadius: 12,
          border: '1px solid var(--dsw-alias-state-error-primary)',
          background: 'var(--dsw-alias-bg-layer-1, var(--dsw-alias-bg-l1, #fff))',
          boxShadow: '0 10px 28px rgba(0,0,0,.18)',
        },
      },
        h('div', {
          onClick: () => setExpanded(false),
          title: '点击缩小到右下角',
          style: {
            display: 'flex', alignItems: 'center', gap: 8, padding: '9px 12px', cursor: 'pointer', flexShrink: 0,
            borderBottom: '1px solid var(--dsw-alias-border-l2)',
            background: 'color-mix(in srgb, var(--dsw-alias-state-error-primary) 7%, var(--dsw-alias-bg-layer-1, #fff))',
          },
        },
          h('span', { style: { width: 7, height: 7, borderRadius: '50%', background: 'var(--dsw-alias-state-error-primary)', flexShrink: 0 } }),
          h('span', { style: { fontWeight: 600, fontSize: 12.5, color: 'var(--dsw-alias-state-error-primary)', flexShrink: 0 } }, '卡点诊断'),
          batchLabel ? h('span', { style: { ...T.hint, fontSize: 11.5, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: batchLabel }, batchLabel) : h('span', { style: { flex: 1 } }),
          h('button', {
            type: 'button', className: 'wf-btn-ghost',
            style: { ...T.miniBtn, flexShrink: 0, padding: '3px 9px', fontSize: 11.5, borderColor: 'var(--dsw-alias-state-error-primary)', color: 'var(--dsw-alias-state-error-primary)' },
            onClick: handleCopyLog,
          }, copiedLog ? '已复制 ✓' : '复制日志'),
          h('button', {
            type: 'button', className: 'wf-btn-ghost',
            style: { ...T.miniBtn, flexShrink: 0, padding: '3px 8px', fontSize: 11.5 },
            onClick: (e) => { if (e && e.stopPropagation) e.stopPropagation(); setExpanded(false); },
          }, '缩小'),
        ),
        h('div', { className: 'wf-scroll', style: { flex: 1, minHeight: 0, overflowY: 'auto', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 10 } },
          hasAdvanceErr ? h('div', { style: { fontSize: 12, color: 'var(--dsw-alias-state-error-primary)', wordBreak: 'break-word', padding: '6px 8px', borderRadius: 8, background: 'color-mix(in srgb, var(--dsw-alias-state-error-primary) 6%, transparent)' } },
            '推进提示：' + flowFeedback.message) : null,
          shown.map((it, i) => h('div', { key: 'd' + i, style: { display: 'flex', flexDirection: 'column', gap: 4, paddingTop: i ? 8 : 0, borderTop: i ? '1px dashed var(--dsw-alias-border-l2)' : 'none' } },
            h('div', { style: { fontSize: 12.5, fontWeight: 600, color: 'var(--dsw-alias-label-primary)' } },
              (shortStageName(it) || it.id) + '：' + (it.reason || statusText(it.status))),
            (it.detail || it.notes)
              ? h('pre', { style: { margin: 0, padding: '6px 8px', borderRadius: 6, background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)', fontSize: 11.5, lineHeight: 1.5, fontFamily: 'inherit', whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: 'var(--dsw-alias-label-secondary)' } },
                  String(it.detail || it.notes))
              : null,
            it.hint ? h('div', { style: { ...T.hint, fontSize: 11.5, wordBreak: 'break-word' } }, '怎么办：' + it.hint) : null,
            it.command
              ? h('div', { style: { display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 } },
                  h('code', { style: { flex: 1, minWidth: 0, fontSize: 11, fontFamily: 'monospace', background: 'var(--dsw-alias-bg-base)', border: '1px solid var(--dsw-alias-border-l2)', borderRadius: 6, padding: '4px 8px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: it.command }, it.command),
                  h('button', { type: 'button', className: 'wf-btn-ghost', style: { ...T.miniBtn, flexShrink: 0 }, onClick: (e) => handleCopyCmd(it.command, i, e) }, copiedCmdIdx === i ? '已复制 ✓' : '复制命令'))
              : null,
          )),
          needsRoleFix && selBatch && selBatch.name
            ? h(MaterialRoleFixer, { key: selBatch.name, batchName: selBatch.name, onRerunStarted: onRefresh })
            : null,
        ),
      );
    }

    function diagBanner(selBatch, stage, onRefresh, flowFeedback) {
      const h = require('react').createElement;
      if (!selBatch || !stage) return null;
      const items = stage.items || [];
      const blocking = items.filter((it) => it.blocking);
      const diagnostics = stage.diagnostics || null;
      const shown = blocking.length
        ? blocking
        : (diagnostics && diagnostics.blocking_count ? [] : []);
      const hasAdvanceErr = Boolean(flowFeedback && flowFeedback.error && flowFeedback.message);
      if (!shown.length && !hasAdvanceErr) return null;
      return h(DiagFloatingWidget, {
        key: 'diag:' + (selBatch.name || ''),
        selBatch, stage, shown, flowFeedback, onRefresh,
      });
    }

