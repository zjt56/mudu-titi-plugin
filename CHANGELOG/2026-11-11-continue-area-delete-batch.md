# 继续处理区新增「删除」入口：可停运行中批次并清缓存

- 日期：2026-11-11
- 触发：用户「像这种流程中的任务，我需要加一个删除任务，因为有的我不想让它跑了，直接给它删除，同时清空它留在这里的缓存，就放在继续处理那边。」

## 一、背景

流程图（继续处理区）头部原只有「继续处理」按钮；要删除批次得回到试卷审核列表的 `⋯ → 彻底删除`。用户希望在**继续处理区**直接加一个删除入口：不想让某个批次继续跑了就删掉，并清空它留下的缓存。

原 `purge_batch` 只能删「未在运行」的批次（运行中直接拒绝），因此要满足「运行中也能删」，必须在删除前先让推进器干净停下。

## 二、改了什么

| 文件 | 变化 |
|---|---|
| `app/workflow/auto.py` | 新增批次级取消标记：`cancel_marker_path`（`workflow/.delete-requested`）、`cancel_requested`、`request_cancel`、`clear_cancel`；`_advance_impl` 的**每个阶段开头**检查该标记，命中即 `_result(..., "cancelled")` 在本轮干净退出。**只在阶段边界停，绝不中断阶段中途**（不留半写产物） |
| `app/workbench/server.py` | 新增 `_cancel_running_batch(name, folder)`：写取消标记、移出推进队列；**排队/等待中的批次直接清状态立即返回**；运行中的批次在 `DELETE_STOP_WAIT_SECONDS`（默认 90s）内等 worker 在阶段边界释放执行锁，超时则 `stopped=False`。新增 `delete_batch(name, confirm_name)`：先停、停稳才 `purge_batch`，停不稳**拒绝删除并保留数据**；新增路由 `POST /api/batches/<name>/delete` |
| `lib/client.js` | 流程图头部「继续处理」旁新增红色「删除」按钮 → 打开确认弹层（输入完整批次名 / 一键填入）→ 调 `/delete`；成功后刷新批次列表。`delBatch` 状态放在 `lastRefresh` 之后，避免打乱既有按索引取态的流程测试 |
| `app/workflow/test_workbench_delete_batch.py`（新增） | 7 条回归：需精确确认名；空闲直接 purge；排队批次移队列后 purge；运行中未停稳拒绝删除且写取消标记、数据不删；停稳后 purge；取消标记读写往返；`_advance_impl` 命中取消标记在首阶段边界返回 `cancelled` |
| `AGENTS.md` 硬规则 21 | 补充「继续处理区删除入口 + 运行中先停到阶段边界再删、停不稳拒删」 |
| `CHANGELOG/RULES.md` | 追加同日规则台账条目 |

## 三、没改什么 / 边界

- **不自动删除**：仍只由用户显式点击「删除」并输入完整批次名触发；自动流程照旧不删用户试卷（硬规则 10/21）。
- **不在阶段中途强杀**：取消只在阶段边界生效；正在执行的那一阶段会跑完再停。这样避免半写产物/损坏 manifest。停不稳就**不删、保数据**，返回明确错误让用户稍后重试。
- **彻底删除语义不变**：`delete_batch` 停稳后复用原 `purge_batch`——删 `workflows/<批次>`、`inbox/<批次>`、回收归档、音频状态、内存调度状态并清 `_OVERVIEW_CACHE`/manifest/review/agent_tasks 等缓存，不留痕迹。
- **列表区 `⋯ → 彻底删除` 保持**：两处入口都走 `purge`/`delete`，行为一致。

## 四、如何验证

```
cd app
python3 -m unittest workflow.test_workbench_delete_batch         # Ran 7 tests OK
python3 -m unittest workflow.test_workbench_catalog_archive workflow.test_workbench_delete_batch \
    workflow.test_workbench_queue workflow.test_workbench_units workflow.test_workbench_group_save
# Ran 51 tests OK (skipped=9)
cd .. && node --test backend/*.test.cjs                          # 253 pass / 0 fail (skipped=2)
node --check lib/client.js lib/index.js                          # OK
```

结果：后端 7 项 + 51 项用例通过（skip 9），前端 253 项通过（skip 2），`node --check` 通过。

## 五、回退方式

- 删除 `app/workflow/test_workbench_delete_batch.py`；
- 撤销 `app/workflow/auto.py` 的取消标记函数与阶段边界检查、`app/workbench/server.py` 的 `delete_batch/_cancel_running_batch/DELETE_STOP_WAIT_SECONDS` 与 `/delete` 路由、`lib/client.js` 的删除按钮/弹层/handler；
- 回退 `AGENTS.md` 规则 21 与 `CHANGELOG/RULES.md` 对应条目、删除本文件。
