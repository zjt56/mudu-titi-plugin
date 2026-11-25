# AGENTS.md 硬规则落地细节

本文件从 `AGENTS.md` 第二节原样移出（2026-11-25），记录每条硬规则的落地文件、函数与回归测试。规则原则以 AGENTS.md 为准；改规则时两处同步，编号一一对应（原文有两条 43，第二条记为“43（第2条）”）。

## 规则 6：审核范围

- 落地：`app/workflow/diagnose.py`、`app/workflow/operations.py`。

## 规则 9：质量≠执行

- 落地：`app/workflow/findings.py`、`checks.py`、`handoff.py`、`batch_fill.py`、`final_audit.py`、`self_heal.py`。
- 落地：`app/scripts/_common.py::report`、`workflow/checks.py`（画线/空档/表格/完成句子设为 `blocking=False`）。
- 落地：`structure_gate._build_scoped_units`、`agent_tasks._validate(types)` 与 `accept_result` findings 降级、`app/prompts/roles/{preassemble,types}.md`。
- 落地：`structure_gate._scope_anchors`、`preview._weak_numbers`；回归 `test_structure_gate::test_cloze_blank_markers_are_not_printed_anchors`、`::test_cloze_group_with_inline_blank_numbers_passes_scoped_gate`。

## 规则 12：原卷预览图片显示

- 落地：`app/workbench/server.py::source_blocks`/`attach_media`、`lib/client.js` 表格样式。

## 规则 13：题组材料必须在题号前完整绑定

- 落地：`app/workflow/question_draft.py::_bound_source`。

## 规则 14：预览不得丢题组材料

- 落地：`app/workflow/preview.py::slice_range`。
- 落地：`app/workflow/question_draft.py::_section_rubric`、`question_content.py::SECTION_LEAD`。

## 规则 15：听力并行、非听力内容审核不等待听力（2026-11-21 用户确认方案 A）

- 落地：`app/workflow/contracts.py`、`app/workflow/review_rounds.py`、`app/workflow/agent.py`。

## 规则 16：Word 表格空白网格位置不得丢失

- 落地：`app/workflow/question_draft.py::_source_table_rows`、`app/workflow/self_heal_actions.py::_prepare_tables`。

## 规则 17：格式诊断必须区分失败与延期检查

- 落地：`app/workflow/diagnose.py::_format_detail`、`app/workflow/format_gate.py`。

## 规则 18：Harness 运维为附加能力

- 落地：`app/workflow/operations.py`、`app/workflow/operations_development.py`、`lib/operations-agent.js`；说明见 `app/docs/harness-operations.md`。

## 规则 19：提示词示例即契约（示例与校验器同源）

- 落地：`app/workflow/agent_tasks.py::EXAMPLE_REQUIRED`、`_build_task_template`、`_fields`、`_numbers_preview`；回归 `app/workflow/test_agent_tasks.py::test_every_stage_example_satisfies_its_declared_contract`、`::test_answers_task_example_carries_every_required_field`。

## 规则 21：删除批次必须彻底删除，不留本地痕迹

- 落地：`app/workbench/server.py::delete_batch/_cancel_running_batch`、`app/workflow/auto.py::cancel_requested/request_cancel`、`lib/client.js` 继续处理区「删除」入口。

## 规则 22：内容来源优先级（Word > PDF > 空占位）

