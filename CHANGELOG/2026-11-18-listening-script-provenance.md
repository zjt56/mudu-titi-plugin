# 修复听力稿解析被来源逐字校验误判

## 背景
批次 `【木牍教育】20年安徽中考英语` 卡在 `assemble`：每次都以
`Explanation not found in supplied source material (verbatim after normalizing marks)`
失败。supervisor 每约 40 秒重派同一阶段（`no_progress_attempts=2`），运维也已接手该异常，
工作台上表现为“流程进度又重跑了”。

判断口径（`app/workflow/agent_tasks.py::_provenance`）：解析去掉标记后必须能在
来源文本（正文/附录/答案等）中逐字找到。

根因：新规则「原卷听力稿放入解析」会把原卷听力稿逐字写进 `explanation`，并加
`听力录音材料：` 标识。该标识**不在原卷正文里**，旧校验把整段解析（含标识）拿去比对，
必然找不到 → 9 个听力单元确定性失败 → 装配每次都失败 → 反复重派。

## 改动
- `app/workflow/agent_tasks.py::_provenance`：解析按 `听力录音材料：` 分段，
  标识以外的每个片段仍须逐字回到来源原文；空段忽略。
- `app/workflow/test_agent_tasks.py`：新增
  `test_listening_script_marker_does_not_break_source_verbatim_check`，覆盖
  “纯听力稿通过”“原解析+听力稿通过”“听力稿里掺入编造内容被拒”“普通编造解析被拒”。

## 边界
只豁免「听力录音材料：」这个程序自己写入的标识，不豁免内容：听力稿正文仍需逐字来自原卷；
编造句子、改写、跨题借用照旧阻断。未放宽任何其他来源校验。

## 验证
- 新用例通过；`workflow.test_listening_script workflow.test_explanations_direct
  workflow.test_redraft ...` 共 42 项通过（实测命令见提交说明）。
- 真实批次复验：`_validate(explanations)` 由失败转为通过；`runner run --stage assemble --force`
  一次通过（`deterministic: true, model_calls: 0`），批次从 `assemble needs_review` 推进到
  `validating`。
- 说明：`workflow.test_agent_tasks` 当前仍有 6 个 error + 1 个 failure，原因在
  `batch_fill._snapshot` 报 “Question draft path escapes batch”，在干净 HEAD（b3e3355）
  上同样失败，与本次改动无关，未在本改动内处理。

## 回退
撤回本文件与 `agent_tasks._provenance` 分段改动即可；批次数据不受影响（装配为确定性重算）。
