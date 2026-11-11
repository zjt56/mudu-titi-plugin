# 结构/体检闸门：阻断态必须写明具体检查；陈旧 blocked 态可提示重跑收敛

## 背景/触发

用户报批次 `04七上人教版课时A计划StarterUnit1Hello!第四课时Projec` 结构自动确认「阶段被阻断：3 条未解决」。实际排查：`07_review/structure-gate.json` 早已 `passed=true`、`blockers=[]`（12 单元），manifest 却是旧的 `blocked`；直接重跑 `structure_gate` 即 `completed`。用户要求修复此类问题，避免下次继续出现。

两个真实缺陷：
1. **卡片与台账只写数量不写具体检查**：blocked 时 `notes` 写成 `f"{len(blockers)} 条未解决"`，用户看不到是哪条检查、哪一行、为什么判失败（违反硬规则 2）。
2. **陈旧阻断态与真实检查不一致**：阶段被置 blocked 后，检查按当前输入通过却没有回写完成；diagnose 继续把旧检查当真实卡点报，误导排查。

## 改了什么

- `app/workflow/runner.py`（structure_gate 阻断分支）：blocked 时 `notes` 写入每条 blocker 的 `代码：原因（正文第N行）`，最多 6 条；无结构化 blocker 才退回计数。
- `app/workflow/diagnose.py`：新增 `_stale_gate_pass(batch, stage_id)`——结构/体检闸门阶段标记 blocked，但当前 `structure-gate.json` / `validation-report.json` 已是 `passed=true` 时，卡片显示「阶段状态陈旧：当前检查已通过，重跑该闸门即收敛」并说明直接重跑回写完成，不再把已解决的历史阻断当卡点。
- 测试：
  - `test_diagnose`：陈旧 blocked+passed 报告显示收敛提示；真实 blockers 时 detail 列出具体检查代码。
  - `test_pipeline_regressions`：结构闸门 blocked 的 notes 含具体 `LAYOUT_UNCERTAIN` 代码与原因。

## 没改什么 / 边界

- **没有放宽任何校验**：陈旧检测仅在“报告已 passed=true”时改展示，不跳检查、不改判通过；真实 blockers 仍照常 blocked。重跑仍走完整确定性闸门。
- **不改用户题目数据、不手改 manifest**、不自动上传发布。
- 只是把「已解决但仍显示旧阻断」与「只显示数量」的信息缺陷修好，让卡点可定位、可收敛。

## 验证

```
cd app
python3 -m unittest workflow.test_diagnose workflow.test_pipeline_regressions workflow.test_structure_gate
# Ran 75 tests … OK
```
新增 3 条回归。`04` 批次复验：structure_gate/preassemble/types 均 completed。

## 回退

`git checkout -- app/workflow/runner.py app/workflow/diagnose.py app/workflow/test_diagnose.py app/workflow/test_pipeline_regressions.py`。
