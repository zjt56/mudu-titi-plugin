# 2026-11-09 切段探活（Windows）与组装不再等听力

## 背景 / 触发

19 年安徽中考批次卡住：`assemble` 连续 15 轮报
`Stage preconditions unmet: Prepare 02_audio/audio_map.json before assemble`，
`self-heal` 空转在同一签名上，工作台一直显示「该批次已有切段任务在运行，请等它结束」。

逐层复核后确认是四个因素叠加：

1. **切段状态是残留**：`02_audio` 里只有 `.segment.lock` 和两个 `.transcribe-*` 临时目录，没有音频切片；`audio/status.json` 却仍写 `running:true`（20:57:07 最后一次心跳，进程已随后端被外部结束，`finally` 未执行）。
2. **Windows 上探活恒为真**：`auto.py::_segment_lock_held` 在 `ImportError` 分支直接 `return True`；`server.py::_audio_has_live_worker` 依赖 `ps -Axo`，Windows 上抛异常后也 `return True`。两者都把「读不到活进程」当成「还在跑」。
3. **旧状态压住手动重跑**：`start_audio_job` 只看状态文件 `running` + 600 秒阈值，面板 10 分钟内点重跑同样被挡。
4. **组装硬绑 audio_map**：`agent_tasks.py::_collect` 对 `assemble` 也执行 `add(ARTIFACTS["audio"], ...)`，与 `contracts.py` 的 assemble 契约（`depends_on` 不含 audio，注释写明「缺映射时按待部署媒体记录」）和 AGENTS.md 硬规则 15 冲突。`pipeline-audit-2026-11-06.md` 的 A5 已记录该缺口。

第 2、3 条让 19 年批次白等 15 分钟心跳窗口，第 4 条让组装永远等一个「听力产不出的前置」。

## 改了什么

1. **`app/audio/segment.py`：新增 `lock_is_held(batch)` 探针**
   - 非阻塞探测 `02_audio/.segment.lock`：无文件→`False`；打不开→`True`；Windows 用 `msvcrt.locking(LK_NBLCK, 1)` 试锁、成功即解锁并返回 `False`；POSIX 用 `fcntl.flock(LOCK_EX | LOCK_NB)`。
   - 不再需要平台分支各自猜，锁本身就是「切段在跑」的唯一真凭据。
2. **`app/workflow/auto.py::_segment_lock_held`**
   - 委托 `audio.segment.lock_is_held`；只有探针本身不可用（`ImportError`/`OSError`）才保守返回 `True`；删除 Windows「回退心跳」的旧分支。
   - `AUDIO_HEARTBEAT_TIMEOUT_SECONDS = 15 * 60` 保留为兜底：锁空闲时按状态心跳判断是否仍在推进。
3. **`app/workbench/server.py`：音频任务状态与重置**
   - 新增 `_audio_batch_folder(batch)`（只认 `WORKFLOWS` 下的直接子目录，防止越界）。
   - `_audio_has_live_worker(batch)` 改为「本进程 `_audio_job` 正在跑该批次」或「`lock_is_held` 为真」，删除 `ps -Axo` 实现。
   - `_audio_reap_stale` 去掉 600 秒年龄门槛：只要状态写 `running` 而没有存活证据，立即重置为 `running=false, ok=false, phase=error`，并写明「切段任务已中断（无存活进程，状态文件为残留），已自动重置，可重跑」。
   - `start_audio_job` 仅在真的有存活证据时才抛「该批次已有切段任务在运行」。
   - `audio_status` 对「状态写 running 但无存活证据且心跳超过 `_AUDIO_STALE_GRACE`（5 秒）」返回 `stale=true, running=false` 与可重跑提示；常量 `_AUDIO_STALE_SECONDS=600` 由 `_AUDIO_STALE_GRACE=5` 取代。
4. **`app/workflow/agent_tasks.py::_collect`：组装不再绑定 audio_map**
   - `assemble` 分支只 `add(ARTIFACTS["knowledge"])`，不再 `add(ARTIFACTS["audio"], ...)`；`review` 保持硬前置。
   - 多音频聚合一致性校验的适用阶段从 `('audio', 'assemble', 'review')` 收窄为 `('audio', 'review')`。
5. **新增回归 `app/workflow/test_audio_liveness.py`**
   - `SegmentLockProbeTest`：锁文件缺失 / 空闲 / 被第二个句柄持有三种情形下 `segment.lock_is_held` 与 `auto._segment_lock_held` 的判定。
   - `StaleAudioStatusTest`：无锁不算活；持锁算活；未知批次不算活；`_audio_reap_stale` 重置残留 running、不动真在跑的任务；`audio_status` 不把死进程报成 running。
   - `AssembleDoesNotWaitForAudioTest`：`manifest.inputs.audio` 存在时 `_collect(batch, "assemble")` 既不把 `02_audio/audio_map.json` 记入 `missing`，也不记入 `input_hashes`；audio_map 存在时组装同样不绑定；`review` 仍必须要有。

## 没改什么 / 边界

- **一审/二审产出的知识点差异不修、不裁决、不自动 resolve**（用户 2026-11-09 原话：差异是需要的产出，用来判断模型和哪些头容易出错，交给人工判断）。本次不改 `07_review/manual-review-queue.json`、不写 `decisions.jsonl`、不改 `review-gate.json` 的 `passed`，也不改任何 knowledge 标签。
- 组装不等听力，但 `review` 仍必须等 `audio_map.json`（审核要读转写核对听力语义），硬规则 15 保持原口径。
- 上传边界不变：`review_gate=needs_review` 仍不放行自动上传；用户手动上传走 `unreviewed_confirmation` 路径（`runner._cmd_upload_locked` 在 `unreviewed` 时跳过 review_gate 的新鲜度检查、允许 `result["passed"] == False`），本次未改动该路径。
- 不重跑、不改写任何已归档批次的数据；19 年批次只做只读复核。

## 如何验证

- `cd app && PYTHONPATH=. PYTHONUTF8=1 python -m unittest workflow.test_audio_liveness workflow.test_auto_audio workflow.test_agent_tasks workflow.test_paper_audio` → **Ran 72 tests, OK**。
  - 不加 `PYTHONUTF8=1` 时 `test_agent_tasks` 有 5 个 `UnicodeDecodeError: 'gbk' codec ...`，是本机 GBK 默认编码下测试自身 `read_text()` 未指定编码的既有问题，与本次改动无关。
- 现场复核（21:12–21:16）：心跳越过 900 秒后 supervisor 判 stale 并重跑切段，21:16:26 产出 `audio_map.json`/`segment-evidence.json`/17 个切片；同一 dispatch 内 `audio→assemble→meta→underlines→validate` 依次 completed，`review` 21:19:24 完成，`review_gate` 落盘 `needs_review`（10 条知识点差异，`manual_only=true`）。
- `workflow/supervisor-result.json` 现为 `{"status":"reviewing","reason":"自动阶段已全部完成"}`，无残留 `running` 阻塞。

## 回退方式

`git revert` 本提交即可；三处改动互不依赖，也可单独回退 `agent_tasks.py` 的 assemble 分支（恢复旧行为会让组装重新等 `audio_map.json`）。
