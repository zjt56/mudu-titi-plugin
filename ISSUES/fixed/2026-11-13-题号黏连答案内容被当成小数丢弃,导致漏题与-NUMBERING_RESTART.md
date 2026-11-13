# 卡点：题号黏连答案内容被当成小数丢弃，导致漏题与 NUMBERING_RESTART

- id: 2026-11-13-题号黏连答案内容被当成小数丢弃,导致漏题与-NUMBERING_RESTART
- status: fixed
- batch: 22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar
- batch_dir: workflows/22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar
- stage: structure_gate
- check: NUMBERING_RESTART
- created: 2026-11-13T17:43:39+08:00
- last_seen: 2026-11-13T17:43:39+08:00
- occurrences: 1
- fixed_at: 2026-11-13T17:43:57+08:00
- fix_commit: 4cf1c6e
- verification: 批次22 structure_gate passed(26单元/9栏目verified)；answers 33条与确定性直抄0 mismatch；相关回归 test_runner_auto/test_structure_gate/test_question_draft/test_answer_lesson_bands/test_content_structure/test_extract 全通过
- title: 题号黏连答案内容被当成小数丢弃，导致漏题与 NUMBERING_RESTART

## 现象（卡点）

结构自动确认被阻断：NUMBERING_RESTART 重复5次；SOURCE_NUMBERS_UNDECLARED 第23行题号4、第29行题号2

## 具体检查与判定

- 失败检查：NUMBERING_RESTART
- 判定口径：正文 4.30 (minute)、2.1st June 的印刷题号后紧跟数字，printed_tokens 的 (?!\d) 防小数规则返回空锚点，_build_scoped_units 独立题分支只认 printed_tokens，导致第4题整题缺失、第2题未归属；_proven_scoped_restart 因本地单元不完整拒绝放行
- 修复方向：structure_gate._build_scoped_units 独立题锚点并入 _strong_number 行首题号（仅强锚点，不引入弱空位号）；补 test_content_structure/test_structure_source_shape_regressions 回归

## 修复记录

- 修复方式：已按来源/视觉证据修复并通过回归（详见 CHANGELOG/2026-11-13-lesson-scoped-anchor-band-options-fixes.md）
- 修复提交：4cf1c6e
- 复验证据：批次22 structure_gate passed(26单元/9栏目verified)；answers 33条与确定性直抄0 mismatch；相关回归 test_runner_auto/test_structure_gate/test_question_draft/test_answer_lesson_bands/test_content_structure/test_extract 全通过
- 标记时间：2026-11-13T17:43:57+08:00
