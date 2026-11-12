# 试卷审核列表按最新时间置顶

日期：2026-11-12。类型：工作台展示排序。

## 背景

用户要求「试卷审核页面的排序，按照最新的在最上面」。旧列表只把 `flow_done` 的批次排在最前，其余保持后端目录名顺序；新上传/最近推进的试卷不一定在顶部。

## 改了什么

- `app/workbench/server.py`：`overview` 增加 `created_at`、`updated_at`（来自 `workflow/manifest.json`），供列表排序使用。
- `lib/client.js`：新增 `batchLatestTime(b)`，取 `flow_finished_at`、`flow_started_at`、`updated_at`、`created_at` 的最大值；审核列表按该时间降序排列。`待人工确认` 徽标保留，但不再强制置顶。
- `backend/upload-flow-redesign.test.cjs`：把旧「flow_done 置顶」断言改为「按最新时间降序」断言，并校验后端暴露两个时间字段。

## 没改什么 / 边界

- 只改审核列表展示顺序，不改批次状态、审核结论、排队区、上传边界或数据。
- 已上传批次仍不进入审核列表；`待人工确认` 标识逻辑不变。
- 排序只影响前端展示，不写回任何批次文件。

## 验证

- `node --test backend/*.test.cjs`：325 项，323 通过、0 失败、2 项按环境跳过。
- `workbench.test_upload_status` + `workbench.test_list_projection`：19 项通过。
- 线上 `/api/batches` 已返回 `created_at/updated_at`；按最大时间降序可把最新批次置顶。

## 回退

撤回 `server.py` 的两个时间字段、`client.js` 的 `batchLatestTime` 与排序表达式，以及对应测试断言即可；不涉及数据回退。
