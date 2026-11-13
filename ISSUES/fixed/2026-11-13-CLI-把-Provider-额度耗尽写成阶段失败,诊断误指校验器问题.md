# 卡点：CLI 把 Provider 额度耗尽写成阶段失败，诊断误指校验器问题

- id: 2026-11-13-CLI-把-Provider-额度耗尽写成阶段失败,诊断误指校验器问题
- status: fixed
- batch: 22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar
- batch_dir: workflows/22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar
- stage: preassemble
- check: PREASSEMBLE_QUOTA_MISCLASSIFIED
- created: 2026-11-13T17:43:50+08:00
- last_seen: 2026-11-13T17:43:50+08:00
- occurrences: 1
- fixed_at: 2026-11-13T17:43:57+08:00
- fix_commit: 4cf1c6e
- verification: 批次22 structure_gate passed(26单元/9栏目verified)；answers 33条与确定性直抄0 mismatch；相关回归 test_runner_auto/test_structure_gate/test_question_draft/test_answer_lesson_bands/test_content_structure/test_extract 全通过
- title: CLI 把 Provider 额度耗尽写成阶段失败，诊断误指校验器问题

## 现象（卡点）

preassemble failed：429 insufficient_quota；诊断提示“按具体失败单元与校验错误定位输入、输出或校验器问题”

## 具体检查与判定

- 失败检查：PREASSEMBLE_QUOTA_MISCLASSIFIED
- 判定口径：网页后台 auto 会识别 429/额度并回 pending+冷却，但 CLI runner 统一写 failed，使外部额度问题看起来像内容缺陷，且诱导原样重跑
- 修复方向：runner._execute_command_stage 复用 auto._is_rate_limited，agent 阶段遇限流/额度回到 pending 并登记冷却（返回4），与后台同口径

## 修复记录

- 修复方式：已按来源/视觉证据修复并通过回归（详见 CHANGELOG/2026-11-13-lesson-scoped-anchor-band-options-fixes.md）
- 修复提交：4cf1c6e
- 复验证据：批次22 structure_gate passed(26单元/9栏目verified)；answers 33条与确定性直抄0 mismatch；相关回归 test_runner_auto/test_structure_gate/test_question_draft/test_answer_lesson_bands/test_content_structure/test_extract 全通过
- 标记时间：2026-11-13T17:43:57+08:00
