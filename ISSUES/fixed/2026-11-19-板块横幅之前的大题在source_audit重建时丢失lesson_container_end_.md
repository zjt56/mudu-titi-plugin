# 卡点：板块横幅之前的大题在source_audit重建时丢失lesson_container_end_line导致误报BATCH_IDENTITY_MISMATCH

- id: 2026-11-19-板块横幅之前的大题在source_audit重建时丢失lesson_container_end_
- status: fixed
- batch: 12九上人教版课时A计划Unit2InspiringPeople第四课时Sect
- batch_dir: workflows/12九上人教版课时A计划Unit2InspiringPeople第四课时Sect
- stage: validate
- check: source_audit_BATCH_IDENTITY_MISMATCH
- created: 2026-11-19T21:19:41+08:00
- last_seen: 2026-11-19T21:19:41+08:00
- occurrences: 1
- fixed_at: 2026-11-19T21:22:05+08:00
- fix_commit: working-tree
- verification: 1) test_native_unnumbered_structure + test_question_content 全过；2) 目标批次 workflow.runner run --stage validate --force 14/14 全部通过，upload_payload 零本地错误
- title: 板块横幅之前的大题在source_audit重建时丢失lesson_container_end_line导致误报BATCH_IDENTITY_MISMATCH

## 现象（卡点）

validate 阶段 source_audit 报 sec-eca1dbdf9e33bddd-u5 BATCH_IDENTITY_MISMATCH（正式题目与确认结构的大题/题目身份不一致；SOURCE_SLICE_MISMATCH）

## 具体检查与判定

- 失败检查：source_audit_BATCH_IDENTITY_MISMATCH
- 判定口径：structure_gate.py 用 if annotated != source_section 保留了板块横幅之前大题的 lesson_container_end_line，而 question_content._rebuild_source_units 仅在 annotated.get('lesson_band') 为真时才更新 source_section，导致无 lesson_band 但有 lesson_container_end_line 的前置大题（如一、教材语篇回顾）重建后缺该字段，与 batch_meta.json 比对误报身份不一致
- 修复方向：将 question_content._rebuild_source_units 对齐为 if annotated != section: unit['source_section'] = annotated，并补齐回归测试后复验 validate 与 format_gate

## 修复记录

- 修复方式：将 question_content._rebuild_source_units 对齐 structure_gate 为 if annotated != section: unit['source_section'] = annotated，保留前置大题的 lesson_container_end_line
- 修复提交：working-tree
- 复验证据：1) test_native_unnumbered_structure + test_question_content 全过；2) 目标批次 workflow.runner run --stage validate --force 14/14 全部通过，upload_payload 零本地错误
- 标记时间：2026-11-19T21:22:05+08:00
