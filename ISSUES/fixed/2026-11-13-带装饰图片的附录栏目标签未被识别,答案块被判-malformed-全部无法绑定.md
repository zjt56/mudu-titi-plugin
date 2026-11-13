# 卡点：带装饰图片的附录栏目标签未被识别，答案块被判 malformed 全部无法绑定

- id: 2026-11-13-带装饰图片的附录栏目标签未被识别,答案块被判-malformed-全部无法绑定
- status: fixed
- batch: 22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar
- batch_dir: workflows/22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar
- stage: answers
- check: scoped_answer_source_scope_unbound
- created: 2026-11-13T17:43:40+08:00
- last_seen: 2026-11-13T17:43:40+08:00
- occurrences: 1
- fixed_at: 2026-11-13T17:43:57+08:00
- fix_commit: 4cf1c6e
- verification: 批次22 structure_gate passed(26单元/9栏目verified)；answers 33条与确定性直抄0 mismatch；相关回归 test_runner_auto/test_structure_gate/test_question_draft/test_answer_lesson_bands/test_content_structure/test_extract 全通过
- title: 带装饰图片的附录栏目标签未被识别，答案块被判 malformed 全部无法绑定

## 现象（卡点）

resolve_scoped_answer 全部 None；_compact_answer_blocks 抛 duplicate or malformed compact answer block；Ⅳ 块 invalid=True

## 具体检查与判定

- 失败检查：scoped_answer_source_scope_unbound
- 判定口径：附录 语法精练/课时精练 标签是 [IMG]…[IMG] 包裹，_LESSON_BAND/is_section_title 均按原始 markdown 匹配失败，标签行被当作无归属散文，把跨两行的 Ⅳ 块标成 invalid
- 修复方向：upload_content 新增 _answer_feature_topics，用已验证视觉栏目标签作答案分区主题并贯通 _answer_section_topics/_compact_answer_blocks/_scoped_answer_candidates；同时新增“确认窗口不得短于已验证板块边界”校验；补 test_answer_lesson_bands 回归

## 修复记录

- 修复方式：已按来源/视觉证据修复并通过回归（详见 CHANGELOG/2026-11-13-lesson-scoped-anchor-band-options-fixes.md）
- 修复提交：4cf1c6e
- 复验证据：批次22 structure_gate passed(26单元/9栏目verified)；answers 33条与确定性直抄0 mismatch；相关回归 test_runner_auto/test_structure_gate/test_question_draft/test_answer_lesson_bands/test_content_structure/test_extract 全通过
- 标记时间：2026-11-13T17:43:57+08:00
