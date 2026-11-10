# 2026-11-10 中断不计入预算、上传计入在途

## 背景 / 触发

用户要求「继续优化修复，查找刚才的卡点，看哪些值得优化」。复盘 23 年安徽中考英语那次卡点（解析/文章大意 13 个单元来源未绑定）时发现两处仍然会在同样场景下再次出问题：

1. **中断被当成内容失败**。自愈在调用模型**之前**就把尝试次数写进台账（这是防止重启后重复诊断的必要设计），但进程被整树杀掉时事件永远停在 `outcome=diagnosing`、没有 `finished_at`。结果是：面板一直显示「AI 自动修复：正在判断卡点原因」，而这次环境中断还被算成一次内容失败，吃掉 `MAX_ATTEMPTS_PER_STAGE` 的预算。23 年那次三次中断（15:56:19 / 15:59:21 / 16:02:35，间隔正好 180s 冷却）就是这样累积的。
2. **上传不在「在途工作」里**。`work_in_flight()` 只统计 advance、排队批次与切段任务。上传被中途回收后端，远端回执会变成 unknown（只能人工核实），代价比本地阶段重跑更高，却恰恰是最不该被打断的。

## 改了什么

1. `app/workflow/self_heal.py`
   - 新增常量 `INTERRUPTED_GRACE_SECONDS = 60.0`。
   - 新增 `_close_interrupted_diagnosis(ledger, attempt_key, stage_id, signature)`：同一签名上最后一条事件若仍是 `outcome=diagnosing`、没有 `finished_at`、且已超过 60 秒（更年轻的 claim 可能属于另一个执行者，不能碰），就把它收尾为 `outcome=diagnostic_interrupted`、写入 `finished_at` 与 `escalation` 说明，并把该签名已占用的尝试次数退回 1 次；同时把 `last_status` 置为 `diagnostic_interrupted`，避免状态文件继续冒充「正在诊断」。
   - `recover()` 在算出签名后立即调用它；命中时清掉 `next_retry_at` 冷却，让同一失败用退回的预算立刻重判。
2. `app/workbench/server.py`：`work_in_flight()` 追加「`_upload_jobs` 里有 running 的上传任务」判据（与 advance、排队、切段并列），仍保持「状态读不到时保守判忙」。
3. `lib/client.js`：`recoveryProgressText` 增加 `diagnostic_interrupted` 文案「上次诊断被重启中断，不是题目问题，已自动重新判断」。
4. 测试：`app/workflow/test_self_heal.py` 新增 `test_interrupted_diagnosis_is_closed_without_consuming_the_budget`（重放「claim 已落盘、结果没写回」的现场，断言收尾为 `diagnostic_interrupted`、有 `finished_at`、下一次尝试仍用第 1 次预算）与 `test_recent_claim_and_finished_diagnosis_are_left_alone`（刚写入的 claim 与已收尾事件都不改写）；`app/workbench/test_health_busy.py` 新增上传忙/上传完成两条。

## 没改什么 / 边界

- 「中断」不写 ISSUES 台账、也不触发 AI 运维接手：这是宿主重启造成的环境问题，运维没有可修复的对象；它只留在自愈台账与状态文件里，并明确标注不是内容失败。
- 真正的语义失败（`repair_failed`、`retry_budget_exhausted` 等）仍然照旧占用预算、照旧落 ISSUES、照旧被运维接管；没有放宽任何质量或上传边界。
- 上传忙只影响「能否回收后端」这一个判断，不改变上传流程本身，也不改变 `upload_gate` 的人工边界。

## 如何验证

- `python -m unittest workflow.test_self_heal workflow.test_operations workbench.test_health_busy` → **81 个用例全部通过**（改动前 self_heal 只有 7 条、health_busy 6 条）。
- `node --check lib/client.js`、`node --check lib/index.js` 均通过。
- 现场核对：23 年批次的 `workflow/self-heal.json` 里三条 `diagnosing` 事件间隔 180s，与 `next_retry_at = now + 180` 一致，确认「中断吃掉预算」的机制成立。

## 回退方式

`git revert` 本提交即可；三处改动互不依赖，回退后行为与 `ac1e14b` 相同。
