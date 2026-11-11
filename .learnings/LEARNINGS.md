# 经验与任务回顾

## [LRN-20261111-002] task_review

**Priority**: high
**Status**: resolved
**Area**: tools

### 内容
来源绑定不能只验证题干在来源窗口中唯一出现：窗口必须仍处于唯一、当前的大题边界内，否则相邻大题的图片也会被错误提升为本题材料。紧凑答案不能在遇到不明非空续行时保留上一行作完整答案；应拒绝该块，只有严格识别出的基础/提升横幅才可结束块。收紧规则后必须用真实只读材料复验，避免把合法横幅当答案续行误拒。

### 建议修复
同时测试跨大题过宽窗口、过期大题坐标、多行答案、伪横幅与真实横幅，并验证正式答案及预览切片到HTTP媒体链路；本轮79项定向通过，真实41项来源逐项通过、两张题图HTTP 200。

### 元数据
- Source: task_review
- Pattern-Key: current-section-boundary-and-answer-continuation

---

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
