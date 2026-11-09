# 2026-11-08 卡点反馈必须标明具体批次

## 背景

用户反馈：2014 年试卷本来已经跑完，但报告卡点时没有明确指出是哪一份试卷；用户反馈后系统又把 2014 年批次拉出来重跑，造成误操作和重复处理。

## 改了什么

- `app/workflow/diagnose.py`：诊断结果增加 `batch_name`，摘要统一带 `批次「批次目录名」`。
- `lib/client.js`：流程卡点条明确显示 `批次：<批次名>`；点击「继续处理」失败时显示 `批次「<批次名>」处理失败：...`。
- 保留原有 `batch`、`batch_dir` 和按批次锁机制，不改变阶段执行规则。

## 边界

- 只改善错误定位和用户反馈，不改变自动推进、重试、自愈或阶段契约。
- 不会因为收到一条泛化错误报告而自行选择其他批次；后续处理必须以报告中明确的批次名/批次目录为目标。
- 不重跑、不修改已完成用户批次。

## 验证

- `workflow.test_diagnose`、`test_agent_entry`、`test_runner_auto`：72 项通过。
- `node --test backend/settings-navigation.test.cjs`：7 项通过。
- `node --check lib/client.js` 与 `git diff --check` 通过。
