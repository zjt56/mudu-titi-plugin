# 2026-11-11 自愈重试预算死循环导致整机卡死（修复）

## 背景 / 触发

用户报告：启动录题任务后电脑直接卡死两次。现场排查（`ps` / `vm.loadavg`）：

- `load average` 冲到 **45.45**（正常个位数），空闲内存被吃光，系统疯狂 swap。
- 同一时刻有 **十几个** `dsh cli.js --profile headless` 进程，每个约 500MB 内存、60%+ CPU。
- 来源：批次 `03七上人教版课时A计划StarterUnit1Hello!第三课时Section` 的 `answers` 阶段
  反复整阶段重跑，每轮按全部分片并发拉起子进程。

## 根因（两层叠加）

1. **配置并发过高**：`app/workflow/local-agent.json` 的 `part_concurrency=16`、
   `review_part_parallel=16`。答案阶段切十几片，最多 16 路并发，每路 fork 一个 headless
   进程，瞬间打满本机内存/CPU。
2. **自愈重试预算被"易变 detail"清零，形成无限循环**（本次修复的核心 bug）：
   `self_heal.recover()` 用
   `signature = sha256(stage_id + "\n" + detail + "\n" + evidence)`
   作为重试预算的 key。`detail` 来自 `diagnose` 的 item detail，**每次整阶段重跑都会变**
   （失败的分片集合不同、被杀子进程的 `exit -15/-9` 不同、分片顺序不同）。
   signature 一变，`attempts` 计数从 0 重新开始，永远达不到 `MAX_ATTEMPTS_PER_STAGE(=3)`，
   于是一个**确定性卡点**（答案附录区间缩写缺少可核验 scope/原文坐标，`scoped answer
   source is not uniquely bound`）被无限整阶段重跑。这与硬规则 2/19
   "确定性失败不得用重试消化 / 不因序列化差异重跑" 直接冲突。
   讽刺的是 `_retry_evidence()` 的注释本就写明 "Stable retry scope: actual
   inputs/code, never feedback or stage clocks"，但 signature 又把易变的 `detail` 拼了回去。

## 改了什么

- `app/workflow/self_heal.py`
  - `recover()`：预算 signature 改为 `sha256(stage_id + "\n" + failure_class + "\n" + evidence)`，
    不再直接拼原始 `detail`。
  - 新增 `_failure_class_key(detail)`：把失败 detail 归一成稳定的"失败类别"——
    抹掉 unit/section id（`sec-…-uN`）、`-qN`、子进程 `exit -N`、时间戳、行号、
    内嵌 evidence JSON 等易变片段，保留检查码/原因（`codes`/`reasons`）与剩余文本形状。
    同一类失败跨轮共享一个预算；真正不同的缺陷（不同 stage 或不同代码/输入）仍得到不同预算。
- `app/workflow/local-agent.json`：`part_concurrency` 16→3、`review_part_parallel` 16→3
  （本机可承受的保守并发；如需更高可调 4~6，不建议回到 16）。

## 没改什么 / 边界

- **没有**改动 `agent_tasks._widen_repair_scope`：首次交付无可复用基线时整阶段交付一次仍
  是正确且必要的（否则批次永远无法首次产出）。修的是"整阶段交付被无限重复"这件事。
- **没有**放宽任何质量闸门：确定性来源绑定失败依旧会被 `_content_retry_scope` 拒绝整卷重跑，
  或升级 `code_repair`；本次只是让"预算耗尽"真正能到达并触发 `retry_budget_exhausted`
  长冷却 + `escalation=code_repair`，而不是无限 requeue。
- **没有**改动用户题目数据、manifest 完成态或上传边界。
- 本次仅"止血"了自动推进循环（把卡死批次临时移到 `workflows/.paused/`），数据原样保留；
  该批次的真实内容问题（答案附录区间缩写缺 scope）仍需按 `ISSUES/open` 台账单独处理。

## 如何验证

- 新增回归 `app/workflow/test_self_heal.py::SelfHealTest::test_volatile_detail_does_not_reset_retry_budget`：
  - 断言 `_failure_class_key` 对"不同 unit id / exit code 的同类失败"返回**相同** key，
    对不同缺陷返回**不同** key；
  - 端到端模拟同类失败、每轮 detail 略变，`attempts` 累积到 `len(details)` 并到达
    `retry_budget_exhausted`（修复前会一直 requeue、attempts 永远为 1）。
- `PYTHONPATH=app python3 -m unittest workflow.test_self_heal workflow.test_self_heal_gaps
  workflow.test_self_heal_routing workflow.test_self_heal_runtime
  workflow.test_self_heal_verification workflow.test_self_heal_actions workflow.test_agent_tasks`
  → **Ran 166 tests, OK**。
- 现场模拟（真实批次失败类别样式）：同一个 answers 预算 key 在三次不同 detail 下
  attempts 1→2→3，第 3 次 `retry_budget_exhausted`。

## 回退方式

- 代码：`git revert` 本次提交；或把 `recover()` 的 signature 那行改回拼 `detail`，
  并删除 `_failure_class_key`（不建议——会恢复卡死 bug）。
- 配置：`app/workflow/local-agent.json.bak-20261111213010`（并发改前备份，值为 16/16）。
- 批次：`workflows/.paused/…第三课时Section/` 移回 `workflows/` 即恢复为活跃批次
  （但需先处理答案来源绑定问题，否则仍会消耗预算后 exhausted）。
