    // ======================================================================
    // 审核页只读内容预览：文章大意 / 解析
    // 与原卷预览并列，数据取 detail.item（与「原卷修改」同一份草稿），只读；
    // 编辑仍走「原卷修改」，这里不回写、不生成、不改上传边界。
    // ======================================================================
    // 占位不是正文：空与规则允许的「无/未提供/未填写」一律按缺失处理。
    const CONTENT_PLACEHOLDERS = new Set(['无', '未提供', '未填写', 'n/a', 'none']);
    function contentText(value) {
      const text = value == null ? '' : String(value).replace(/\r\n?/g, '\n').trim();
      return text && !CONTENT_PLACEHOLDERS.has(text.toLowerCase()) ? text : '';
    }
    // 把 detail 归一成卡片模型：整卷/组题解析、文章大意、逐题解析与缺失统计。
    function contentPreviewModel(detail) {
      const item = (detail && detail.item) || {};
      const subs = Array.isArray(detail && detail.subquestions) ? detail.subquestions : [];
      const byNumber = new Map(subs.filter((s) => s && s.number != null).map((s) => [String(s.number), s]));
      const rows = (Array.isArray(item.subquestions) ? item.subquestions : [])
        .filter((s) => s && s.number != null)
        .map((s) => {
          const sub = byNumber.get(String(s.number)) || {};
          // 无印刷题号只能用内部身份定位并显示「—」，绝不把内部号伪装成印刷题号。
          const display = sub.display_number !== undefined ? sub.display_number : s.number;
          return {
            number: s.number,
            display: display == null ? '—' : String(display),
            unnumbered: display == null,
            answer: contentText(s.answer) || contentText(sub.answer),
            text: contentText(s.explanation),
          };
        });
      const unitText = contentText(item.explanation);
      const summaryText = contentText(item.articleSummary);
      const single = rows.length === 1 ? rows[0] : null;
      const type = String((detail && detail.type) || item.question_type || '');
      // 独立题没有逐题行时，答案挂在解析块标题上，避免为了一个答案再造一行小题。
      const unitAnswer = rows.length ? '' : contentText((subs[0] || {}).answer);
      return {
        summary: summaryText,
        // 整题解析与唯一小题解析逐字相同就只显示一次，避免同一段出现两遍。
        unit: single && unitText === single.text ? '' : unitText,
        rows,
        withText: rows.filter((r) => r.text).length,
        missing: rows.filter((r) => !r.text).length,
        unitAnswer,
        written: /书面表达/.test(type),
        group: String((detail && detail.unit_kind) || item.unit_kind || '') === 'group',
      };
    }
    function ContentPreviewCard(props) {
      const React = require('react');
      const h = React.createElement;
      const { detail, onEdit } = props;
      const model = contentPreviewModel(detail);
      const [parseExpanded, setParseExpanded] = React.useState(false);
      function normalText(text) {
        return h('div', {
          className: 'wf-content-text',
          style: { fontWeight: 400 },
          dangerouslySetInnerHTML: { __html: markLine(String(text || '').replace(/\*\*/g, ''), false) },
        });
      }
      const sectionHead = (title, badge) => h('div', { className: 'wf-content-head' },
        h('span', { className: 'wf-content-title' }, title),
        h('span', { style: { flex: 1 } }),
        badge,
      );
      const summaryIsAi = model.summary && model.summary.includes('（AI生成');
      const summaryBadge = model.summary
        ? h('span', { style: T.badge(summaryIsAi ? 'warn' : '') }, summaryIsAi ? 'AI生成' : '已有')
        : null;
      const unitIsAiTranscript = !!(model.unit && model.unit.includes('（AI转录'));
      const unitIsAiGen = !!(model.unit && model.unit.includes('（AI生成'));
      const aiTranscriptBadge = unitIsAiTranscript
        ? h('span', { style: T.badge('warn'), title: '由录音 AI 转录并去重整理，可能存在识别误差，请人工核对' }, 'AI转录')
        : null;
      const parseBadge = model.rows.length
        ? h('span', { style: { display: 'inline-flex', alignItems: 'center', gap: 6 } },
            aiTranscriptBadge,
            h('span', { style: T.badge(''), title: '已填解析的小题数 / 小题总数' },
              '已填 ' + model.withText + '/' + model.rows.length))
        : (model.unit
            ? (aiTranscriptBadge || h('span', { style: T.badge(unitIsAiGen ? 'warn' : '') }, unitIsAiGen ? 'AI生成' : '整题'))
            : null);
      const renderRow = (row) => h('div', {
        key: 'row:' + row.number,
        className: 'wf-content-row',
        style: { padding: '8px 0' },
      },
        h('span', { className: 'wf-content-num', title: row.unnumbered ? '原卷未标小题号' : '第 ' + row.display + ' 题' }, row.display),
        h('div', { className: 'wf-content-row-body', style: { gap: 5 } },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' } },
            row.answer
              ? h('span', { className: 'wf-content-ans', style: { color: 'var(--dsw-alias-label-primary)', fontWeight: 600 }, title: '答案' }, '答案：' + row.answer)
              : h('span', { style: { ...T.hint, fontSize: 11.5 } }, '暂无答案'),
            row.text && row.text.includes('（AI转录')
              ? h('span', { style: { ...T.badge('warn'), fontSize: 10.5, padding: '0 5px' }, title: '由录音 AI 转录并去重整理，可能存在识别误差，请人工核对' }, 'AI转录')
              : (row.text && row.text.includes('（AI生成')
                ? h('span', { style: { ...T.badge('warn'), fontSize: 10.5, padding: '0 5px' } }, 'AI生成')
                : null),
          ),
          row.text
            ? normalText(row.text)
            : h('span', { style: { ...T.hint, fontSize: 12 } }, '暂无解析'),
        ),
      );
      const summarySection = h('div', { key: 'summary', className: 'wf-content-section', style: { marginTop: 6 } },
        sectionHead('文章大意', summaryBadge),
        model.summary
          ? h('div', { className: 'wf-content-quote' }, normalText(model.summary))
          : h('div', { className: 'wf-content-empty', style: T.hint },
              model.written ? '书面表达不要求文章大意。' : '暂无文章大意'),
      );
      const parseSection = h('div', { key: 'parse', className: 'wf-content-section' },
        sectionHead('答案解析', parseBadge),
        model.unit ? h('div', { className: 'wf-content-quote', style: { marginBottom: model.rows.length ? 8 : 0 } },
          h('div', { className: 'wf-content-sub-label', style: { fontWeight: 500 } },
            model.group ? '整组解析' : '本题答案与解析',
            (!model.group && model.unitAnswer)
              ? h('span', { className: 'wf-content-ans', style: { color: 'var(--dsw-alias-label-primary)' }, title: '答案' }, '答案：' + model.unitAnswer)
              : null),
          normalText(model.unit),
        ) : (!model.rows.length && !model.group && model.unitAnswer
          ? h('div', { className: 'wf-content-quote', style: { marginBottom: 6 } },
              h('span', { className: 'wf-content-ans', style: { color: 'var(--dsw-alias-label-primary)' } }, '答案：' + model.unitAnswer))
          : null),
        model.rows.length ? h('div', { className: 'wf-content-rows' },
          (model.rows.length > 1 && !parseExpanded ? model.rows.slice(0, 1) : model.rows).map(renderRow),
          model.rows.length > 1 ? h('div', { style: { display: 'flex', justifyContent: 'flex-start', paddingTop: 6, borderTop: '1px dashed var(--dsw-alias-border-l1)' } },
            h('button', {
              type: 'button',
              className: 'wf-content-link',
              'aria-expanded': parseExpanded,
              onClick: () => setParseExpanded((v) => !v),
            }, parseExpanded ? '收起（仅显示第 1 题）' : '展开其余 ' + (model.rows.length - 1) + ' 题'),
          ) : null,
        ) : null,
        !model.unit && !model.rows.length && (model.group || !model.unitAnswer)
          ? h('div', { className: 'wf-content-empty', style: T.hint },
              model.written ? '书面表达不要求解析。' : '暂无答案解析')
          : null,
      );
      // 「修改」固定在卡片右上角。
      return h('div', { style: { ...T.card, marginTop: 12, marginBottom: 0, position: 'relative' } },
        onEdit ? h('button', {
          type: 'button',
          className: 'wf-btn-primary',
          style: { ...T.btnPrimary, position: 'absolute', top: 10, right: 12, zIndex: 1 },
          onClick: onEdit,
        }, '修改') : null,
        summarySection,
        parseSection,
      );
    }

    function ContentEditModal(props) {
      const React = require('react');
      const h = React.createElement;
      const { detail, busy, saveError, onSave, onClose } = props;
      const item = (detail && detail.item) || {};
      const subs = Array.isArray(item.subquestions) ? item.subquestions : [];
      const isGroup = (detail && detail.unit_kind === 'group') || subs.length > 0;
      const initial = React.useMemo(() => ({
        summary: String(item.articleSummary ?? item.article_summary ?? ''),
        unitAns: String(item.answer ?? ''),
        unitExpl: String(item.explanation ?? ''),
        subs: subs.map((s, idx) => ({
          number: s.number !== undefined && s.number !== null ? s.number : idx + 1,
          answer: String(s.answer ?? ''),
          explanation: String(s.explanation ?? ''),
        })),
      }), [detail]);
      const [summary, setSummary] = React.useState(initial.summary);
      const [unitAns, setUnitAns] = React.useState(initial.unitAns);
      const [unitExpl, setUnitExpl] = React.useState(initial.unitExpl);
      const [subEdits, setSubEdits] = React.useState(() => initial.subs.map((row) => ({ ...row })));
      const saving = busy === 'contentEdit';
      const setSub = (idx, key, val) => setSubEdits((prev) => prev.map((row, i) => i === idx ? { ...row, [key]: val } : row));
      const { fontSize: _size, lineHeight: _height, ...paperInput } = T.input;
      const submit = () => {
        const patch = { unit_id: detail.unit_id };
        const expected = {};
        if (summary !== initial.summary) {
          patch.articleSummary = summary;
          expected.articleSummary = initial.summary;
        }
        if (!subs.length) {
          if (unitAns !== initial.unitAns) {
            patch.answer = unitAns;
            expected.answer = initial.unitAns;
          }
          if (unitExpl !== initial.unitExpl) {
            patch.explanation = unitExpl;
            expected.explanation = initial.unitExpl;
          }
        } else {
          if (unitExpl !== initial.unitExpl) {
            patch.explanation = unitExpl;
            expected.explanation = initial.unitExpl;
          }
          const changedSubs = [];
          subEdits.forEach((row, idx) => {
            const orig = initial.subs[idx];
            if (!orig) return;
            const ansChanged = row.answer !== orig.answer;
            const explChanged = row.explanation !== orig.explanation;
            if (!ansChanged && !explChanged) return;
            const subPatch = { number: row.number };
            const subExpected = {};
            if (ansChanged) {
              subPatch.answer = row.answer;
              subExpected.answer = orig.answer;
            }
            if (explChanged) {
              subPatch.explanation = row.explanation;
              subExpected.explanation = orig.explanation;
            }
            subPatch.expected = subExpected;
            changedSubs.push(subPatch);
          });
          if (changedSubs.length) patch.subquestions = changedSubs;
        }
        if (Object.keys(patch).length <= 1) {
          onClose();
          return;
        }
        if (Object.keys(expected).length) patch.expected = expected;
        onSave(patch);
      };
      return h(ModalShell, {
        title: '修改文章大意与答案解析',
        tag: detail?.label ? '第 ' + detail.label + ' 题' : '',
        width: 660,
        onClose: () => { if (!saving) onClose(); },
        footer: h('div', { style: { width: '100%' } },
          saveError ? h('div', { role: 'alert', style: { ...T.hint, color: 'var(--dsw-alias-state-error-primary)', marginBottom: 8 } }, '保存失败：' + saveError) : null,
          h('div', { style: { display: 'flex', justifyContent: 'flex-end', gap: 8 } },
            h('button', { className: 'wf-btn', style: T.btn, onClick: onClose, disabled: saving }, '取消'),
            h('button', { className: 'wf-btn-primary', style: T.btnPrimary, onClick: submit, disabled: saving }, saving ? '保存中…' : '保存'),
          ),
        ),
      },
        h('div', { style: { display: 'flex', flexDirection: 'column', gap: 14 } },
          isGroup ? formField('文章大意',
            h('textarea', {
              className: 'wf-input wf-paper-input',
              rows: 3,
              value: summary,
              placeholder: '输入文章大意（原卷没有可留空或填「无」）',
              style: { ...paperInput, width: '100%', resize: 'vertical' },
              onChange: (e) => setSummary(e.target.value),
            })) : null,
          !subs.length ? h(React.Fragment, null,
            formField('答案',
              h('input', {
                className: 'wf-input wf-paper-input',
                value: unitAns,
                style: { ...paperInput, width: '100%' },
                onChange: (e) => setUnitAns(e.target.value),
              })),
            formField('答案解析',
              h('textarea', {
                className: 'wf-input wf-paper-input',
                rows: 4,
                value: unitExpl,
                style: { ...paperInput, width: '100%', resize: 'vertical' },
                onChange: (e) => setUnitExpl(e.target.value),
              })),
          ) : h('div', { style: { display: 'flex', flexDirection: 'column', gap: 10 } },
            h('span', { style: T.fieldLabel }, '各小题答案与解析'),
            subEdits.map((row, idx) => h('div', {
              key: String(row.number),
              style: { padding: '10px 12px', border: '1px solid var(--dsw-alias-border-l1)', borderRadius: 8, background: 'var(--dsw-alias-bg-layer-2)', display: 'flex', flexDirection: 'column', gap: 8 },
            },
              h('div', { style: { display: 'flex', alignItems: 'center', gap: 8 } },
                h('span', { className: 'wf-content-num' }, String(row.number)),
                h('span', { style: { fontSize: 12, color: 'var(--dsw-alias-label-secondary)', flexShrink: 0 } }, '答案'),
                h('input', {
                  className: 'wf-input wf-paper-input',
                  value: row.answer,
                  style: { ...paperInput, flex: 1, minWidth: 0 },
                  onChange: (e) => setSub(idx, 'answer', e.target.value),
                }),
              ),
              h('textarea', {
                className: 'wf-input wf-paper-input',
                rows: 2,
                value: row.explanation,
                placeholder: '输入该小题答案解析',
                style: { ...paperInput, width: '100%', resize: 'vertical' },
                onChange: (e) => setSub(idx, 'explanation', e.target.value),
              }),
            )),
          ),
        ),
      );
    }

