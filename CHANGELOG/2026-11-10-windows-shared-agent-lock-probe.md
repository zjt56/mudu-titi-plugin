# 2026-11-10 Windows 并行模型调用期间 supervisor 误判空闲（共享锁槽探测）

## 背景 / 触发

拉取 `origin/master`（9 个提交，`d2fc74d..11987dc`）后按项目验证要求跑受影响模块，
`workflow.test_supervisor` 在本机（Windows）稳定失败一条：

```
FAIL: test_external_auto_runner_and_model_locks_prevent_dispatch
AssertionError: False is not true   # supervisor.execution_busy(folder) 应为 True
```

定位到 `app/workflow/supervisor.py::execution_busy`：它用 `_try_file_lock` 逐个探测
`mudu-auto-<sha>.lock`、`<批次>/workflow/.run.lock`、`mudu-local-agent-<sha>.lock`
的**第 0 字节**。

但 `app/workflow/local_agent.py::_agent_lock` 在 Windows 上的锁布局是字节区间：
独占调用锁 0..1023，**共享调用（并行模型任务）各占 1..1023 中的一个字节**
（`WINDOWS_LOCK_RANGE = 1024`，msvcrt 没有共享模式，故用不同字节模拟 POSIX flock）。

结果：并行模型任务正在跑时，supervisor 只探测字节 0 → 探测成功 → 判「不忙」→
可能在同一批次上再派发一轮 advance，重复烧模型调用（正是硬规则 2/18 要避免的
「同一策略重复派发」）。POSIX 上 `flock` 共享/独占互斥，不受影响，所以这个问题
只在 Windows 出现，Linux 上跑测试看不出来。

## 改了什么

1. `app/workflow/supervisor.py`
   - 新增 `_agent_lock_busy(path)`：POSIX 仍用非阻塞 `flock` 探测；
     Windows 先按需把锁文件补足到 1024 字节，再用
     `msvcrt.locking(fd, LK_NBLCK, WINDOWS_LOCK_RANGE)` **独占锁整个区间 0..1023**——
     与任何共享槽位（1..1023）和独占调用（0..1023）都冲突，因此任一活着的模型调用
     都会被判为忙；探测成功后立即 `LK_UNLCK` 释放，不改变文件内容与既有锁语义。
   - `execution_busy` 改为：先探 `.run.lock` / `mudu-auto-*.lock`（行为不变），
     最后用 `_agent_lock_busy` 探 `mudu-local-agent-<sha>.lock`。
   - 只读探测失败（拿不到句柄）时按「忙」处理，宁可少派发一次也不重复执行。
2. `app/workflow/test_supervisor.py`
   - `test_removed_batch_is_not_recreated` 在 Windows 上加 `skipIf` 并写明原因：
     tick 期间持有 `workflow/.supervisor.lock` 句柄，Windows 内核禁止重命名该批次目录
     （即使文件以 FILE_SHARE_DELETE 打开也不行，已实测），所以「批次在 tick 中途被移走」
     在 Windows 上物理不成立；POSIX 上重命名会成功，该用例继续覆盖「不得重建已删除批次」。
     这是平台语义差异，不是放宽断言。

## 没改什么 / 边界

- 不改 `local_agent._agent_lock` 的锁布局，也不改任何模型的并发上限：只是让 supervisor
  的探活与真实锁布局对齐。
- 不把 Windows 上「tick 中无法重命名批次目录」当成已解决：用户删除批次走
  `server.purge_batch`（硬规则 21），失败时仍如实报错；本次没有放宽 `purge_batch`。
- `workflow.test_runner_auto` 在本机有 2 个既有失败/错误（并行 wave 与 rate-limit 用例，
  报 `PermissionError` 读 `manifest.json`）。用 `git stash` 回到改动前代码复跑同样失败
  （`Ran 43 tests, FAILED (failures=2)`），属 Windows 并发文件句柄的既有问题，本次未处理。

## 如何验证

从 `app/` 目录：

```
$env:PYTHONPATH=(Get-Location); $env:PYTHONUTF8=1
python -m unittest workflow.test_supervisor
```

结果：**Ran 29 tests, OK (skipped=1)**（skip 即上面的 Windows 目录重命名用例）——
29 个用例全部通过，其中 1 个按平台跳过。

改动前同一命令：`FAILED (failures=1)`，失败点正是
`test_external_auto_runner_and_model_locks_prevent_dispatch` 的 `agent` 子用例。

组合回归（`test_review_rounds`、`test_diagnose`、`test_agent_tasks`、`test_operations`、
`test_supervisor`、`test_content_fragments`、`test_runner_auto`）：**Ran 338 tests**，
仅 `test_runner_auto` 的 1 个既有 Windows 并发错误/失败，与本改动无关（基线同样失败）。

## 回退方式

`git revert <commit>`。回退后 supervisor 在 Windows 上会重新看不到并行共享锁槽，
可能出现重复派发。
