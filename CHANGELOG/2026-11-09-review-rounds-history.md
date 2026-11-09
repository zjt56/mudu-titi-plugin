# 2026-11-09 跨轮审核差异留档（rounds-history + 过期观察先归档再出队）

## 背景 / 触发

用户明确（原话）：「一审和二审产出的差异是我需要的，不是作为卡点，这个项目就是为了找出这个不一样的地方，记录在试卷审核的工作台，知识点差异是我们需要的，用来判断大模型和哪些头容易出错，从而为了引入人工判断」。

即：知识点差异是**交付物**，不是需要消灭的故障。

复核 19 年安徽中考批次时发现一个与此直接冲突的行为：21:19 那次审核产出 10 条差异，
21:27 因代码改动触发后端热重启、批次重跑 assemble+review 后只剩 6 条；消失的 4 条
（q-1、q-8、group-26-27-28-29-30、group-75-76-77-78）在批次目录里**没有任何留档**：

- `ingest.build_review_files` 的 `_superseded` 分支（旧一轮 `round_sources` 的
  `input_binding` 与新计划不同）直接把未处理项从队列里过滤掉，且不写归档；
- 只有 `reconcile_rounds=True` 那条路径才会把过时项写进
  `07_review/retired-round-findings.json`；
- 轮次结果文件 `07_review/review-rounds.json` 每轮整份覆盖，没有按轮次留快照。

而「哪个单元这一轮被判为差异、下一轮模型不再上报」恰恰是用户要观察的信息
（哪类知识点、哪个模型头容易误判）。丢档等于丢证据。

## 改了什么

1. **`app/workflow/review_rounds.py`：新增跨轮快照**
   - `record_rounds_history(batch, evaluated=None)`：把 `review.evaluate_rounds` 的当前
     结果按轮追加写入 `07_review/rounds-history.jsonl`（一行一份快照：`recorded_at`、
     `protocol`、`input_binding`、`checked_rounds`、`passing`、`digest`、`findings`）。
   - 只追加，不覆盖；`digest`（binding + protocol + 规范化 findings）相同则不重复记账，
     导入重放、闸门刷新都不会刷出重复行。
   - 没有真正完成的轮次（`checked_rounds` 为空，即记录缺失/过期）**不写**，避免空快照
     冒充一次真实审核。
   - `write_summary()` 在汇总时调用它，并在 `overall-review-report.json` 增加
     `rounds_history: "07_review/rounds-history.jsonl"`；`overall-review-report.md` 增加一行说明。
2. **`app/workflow/ingest.py::build_review_files`：过期观察先归档再出队**
   - `current_binding` 分支里被 `_superseded` 过滤的**未处理**项，先写入
     `07_review/retired-round-findings.json`（`retired_from` 记录来源文件、
     `retired_reason="旧轮次输入绑定已过期；原判据保留，供跨轮比对"`），再移出队列。
   - 已有人工结论（`decided_by` / 非 open）的条目行为不变：仍保留在队列与 `decisions.jsonl`。
   - 归档去重：同一条记录不会因重复导入而重复追加。
3. **`app/workflow/test_review_rounds.py`：补 3 条回归**
   - `test_new_binding_retires_superseded_open_items_but_keeps_decisions` 增加断言：
     被淘汰的 `WF-9001` 必须出现在 `retired-round-findings.json` 且带 `retired_reason`。
   - `test_rounds_history_is_append_only_and_deduplicates`：同一轮重复调用只记一行；
     未完成的轮次不记；判据变化后追加第二行且旧行保留。
   - `test_write_summary_publishes_rounds_history_pointer`：汇总报告带历史文件指针且文件存在。

## 没改什么 / 边界

- 不改变队列语义：`manual-review-queue.json` 仍然只列当前轮次未处理的真实差异；
  `review_gate` 仍然 `needs_review`，不自动 resolve、不改标签、不为放行改判。
- 不回填历史：19 年批次 21:27 之前消失的 4 条没有存档来源，本次不伪造写入；
  从下一次审核开始逐轮留档。
- `rounds-history.jsonl` 只是证据文件，不参与闸门判定；`07_review` 没有未知文件白名单，
  新增该文件不影响 `validate`/`review` 阶段产物校验。

## 如何验证

从 `app/` 目录：

```
$env:PYTHONPATH=(Get-Location); $env:PYTHONUTF8=1
python -m unittest workflow.test_review_rounds
```

结果：**Ran 70 tests, OK（70 项通过）**（含新增 3 条）。

```
python -m unittest workflow.test_review_rounds workflow.test_review_rounds_recovery workflow.test_pipeline_regressions workflow.test_review_consensus
```

结果：**Ran 139 tests，仅 1 个既有错误**
`test_review_rounds_recovery.test_default_reuses_completed_rounds_and_fresh_archives_without_rebinding`；
该错误在改动前的 `9387566` 上同样存在（用临时 worktree 复跑确认），与本次改动无关。
`workflow.test_review_legacy` 的 5 个失败同样在改动前就存在（规则冻结键格式
`rules:docs/...` 与旧断言 `/docs/...` 不匹配），本次未触碰该模块。

## 回退方式

`git revert <commit>`；或删除 `07_review/rounds-history.jsonl` 并还原
`review_rounds.write_summary` / `ingest.build_review_files` 两处调用即可，队列与闸门
数据均由既有文件重算，无需迁移。