- 落地：`explanations_direct._pdf_sources`、`upload_content` 的 attachment 兜底；提示词 `content_rules.RULES`、`RULES["answers"]`、`app/prompts/roles/explanations.md`、`roles/answers.md`。**防遗漏交付约束**：`【解析】`、`[解析]`、`[IMG ...]解析[IMG ...]` 及对应文章大意标题使用同源代码规则识别，装饰图片不得作为缺失理由；裸图片不证明解析含义。大意按来源坐标与完整题号集合绑定，禁止按列表顺序分配。直抄、source_ref、模型字符串共用来源/归属/遗漏校验，说明文字不能豁免；`ORIGINAL_EXPLANATION_OMITTED` / `ORIGINAL_SUMMARY_OMITTED` 必须定位原文局部修复复验，禁止改完成状态、改无关标签或跳检查。组装及后续元数据/画线/最终产物须同步已接受内容；正文与展示消费链路都要验证。落地：`content_labels.py`、`content_fragments.py`、`content_rules.py`、`unit_tasks.py`、`handoff.py`、`manifest.py`；全量任务、文件协议、并行分片和局部修复提示词都须包含该约束，回归必须验证实际构建出的任务提示。书面表达仍按规则23。

## 规则 23：书面表达不按客观题校验

- 落地：`upload_content` 对 `书面表达` 只告警不报错、`agent_tasks` 答案覆盖豁免、`check_answers`/`check_explanations`/`publish_check` 书面表达豁免；提示词 `content_rules.RULES`、`RULES["answers"]`、`roles/explanations.md`、`roles/answers.md`。

## 规则 24：卡点必须落台账（`ISSUES/`）

- 落地：`ISSUES/README.md`、`app/workflow/issue_log.py`、`app/workflow/self_heal.py`、`app/workflow/supervisor.py`；回归 `app/workflow/test_issue_log.py`。

## 规则 26：课时同步来源按单课时识别

- 落地：`app/workflow/metadata_ai.py`、`app/workflow/metadata_policy.py::paper_meta/resolved_paper_meta`、`app/workflow/test_metadata_ai.py`、`app/workflow/test_metadata_source_name.py`，说明见 `app/docs/sync-practice.md`。

## 规则 27：同课时重号按来源身份关联，保留原卷顺序

- 落地：`app/workflow/question_identity.py`、结构/答案/解析/组装/审核/预览链路，说明见 `app/docs/sync-practice.md`。**语音组题的原生图片与导语**：图片转换为 `placement=question` 原生附件后，空 `material` 不等于丢图；公共作答导语须逐字绑定当前大题来源。只有当前来源复建、媒体文件与归属有效、题组及子题完整对应时，才认可共享图或无共享材料的合法语音组形态；无印刷号纯图题的内部别名不得伪造题干。组装、体检、上传格式使用同一来源证明，缺证明的裸数据仍严格拒绝；不得普遍豁免 A8/A9。

## 规则 28：先分材料角色，同角色 Word 优先；PDF 直接 OCR 出 JSON＋Markdown

- 落地：`app/workflow/source_policy.py`、`pdf_ocr.py`、`extract.py`、`paddle_jobs.py`、`structure_ai.py`、`app/workbench/server.py`，说明见 `app/docs/extraction.md`。

## 规则 29：AI运维消息固定在下方区域、内部上下滚动

- 落地：`lib/client.js::OperationsPanel`、`backend/operations-ui.test.cjs`。

## 规则 30：审核页必须直接显示目录题型，不许拿题干充题型

- 落地：`app/references/sync-practice-rules.json`、`app/workflow/sync_practice.py`、`app/workbench/server.py::decorate_review_rows`、`lib/client.js`、`app/docs/sync-lesson-types.md`。

## 规则 31：当前只匹配知识点标签，教材匹配暂停

- 落地：`app/workflow/agent_tasks.py`、`assemble_local.py`、`batch_fill.py`、`substeps.py`，说明见 `app/docs/content-labeling.md`。

## 规则 32：上传题号按原卷显式范围，不得用 location/paperOrder 代替

- 落地：`app/workflow/upload_payload.py`、`upload_numbering.py`、`format_gate.py`、`manifest.py`、`stage_semantics.py`、`review.py`、`assemble_local.py`；回归 `test_upload_numbering.py`、`test_upload_payload.py`、`test_stage_semantics.py`；说明见 `app/docs/question-number-upload-design.md`、`app/docs/upload-api.md`。

## 规则 33：前期只做内容体检，格式与上传适配集中在最终闸门

