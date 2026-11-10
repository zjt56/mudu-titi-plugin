# 2026-11-10 试卷审核页卡片显示完成时间与流程耗时

## 背景

用户要求：试卷流程完成后，进入试卷审核页面，每个试卷卡片后面显示时间（精确到月-日 时:分），并显示流程完成总耗时（以分钟为单位）。

## 改了什么

- `app/workbench/server.py`：新增 `_parse_iso`、`_flow_timing(manifest)`。从各阶段真实 `started_at`/`finished_at` 计算流程开始时间、完成时间（优先取最终格式检查 `format_gate.finished_at`，否则最晚完成阶段）与总耗时分钟（最晚完成减最早开始，四舍五入）。`overview()` 输出新增 `flow_started_at`、`flow_finished_at`、`flow_minutes`，缺失或非法时间戳时返回空而不报错。
- `lib/client.js`：审核页卡片新增时间行。已完成流程显示「完成时间：MM-DD HH:mm · 流程耗时 N 分钟」；未完成显示「进行中 · 最近进度 MM-DD HH:mm」。时间为浏览器本地时区，按 `月-日 时:分` 展示。
- `app/workbench/test_upload_status.py`：新增 `_flow_timing` 回归，覆盖正常完成（81 分钟）、空阶段与非法时间戳均不崩溃。

## 没改什么 / 边界

只增加只读展示字段，不改动流程、审核结论或批次数据；耗时来自真实阶段时间戳，不做估算；不影响上传/发布边界。

## 验证

- `workbench.test_upload_status` 8 项通过（含耗时 81 分钟用例）。
- `workbench.test_upload_status`、`test_list_projection`、`test_stage_parallel_display`、`workflow.test_workbench_review_status`、`test_workbench_catalog_archive` 共 53 项通过。
- 接口实测：17 年卷 `flow_finished_at=2026-11-09T23:39:28+08:00`、`flow_minutes=81`；卡片文案模拟输出「完成时间：11-09 23:39 · 流程耗时 81 分钟」。
- `node --check lib/client.js lib/index.js` 通过。

## 回退方式

回退本提交即可；新增字段与展示互不影响，无数据迁移。
