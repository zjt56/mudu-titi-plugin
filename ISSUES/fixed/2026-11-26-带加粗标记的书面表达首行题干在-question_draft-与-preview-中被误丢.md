# 卡点：带加粗标记的书面表达首行题干在 question_draft 与 preview 中被误丢

- id: 2026-11-26-带加粗标记的书面表达首行题干在-question_draft-与-preview-中被误丢
- status: fixed
- batch: 6326秋八上人教版课时A计划Unit8Let’sCommunicate!单元话
- batch_dir: /Users/zjt/Desktop/上传插件版/workflows/6326秋八上人教版课时A计划Unit8Let’sCommunicate!单元话
- stage: validate
- check: audit_source_marks
- created: 2026-11-26T00:00:54+08:00
- last_seen: 2026-11-26T00:00:54+08:00
- occurrences: 1
- fixed_at: 2026-11-26T00:04:10+08:00
- fix_commit: -
- verification: workflow.test_standalone_marked_heading + workflow.test_writing_preview 12/12 通过；实卷 6326秋八上人教版课时A计划Unit8Let’sCommunicate!单元话 assemble --force + validate --force 14/14 项全通过（0 阻断、0 提示）
- title: 带加粗标记的书面表达首行题干在 question_draft 与 preview 中被误丢

## 现象（卡点）

sec-99ec2ce5ab36062c-u27 缺少 [2024·四川南充]（D27/B9），且题干丢失首行写作指令

## 具体检查与判定

- 失败检查：audit_source_marks
- 判定口径：原卷第26行 [2024·**四川南充**]假如你是李明... 含 Markdown 加粗符，section.title 存储的是去格式纯文本，question_draft._standalone_marked_heading 与 preview._scoped_source_slice 直接做原始字符串全等比对导致匹配失败并丢弃首行
- 修复方向：统一用 sync_practice.plain_text 去除 Markdown 加粗格式后再比对 heading 与 section.title，同时允许 preview 跨越首行与正文之间的空行

## 修复记录

- 修复方式：question_draft._standalone_marked_heading 与 preview._scoped_source_slice 统一改用 sync_practice.plain_text 去除 Markdown 加粗格式后再比对 heading 与 section.title，并允许 preview 跨越首行与正文之间的空行
- 修复提交：-
- 复验证据：workflow.test_standalone_marked_heading + workflow.test_writing_preview 12/12 通过；实卷 6326秋八上人教版课时A计划Unit8Let’sCommunicate!单元话 assemble --force + validate --force 14/14 项全通过（0 阻断、0 提示）
- 标记时间：2026-11-26T00:04:10+08:00
