# 卡点：整卷栏目归属视觉调用预算不足被判不可用（COLUMN_OWNERSHIP_UNRESOLVED）

- id: 2026-11-13-整卷栏目归属视觉调用预算不足被判不可用(COLUMN_OWNERSHIP_UNRESOLVED)
- status: fixed
- batch: 22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar
- batch_dir: workflows/22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar
- stage: structure_gate
- check: COLUMN_OWNERSHIP_UNRESOLVED
- created: 2026-11-13T17:43:50+08:00
- last_seen: 2026-11-13T17:43:50+08:00
- occurrences: 1
- fixed_at: 2026-11-13T17:43:57+08:00
- fix_commit: 4cf1c6e
- verification: 批次22 structure_gate passed(26单元/9栏目verified)；answers 33条与确定性直抄0 mismatch；相关回归 test_runner_auto/test_structure_gate/test_question_draft/test_answer_lesson_bands/test_content_structure/test_extract 全通过
- title: 整卷栏目归属视觉调用预算不足被判不可用（COLUMN_OWNERSHIP_UNRESOLVED）

## 现象（卡点）

visual-columns.json status=unavailable reason=TRUNCATED_RESPONSE；闸门报完整页已准备但栏目视觉归属未完成

## 具体检查与判定

- 失败检查：COLUMN_OWNERSHIP_UNRESOLVED
- 判定口径：visual_columns 默认 max_tokens=16000、超时180s；整卷4页栏目枚举实测需约6k tokens/170s，默认预算在生成途中截断，栏目归属无法落盘
- 修复方向：默认 max_tokens 32000、超时600s（WF_VISUAL_TIMEOUT 可覆盖）、截断重试上限64000；仍严格绑定所选路由不降级；批次22重建后 verified 9 栏目

## 修复记录

- 修复方式：已按来源/视觉证据修复并通过回归（详见 CHANGELOG/2026-11-13-lesson-scoped-anchor-band-options-fixes.md）
- 修复提交：4cf1c6e
- 复验证据：批次22 structure_gate passed(26单元/9栏目verified)；answers 33条与确定性直抄0 mismatch；相关回归 test_runner_auto/test_structure_gate/test_question_draft/test_answer_lesson_bands/test_content_structure/test_extract 全通过
- 标记时间：2026-11-13T17:43:57+08:00
