# 2026-11-10 卡点台账升级为代码强制：自愈/监督器自动留档 + sync 汇总

## 背景

用户问「agents 里面的硬规则是否在代码层面已经做约束」。逐条核对后发现：24 条硬规则里绝大多数有代码校验/闸门与回归，但有两处只是文档约定——

1. **规则 24（卡点台账）**：`issue_log` 只是工具，`grep issue_log` 在 `app/`、`lib/`、`backend/` 里**没有任何调用方**，也就是说“每次卡点都记录”完全依赖 Agent 自觉；
2. **规则 7（流程图 ≤10 节点）**：只有 `lib/client.js` 里 9 组硬编码，没有任何断言拦住。

用户确认“需要”补代码约束。本轮先补第 1 点（第 2 点已在 `257f167` 用 `test_flow_chart_nodes.py` 补上）。

## 改了什么

### 批次内自动台账（新）

- `app/workflow/issue_log.py`：
  - `record_blocker(batch_dir, stage, check=…, symptom=…, why=…, how=…, source=…)`：把一次真实卡点写进**该批次自己**的 `workflow/blockers.json`；同一「阶段+检查+现象」只累加 `occurrences`、更新 `last_seen`；
  - **best-effort**：批次目录不存在/不可写/是软链接时返回 `None`，绝不抛异常打断流程；不创建幽灵批次目录；
  - `load_blockers`、`blockers_path` 只读视图；
  - `sync(batch_dir)` + CLI `sync --batch <目录> | --all`：把批次内台账汇总进 `ISSUES/open/`，按「批次+阶段+检查」去重（首次建单，之后只累加）。
- `app/workflow/self_heal.py`：`_save` 在写台账前调用 `_record_blockers`，仅对真实卡点 outcome
  （`repair_not_applicable` / `repair_failed` / `retry_budget_exhausted` / `gate_retry_budget_exhausted`）
  登记；`retry_scheduled`、`upstream_escalated`、`content_retry_with_feedback` 等“仍在推进”的结果不登记；
  同一事件（阶段+检查+时间）用 `self-heal.json` 里的 `blockers_recorded` 去重，重复 `_save` 不会刷高次数。
- `app/workflow/supervisor.py`：`_tick_batch` 判定 `exhausted`（连续 `max_no_progress` 次调度无有效进展）时
  调 `_record_exhausted_blocker`，按当前第一个未完成阶段登记 `no_progress_exhausted`。

### 文档与规则同步

- 根 `AGENTS.md` 硬规则 24 增补「代码强制」段（自动留档点、`sync`、best-effort、不等于已修复）；
- `app/AGENTS.md`「卡点台账」小节同步；
- `ISSUES/README.md` 新增「自动留档（代码强制）」章节与 `sync` 用法。

## 没改什么 / 边界

- 自动留档**只写批次内** `workflow/blockers.json`，测试与离线操作不会污染仓库 `ISSUES/`；仓库台账仍由 `sync` 显式汇总。
- **自动留档 ≠ 已修复**：条目仍留在 `open/`，必须真的修好并复验后 `issue_log fix`；不自动 resolve、不改批次状态、不改 manifest/收据、不碰上传边界。
- 台账写入失败绝不影响自愈/调度（best-effort），也不因此让流程停住。
- 审核差异（`review_gate=needs_review + execution_completed`）与限流等瞬时错误仍不算卡点，不登记。

## 验证

- `workflow.test_issue_log` 14 项通过，新增覆盖：批次内 upsert 累加且不污染仓库、best-effort 返回 None、
  `sync` 首建/再同步只累加、自愈只记真实卡点且同一事件不重复计数、监督器 exhausted 登记。
- 自愈/监督器相关 9 个套件共 **158 项通过**（`test_self_heal*`、`test_supervisor*`、`test_issue_log`）。
- 手工 CLI 验证：`record_blocker` ×2 → occurrences 1→2；`sync` 首建 2 条、再同步 0 新建 2 更新。
- 全量结果见提交记录（沙箱禁止 `ps` 的 2 项环境错误与另一会话未提交改动导致的 1 项失败与本改动无关）。

## 回退方式

回退本提交即可：`workflow/blockers.json` 与 `ISSUES/` 都是纯数据，删除不影响任何批次与工作流产物。