- 落地：`app/workflow/checks.py`、`runner.py::do_validate`、`assemble_precheck.py`、`format_gate.py`、`substeps.py`、`app/scripts/06-upload/audit_type_layout.py`；说明见 `app/docs/pipeline.md`。

## 规则 34：视觉理解已停用，无号题不得漏收或补造题号

- 落地：`app/workflow/lesson_bands.py`、结构/身份/下游来源复建链路。

## 规则 35：原卷听力稿放入解析

- 落地：`workflow/listening_script.py`、`explanations_direct.py`、`upload_content.py`、`content_rules.py`。

## 规则 36：远端暂存使用专用上传 Key

- 落地：`app/workflow/upload_client.py`、`app/workflow/upload.py`。

## 规则 37：原卷修改的解析/文章大意真正写入交付

- 落地：`app/workflow/manual_content.py`、`app/workbench/server.py::apply_manual_content`、`app/workflow/upload_content.py`、`lib/client.js`；验证见 `CHANGELOG/2026-11-18-content-modal-article-summary.md`。

## 规则 39：CHANGELOG 进上传仓库忽略名单并超过两天自动删除

- 落地：`app/workflow/changelog_retention.py`、`app/workbench/server.py`；测试 `workflow.test_changelog_retention`；记录 `CHANGELOG/2026-11-19-changelog-ignore-and-retention.md`。

## 规则 41：选词填空与教材语篇填空不加答案考点

- 落地：`app/references/type-knowledge-map.json`、`app/workbench/taxonomy.py`、`app/workflow/upload_payload.py`、`app/workflow/agent_roles.py`、`app/workflow/review_rounds.py`。

## 规则 42：中文一级大题标题与前置无号大题全链路同源口径

- 落地：`app/workflow/upload_content.py`、`app/workflow/shared_rubric.py`、`app/workflow/question_content.py`。

## 规则 43：纯图片题组由同大题参考答案确定性反推小题号 + 提示词瘦身与独立会话隔离

- 落地：`app/workflow/upload_content.py`、`structure_gate.py`、`question_identity.py`、`question_content.py`、`question_draft.py`、`shared_rubric.py`、`phonetic_structure.py`、`upload_numbering.py`、`structure_ai.py`、`preview.py`、`agent_tasks.py`、`review_rounds.py`、`app/workbench/server.py`、`lib/client.js`；回归 `workflow.test_answer_inferred_image_group`。

## 规则 43（第2条）：题干边界洁净、解析附件保留与提示词减负通用规则

- 落地：`app/workflow/question_draft.py`、`lesson_bands.py`、`wire_format.py`、`question_content.py`、`assemble_local.py`、`unit_tasks.py`、`app/scripts/06-upload/audit_sentence_blanks.py`。

## 规则 44：提取层“容器与题内分离、语境与作答分离”通用规则

- 落地：`app/workflow/extract.py`、`lesson_bands.py`、`question_content.py`、`structure_gate.py`、`sync_practice.py`、`agent_protocol.py`；回归 `app/workflow/test_golden_layouts.py`。

## 规则 45：四合一附件组通用角色识别（正文 / 答案解析 / 独立听力材料 / 音频）

- 落地：`app/workflow/material_classify.py`、`listening_script.py`、`upload_content.py`、`explanations_direct.py`。

## 规则 46：题干上的原卷提示标记必须保留且按子题真实句干归属（单句列表型选词填空不误判 D27/B9）

- 落地：`app/workflow/sync_practice.py::_mark_requirements`、`app/docs/sync-practice.md`；回归 `app/workflow/test_sync_source_marks.py::test_word_box_sentence_list_keeps_source_mark_on_child_stem`。

## 规则 48：大长材料组题正文段落统一首行缩进两字符（`\u3000\u3000`）

- 落地：`app/workflow/wire_format.py`、`app/workflow/upload_payload.py`、`app/workbench/preview_render.py`、`lib/client.js`。
