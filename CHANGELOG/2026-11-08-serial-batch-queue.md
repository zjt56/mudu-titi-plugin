# 2026-11-08 多份试卷上传改为全局串行排队

## 背景

用户要求：上传多份试卷后，排队区必须**依次串行**执行——第一份跑完才轮到第二份，不能并行同时跑。

## 改了什么

- `app/workbench/server.py`：
  - 新增进程级 FIFO 队列 `_advance_queue` + 单一调度线程 `_advance_worker_loop`，同一时刻只允许一个批次在推进。
  - `_launch_advance` 不再直接起每批次独立线程，而是把批次入队并唤醒唯一 worker；`_advance_state[name]` 记录 `queued/running`。
  - 抽取 `_run_advance_now` 作为单批次同步执行体（保留原 claim/record_result 逻辑）。
  - 重复入队去重；worker 队列空时退出，下次入队重新拉起。
- `lib/client.js`：
  - `recoveryProgressText` 增加“排队等待：前一份试卷跑完后自动开始”提示。
  - 「继续处理」按钮在 `queued` 时显示「排队中…」，`running` 时显示「推进中…」。

## 边界

- 只限制**多批次之间**串行；单批次内部各阶段/分片的既有并行（如独立 agent 分片）不变。
- 不改变每个批次自身的推进逻辑、审核、上传边界。

## 验证

- 模拟三批次（A、B、C）入队：`max concurrent = 1`，执行顺序严格 `A end → B start … C end`，无并行。
- `node --check lib/client.js`、`py_compile server.py` 通过；`backend-lifecycle` + `settings-navigation` 20 项通过。

## 回退

恢复 `_launch_advance` 直接起线程、移除队列与 worker 即可。
