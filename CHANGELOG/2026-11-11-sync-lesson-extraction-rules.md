# 2026-11-11 课时拆分件提取口径对齐（课时标题变体、版块边界、提示标记保真）

## 背景 / 触发

用户（m03765）给出 `https://github.com/mudujiaoyu/Mudu-English-Upload.git`，要求「了解该项目中关于课时的
注意事项以及分类等条件，完善该插件对应课时的提取规则」。

对方仓库即本机已安装技能 `~/.agents/skills/mudu-english-upload`（origin 为同一仓库，SSH 可 fetch，
HTTPS 被重置）。比对后确认三处可确定性对齐的缺口：

1. 课时标题形态只认「第一课时」；拆分件/练习册里还出现 `第1课时`、`第1节课`、`课时1`、
   `Period 1`、`Lesson 1`，且课时标题后常直接跟 `Section A`（无空格）。
2. 与课时平级的版块标题只列了 `综合阅读提升`/`单元话题写作`/`Pronunciation`；对方的课时目录里还有
   `Reading Plus`、`Project`、`Reflecting`，这些不能当大题，也不能并进上一大题的共享材料。
3. 原卷提示标记（`[2025·六安月考]`、`[2023·安徽]`、`[新考法·跨学科]`、`[情境题]`、`[改编]`）属于原文，
   插件没有确定性核对；另外规则文件的 `type_kind` 缺 `听力其他`，与 27 主类目录不一致。

## 改了什么

- `app/workflow/sync_practice.py`
  - `_LESSON` 扩展为 `第一课时|第1课时|第1节课|课时1|Period 1|Lesson 1`，课时后不要求空白
    （`第一课时Section A` 也认），并排除 `第一课`、`课时安排` 这类非课时标题。
  - 新增 `_SECTION_TITLES`（含 `Reading Plus`/`Project`/`Reflecting`）与 `is_section_title()`；
    `heading_candidates()` 的课时上下文与窗口结束边界改用它（原来是比较字面元组）。
  - 新增 `source_marks()`（只取真题来源/命题特征标记，排除 `[IMG ...]`/`[TABLE ...]`/`【解析】`/`[答案]`/
    范文类标签）与 `audit_source_marks(units, raw_text)`：按来源窗口逐题核对标记是否仍在
    题干/材料/选项/子题/大题标题里，缺失只生成提示，不改题干、不改闸门。
  - `prompt_block()` 增加一条「方括号提示标记必须原样留在题干或材料里」。
- `app/workflow/structure_ai.py`：`auto_classify` 把 `audit_source_marks` 结果写入
  `01_source/structure.ai.classified.json` 的 `sync_marks` 并随返回值返回（与 `sync_audit` 同层，只留痕）。
- `app/references/sync-practice-rules.json`：`type_kind` 补 `听力其他`，27 主类齐备。
- `app/workflow/test_sync_lesson_extraction.py`（新增，10 项）：课时标题变体、非课时行、试卷卷头优先、
  版块标题与窗口边界、提示标记识别与缺失留痕、试卷不审计、27 主类与自检、提示词含标记要求。
- 文档：`app/docs/sync-practice.md` 新增「九、课时拆分件与提示标记」；根 `AGENTS.md` 规则 25 补句。

## 没改什么 / 边界

- 只做识别与留痕：`sync_marks`/`sync_audit` 都不改题型、组题/单独题判定，也不放行结构闸门。
- 试卷卷头出现「单元测试/期中/期末/月考/模拟」时仍不按同步练习处理，不被「第一课时复习」误触发。
- 未把 `【1】`、`（1）`、`(1)` 纳入题号识别：与「单元话题写作」的 `（1）审题思路` 等步骤号同形，
  贸然识别会产生伪题号；等真实卷面出现该形态再按来源窗口局部支持。
- 语音题整大题记组题、改错记单独题等用户既定口径不变（与对方 `constraints.md` D29 不同，以本表为准）。
- 历史批次不改写；不安装、不上传、不调用真实模型。

## 如何验证

- `python -m workflow.sync_practice` → `规则自检: 通过`，`主类数: 27 规则数: 32`。
- `python -m unittest workflow.test_sync_lesson_extraction` → 10 tests OK。
- `python -m unittest workflow.test_sync_practice workflow.test_sync_practice_integration
  workflow.test_sync_lesson_extraction workflow.test_extract workflow.test_structure_gate
  workflow.test_agent_tasks` → 145 tests OK。
- `python -m unittest workflow.test_structure_ai` → 21 tests OK。
- 真实原件回归（`.tmp/sync-source-native/body.txt`，七上学用正文）：`looks_like_sync_practice=True`，
  大题候选仍为 305 处，分布与改前完全一致（单词拼写 61、完成句子 54、阅读理解 30、单项填空 27、
  语音题 24、完形填空 23、任务型阅读 19、补全对话 16、书面表达 14、翻译 11、选词填空 10、短文填空 8、
  教材语篇填空 7、改错 1），课时标题 47 处，无新增/丢失窗口。

## 回退方式

`git revert <本次提交>`；规则口径回退只需删掉 `type_kind` 的 `听力其他` 与新增审计函数，
`_LESSON` 恢复单行正则。
