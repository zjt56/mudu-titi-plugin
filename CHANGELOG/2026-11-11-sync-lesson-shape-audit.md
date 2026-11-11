# 课时编排自检 + 课时录入约束进提示词（2026-11-11）

## 背景 / 触发

用户要求「查看该技能（Mudu-English-Upload）对于课时录入规则的补充和约束，运用到该插件，补强代码约束和提示词增强」。
第一轮已对齐课时标题变体、版块边界和原卷提示标记（`0b42780`），本轮补上**课时编排本身**的可判定错误：
课时缺单元名、空课时、课时序号跳号——这三类在原卷里一旦出现，就是整块课时或整段来源被切丢，
但此前只表现为「题目少了」，没有任何留痕。

## 改了什么

- `app/references/sync-practice-rules.json` 新增 `lesson_constraints`（6 条：课时必须有单元名、课时至少一个大题、
  课时序号连续、版块与课时平级、材料带齐题号时不再重复生成同号子题、单元话题写作第1步词汇积累/思维导图不是题）。
  该数组是课时录入约束的唯一文字来源，提示词与自检共用，不另写一份提示词。
- `app/workflow/sync_practice.py`：
  - 新增 `_section_number()`（中文数字含 `十二`/`二十一`/`1`/`01`）与 `_lesson_number()`（`第一课时`/`第1节课`/`课时3`/`Period 2`/`Lesson 4`）；
  - 新增 `lesson_constraints()` 读取约束数组；
  - 新增 `audit_lesson_shape(raw_text)`：报 `LESSON_UNIT_MISSING`（课时前无 `Unit N`）、
    `LESSON_EMPTY`（课时与下一课时/答案区之间没有任何大题标题）、`LESSON_NUMBER_GAP`（同单元课时序号跳号），
    按行号排序，只读、不改结构；
  - `prompt_block()` 追加 `lesson_constraints` 条目（提示词由 1675 字符增至 2591 字符）。
- `app/workflow/extract.py::infer_structure`：同步材料写入 `structure.sync_practice.lesson_audit`，提取报告直接可见。
- 新增 `app/workflow/test_sync_lesson_shape.py` 10 项（课时序号各形态、缺单元名、空课时、跳号、试卷不审计、审计不改结构、
  提示词含约束、约束记录完整、extract 写入 lesson_audit）。
- `app/docs/sync-practice.md` 第九节追加「课时编排自检」表与提示词约束说明；`CHANGELOG/RULES.md`、根 `AGENTS.md` 规则 25 各补一条。

## 没改什么 / 边界

- 不把 `【1】`/`（1）`/`(1)` 纳入题号识别（与写作步骤号同形，会产生伪题号），课时拆分件的空位仍按材料原文处理。
- 不按 Mudu 文档反转既有口径：语音题仍 group、改错仍 standalone。
- `lesson_audit` 只是留痕，不改题型/组单、不放宽闸门、不改写历史批次；也不代替结构闸门对漏题、跨材料、同窗重复的阻断。
- 未引入远端技能里「自行生成答案/范文」的做法（与规则 22/23 冲突）。

## 如何验证

- `PYTHONPATH=app python3 -m workflow.sync_practice` → `规则自检: 通过`，`主类数: 27 规则数: 32`。
- `python -m unittest workflow.test_sync_lesson_shape` → 10 tests OK。
- `workflow.test_sync_practice + test_sync_practice_integration + test_sync_lesson_extraction + test_sync_lesson_shape + test_extract` → 86 tests OK。
- `workflow.test_structure_ai + test_structure_gate + test_question_draft + test_question_content + test_question_identity + test_question_identity_structure` → 84 tests OK（skipped=4）。
- 真实七上正文只读复验（`.tmp/sync-source-native/body.txt`）：47 个课时标题、305 处大题、`lesson_audit=[]`。
- 课时拆分件单课时文档（`01 七上 人教版 课时A计划 Starter Unit 1 Hello! 第一课时 Section A (1a-2d).docx`）：
  `is_sync=True`、`lesson_audit=[]`、9 处大题。

## 回退

`git revert <commit>` 即可；`lesson_constraints` 是新增键，旧代码忽略它，规则自检不受影响。
