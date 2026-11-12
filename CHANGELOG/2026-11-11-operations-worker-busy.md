# AI 运维执行锁竞争的准入等待

## 背景（触发）

用户反馈 AI 运维接手时出现 `worker_busy: 批次执行锁已占用; name=Error`。现场批次为「【木牍教育】12年安徽中考英语」，目录 `C:\Users\Administrator\.dsh\wf-plugin-data\workflows\【木牍教育】12年安徽中考英语`，事件 `9eb1f62f9c3c45d1b36c93ad3fe7e961`。11:19 的只读证据读取后，释放因锁竞争被错误显示为 `release_unknown`。

根因是动作还没通过锁准入就被拒绝，但 HTTP 丢失拒绝类型，宿主把普通错误统一归入结果未知；心跳遇到相同竞争也立即判定失租约。原锁上下文捕获范围还覆盖动作体，可能把动作内部的 lock_timeout 错当成准入拒绝。

## 改了什么

- `app/workflow/operations.py`：专用 `OperationsBusy` 只表示 auto/run/global 锁的准入拒绝；用 `ExitStack.enter_context` 收窄捕获范围，动作体异常不改型。心跳回执携带真实 `lease_expires_at`。
- `app/workbench/server.py`：6个运维 POST 入口仅对专用拒绝返回409，附 `worker_busy / admitted=false / retryable=true / retry_after_ms=500` 和批次名称、目录。
- `lib/index.js`：HTTP 桥保留可信拒绝元数据。
- `lib/operations-agent.js`：新请求明确未准入时，同一 body/request_id 最多等待10秒；认领忙等待下次扫描。临时心跳竞争不立即取消，仍受真实期限约束；等待耗尽显示等待执行锁或等待释放，不伪造未知回执或成功。已有未知动作继续先查回执，网络/500/纯文字 busy 不自动重发。
- `lib/client.js`：增加等待执行锁、等待释放锁状态；消息区原有固定高度和内部滚动保留。
- Python、宿主回归覆盖拒绝无副作用、旧未知不被清除、同请求只执行一次、锁释放续接、心跳期限和忙耗尽不假成功；运维文档补充协议。

## 没改什么 / 边界

未取消任何批次/执行/全局模型锁，未改用户数据、manifest、审核差异或上传边界。动作内部失败仍按原失败处理，未知结果禁止盲重发。

12年另有解析来源未唯一绑定的业务阻断，以及模型 `Insufficient Balance / code=QUOTA`。11:27:46 现场已成功 claim、progress、heartbeat，11:27:53 成功 release；事件仍 queued，verification 为空，不能称解析故障已修好。模型余额问题不登记为代码卡点，也不换模型兜底。

11:38 用户切换模型后复核：插件 default 与 Harness default 均为 `opencode-go / deepseek-v4.1-flash`；同一运维会话的持久 `modelSelection.lastUsed` 为该路由，pending为空，现场当前尝试已执行到 read_evidence 且error为空。此前QUOTA为历史尝试，不能继续当成当前阻断。

Python 后端由原热重启机制加载；宿主 JS 源码更改不等于当前进程已加载新版本。本轮以受控真实 HTTP 和宿主回归验证锁协议，不人为制造真实试卷故障；若无加载证据，不能宣称现场等待分支已触发。

## 如何验证

- Python相关11模块：**170/170通过**（52.113秒），包括5个 operations 模块及 health_busy、manual_advance_reset、stage_parallel_display、list_projection、prepare_feedback、upload_status。
- 宿主5模块最新：**76/76通过**（2.813秒）：operations-agent、operations-agent-autonomy、operations-ui、operations-host、operations-http。
- 前端/路由全量：253项，**237通过、15失败、1跳过**。失败分布：backend-lifecycle 8项、model-router 6项、release-handoff 1项；包含本机 Windows python3 空壳9009及生命周期环境问题，不宣称全量通过。日志：`C:\Users\Administrator\AppData\Local\Temp\tiku-worker-busy-node-full-utf8.log`。
- Python额外188项广测：183通过、1失败、4错误；用精确HEAD原版 server 内存基线复跑，同5项仍失败（needs_human工作提示、Windows缺os.O_DIRECTORY 3项、SSH配置），未扩大修改范围。
- `node --check` 三个宿主文件、`git diff --check` 通过。
- 文档制度回归5项：3通过、2失败，分别是既有来源策略记录缺少规定章节标题、既有 misc 记录未进索引；本轮新记录章节与索引齐全，不把该结果记为全过。

## 回退方式

回退本次提交中的运维准入协议和宿主等待补丁；无用户数据迁移。旧宿主不能识别结构化拒绝时会按普通失败展示，不允许以此放宽锁或未知请求规则。
