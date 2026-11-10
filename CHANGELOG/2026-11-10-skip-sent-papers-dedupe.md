# 2026-11-10 上传不再校验已发过的试卷；删除批次彻底清除

## 背景

用户明确要求：上传试卷时不要校验“之前已经发过（已提交云端）”的试卷，也不留存对应记录；只要删除批次，就删除干净、不留痕迹。

此前重复上传一份已上传到云端的试卷时，去重逻辑会复用那个存量批次，而该批次在列表中被隐藏，导致“批次任务不见了”。

## 改了什么

- `app/workbench/server.py::_existing_upload_batch`：新增 `_batch_already_sent(state)` 判定（上传阶段为 `completed`/`approved`，含待人工确认）。已发过的试卷在去重时直接跳过，既不比对也不复用；重新上传会生成新的可见任务。仍在处理中（未发）的重复内容仍按原规则复用，避免重复跑模型。
- `app/workbench/test_upload_status.py`：新增去重跳过已发批次的回归；覆盖“已发不复用、未发仍复用”。
- 删除批次：沿用并复核 `purge_batch`——永久删除 `workflows/<批次>`、`inbox/<批次>`、同批次回收归档（`.workbench-trash`/`.trash`/`archives`）、音频状态与全局镜像、调度/上传/音频作业并清空内容缓存；既有测试覆盖 live+回收+缓存彻底清除。

## 没改什么 / 边界

不改变“未发批次同内容复用”的防重复策略；不自动上传/放行；普通 `archive` 可恢复接口保留给旧入口，工作台“彻底删除”仍走 `purge`；自动流程不得自行删除用户试卷。

## 验证

- `workbench.test_upload_status` 10 项通过（含已发跳过、未发复用）。
- `workbench.test_upload_status`、`test_list_projection`、`workflow.test_workbench_catalog_archive`、`test_upload_content`、`test_upload_payload` 共 153 项通过（含 purge 彻底删除用例）。
- 真实数据核对：16年/19年/2024/16年-2 四个已发批次的同内容去重均返回 `None`（不再复用）。

## 回退方式

回退本提交即可；无数据结构变化。
