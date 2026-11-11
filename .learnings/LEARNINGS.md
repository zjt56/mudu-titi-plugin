# 经验与任务回顾

## [LRN-20261111-001] best_practice

**Priority**: high
**Status**: resolved
**Area**: infra

### 内容
Windows 不可把 POSIX 的 `os.kill(pid, 0)` 当作无信号进程探活：在本机会发控制台控制事件，导致同组任务或测试被中断。显示层探活改用 `_winapi.OpenProcess(SYNCHRONIZE, False, pid)` 与 `WaitForSingleObject(handle, 0)`，仅 WAIT_TIMEOUT 视为活跃，并在 finally 关闭句柄；POSIX 仍用原查询。冻结专项156项通过，含Windows真实子进程活/退出与异常关闭句柄回归。

### 建议修复
跨平台探活须分平台实现。批量回归为每模块提供独立TEMP/TMP及进程组，避免锁与控制台事件污染结果；中断的测试轮次不冒充测试失败或通过。

### 元数据
- Source: task_review
- Pattern-Key: windows-process-liveness-no-control-signal

---
