# 排队区支持移出/恢复，当前 13 份处理中批次已移出

日期：2026-11-12。类型：工作台运行行为。

## 背景

用户要求「把目前排队区的都移除来」。排队区不是持久队列文件，而是由 `/api/batches` 的 `flow_done/uploaded` 实时投影；不能靠删批次或改 manifest 完成态来“清空”，否则会丢数据或伪造完成。

## 改了什么

- `app/workbench/server.py`
  - 新增批次标记 `workflow/.queue-removed`（JSON：移出时间、原因），只影响排队展示和自动串行，不改题目数据、阶段状态或上传回执。
  - `_queue_batch_order` 跳过带标记的批次；`overview` 返回 `queue_removed`；overview 指纹纳入标记，缓存不会继续显示旧状态。
  - 新增 `remove_from_queue` / `restore_queue`，以及 `POST /api/batches/{name}/queue/remove|restore`。
  - 自动调度（supervisor / 运维）遇到标记返回 `removed`，不会重新拉起；用户点「继续处理」时 `start_advance(reset_budget=True)` 自动清除标记并恢复排队。
- `app/workflow/supervisor.py`：收到 `removed` 时把 supervisor 状态记为 `removed`，不再按重试预算反复调度。
- `lib/client.js`：上传页排队区过滤 `queue_removed`；supervisor 状态显示「已移出排队区」。
- `app/workbench/test_upload_status.py`：新增移出/恢复、调度不重启、状态映射回归。

## 本机执行结果

- 当前排队区 **13 份处理中批次全部移出**：02、05、06、2025 卷、13–18 年、21–23 年。
- 移出后 `/api/batches` 排队区 `pending=0`；13 份均保留在批次列表与审核页，标记 `queue_removed=true`，数据、阶段、上传回执未变。
- 自动调度不再启动这些批次；点「继续处理」可恢复。

## 没改什么 / 边界

- 不删除任何批次、不移动批次目录、不改 `workflow/manifest.json`、不伪造 `flow_done/uploaded`。
- 不移出已上传或已完成批次（它们本来不在排队区）。
- 移出只阻止自动推进，不改变审核差异、上传边界或已批准冻结件。

## 验证

- `workbench.test_upload_status` 16 项通过；`workbench.test_list_projection` 与 `workflow.test_supervisor` 合并 51 项通过；`node --test backend/*.test.cjs` 267 项：265 通过、0 失败、2 项按环境跳过；`node --check lib/client.js` 通过。
- 线上后端 `/api/batches` 复核：排队区 0 份，13 份 `queue_removed=true`。

## 回退

- 单批恢复：`POST /api/batches/{name}/queue/restore`，或在界面点「继续处理」。
- 全部恢复：删除各批次 `workflow/.queue-removed` 后刷新；不涉及数据回退。
