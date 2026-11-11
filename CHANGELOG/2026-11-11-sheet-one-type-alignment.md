# 2026-11-11 表一（题型汇总）改判落到同步练习规则

## 背景 / 触发

用户给了桌面 `C:\Users\Administrator\Desktop\英语题库相关\英语题型汇总.xlsx`，说「这个对应在表一做了修改，请你也做对应的改动」。
表一（`题型汇总` sheet）是题型/子类/组单的最终口径，本轮对照表二（`逐处明细`）比出 15 行真实改判，
插件 `app/references/sync-practice-rules.json` 必须跟上。

## 改了什么

- `app/references/sync-practice-rules.json`
  - `spell-grammar-word`（一句一空语法功能词填空：人称代词、do/does、介词 in/on/under、连词 and/but/because、
    适当的介词、How/What、for/of、if/whether/that、关系代词）`单词拼写/句意` → `选词填空/多句`，仍 standalone。
  - `spell-modal`（can/can't/could/couldn't 的适当形式）`单词拼写/句意` → `选词填空/多句`，仍 group。
  - 新增 `sentence-picture`（看图补全句子：图片提示完成下列句子、家谱、地图方位介词、图示方位介词、
    图片信息完成句子）→ `完成句子/补全句子` group；排在 `spell-picture` **之前**。
  - `task-reading-mindmap`（短文后完成思维导图）`任务型阅读/信息摘录` → `任务型阅读/阅读综合`。
  - 规则总数 32 → 33。
- `app/references/filter-framework.json`：`完成句子` 子类本地补入 `补全句子`（平台 1.0 框架原无此子类）。
- `app/workflow/test_sync_practice.py`：CASES 第 47/48/49 行随表一更新；新增
  `TestWorkbookSheetOneAlignment`（15 行改判 + 2 行保持看图填词 + `完成句子` 目录含 `补全句子`）。
- 文档：`app/docs/sync-practice.md` 第三节按新口径重写并加表一说明；`app/docs/sync-lesson-types.md`
  新增 5.1 节（改判对照表、`sentence-picture` 顺序要求、本地补子类的风险）；根 `AGENTS.md` 规则 25
  增「表一口径优先」；`CHANGELOG/RULES.md`、`CHANGELOG/README.md` 同步。

## 没改什么 / 边界

- 组题/单独题数量结构不变（仍是 371 组题 + 483 单独题 = 854 处），只改题型/子类归类。
- 语音题 group、改错 standalone、`【1】`/`（1）` 不作题号等用户既定口径不动；`spell-picture` 仍负责
  「根据首字母及图片提示完成单词」「图片中的节日」等看图填词行。
- 不改历史批次数据；`补全句子` 只是本地目录补项，**平台若拒收，上传体检 A7 会报『子类不属于完成句子』**，
  届时按用户裁决退回 `看图填词` 或改投 `情景运用`，不得静默改回。

## 如何验证

- `PYTHONPATH=app python3 -m workflow.sync_practice` → `规则自检: 通过` / `主类数: 27 规则数: 33`。
- `python -m unittest workflow.test_sync_practice` → 28 tests OK（含新 `TestWorkbookSheetOneAlignment` 3 项）。
- 同步类与结构类回归：`test_sync_practice + test_sync_practice_integration + test_sync_lesson_extraction +
  test_sync_lesson_shape`、`test_extract + test_structure_gate + test_agent_tasks`（数字见提交说明）。

## 回退方式

`git revert` 本次提交即可；规则 JSON 恢复 32 条、`完成句子` 子类回到 6 项，测试表一用例一并回退。
