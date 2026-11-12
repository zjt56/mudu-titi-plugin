# 经验与任务回顾

## [LRN-20261112-002] knowledge_gap

**Priority**: medium
**Status**: resolved
**Area**: infra

### 内容
运行时子Agent方案评估必须先核对生产者与消费者，不能把单次调用的限制直接认定为业务能力缺失。本项目lesson_bands已经按8图分批、按图片字节和实际路由缓存并由正文位置重建边界；stage_preflight已有答案和解析零模型直抄。operations-agent的tools.register.parameters约束模型输入，output.schema约束工具返回，后者不能直接作为模型提交候选的schema。子Agent可增加多轮取证，但不证明准确率或成本改善。

### 建议修复
保留确定性直抄和现有缓存；候选提交使用parameters及现有业务验收器；先在限定单元试点并衡量总耗时、模型调用、token与来源误配。结构分窗前保留全卷目录、共享材料及边界闭合检查；同会话反馈不得重试确定性来源或校验器故障。

### 元数据
- Source: task_review
- Pattern-Key: runtime-agent-benefit-requires-consumer-and-fastpath-audit

---

## [LRN-20261112-001] task_review

**Priority**: high
**Status**: resolved
**Area**: source_projection

### 内容
原生表格引用和上传 schema 同时有效，仍不证明投影保真：单元格可以互换、合并跨度可以合法改动而不触发 schema。最终适配必须逐来源身份对比格内文字、顺序与跨度。空白线可以归一化，但字母下划线有题意，不能统一作为样式剥除。同图跨选项/答案/解析的显示归属不同，文件名相同不应全局去重。

### 建议修复
使用合成 DOCX 串起提取、组装、预览与实际 payload builder；同时测试合法网格中的换格/跨度修改/漏表，及画线丢失/移位。本轮37项新增专项通过，平台不支持的格内媒体位置明确报告，不以题干附件冒充保真。

### 元数据
- Source: task_review
- Pattern-Key: source-table-projection-cell-ownership

---

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
