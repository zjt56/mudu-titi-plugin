# 卡点：独立选择题只绑定题干行丢失选项，组装报 OPTIONS_MISMATCH

- id: 2026-11-13-独立选择题只绑定题干行丢失选项,组装报-OPTIONS_MISMATCH
- status: fixed
- batch: 22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar
- batch_dir: workflows/22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar
- stage: assemble
- check: OPTIONS_MISMATCH
- created: 2026-11-13T17:43:50+08:00
- last_seen: 2026-11-13T17:43:50+08:00
- occurrences: 1
- fixed_at: 2026-11-13T17:43:57+08:00
- fix_commit: 4cf1c6e
- verification: 批次22 structure_gate passed(26单元/9栏目verified)；answers 33条与确定性直抄0 mismatch；相关回归 test_runner_auto/test_structure_gate/test_question_draft/test_answer_lesson_bands/test_content_structure/test_extract 全通过
- title: 独立选择题只绑定题干行丢失选项，组装报 OPTIONS_MISMATCH

## 现象（卡点）

Assemble source audit failed：第1/2/3题选项标签/数量与原卷不一致；batch.base options=[]

## 具体检查与判定

- 失败检查：OPTIONS_MISMATCH
- 判定口径：question_draft._bound_source 对 basis=ai_classified 的独立题只绑定 stem 行（26），选项在27-28行且单元已声明 source_parts，绑定窗口把选项整段丢掉
- 修复方向：按已声明 source_parts 在确认 span 内把同一题的选项行一并绑定（不越界吞下一题）；batch.base 复建后选项恢复为4项

## 修复记录

- 修复方式：已按来源/视觉证据修复并通过回归（详见 CHANGELOG/2026-11-13-lesson-scoped-anchor-band-options-fixes.md）
- 修复提交：4cf1c6e
- 复验证据：批次22 structure_gate passed(26单元/9栏目verified)；answers 33条与确定性直抄0 mismatch；相关回归 test_runner_auto/test_structure_gate/test_question_draft/test_answer_lesson_bands/test_content_structure/test_extract 全通过
- 标记时间：2026-11-13T17:43:57+08:00
