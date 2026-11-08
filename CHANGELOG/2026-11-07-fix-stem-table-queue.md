# 2026-11-07 修复装配改写题干 / 信息转换表格空档 / 排队区进度分母

## 背景 / 触发
1. 安徽卷 `validate` 阻断：`q-43/q-44` 题干被改成以 `</u>` 开头（空档标签丢失）——装配阶段允许模型改写题干。
2. `group-26-27-28-29-30` 信息转换表格 28/29/30 报"未在表格中出现"——上传要求 `__N__` + variant，
   而切片逐字照抄了原文 `<u>　N　</u>`。
3. 用户指出上传列表显示"十几/18"错误——分母用了内部 18 阶段，而流程图只有 9 节点。

## 改了什么
- `app/workflow/agent_tasks.py`：新增 `_restore_source_fields`，装配导入时以原卷确定性切片的 `stem`（含子题 stem）
  为准恢复；选项/材料仍走各自原文核对，未被放宽。
- `app/workflow/question_draft.py`：仅对听力信息转换表格，把 `<u>　N　</u>` 转成 `__N__` 并标 `variant=information-conversion`；
  普通阅读表格保持原文标记不动。
- `lib/client.js`：新增 `compactProgress`，排队区进度按流程图节点统计（≤9），不再显示内部 18 分母。

## 没改什么 / 边界
- 装配仍不判断答案/解析；题干恢复只为防止模型改写，不代表放弃"原文保真"校验。
- 表格转换只限 `信息转换` 类型，未波及阅读等普通表格。

## 如何验证
- 该批次重跑 validate：19 项全通过 exit 0。
- 新增回归：`workflow.test_question_draft`（表格转换/普通表格不转换）、`workflow.test_agent_tasks`
  （装配不得改写题干）、`backend/upload-stage-flow.test.cjs`（进度不用 18 分母）。
- 全量 Python 1578 项、Node 110 项通过。

## 回退方式
撤销上述三处改动即可；均为局部行为修正，无数据结构变更。
