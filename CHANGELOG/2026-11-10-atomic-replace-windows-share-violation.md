# 2026-11-10 Windows 上原子替换遇共享冲突导致审核写盘失败

## 背景 / 触发

拉取上游 24 个提交（`3ee3528..922715a`）后按项目要求跑受影响模块，`workflow.test_review_rounds`
在 Windows 上连续两次整模块失败，每次失败的是**不同**用例，但栈底一模一样：

```
File "app/workflow/review_rounds.py", line 1265, in write_summary
    _atomic(_path(batch, "07_review/overall-review-report.md"), ...)
File "app/workflow/agent_protocol.py", line 69, in _atomic
    os.replace(temporary, path)
PermissionError: [WinError 5] 拒绝访问。:
  '...\07_review\.protocol-so6mv_cb' -> '...\07_review\overall-review-report.md'
```

根因是 Windows 的文件共享语义：只要还有任何句柄以默认共享模式打开着
`overall-review-report.md`（面板/总览正在读、杀毒在扫描刚写出的临时文件），
`os.replace` 就返回 WinError 5。`_atomic` 原来只调用一次 `os.replace`，第一次撞上就抛出，
`review_rounds._run` 因此中断——在真实批次上表现为**内容审核阶段直接失败**，而不是重试一次就好。

同一批测试单独运行该用例时通过，说明不是逻辑错误，而是毫秒级的句柄竞争窗口。

## 改了什么

1. `app/workflow/agent_protocol.py`
   - 新增 `_replace_atomic(temporary, path, attempts=10, delay=0.05)`：遇到 `PermissionError`
     按递增退避重试（0.05s、0.10s……约 2.75s 上限），最后一次仍失败则抛出**原始** `PermissionError`，
     不吞错误、不改写错误类型。
   - `_atomic` 改为调用 `_replace_atomic`；仍保持「先写临时文件 → fsync → 替换 → 失败清理临时文件」，
     失败路径不会留下 `.protocol-*` 残留。
2. `app/workflow/test_agent_protocol.py`
   - 新增 `AtomicReplaceRetryTest`：
     - `test_retries_share_violation_then_succeeds`：`os.replace` 前两次抛 `PermissionError`，
       第三次成功后内容正确、目录只剩目标文件（验证重试次数与临时文件清理）。
     - `test_persistent_violation_still_raises_after_bounded_retries`：始终冲突时恰好尝试 10 次，
       仍抛 `PermissionError`，且目标不存在、目录为空（验证有界、不静默成功）。

## 没改什么 / 边界

- 没有放宽任何文件校验、任务绑定或审核闸门；只是把「写盘瞬时冲突」从失败重试掉。
- 没有把持续冲突当成成功：重试耗尽依然抛错，由阶段失败与自愈流程按原逻辑处理。
- 不改变 `_atomic` 的原子性语义：仍是「临时文件 + 同目录替换」，读者不会看到半截文件。
- 既有 Windows 平台失败（`test_agent_entry` 的路径分隔符断言、`test_local_agent` 的候选缓存用例）
  在拉取前的提交 `3ee3528` 上同样失败，不属本次改动范围，未处理。

## 如何验证

从 `app/` 目录（`PYTHONPATH` 指向 `app`）：

```
python -m unittest workflow.test_agent_protocol
```

→ **Ran 32 tests, OK (skipped=1)**（含新增的 2 个重试用例）。

```
python -m unittest workflow.test_review_rounds
```

→ 修复前连续两次 `FAILED (errors=1)`（不同用例、同一 `PermissionError`）；
修复后连续两次 **Ran 84 tests, OK**（84 个用例两次全部通过），耗时 85s / 47s。

回归组合：`workflow.test_supervisor` + `workflow.test_diagnose` → **Ran 76 tests, OK (skipped=1)**；
`workflow.test_agent_tasks` + `test_agent_entry` + `test_local_agent` + `test_issue_log` + `test_operations`
→ 除 `test_agent_entry` 与 `test_local_agent` 各 1 条既有 Windows 失败（基线同样失败）外全部通过。

## 回退方式

`git revert <commit>`；回退后 Windows 上写覆盖仍可能因瞬时读句柄失败而中断审核。
