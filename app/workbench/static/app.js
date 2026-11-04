/* 录题工作台：Vue 3 + Element Plus（本地 vendor，无构建步骤）。
   首页负责上传试卷并推进流程，工作台按“入库单元”审核标签与题面。 */
/* global Vue, ElementPlus, ElementPlusLocaleZhCn, ElementPlusIconsVue */

const { createApp, reactive, computed, onMounted } = Vue;

const esc = (value) => String(value ?? '').replace(/[&<>"]/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
));
const SECTION = /^(?:第[一二三四五六七八九十]+部分|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\.|[IVX]{1,4}\.)/;

function markLine(text) {
  let out = esc(text);
  out = out.replace(/&lt;(\/?)(u|b|i|strong|em)&gt;/g, '<$1$2>');
  out = out.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/__(.+?)__/g, '<u>$1</u>');
  out = out.replace(/\u3000(\d{1,3})\u3000/g, '<span class="blank">$1</span>');
  return out;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || ('请求失败：' + response.status));
  return data;
}

const SEVERITY = { blocker: ['danger', '阻断'], major: ['warning', '重要'], minor: ['info', '一般'] };
const STATUS_TEXT = { open: '待处理', accepted: '已通过', fixed: '已修改', rejected: '已驳回', hold: '保留' };

