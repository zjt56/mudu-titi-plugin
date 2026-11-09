# 2026-11-09 Windows 审核并行锁与已完成审核的输入新鲜度

## 背景 / 触发

另一台 Windows 电脑的审查报告指出：审核 5 个分片的完成时刻严格首尾相接，18 年安徽卷 18分17秒墙钟约等于 5 个分片耗时累加（105.7/226.8/219.8/75/469.8 秒），配置的 `review_part_parallel: 4` 实际没有并行。根因是 `app/workflow/local_agent.py::_agent_lock()` 的 Windows 分支只有 `msvcrt.locking(..., LK_NBLCK, 1)` 独占锁，`shared=True` 被忽略；于是所有模型调用（包括审核四路分片）在 Windows 上全局串行。

同时报告提到：20 年安徽卷审核在 13:44/13:46 已完成两轮，但 14:02 答案阶段重跑并重建 assemble 后，旧审核结论没有及时作废，直到 18:36 才失效，白等约 4.5 小时。该问题不是全局“所有下游自动重跑”（项目此前明确不做），而是**已完成审核自身绑定的输入已经变化却仍保持 completed**；审核是最终质量闸门，必须与当前批次输入绑定。

## 改了什么

1. **`app/workflow/local_agent.py`：Windows 共享锁**
   - 新增 `WINDOWS_LOCK_RANGE = 1024`、`_windows_lock()`、`_windows_unlock()`。
   - 独占调用锁字节 `0..1023`；每个共享调用在 `1..1023` 中占用一个**不同字节**。Windows 字节区间锁天然互斥/重叠检测，因此多个 `shared=True` 调用可以真正并发，而任何独占调用仍排除所有共享调用，语义与 POSIX `flock` 的 `LOCK_SH`/`LOCK_EX` 一致。
   - 锁文件在 Windows 下自动扩展到 1024 字节（兼容旧 1 字节锁文件）；释放时精确解锁所持区间。
   - POSIX 路径、`pass_fds` 子进程继承语义、超时与错误码完全不变。
2. **`app/workflow/manifest.py`：审核绑定新鲜度**
   - 新增 `review_bound_inputs_changed(batch_dir, manifest)`：对 completed 的 review，比较完成时记录的 `input_hashes` 与当前输入。
   - 兼容旧台账：旧记录用裸 SHA-256，当前记录用 `json:`/`raw:` 规范化内容摘要；按每个键记录时的表示形式分别比较，忽略 `rule:` 规则文件及旧记录中不存在的后加键，避免把代码/规则更新误判为批次内容变化。
3. **`app/workflow/auto.py`：`_refresh_completed_review()`**
   - 在审核契约检查前先检查绑定输入；一旦变化，立即把 review 置为 `invalidated` 并写明“审核绑定输入已变化”，下一轮自动调度重新导出审核任务，而不是等数小时后才由任务绑定过期发现。
   - `manifest.record_delivery()` 在任何上游阶段重新交付时也执行同一检查，单步 `runner run --stage assemble` 等路径同样会立即作废旧审核。
4. **插件审核并发配置（`backend/local-agent.plugin.json`）**
   - `review_part_parallel` 从 4 调到 5：48 单元批次通常是 4 个标签片（15/15/15/3）+ 1 个 sample 片，5 路正好让 5 片同波跑完，避免 4 路下一片排队。普通阶段仍保持 4 路内存保护。
   - `review_part_units` 保持默认 15，不放大单次上下文。
5. **`backend/route_pick.py` / `backend/wf-router.mjs`：审核模型路由**
   - 修复审核分支漏读 `stages.review`、以及审核提示词没有 `Agent:` 头时 `roles.review` 不生效的问题。
   - 审核路由顺序明确为：`review.rounds[round]` → `roles[agent]` → `roles.review` → `stages.review` → `review.default` → 设备默认模型。这样插件设置里“审核”单独指定模型和 `config/model-router.json` 的 `stages.review` 都会真正生效，不会静默落回设备当前慢模型。
6. **`app/docs/local-agent.md`**：删除“Windows 仍独占、会串行”的旧说明，记录 Windows 字节区间共享/独占锁语义与插件并发默认值。

## 没改什么 / 边界

- **不引入全局下游级联失效**：`test_changed_producer_does_not_auto_invalidate_descendants` 所代表的既有规则保持不变——普通填充阶段的内容变化不自动把所有下游判失效；这里只保护最终审核闸门自身的输入绑定。
- 不固定任何具体模型：`config/model-router.json` 的 `default` 仍为空，审核模型由各设备插件设置或显式 `stages.review` / `review.rounds` 指定；没有可用模型时仍报错，不静默兜底。
- 不改变审核口径、人工审核范围、上传/发布边界；不自动上传。
- Windows 共享锁只影响并发调度，不改变任何阶段产物或质量判定。

## 如何验证

- `cd app && PYTHONPATH=. python3 -m unittest workflow.test_local_agent` → **37 tests OK**（含新增 `WindowsByteRangeLockTest` 3 项：共享调用占不同字节、独占排除全部、`_agent_lock` 真实分支加解锁）。
- `workflow.test_review_refresh workflow.test_delivery workflow.test_stage_semantics workflow.test_local_agent` → **79 tests OK**，含新增 `test_changed_bound_input_invalidates_completed_review`、`test_legacy_raw_review_binding_does_not_false_positive`、`test_upstream_delivery_invalidates_completed_review_immediately`。
- `workflow.test_runner_auto workflow.test_review_rounds workflow.test_review_rounds_recovery workflow.test_review_local_reuse workflow.test_agent_entry` → **178 tests OK**。
- `workflow.test_route_pick` → **12 tests OK**（含 `stages.review` 与无 `Agent:` 头的 `roles.review` 回退）。
- `node --test backend/wf-router-native.test.cjs` → **8 passed / 2 skipped**（含 `stages.review` 路由）；`node --test backend/model-router.test.cjs` → **8 passed**（插件并发配置断言已随 5 路更新）。
- `node --test backend/*.test.cjs` 全量 → 209 项：205 通过、2 跳过、2 失败；两个失败均为既有 `review-layout.test.cjs` 的 CSS 断言，与本次锁/路由改动无关。
- 真实存量批次只读核对：16年安徽 / 19年安徽 / 2024卷的 `review_bound_inputs_changed` 均返回 `[]`（不会因本次改动误重审）。
- `workflow.test_local_agent_ownership` 在本机受沙箱限制（`ps` PermissionError）无法完整执行；POSIX 锁语义相关项 `test_local_agent` 中已通过。

## 回退

恢复 `app/workflow/local_agent.py` 的 `_agent_lock` Windows 分支为原来的单字节 `LK_NBLCK`，并移除 `review_bound_inputs_changed` 与 `auto._refresh_completed_review` 中的调用即可；不影响其它阶段产物。
