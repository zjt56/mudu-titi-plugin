    // ======================================================================
    // 修改标签弹窗（左右两列：左列知识点目录树，右列题型/子类/难度/分类/年份/来源）
    // ======================================================================
    function LabelModal(props) {
      const React = require('react');
      const h = React.createElement;
      const { tax, labels, setL, warnings, busy, saveError, onSave, onClose,
        unitLabel, unitKind, numbers, fetchKnowledgeTree } = props;
      const nums = (numbers || []).map((n) => String(n));
      const labelText = String(unitLabel == null ? '' : unitLabel).trim();
      const isGroup = unitKind === 'group' || nums.length > 1;
      let rangeText = '';
      if (nums.length > 1) {
        const sorted = nums.slice().sort((a, b) => (Number(a) || 0) - (Number(b) || 0));
        rangeText = sorted[0] + '–' + sorted[sorted.length - 1];
      }
      let qTitle = '';
      if (isGroup && rangeText) qTitle = '第 ' + rangeText + ' 题';
      else if (labelText) qTitle = '第 ' + labelText + ' 题';
      else if (nums.length === 1) qTitle = '第 ' + nums[0] + ' 题';
      const qTag = isGroup
        ? qTitle + '（题组·' + nums.length + ' 小题）' : qTitle;
      const subs = (tax.subtypes || {})[labels?.type] || [];
      const diffOpts = (Array.isArray(tax.difficulties) && tax.difficulties.length)
        ? tax.difficulties : ['容易', '适中', '困难'];
      const catOpts = (Array.isArray(tax.categories) && tax.categories.length)
        ? tax.categories : ['优选题', '易错题'];
      const selectedCats = Array.isArray(labels?.categories) ? labels.categories : [];
      return h(ModalShell, {
        title: '修改标签', tag: qTag, width: 820, onClose,
        footer: [
          h('button', { key: 'cancel', className: 'wf-btn', style: T.btn, onClick: onClose }, '取消'),
          h('button', { key: 'save', className: 'wf-btn-primary', style: T.btnPrimary, onClick: onSave, disabled: busy === 'save' }, busy === 'save' ? TEXT.saving : TEXT.saveLabels),
        ],
      },
        h(React.Fragment, null,
          h('div', { className: 'wf-label-modal-grid' },
            // 左列：搜索 + 已选知识点 + 题型限定知识点目录树
            h('div', { role: 'group', 'aria-label': TEXT.knowledge, style: { minWidth: 0 } },
              h(KnowledgePicker, {
                paths: labels?.knowledge || [],
                mainType: labels?.type || '',
                onChange: (arr) => setL('knowledge', arr),
                fetchTree: fetchKnowledgeTree,
              }),
            ),
            // 右列：题型、子类、难度（3按钮）、分类（2按钮）、年份（直接填写）、来源类型
            h('div', { className: 'wf-label-right' },
              formField(TEXT.type, h('select', {
                className: 'wf-input',
                style: { ...T.input, width: '100%' },
                'aria-label': TEXT.type,
                value: labels?.type || '',
                onChange: (e) => { setL('type', e.target.value); setL('subtypes', []); },
              },
                h('option', { value: '' }, '—'),
                (tax.types || []).map((t) => h('option', { key: t, value: t }, t)),
              )),
              formField(TEXT.subtype, h('select', {
                className: 'wf-input',
                style: { ...T.input, width: '100%' },
                'aria-label': TEXT.subtype,
                disabled: !subs.length,
                value: (labels?.subtypes || [])[0] || '',
                onChange: (e) => setL('subtypes', e.target.value ? [e.target.value] : []),
              },
                h('option', { value: '' }, subs.length ? '—' : '无子类'),
                subs.map((s) => h('option', { key: s, value: s }, s)),
              )),
              formField(TEXT.difficulty, h('div', { className: 'wf-seg-btn-row cols-3', role: 'radiogroup', 'aria-label': TEXT.difficulty },
                diffOpts.map((d) => {
                  const active = (labels?.difficulty || '') === d;
                  return h('button', {
                    key: d,
                    type: 'button',
                    role: 'radio',
                    'aria-checked': active ? 'true' : 'false',
                    className: 'wf-seg-btn' + (active ? ' is-active' : ''),
                    onClick: () => setL('difficulty', d),
                  }, d);
                }),
              )),
              formField(TEXT.categories, h('div', { className: 'wf-seg-btn-row cols-2', role: 'group', 'aria-label': TEXT.categories },
                catOpts.map((cat) => {
                  const active = selectedCats.includes(cat);
                  return h('button', {
                    key: cat,
                    type: 'button',
                    'aria-pressed': active ? 'true' : 'false',
                    className: 'wf-seg-btn' + (active ? ' is-active' : ''),
                    onClick: () => {
                      const clean = selectedCats.filter((x) => x && x !== '未填写');
                      const next = active ? clean.filter((x) => x !== cat) : [...clean, cat];
                      setL('categories', next);
                    },
                  }, cat);
                }),
              )),
              formField('年份', h('input', {
                className: 'wf-input',
                style: { ...T.input, width: '100%' },
                'aria-label': '年份',
                value: labels?.year || '',
                placeholder: '如 2026',
                onChange: (e) => setL('year', e.target.value),
              })),
              formField(TEXT.sourceType, h('select', {
                className: 'wf-input',
                style: { ...T.input, width: '100%' },
                'aria-label': TEXT.sourceType,
                value: labels?.source_type || '',
                onChange: (e) => setL('source_type', e.target.value),
              },
                h('option', { value: '' }, '未选择'),
                labels?.source_type && !(tax.sources || []).includes(labels.source_type)
                  ? h('option', { value: labels.source_type, disabled: true }, labels.source_type + '（当前值，请从目录选择）') : null,
                (tax.sources || []).map((source) => h('option', { key: source, value: source }, source)),
              )),
            ),
          ),
          saveError ? h('div', { role: 'alert', style: { ...T.hint, color: 'var(--dsw-alias-state-error-primary)', marginTop: 10 } }, '保存失败：' + saveError) : null,
          (warnings || []).length ? h('div', { style: { ...T.hint, color: 'var(--dsw-alias-state-warn-primary)', marginTop: 10 } }, '提示：' + (warnings || []).join('；')) : null,
        ),
      );
    }

    // 原卷修改弹窗：仅编辑正文/材料（整组共用）+ 逐小题题干/选项/答案；文章大意与解析由下方专用卡片编辑。
    // 组题按每个小题单独折叠（点一题开一题），支持一键展开/收起全部小题。
    function ContentModal(props) {
      const React = require('react');
      const h = React.createElement;
      const { content, detail, onSub, onUnit, onSave, onClose, busy, dirty, saveError } = props;
      const saving = busy === 'content';
      const subs = (content && content.subs) || [];
      const qType = String((detail && detail.type) || (detail && detail.item && detail.item.question_type) || '');
      const isGroup = (detail && (detail.unit_kind === 'group' || (detail.numbers || []).length > 1));
      const noChildStemGroup = isGroup && /(完形|短文填空|选词填空|语法选择|补全对话|教材语篇)/.test(qType);
      const isChoiceType = /(选择|单选|完形|阅读理解|语法选择|语音|听力)/.test(qType);
      const isBlankStem = (val) => {
        const t = String(val || '').replace(/<\/?u[^>]*>/gi, '').replace(/[_\s\u3000]/g, '');
        return !t;
      };
      const [openSubs, setOpenSubs] = React.useState(() => isGroup ? {} : subs.reduce((m, s) => { m[String(s.number)] = true; return m; }, {}));
      const toggleSub = (num) => setOpenSubs((x) => ({ ...x, [String(num)]: !x[String(num)] }));
      const allOpen = subs.length > 0 && subs.every((s) => !!openSubs[String(s.number)]);
      const toggleAllSubs = () => setOpenSubs(allOpen ? {} : subs.reduce((m, s) => { m[String(s.number)] = true; return m; }, {}));
      const nums = ((detail && detail.numbers) || []).map(String);
      const range = nums.length > 1 ? (nums.slice().sort((a, b) => (Number(a) || 0) - (Number(b) || 0)).reduce((acc, n, i, arr) => i === 0 ? n : (i === arr.length - 1 ? acc + '–' + n : acc), '')) : (nums[0] || '');
      const qTag = range ? ('第 ' + range + ' 题' + (isGroup ? '（题组·' + nums.length + ' 小题）' : '')) : '';
      const { fontSize: _size, lineHeight: _height, ...paperInput } = T.input;
      const materialLen = String((content && content.material) || '').length;
      return h(ModalShell, {
        title: '原卷修改', tag: qTag, dirty, width: 760, onClose,
        footer: h('div', { style: { width: '100%' } },
          saveError ? h('div', { role: 'alert', style: { ...T.hint, color: 'var(--dsw-alias-state-error-primary)', marginBottom: 8, overflowWrap: 'anywhere' } }, '保存失败：' + saveError) : null,
          h('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 8 } },
            h('button', { className: 'wf-btn', style: T.btn, onClick: onClose, disabled: saving }, '取消'),
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: onSave, disabled: saving || !dirty }, saving ? '保存中…' : '保存内容'),
          ),
        ),
      },
        h('div', { className: 'wf-cm-body' },
          h('div', { className: 'wf-cm-notice' },
            h('span', { className: 'wf-cm-notice-dot', 'aria-hidden': 'true' }),
            h('span', null, '此处仅修改原卷正文/材料与小题题干、选项、答案（保存为草稿）；文章大意与答案解析请在下方「文章大意 · 解析」卡片中修改。'),
          ),
          h('div', { className: 'wf-cm-section' },
            h('div', { className: 'wf-cm-section-head' },
              h('div', { className: 'wf-cm-section-title' },
                '正文 / 材料',
                h('span', { className: 'wf-cm-section-sub' }, '整组共用 · 含听力原文、阅读原文'),
              ),
              h('span', { className: 'wf-cm-meta' }, materialLen ? materialLen + ' 字' : '空'),
            ),
            h('textarea', {
              className: 'wf-input wf-paper-input',
              value: content.material,
              rows: isGroup ? 9 : 5,
              placeholder: '输入或修改整组共用的正文、阅读材料或听力原文…',
              style: { ...paperInput, width: '100%', resize: 'vertical', minHeight: isGroup ? 170 : 104 },
              onChange: (e) => onUnit('material', e.target.value),
            }),
          ),
          subs.length === 0 ? null : h('div', null,
            h('div', { className: 'wf-cm-section-head', style: { padding: '0 2px' } },
              h('div', { className: 'wf-cm-section-title' },
                '小题内容',
                h('span', { className: 'wf-cm-section-sub' }, '共 ' + subs.length + ' 小题'),
              ),
              subs.length > 1 ? h('button', { type: 'button', className: 'wf-cm-toggle-btn', onClick: toggleAllSubs }, allOpen ? '全部收起' : '全部展开') : null,
            ),
            h('div', { className: 'wf-cm-sub-list' },
              subs.map((s, i) => {
                const open = !!openSubs[String(s.number)];
                const showStem = !noChildStemGroup && (!isGroup || !isBlankStem(s.stem));
                const showOptions = Boolean(String(s.optionsText || '').trim()) || isChoiceType;
                const previewText = showStem ? String(s.stem || '').replace(/\s+/g, ' ').trim().slice(0, 48) : String(s.optionsText || '').replace(/\n+/g, ' ').trim().slice(0, 48);
                const ansPreview = String(s.answer || '').trim();
                return h('div', { key: String(s.number), className: 'wf-cm-sub-card' + (open ? ' is-open' : '') },
                  h('button', { type: 'button', className: 'wf-cm-sub-head', 'aria-expanded': open, onClick: () => toggleSub(s.number) },
                    h('span', { className: 'wf-cm-sub-num' }, s.display_number === null ? '—' : s.number),
                    h('span', { style: { fontSize: 13, fontWeight: 600, flexShrink: 0 } }, s.display_number === null ? '原卷未标小题号' : '第 ' + s.number + ' 题'),
                    h('span', { style: { ...T.hint, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, previewText || '（暂无预览）'),
                    ansPreview ? h('span', { className: 'wf-cm-sub-ans', title: '当前答案：' + ansPreview }, '答 ' + ansPreview) : null,
                    h('span', { className: 'wf-cm-chevron', 'aria-hidden': 'true' }, '▸'),
                  ),
                  open ? h('div', { className: 'wf-cm-sub-body' },
                    showStem ? formField('题干', h('textarea', { className: 'wf-input wf-paper-input', value: s.stem, rows: 2, placeholder: '输入题干内容…', style: { ...paperInput, width: '100%', resize: 'vertical' }, onChange: (e) => onSub(i, 'stem', e.target.value) })) : null,
                    showOptions ? formField('选项（每行一项）', h('textarea', { className: 'wf-input wf-paper-input', value: s.optionsText, rows: 4, placeholder: '每行一项，如 A. xxx', style: { ...paperInput, width: '100%', resize: 'vertical' }, onChange: (e) => onSub(i, 'optionsText', e.target.value) })) : null,
                    formField('答案', h('input', { className: 'wf-input wf-paper-input', value: s.answer, placeholder: '输入参考答案…', style: { ...paperInput, width: '100%' }, onChange: (e) => onSub(i, 'answer', e.target.value) })),
                  ) : null,
                );
              }),
            ),
          ),
        ),
      );
    }