const App = {
  setup() {
    const state = reactive({
      view: 'home',
      batch: '',
      unit: '',
      batches: [],
      batchesLoading: false,
      overview: null,
      detail: null,
      detailLoading: false,
      filter: { tab: 'all', q: '' },
      upload: { title: '', year: String(new Date().getFullYear()), region: '', subject: 'english' },
      files: [],
      uploading: false,
      labelDlg: false,
      saving: false,
      warnings: [],
      form: { source_type: '', year: '', location: '', source: '', type: '', subtypes: [], difficulty: '', categories: [], knowledge: [] },
      knowledgeTree: [],
      knowledgeTotal: 0,
      knowledgeLeaves: 0,
      knowledgeQuery: '',
      knowledgeError: '',
      knowledgeLoading: false,
      archiveTarget: null,
      archiveConfirm: '',
      archiving: false,
      openSubs: [],
      autoLabels: false,
      autoFind: '',
      autoFix: '',
      findDlg: false,
      fixTarget: null,
      findForm: { finding_id: '', field: '', status: 'accepted', note: '', question: '' },
      tax: null,
      timer: null,
    });

    const rows = computed(() => {
      const list = state.overview?.rows || [];
      const query = state.filter.q.trim().toLowerCase();
      return list.filter((row) => {
        if (state.filter.tab === 'open' && !row.needs_human) return false;
        if (state.filter.tab === 'blocked' && row.state !== 'blocked') return false;
        if (state.filter.tab === 'unverified' && row.state !== 'unverified') return false;
        if (!query) return true;
        return [row.label, row.type, (row.numbers || []).join(' ')].join(' ').toLowerCase().includes(query);
      });
    });

    // 左栏试卷清单：待审多的排前面，一行一份试卷。
    const reviewPapers = computed(() => state.batches.slice().sort((a, b) =>
      (b.needs_human || 0) - (a.needs_human || 0) || String(a.name).localeCompare(String(b.name))));

    const batchFindings = computed(() => state.overview?.batch_findings || []);

    const tax = computed(() => state.tax || {});
    const types = computed(() => tax.value.types || []);
    const sources = computed(() => tax.value.sources || []);
    const regions = computed(() => tax.value.regions || []);
    const difficulties = computed(() => tax.value.difficulties || []);
    const categories = computed(() => tax.value.categories || []);
    const subtypeOptions = computed(() => (tax.value.subtypes || {})[state.form.type] || []);
    const knowledgeTree = computed(() => {
      const query = state.knowledgeQuery.trim().toLocaleLowerCase();
      if (!query) return state.knowledgeTree;
      function filter(nodes) {
        return nodes.flatMap((node) => {
          const children = filter(node.children || []);
          return node.path.toLocaleLowerCase().includes(query) || children.length
            ? [{ ...node, children }] : [];
        });
      }
      return filter(state.knowledgeTree);
    });
    const knowledgeExpanded = computed(() => {
      if (!state.knowledgeQuery.trim()) return [];
      const paths = [];
      function visit(nodes) {
        nodes.forEach((node) => { if (!node.leaf) paths.push(node.path); visit(node.children || []); });
      }
      visit(knowledgeTree.value);
      return paths;
    });
    const previewNote = computed(() => {
      const detail = state.detail;
      if (!detail) return '';
      const count = (detail.subquestions || []).length;
      return count > 1 ? ('整组连续切片：共用材料 + ' + count + ' 小题') : '按题号边界切片，含题内图片与表格';
    });
    // 连续的多张题图合成一行，避免竖着排一长条。
    const previewBlocks = computed(() => {
      const out = [];
      for (const block of state.detail?.preview_blocks || []) {
        if (block.type === 'image') {
          const last = out[out.length - 1];
          if (last && last.type === 'images') last.items.push(block);
          else out.push({ type: 'images', items: [block] });
        } else {
          out.push(block);
        }
      }
      return out;
    });

    function yearText(paper) {
      if (!paper) return '';
      return [paper.year, paper.region].filter(Boolean).join(' · ') || '—';
    }
    // 选项 [{label,text}] ↔ 多行文本，编辑时不显示 [object Object]，保存时保留字母标签。
    function optionsToText(options) {
      if (!Array.isArray(options)) return typeof options === 'string' ? options : '';
      return options.map((opt) => {
        if (opt && typeof opt === 'object') {
          const label = String(opt.label || '').replace(/[.．、]\s*$/, '');
          const text = opt.text != null ? opt.text : (opt.content != null ? opt.content : '');
          return label ? `${label}. ${text}` : String(text);
        }
        return String(opt);
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
    function progress(row) {
      const stage = row?.stage;
      if (!stage || !stage.total) return 0;
      return Math.round((stage.done / stage.total) * 100);
    }
    function stageType(row) {
      const status = row?.stage?.current?.status;
      if (status === 'blocked' || status === 'failed') return 'danger';
      if (row?.advance?.running || row?.stage?.running_unknown) return 'warning';
      return 'info';
    }
    function severityType(value) { return (SEVERITY[value] || ['info'])[0]; }
    function severityText(value) { return (SEVERITY[value] || ['', value || '一般'])[1]; }
    function statusText(value) { return STATUS_TEXT[value] || value || '待处理'; }
    function comparisonText(value) {
      if (Array.isArray(value)) return value.length ? value.join('、') : '（空）';
      return value == null || value === '' ? '（空）' : String(value);
    }
    const FIELD_TEXT = { knowledge: '知识点', type: '题型', subtypes: '子类', sample: '三审', answer: '答案',
      child_knowledge: '小题知识点', difficulty: '难度', explanation: '解析', articleSummary: '文章大意', stem: '题干', options: '选项' };
    function fieldText(field) {
      const name = String(field || '').split('.').pop().replace(/\[.*?\]/g, '');
      return FIELD_TEXT[name] || FIELD_TEXT[String(field || '')] || name || '标签';
    }
    function memberLabel(member) {
      const field = String((member && member.field) || '');
      const direct = /subquestions\[number=(\d+)\]/.exec(field);
      if (direct) return '第 ' + direct[1] + ' 题';
      const text = String((member && (member.reason || member.question)) || '');
      const numbered = /第\s*(\d{1,3})\s*题/.exec(text);
      if (numbered) return '第 ' + numbered[1] + ' 题';
      return (member && member.scope) || '本题';
    }
    function shortReason(finding) {
      const rounds = (finding && finding.comparison && finding.comparison.rounds) || [];
      const changed = rounds.some((round) => (round.checks || []).some((check) => (check.differences || []).length));
      if (changed) return fieldText(finding.field) + '标注前后不一致，需人工确认。';
      const text = String((finding && (finding.reason || finding.question || finding.message)) || '待人工确认');
      return text.length > 32 ? text.slice(0, 32) + '…' : text;
    }
    function labelValue(value) {
      if (Array.isArray(value)) return value.map((x) => String(x)).join('、');
      return String(value == null ? '' : value);
    }
    function leafName(value) {
      const parts = String(value == null ? '' : value).split('/').filter(Boolean);
      return parts.length ? parts[parts.length - 1] : String(value == null ? '' : value);
    }
    function diffText(diff) {
      const original = Array.isArray(diff.original) ? diff.original : [diff.original];
      const reviewed = Array.isArray(diff.reviewed) ? diff.reviewed : [diff.reviewed];
      const removed = original.filter((x) => !reviewed.includes(x)).map(leafName);
      const added = reviewed.filter((x) => !original.includes(x)).map(leafName);
      const prefix = diff.scope === 'subquestion' && diff.number ? '第' + diff.number + '题 ' : '';
      if (removed.length && added.length) return prefix + '去掉 ' + removed.join('、') + '；增加 ' + added.join('、');
      if (removed.length) return prefix + '去掉 ' + removed.join('、');
      if (added.length) return prefix + '增加 ' + added.join('、');
      return prefix + leafName(original[0]) + ' → ' + leafName(reviewed[0]);
    }
    function leaves(value) {
      const list = Array.isArray(value) ? value : [value];
      return list.filter((x) => x != null && x !== '').map(leafName).join('、');
    }
    function roundText(round, finding) {
      if (!round) return '';
      if (round.status === '待重新审核') return '历史记录已过期或无法验证，待重新审核';
      if (round.status === '历史来源未验证') return '当前无有效记录；目录有来源未验证的历史文件';
      if (round.status === '未提供') return '未执行';
      if (round.status === '未覆盖') return round.round_id === 'sample' ? '未抽中' : '未覆盖';
      if (round.status === '未涉及') return '未提供该字段';
      const checks = round.checks || [];
      if (!checks.length) return '未提供该字段';
      return checks.map((check) => {
        const target = round.round_id === 'sample' && check.number != null ? '抽中第' + check.number + '题：' : '';
        return target + comparisonText(check.value);
      }).join('；');
    }
    // 只给一句话结论，不展开原始值、依据和推理过程。
    function shortRound(round) {
      if (!round) return '';
      if (round.status === '待重新审核') return '历史记录已过期或无法验证，待重新审核';
      if (round.status === '历史来源未验证') return '当前无有效记录；目录有来源未验证的历史文件';
      if (round.status === '未涉及') return '未涉及';
      if (round.status === '未覆盖') return '未抽到';
      if (round.status === '未提供') return '未提供';
      const checks = round.checks || [];
      if (!checks.length) return '未涉及';
      if (checks.some((c) => c.verdict === 'error')) return '有差异';
      if (checks.some((c) => c.verdict === 'uncertain')) return '有疑点';
      return '通过';
    }
    function roundSummary(round) {
      if (round.status === '未涉及') return round.note || '未涉及该字段';
      if (round.status !== '已提供') return round.status;
      const checks = round.checks || [];
      if (!checks.length) return '未涉及该字段';
      return checks.map((check) => {
        const result = ({ ok: '一致', error: '有差异', uncertain: '有疑点' })[check.verdict] || '已检查';
        const changes = (check.differences || []).filter((d) => d.scope !== 'subquestion').map((d) => {
          const original = Array.isArray(d.original) ? d.original : [d.original];
          const reviewed = Array.isArray(d.reviewed) ? d.reviewed : [d.reviewed];
          const removed = original.filter((x) => !reviewed.includes(x));
          const added = reviewed.filter((x) => !original.includes(x));
          const prefix = d.number ? '第' + d.number + '题：' : '';
          if (removed.length && added.length) return prefix + comparisonText(removed) + ' → ' + comparisonText(added);
          if (removed.length) return prefix + '去掉 ' + comparisonText(removed);
          if (added.length) return prefix + '增加 ' + comparisonText(added);
          return prefix + comparisonText(d.original) + ' → ' + comparisonText(d.reviewed);
        });
        const reason = check.verdict !== 'ok' && check.rationale
          ? String(check.rationale).slice(0, 90) + (String(check.rationale).length > 90 ? '…' : '')
          : '';
        const detail = changes.length ? changes.join('；') : reason;
        return result + (detail ? '：' + detail : '');
      }).join('；');
    }
    function isHeading(text) { return SECTION.test(String(text || '').trim()); }
    function isHeaderRow(row) {
      return Array.isArray(row) && row.length > 0 && row.every((cell) => String(cell ?? '').length <= 10);
    }
    function isActive(row) {
      if (!state.detail) return false;
      if (row.unit_id && state.detail.unit_id) return row.unit_id === state.detail.unit_id;
      return String(row.label) === String(state.detail.label);
    }

    async function loadTaxonomy() {
      if (state.tax) return;
      try { state.tax = await api('/api/taxonomy'); } catch (error) { console.warn(error); }
    }

    async function loadBatches() {
      state.batchesLoading = true;
      try {
        const data = await api('/api/batches');
        state.batches = data.batches || [];
      } catch (error) {
        ElementPlus.ElMessage.error(String(error.message || error));
      } finally {
        state.batchesLoading = false;
      }
    }

    async function loadOverview(name) {
      state.overview = await api('/api/batches/' + encodeURIComponent(name) + '/detail').catch(async () => {
        const data = await api('/api/batches');
        return (data.batches || []).find((item) => item.name === name) || null;
      });
    }

    // 请求序号：上下快速切题会并发多个 loadDetail，只有最后一次请求能写回结果，
    // 避免旧题目的响应晚到覆盖新题目（表现为内容错位或空白）。
    let detailSeq = 0;

    // 切换题目后把右侧工作栏滚回顶部；否则长文滑到底再切到短题，会停在空白处。
    function resetWorkScroll() {
      const el = document.querySelector('.col.work');
      if (el) el.scrollTop = 0;
    }

    async function loadDetail() {
      if (!state.batch || !state.unit) return;
      const seq = ++detailSeq;
      const unit = state.unit;
      state.detailLoading = true;
      try {
        const data = await api('/api/batches/' + encodeURIComponent(state.batch) + '/units/' + encodeURIComponent(unit));
        if (seq !== detailSeq || String(unit) !== String(state.unit)) return; // 已被更新的切换取代，丢弃
        state.detail = data;
        // 选项是 [{label,text}] 对象数组，转换成可编辑文本行，避免显示 [object Object]
        (state.detail.subquestions || []).forEach((sub) => { sub.optionsText = optionsToText(sub.options); });
        state.openSubs = [];
        await Vue.nextTick();
        resetWorkScroll();
      } catch (error) {
        if (seq === detailSeq) ElementPlus.ElMessage.error(String(error.message || error));
      } finally {
        if (seq === detailSeq) state.detailLoading = false;
      }
    }

    async function loadWorkbench() {
      if (!state.batch) { state.view = 'home'; return; }
      state.view = 'workbench';
      try {
        const data = await api('/api/batches');
        state.batches = data.batches || [];
        state.overview = state.batches.find((item) => item.name === state.batch) || null;
      } catch (error) {
        ElementPlus.ElMessage.error(String(error.message || error));
      }
      if (!state.unit && state.overview?.rows?.length) {
        state.unit = state.overview.rows[0].unit_id || state.overview.rows[0].label;
      }
      if (state.unit) await loadDetail();
      await Vue.nextTick();
      const active = document.querySelector('.unit-row.active');
      if (active) active.scrollIntoView({ block: 'nearest' });
      if (state.autoLabels && state.detail && !state.labelDlg) {
        state.autoLabels = false;
        openLabels();
      }
      if (state.autoFind && state.detail) {
        const target = (state.detail.label_findings || []).find((item) => item.finding_id === state.autoFind);
        state.autoFind = '';
        if (target) openFinding(target);
      }
      if (state.autoFix && state.detail) {
        const target = (state.detail.label_findings || []).find((item) => item.finding_id === state.autoFix);
        state.autoFix = '';
        if (target) openFix(target);
      }
    }

    function parseHash() {
      const raw = location.hash.replace(/^#\/?/, '');
      const [path, query] = raw.split('?');
      const parts = path.split('/').filter(Boolean);
      if (parts[0] === 'batch' && parts[1]) {
        state.batch = decodeURIComponent(parts[1]);
        const query = new URLSearchParams(raw.split('?')[1] || '');
        state.unit = query.get('unit') || '';
        state.autoLabels = query.get('labels') === '1';
        state.autoFind = query.get('find') || '';
        state.autoFix = query.get('fix') || '';
      } else {
        state.batch = '';
        state.unit = '';
      }
    }

    function goHome() {
      location.hash = '#/';
    }

    // 顶部「试卷审核」：已经在某份试卷里就留在原地，否则打开待审最多的那份。
    async function goReview() {
      if (!state.batches.length) await loadBatches();
      if (state.batch) { state.view = 'workbench'; return; }
      const target = reviewPapers.value.find((item) => item.needs_human) || reviewPapers.value[0];
      if (target) openBatch(target.name);
      else ElementPlus.ElMessage.info('还没有可审核的试卷，先到首页上传');
    }

    function openBatch(name, unit) {
      location.hash = '#/batch/' + encodeURIComponent(name) + (unit ? ('?unit=' + encodeURIComponent(unit)) : '');
    }

    function selectRow(row) {
      const key = row.unit_id || row.label;
      if (String(key) === String(state.unit)) return;
      location.hash = '#/batch/' + encodeURIComponent(state.batch) + '?unit=' + encodeURIComponent(key);
    }

    async function reload() {
      await loadBatches();
      if (state.view === 'workbench') {
        state.overview = state.batches.find((item) => item.name === state.batch) || state.overview;
        await loadDetail();
      }
    }

    // 键盘上下切换题目（↑/↓ 或 k/j），保持原来手不用离开键盘的习惯。
    async function step(offset) {
      const list = rows.value;
      if (!list.length) return;
      let index = list.findIndex((row) => isActive(row));
      if (index < 0) index = 0;
      const next = list[Math.min(list.length - 1, Math.max(0, index + offset))];
      if (!next || isActive(next)) return;
      selectRow(next);
      await Vue.nextTick();
      const active = document.querySelector('.unit-row.active');
      if (active) active.scrollIntoView({ block: 'nearest' });
    }

    function onKeydown(event) {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const tag = (event.target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || event.target?.isContentEditable) return;
      if (state.view !== 'workbench' || state.labelDlg || state.findDlg || state.archiveTarget) return;
      if (event.key === 'ArrowDown' || event.key === 'j') {
        event.preventDefault();
        step(1);
      } else if (event.key === 'ArrowUp' || event.key === 'k') {
        event.preventDefault();
        step(-1);
      }
    }

    function onFileChange(file, files) { state.files = files; }
    function onFileRemove(file, files) { state.files = files; }

    async function submitUpload() {
      if (!state.files.length) {
        ElementPlus.ElMessage.warning('先选一个试卷文件（docx 或 pdf）');
        return;
      }
      state.uploading = true;
      try {
        const body = new FormData();
        ['title', 'year', 'region', 'subject'].forEach((key) => body.append(key, state.upload[key] ?? ''));
        state.files.forEach((file) => body.append('files', file.raw || file, file.name));
        const response = await fetch('/api/uploads', { method: 'POST', body });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || '上传失败');
        ElementPlus.ElMessage.success('已建批次：' + data.name + '，' + (data.stage?.hint || '已开始处理'));
        state.files = [];
        state.upload.title = '';
        await loadBatches();
        openBatch(data.name);
      } catch (error) {
        ElementPlus.ElMessage.error(String(error.message || error));
      } finally {
        state.uploading = false;
      }
    }

    async function advance(name) {
      try {
        const data = await api('/api/batches/' + encodeURIComponent(name) + '/advance', { method: 'POST', body: '{}' });
        ElementPlus.ElMessage.info(data.running ? '已在后台推进流程' : '流程已是最新');
        await loadBatches();
      } catch (error) {
        ElementPlus.ElMessage.error(String(error.message || error));
      }
    }

    async function decide(finding, status) {
      const ids = (finding.member_ids && finding.member_ids.length) ? finding.member_ids : [finding.finding_id];
      try {
        for (const id of ids) {
          await api('/api/batches/' + encodeURIComponent(state.batch) + '/decisions', {
            method: 'POST',
            body: JSON.stringify({ item: { finding_id: id, status } }),
          });
        }
        await reload();
        if (state.autoAdvance) startAdvance();
      } catch (error) {
        ElementPlus.ElMessage.error(String(error.message || error));
      }
    }

    async function resolveStructure(finding, status) {
      try {
        await api('/api/batches/' + encodeURIComponent(state.batch) + '/decisions', {
          method: 'POST',
          body: JSON.stringify({ item: { finding_id: finding.finding_id, status,
                                         resolution_note: status === 'fixed' ? '已核对原卷：' + (finding.reason || '') : '' } }),
        });
        await reload();
        ElementPlus.ElMessage.success(status === 'fixed' ? '已确认结构疑点，重跑结构闸门即可放行' : '已驳回');
      } catch (error) {
        ElementPlus.ElMessage.error(String(error.message || error));
      }
    }

    function openFinding(finding) {
      state.findForm = {
        finding_id: finding.finding_id,
        field: finding.field || '',
        status: finding.status || 'accepted',
        note: finding.note || '',
        question: finding.reason || finding.question || finding.message || '',
      };
      state.findDlg = true;
    }

    async function saveFinding() {
      try {
        await api('/api/batches/' + encodeURIComponent(state.batch) + '/decisions', {
          method: 'POST',
          body: JSON.stringify({
            item: {
              finding_id: state.findForm.finding_id,
              status: state.findForm.status,
              note: state.findForm.note,
            },
          }),
        });
        ElementPlus.ElMessage.success('已更新处理意见');
        state.findDlg = false;
        await reload();
      } catch (error) {
        ElementPlus.ElMessage.error(String(error.message || error));
      }
    }

    // 「手动修改」：直接改真值，保存后把这条意见标成已修改。
    function openFix(finding) {
      state.fixTarget = {
        id: finding.finding_id,
        ids: (finding.member_ids && finding.member_ids.length) ? finding.member_ids : [finding.finding_id],
        field: finding.field || '',
        question: finding.reason || finding.question || finding.message || '',
        scope: finding.scope || '',
      };
      openLabels();
    }

    function openLabels() {
      const labels = state.detail?.labels || {};
      state.form = {
        source_type: labels.source_type || '',
        year: String(labels.year || ''),
        location: labels.location || '',
        source: labels.source || '',
        type: labels.type || '',
        subtypes: (labels.subtypes || []).slice(),
        difficulty: labels.difficulty || '',
        categories: (labels.categories || []).slice(),
        knowledge: (labels.knowledge || []).slice(),
      };
      state.warnings = labels.warnings || [];
      state.knowledgeQuery = '';
      loadKnowledge();
      state.labelDlg = true;
    }

    async function loadKnowledge() {
      state.knowledgeLoading = true;
      state.knowledgeError = '';
      try {
        const data = await api('/api/knowledge/tree');
        if (!data.complete) throw new Error('目录未完整加载，请重试');
        state.knowledgeTree = data.items || [];
        state.knowledgeTotal = data.total;
        state.knowledgeLeaves = data.leaf_total;
      } catch (error) {
        state.knowledgeError = String(error.message || error);
      } finally {
        state.knowledgeLoading = false;
      }
    }

    function toggleKnowledge(node) {
      if (!node.leaf) return;
      if (state.form.knowledge.includes(node.path)) removeKnowledge(node.path);
      else state.form.knowledge.push(node.path);
    }

    function removeKnowledge(path) {
      state.form.knowledge = state.form.knowledge.filter((value) => value !== path);
    }

    function openArchive(item) {
      state.archiveTarget = item;
      state.archiveConfirm = '';
    }

    async function archiveBatch() {
      const target = state.archiveTarget;
      if (!target || state.archiveConfirm !== target.name) return;
      state.archiving = true;
      try {
        const data = await api('/api/batches/' + encodeURIComponent(target.name) + '/archive', {
          method: 'POST', body: JSON.stringify({ confirm_name: state.archiveConfirm }),
        });
        state.archiveTarget = null;
        if (state.batch === target.name) {
          state.detail = null;
          state.overview = null;
          state.labelDlg = false;
          state.findDlg = false;
          goHome();
        }
        await loadBatches();
        ElementPlus.ElMessage.success({ message: '已移到 ' + data.archive + '；可重新上传录入，原数据可从回收目录恢复。', duration: 8000 });
      } catch (error) {
        ElementPlus.ElMessage.error(String(error.message || error));
      } finally {
        state.archiving = false;
      }
    }

    async function saveLabels() {
      state.saving = true;
      try {
        const data = await api('/api/batches/' + encodeURIComponent(state.batch) + '/units/' + encodeURIComponent(state.unit), {
          method: 'POST',
          body: JSON.stringify({ labels: state.form }),
        });
        if (data.warnings && data.warnings.length) {
          state.warnings = data.warnings;
          ElementPlus.ElMessage.warning('已保存，但目录校验有 ' + data.warnings.length + ' 条提醒');
        } else {
          if (state.fixTarget) {
            // 手工改完标签，顺便把对应的意见结掉，省得人再点一次。
            for (const id of (state.fixTarget.ids || [state.fixTarget.id])) {
              await api('/api/batches/' + encodeURIComponent(state.batch) + '/decisions', {
                method: 'POST',
                body: JSON.stringify({
                  item: { finding_id: id, status: 'fixed', note: '手动修改' },
                }),
              });
            }
            ElementPlus.ElMessage.success('标签已保存，处理意见已标记为已修改');
            state.fixTarget = null;
          } else {
            ElementPlus.ElMessage.success('标签已保存');
          }
          state.labelDlg = false;
        }
        await reload();
      } catch (error) {
        ElementPlus.ElMessage.error(String(error.message || error));
      } finally {
        state.saving = false;
      }
    }

    async function saveContent() {
      const subs = state.detail?.subquestions || [];
      if (!subs.length) return;
      state.saving = true;
      try {
        const items = subs.map((sub) => ({
          number: sub.number, stem: sub.stem,
          options: textToOptions(sub.optionsText, sub.options),
          answer: sub.answer,
        }));
        const data = await api('/api/batches/' + encodeURIComponent(state.batch) + '/units/' + encodeURIComponent(state.unit), {
          method: 'POST',
          body: JSON.stringify({ items }),
        });
        ElementPlus.ElMessage.success('草稿已保存：' + (data.saved || []).length + ' 个小题');
        await reload();
      } catch (error) {
        ElementPlus.ElMessage.error(String(error.message || error));
      } finally {
        state.saving = false;
      }
    }

    onMounted(async () => {
      await loadTaxonomy();
      parseHash();
      if (state.batch) await loadWorkbench(); else await loadBatches();
      window.addEventListener('hashchange', async () => {
        const prevBatch = state.batch;
        const prevUnit = state.unit;
        parseHash();
        if (!state.batch) { state.view = 'home'; await loadBatches(); return; }
        if (state.batch === prevBatch && state.unit && state.unit !== prevUnit) {
          // 同一份试卷内上下切题：只取该题详情，不重复拉整份批次列表（否则快速切题会卡顿）。
          state.view = 'workbench';
          await loadDetail();
          await Vue.nextTick();
          const active = document.querySelector('.unit-row.active');
          if (active) active.scrollIntoView({ block: 'nearest' });
          return;
        }
        await loadWorkbench();
      });
      window.addEventListener('keydown', onKeydown);
      state.timer = setInterval(async () => {
        if (state.batches.some((item) => item.advance?.running)) await loadBatches();
      }, 4000);
    });

    return {
      state, rows, types, sources, regions, difficulties, categories,
      subtypeOptions, knowledgeTree, knowledgeExpanded, previewNote, previewBlocks, reviewPapers,
      batchFindings, resolveStructure,
      yearText, progress, stageType, severityType, severityText, statusText, comparisonText, roundSummary, fieldText, shortRound, memberLabel, shortReason, roundText,
      isHeading, isHeaderRow, isActive, markLine,
      goHome, goReview, openBatch, selectRow, reload, loadBatches, advance,
      onFileChange, onFileRemove, submitUpload,
      decide, openLabels, loadKnowledge, toggleKnowledge, removeKnowledge, saveLabels, saveContent,
      openArchive, archiveBatch,
      openFinding, saveFinding, openFix,
    };
  },
  template: `
  <div class="shell">
    <div class="topbar">
      <div class="brand" @click="goHome"><span class="logo">录</span>录题工作台</div>
      <div class="nav">
        <span class="nav-item" :class="{ active: state.view === 'home' }" @click="goHome">首页</span>
        <span class="nav-item" :class="{ active: state.view === 'workbench' }" @click="goReview">试卷审核</span>
      </div>
      <template v-if="state.view === 'workbench' && state.overview">
        <span class="paper-name">{{ state.overview.paper.title }}</span>
        <span class="paper-meta">{{ state.overview.paper.batch }} · {{ yearText(state.overview.paper) }}</span>
      </template>
      <span class="spacer"></span>
      <span class="build" title="界面版本，用来确认浏览器有没有拿到最新的前端">v2026-11-03h</span>
      <template v-if="state.view === 'workbench' && state.overview">
        <el-tag size="small" effect="plain" :type="stageType(state.overview)">{{ state.overview.stage.hint }}</el-tag>
        <el-button size="small" @click="reload">刷新</el-button>
        <el-button size="small" type="danger" text :disabled="state.overview.advance?.running" @click="openArchive(state.overview)">删除批次</el-button>
      </template>
    </div>

    <!-- ---------------------------------------------------------- 首页 -->
    <div v-if="state.view === 'home'" class="home">
      <h2>上传试卷</h2>
      <div class="lead">选原卷（docx / pdf，可带答案与听力音频），提交后自动建批次并推进流程；代理阶段、人工确认会停住等人。</div>

      <el-card shadow="never">
        <el-form label-position="top" size="small" class="upload-grid">
          <el-form-item label="试卷名">
            <el-input v-model="state.upload.title" placeholder="如 2025年安徽省初中学业水平考试英语" />
          </el-form-item>
          <el-form-item label="年份">
            <el-input v-model="state.upload.year" placeholder="2025" />
          </el-form-item>
          <el-form-item label="地区">
            <el-select v-model="state.upload.region" filterable allow-create clearable placeholder="安徽">
              <el-option v-for="item in regions" :key="item" :label="item" :value="item" />
            </el-select>
          </el-form-item>
          <el-form-item label="科目">
            <el-select v-model="state.upload.subject">
              <el-option label="英语" value="english" />
            </el-select>
          </el-form-item>
          <el-form-item class="full" label="文件">
            <el-upload drag multiple :auto-upload="false" :file-list="state.files"
                       accept=".docx,.pdf,.mp3,.wav,.m4a,.ogg,.flac"
                       :on-change="onFileChange" :on-remove="onFileRemove">
              <div style="font-size:12.5px;line-height:1.9">把原卷、答案、听力音频拖到这里，或<em style="color:#2f5bea">点击选择</em></div>
            </el-upload>
          </el-form-item>
          <div class="full" style="display:flex;align-items:center;gap:10px">
            <el-button type="primary" :loading="state.uploading" @click="submitUpload">上传并开始处理</el-button>
            <span class="hint">文件名含“答案/解析”的会自动识别为答案卷</span>
          </div>
        </el-form>
      </el-card>
      <h2 class="batch-list-title">本地试卷批次</h2>
      <el-table :data="reviewPapers" v-loading="state.batchesLoading" empty-text="还没有批次，请先上传试卷" size="small">
        <el-table-column label="试卷" min-width="220">
          <template #default="{ row }"><div>{{ row.paper.title }}</div><div class="hint">批次：{{ row.name }}</div></template>
        </el-table-column>
        <el-table-column label="当前流程" min-width="200"><template #default="{ row }">{{ row.stage.hint }}</template></el-table-column>
        <el-table-column label="操作" width="240">
          <template #default="{ row }">
            <el-button size="small" @click="openBatch(row.name)">进入审核</el-button>
            <el-button size="small" :disabled="row.advance?.running" @click="advance(row.name)">继续处理</el-button>
            <el-button size="small" type="danger" text :disabled="row.advance?.running" @click="openArchive(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- ---------------------------------------------------------- 工作台 -->
    <div v-else class="main">
      <div class="col side">
        <div class="side-title">需要审核的试卷</div>
        <div class="paper-list">
          <div v-for="item in reviewPapers" :key="item.name" class="paper-row"
               :class="{ active: item.name === state.batch }" :title="item.paper.title"
               @click="openBatch(item.name)">
            <span class="paper-row-name">{{ item.paper.title }}</span>
            <span v-if="item.needs_human" class="unit-flag" :title="item.needs_human + ' 题待审标签'">{{ item.needs_human }}</span>
            <span v-else class="paper-row-dot"></span>
          </div>
          <div v-if="!reviewPapers.length" class="hint" style="padding:8px 4px">还没有需要审核的试卷。</div>
        </div>
        <div class="stat-grid">
          <div class="stat"><b>{{ state.overview ? state.overview.unit_total : 0 }}</b><span>题组</span></div>
          <div class="stat"><b>{{ state.overview ? state.overview.question_total : 0 }}</b><span>小题</span></div>
          <div class="stat warn"><b>{{ state.overview ? state.overview.needs_human : 0 }}</b><span>待审标签</span></div>
          <div class="stat"><b>{{ state.overview ? (state.overview.counts.ok || 0) : 0 }}</b><span>无误</span></div>
        </div>
        <div v-if="batchFindings.length" class="batch-findings">
          <div class="bf-title">结构待确认（{{ batchFindings.length }}）</div>
          <div v-for="f in batchFindings" :key="f.finding_id" class="bf-item">
            <div class="bf-reason">{{ f.reason }}</div>
            <div class="bf-actions">
              <el-button size="small" type="primary" plain @click="resolveStructure(f, 'fixed')">核对无误</el-button>
              <el-button size="small" @click="resolveStructure(f, 'rejected')">驳回</el-button>
            </div>
          </div>
        </div>
      </div>

      <div class="col list">
        <div class="list-head">
          <el-radio-group v-model="state.filter.tab" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="open">待审</el-radio-button>
            <el-radio-button value="blocked">阻断</el-radio-button>
            <el-radio-button value="unverified">未核对</el-radio-button>
          </el-radio-group>
          <el-input v-model="state.filter.q" size="small" placeholder="题号 / 题型 / 知识点" clearable style="margin-top:6px" />
        </div>
        <div class="list-scroll" v-loading="state.detailLoading">
          <div v-for="row in rows" :key="row.unit_id || row.label" class="unit-row" :class="{ active: isActive(row) }" @click="selectRow(row)">
            <span class="unit-no">{{ row.label }}</span>
            <span class="unit-type">{{ row.type }}</span>
            <span v-if="row.needs_human" class="unit-flag" :title="row.open_count + ' 项待审标签'">{{ row.open_count }}</span>
            <span v-else class="unit-dot" :class="row.state"></span>
          </div>
        </div>
      </div>

      <div class="col work" v-loading="state.detailLoading">
        <template v-if="state.detail">
          <div class="work-head">
            <span class="title">第 {{ state.detail.label }} 题</span>
            <el-tag v-if="state.detail.needs_human" size="small" type="warning" effect="plain">待审标签</el-tag>
            <el-tag v-else size="small" type="success" effect="plain">无待审</el-tag>
            <span class="sub">{{ state.detail.type }} · {{ state.detail.numbers.length }} 小题</span>
            <span class="spacer"></span>
            <el-button size="small" @click="openLabels">修改标签</el-button>
            <el-button size="small" type="primary" :loading="state.saving" @click="saveContent">保存草稿</el-button>
          </div>

          <div class="work-body">
            <div class="work-main">
              <el-card shadow="never" class="panel">
                <template #header>
                  <div style="display:flex;align-items:center;gap:10px;flex-wrap:nowrap">
                    <span style="flex:none;font-weight:600">原卷预览</span>
                    <span v-if="state.detail.preview_labels" class="preview-labels">
                      <span v-if="state.detail.preview_labels.source_type" class="pl-item"><span class="pl-key">来源</span>{{ state.detail.preview_labels.source_type }}</span>
                      <span v-if="state.detail.preview_labels.difficulty" class="pl-item"><span class="pl-key">难度</span>{{ state.detail.preview_labels.difficulty }}</span>
                      <span v-if="state.detail.preview_labels.word_count" class="pl-item"><span class="pl-key">词数</span>{{ state.detail.preview_labels.word_count }}</span>
                      <span v-if="(state.detail.preview_labels.knowledge || []).length" class="pl-item"><span class="pl-key">知识点</span>{{ state.detail.preview_labels.knowledge.join('；') }}</span>
                      <span v-else class="pl-item pl-empty"><span class="pl-key">知识点</span>未标注</span>
                    </span>
                  </div>
                </template>
                <div class="paper">
                  <template v-for="(block, index) in previewBlocks" :key="index">
                    <div v-if="block.type === 'text'" class="line" :class="{ 'paper-heading': isHeading(block.text) }" v-html="markLine(block.text)"></div>
                    <div v-else-if="block.type === 'images'" class="fig">
                      <a v-for="image in block.items" :key="image.name" :href="image.url" target="_blank"><img :src="image.url" alt="" /></a>
                    </div>
                    <table v-else-if="block.type === 'table'" class="source-table">
                      <tbody>
                        <tr v-for="(cells, rowIndex) in block.rows" :key="rowIndex">
                          <template v-if="isHeaderRow(cells)">
                            <th v-for="(cell, cellIndex) in cells" :key="cellIndex" v-html="markLine(cell)"></th>
                          </template>
                          <template v-else>
                            <td v-for="(cell, cellIndex) in cells" :key="cellIndex" v-html="markLine(cell)"></td>
                          </template>
                        </tr>
                      </tbody>
                    </table>
                    <div v-else-if="block.type === 'audio'" class="audio-attach">
                      <span class="audio-label">{{ block.label || '听力音频附件' }}</span>
                      <audio :src="block.url" controls preload="none" style="width:100%;margin-top:4px"></audio>
                      <a :href="block.url" target="_blank" class="hint">下载音频</a>
                    </div>
                  </template>
                </div>
                <div v-if="(state.detail.hidden_media || []).length" class="hidden-note">已隐藏 {{ state.detail.hidden_media.length }} 张非题目图片</div>
              </el-card>

              <el-card shadow="never" class="panel">
                <template #header>
                  <div style="display:flex;align-items:center;gap:8px">
                    内容修改
                    <span style="flex:1"></span>
                    <span class="hint">{{ (state.detail.subquestions || []).length }} 小题，点开修改</span>
                  </div>
                </template>
                <el-collapse v-model="state.openSubs">
                  <el-collapse-item v-for="sub in state.detail.subquestions" :key="sub.number" :name="String(sub.number)">
                    <template #title>
                      <span>第 {{ sub.number }} 题</span>
                      <span class="hint" style="margin-left:8px">{{ sub.type }}</span>
                    </template>
                    <el-form label-position="top" size="small">
                      <el-form-item label="题干">
                        <el-input v-model="sub.stem" type="textarea" :rows="2" />
                      </el-form-item>
                      <el-form-item label="选项（每行一项）">
                        <el-input v-model="sub.optionsText" type="textarea" :rows="4" placeholder="每行一项，如 A. xxx" />
                      </el-form-item>
                      <el-form-item label="答案">
                        <el-input v-model="sub.answer" />
                      </el-form-item>
                    </el-form>
                  </el-collapse-item>
                </el-collapse>
              </el-card>
            </div>

            <div class="find-col">
              <el-card shadow="never" class="panel">
                <template #header>
                  <div style="display:flex;align-items:center;gap:8px">
                    待人工处理
                    <span style="flex:1"></span>
                    <span class="hint">只看一审、二审、三审结论</span>
                  </div>
                </template>
                <div v-if="!(state.detail.label_findings || []).length" class="hint">这一题没有待人工处理的问题。</div>
                <div v-for="finding in state.detail.label_findings" :key="finding.finding_id" class="find-row">
                  <div class="find-head">
                    <span class="field">{{ fieldText(finding.field) }}<template v-if="finding.comparison?.number"> · 第 {{ finding.comparison.number }} 题</template></span>
                    <el-tag size="small" effect="plain" :type="severityType(finding.severity)">{{ severityText(finding.severity) }}</el-tag>
                    <span v-if="finding.merged_count" class="merged">合并 {{ finding.merged_count }} 条</span>
                    <span class="spacer"></span>
                    <span class="status">{{ statusText(finding.status) }}</span>
                  </div>
                  <div class="msg">{{ shortReason(finding) }}</div>
                  <div v-if="finding.comparison" class="round-line">
                    <span class="round-chip">原标注：{{ finding.comparison.original_provided ? comparisonText(finding.comparison.original) : '未提供' }}</span>
                    <span v-for="round in finding.comparison.rounds" :key="round.round_id" class="round-chip">
                      {{ round.name }}：{{ roundText(round, finding) }}
                    </span>
                  </div>
                  <div class="find-actions">
                    <el-button size="small" type="primary" plain @click="openFix(finding)">手动修改</el-button>
                    <el-button size="small" @click="decide(finding, 'accepted')">通过</el-button>
                    <el-button size="small" type="success" plain @click="decide(finding, 'fixed')">已修改</el-button>
                    <el-button size="small" type="info" plain @click="decide(finding, 'rejected')">驳回</el-button>
                  </div>
                </div>
              </el-card>
            </div>
          </div>
        </template>
        <el-empty v-else description="选左边一道题开始审核" />
      </div>
    </div>

    <!-- ---------------------------------------------------------- 标签弹窗 -->
    <el-dialog v-model="state.labelDlg" title="修改标签" width="640px" append-to-body @closed="state.fixTarget = null">
      <el-alert v-if="state.fixTarget" type="warning" :closable="false" show-icon style="margin-bottom:12px"
        :title="'正在修改：' + (state.fixTarget.scope || '本题') + ' · ' + state.fixTarget.field"
        :description="(state.fixTarget.question || '') + '（保存后这条意见会自动标记为已修复）'" />
      <el-form label-position="top" size="small">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="来源类型">
              <el-select v-model="state.form.source_type" clearable filterable style="width:100%">
                <el-option v-for="item in sources" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="年份">
              <el-input v-model="state.form.year" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="地区">
              <el-select v-model="state.form.location" clearable filterable allow-create style="width:100%">
                <el-option v-for="item in regions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="试卷来源">
              <el-input v-model="state.form.source" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="主类（27 类固定）">
              <el-select v-model="state.form.type" clearable filterable style="width:100%">
                <el-option v-for="item in types" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="子类">
              <el-select v-model="state.form.subtypes" multiple collapse-tags clearable style="width:100%"
                         :disabled="!subtypeOptions.length" :placeholder="subtypeOptions.length ? '可多选' : '该主类无子类'">
                <el-option v-for="item in subtypeOptions" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="难度">
              <el-select v-model="state.form.difficulty" clearable style="width:100%">
                <el-option v-for="item in difficulties" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="分类">
              <el-select v-model="state.form.categories" multiple collapse-tags clearable style="width:100%">
                <el-option v-for="item in categories" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="24">
            <el-form-item :label="'知识点（' + (state.detail ? (state.detail.labels.subquestion_count || state.detail.numbers.length) : 1) + ' 小题共用一套）'">
              <div class="knowledge-picker">
                <div class="knowledge-selected" aria-label="已选知识点">
                  <el-tag v-for="path in state.form.knowledge" :key="path" closable @close="removeKnowledge(path)">{{ path }}</el-tag>
                  <span v-if="!state.form.knowledge.length" class="hint">尚未选择知识点，请展开目录后添加叶子。</span>
                </div>
                <el-input v-model="state.knowledgeQuery" clearable placeholder="搜索完整目录，如 细节理解 / 主题" aria-label="搜索知识点目录" />
                <div class="hint" role="status">{{ state.knowledgeLoading ? '正在加载完整目录…' : state.knowledgeError ? '目录加载失败' : '完整目录：' + state.knowledgeTotal + ' 个节点，' + state.knowledgeLeaves + ' 个可选叶子' }}</div>
                <div v-if="state.knowledgeError" role="alert">{{ state.knowledgeError }} <el-button size="small" @click="loadKnowledge">重新加载</el-button></div>
                <div v-else class="knowledge-tree" v-loading="state.knowledgeLoading">
                  <el-tree :key="state.knowledgeQuery" :data="knowledgeTree" node-key="path" :props="{ label: 'name', children: 'children' }"
                           :default-expanded-keys="knowledgeExpanded" :expand-on-click-node="true" empty-text="没有匹配的知识点，请换一个词搜索">
                    <template #default="{ data }">
                      <span class="knowledge-node" :title="data.path">
                        <span>{{ data.name }}</span>
                        <el-button v-if="data.leaf" size="small" text :type="state.form.knowledge.includes(data.path) ? 'danger' : 'primary'"
                                   :aria-label="(state.form.knowledge.includes(data.path) ? '移除 ' : '添加 ') + data.path" @click.stop="toggleKnowledge(data)">
                          {{ state.form.knowledge.includes(data.path) ? '移除' : '添加' }}
                        </el-button>
                      </span>
                    </template>
                  </el-tree>
                </div>
                <div class="hint">仅目录叶子可添加；已选项可逐个移除。组级写整段材料的角度。</div>
              </div>
            </el-form-item>
          </el-col>
        </el-row>
        <el-alert v-if="state.warnings.length" type="warning" :closable="false" class="kp-warning"
                  :title="'目录校验：' + state.warnings.length + ' 条提醒'">
          <div v-for="(warning, index) in state.warnings" :key="index" class="hint">{{ warning }}</div>
        </el-alert>
      </el-form>
      <template #footer>
        <el-button size="small" @click="state.labelDlg = false">取消</el-button>
        <el-button size="small" type="primary" :loading="state.saving" @click="saveLabels">保存标签</el-button>
      </template>
    </el-dialog>

    <el-dialog :model-value="!!state.archiveTarget" @update:model-value="value => { if (!value && !state.archiving) state.archiveTarget = null; }"
               title="删除本地批次" width="min(520px, calc(100vw - 32px))" append-to-body :close-on-click-modal="false" :close-on-press-escape="!state.archiving" :show-close="!state.archiving">
      <template v-if="state.archiveTarget">
        <p>将移出本地审核列表的试卷：<strong>{{ state.archiveTarget.paper.title }}</strong></p>
        <p>批次目录：<strong class="archive-name">{{ state.archiveTarget.name }}</strong></p>
        <p class="hint">批次及同名收件箱会移到工作区 .workbench-trash 回收目录，保留原文件与恢复记录；随后可以重新上传录入。正在运行的批次无法删除。</p>
        <el-input v-model="state.archiveConfirm" :disabled="state.archiving" :placeholder="'输入批次目录名：' + state.archiveTarget.name" aria-label="确认要删除的批次目录名" />
      </template>
      <template #footer>
        <el-button size="small" :disabled="state.archiving" @click="state.archiveTarget = null">取消</el-button>
        <el-button size="small" type="danger" :loading="state.archiving" :disabled="state.archiveConfirm !== state.archiveTarget?.name" @click="archiveBatch">移到回收目录</el-button>
      </template>
    </el-dialog>

    <!-- ------------------------------------------------------ 待处理意见弹窗 -->
    <el-dialog v-model="state.findDlg" title="修改处理意见" width="520px" append-to-body>
      <el-form label-position="top" size="small">
        <el-form-item label="问题字段">
          <el-input :model-value="state.findForm.field" disabled />
        </el-form-item>
        <el-form-item label="问题说明">
          <el-input :model-value="state.findForm.question" type="textarea" :rows="3" disabled />
        </el-form-item>
        <el-form-item label="处理状态">
          <el-select v-model="state.findForm.status" style="width:100%">
            <el-option label="待处理" value="open" />
            <el-option label="已接受" value="accepted" />
            <el-option label="已修复" value="fixed" />
            <el-option label="驳回" value="rejected" />
          </el-select>
        </el-form-item>
        <el-form-item label="处理意见（可留空）">
          <el-input v-model="state.findForm.note" type="textarea" :rows="3" placeholder="如：知识点已改为 设题角度/听力/细节理解" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button size="small" @click="state.findDlg = false">取消</el-button>
        <el-button size="small" type="primary" @click="saveFinding">保存</el-button>
      </template>
    </el-dialog>
  </div>`,
};

const app = createApp(App);
app.use(ElementPlus, { locale: ElementPlusLocaleZhCn });
if (window.ElementPlusIconsVue) {
  Object.entries(window.ElementPlusIconsVue).forEach(([name, icon]) => app.component(name, icon));
}
app.mount('#app');
