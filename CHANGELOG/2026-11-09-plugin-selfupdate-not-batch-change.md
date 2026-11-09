# 2026-11-09 修复：插件自更新导致“前置批次组装”误判为输入变化

## 背景

用户在另一台设备（Windows，`C:\Users\Administrator\.dsh\plugin-src\md-upload-tiku`）运行「前置批次组装（含题型）」时报卡点：

```
Bundled input changed during agent task: ...\backend\wf-router.mjs
```

这不是试卷内容变化，而是**插件自身源码在模型调用期间被更新**（插件同步/升级会重写 `plugin-src` 内的 `backend/wf-router.mjs`）。该文件此前被 `_part_model_binding` 当作“模型配置指纹”混入 `task.input_hashes`，又被 `_check_input_hashes` 当成批次内容做前后严格比对，于是插件升级被误判为题目输入变化，阶段直接 failed。

## 改了什么

- `app/workflow/local_agent.py`：把「批次内容哈希」与「模型身份指纹」拆开。
  - `strict_hashes`（题干、来源、上游产物等批次内容）用于模型调用前后的 `_check_input_hashes`。
  - `cache_hashes`（另含 `wf-router.mjs`、`model-router.json`）只用于候选缓存键与分片快照键；插件自更新最多导致缓存不命中，绝不判阶段失败。
  - 分片取消/汇总路径同样只校验 `strict_hashes`。
- `app/workflow/label_execution.py`：答案/解析的有界分片路径做同样拆分；返回给外层 `agent.main` 的 `input_hashes` 也只含批次内容。
- 新增回归 `workflow.test_preassembly.test_plugin_router_update_during_model_call_does_not_fail_stage`：模型调用期间改写 `wf-router.mjs`，阶段仍应完成；原有的「原卷文件在调用期间变化必须拒绝」用例保持通过，证明内容严格性没有被放松。

## 没改什么 / 边界

- 试卷原文、来源切片、上游产物在模型调用期间变化仍然严格拒绝安装，不降低原文保真与交接要求。
- 模型真实身份变化仍会改变缓存键，不会复用旧模型结果。
- 不自动重跑用户批次；本修复只保证插件自更新不再制造假卡点。

## 验证

- `workflow.test_preassembly`（13 项，含 2 项 in-flight 变更对照）通过。
- `test_preassembly + test_local_agent + test_label_execution + test_candidate_cache + test_agent_protocol + test_runner_auto + test_progressive_pipeline`：**143 项通过**。

## 回退

回退 `local_agent.py` 与 `label_execution.py` 的哈希拆分，并删除新增回归用例即可；行为回到“模型配置文件变化即阶段失败”。
