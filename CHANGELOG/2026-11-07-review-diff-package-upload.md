# 2026-11-07 审核差异不再阻断打包：一审二审差异送人工 + 上传批次照常打包

## 背景 / 触发
用户澄清流程目的：**一审 + 二审找出与原标签的差异，写进审核工作台的差异队列给人工；
随后按上传格式把批次打包好（对接服务器上传 API 的数据包），这就是自动流程的终点。**
后续「点上传批次 → 服务器题库」是人工操作，不属于自动流程。

问题：`review_gate` 只要人工队列还有 open 项就判「闸门未通过」，`format_gate`（depends_on
review_gate）因此永远不跑，**上传批次根本打不出来**。用户看到的就是「审核放行判断：阶段被
阻断，原因：人工队列仍有 11 项未处理」。

同时发现一个**判定缺陷**：q-6、q-8 这类「看不到图片、未听音频」的**能力受限**项，因为
`review_consensus.compare` 构造 finding 的 `round_sources` 里**没带 rationale**，导致
`review._is_capability_gap` 读不到「未实际听音/未查看图片」的说明，把它们误当知识点差异塞进
人工队列。能力受限项按硬规则 6 只作提示、不进人工、不阻断。

## 改了什么
1. `workflow/review_consensus.py`：`source` 里补 `rationale`、`verdict`，让能力受限检测生效。
2. `workflow/review.py`：`_evaluate_uncached` 增加 `manual_only`——
   「除人工待审差异（REVIEW_ROUND_FINDING/MANUAL_REVIEW_OPEN/REVIEW_FINDING_OPEN）外无其它
   硬错，且轮次已完成」时为 True。
3. `workflow/runner.py` `do_review_gate`：`passed` 照旧（质量不通过）；但 `manual_only` 时把
   review_gate 标为 `needs_review + execution_completed + continue_unqualified +
   release_ready=False`，`record_delivery` 记为 `delivered_unqualified`，返回 0 让流程继续到
   `format_gate`。review-gate.json 附带 `batch_sha256` 供下游对齐批次。
4. `workflow/handoff.py` `inspect_stage`：`review_gate` 报告 `manual_only=True` 且轮次完成、
   batch_sha 与当前 batch_meta 一致时，返回 `passed=True, unqualified=True, release_ready=False`；
   过期/真错仍拒绝。
5. `workflow/execution_state.py` `executed(allow_unqualified=True)`：额外接受
   `needs_review + execution_completed + continue_unqualified + release_ready=False`。
6. `workflow/manifest.py` `dependency_satisfied`：透传 allow_unqualified；对上述未放行的
   review_gate 在 allow_unqualified（零人工自动）下视为满足，其余阶段仍须各自 inspect_stage 通过。

## 没改什么 / 边界
- **质量仍不放行**：review_gate `passed` 仍为 False，`release_ready=False`，`quality_status=pending`，
  交付为 `delivered_unqualified`。上传/发布仍须人工在差异处理后显式确认；`upload`/`upload_gate`/
  `publish_gate` 保持 pending 或 human/skipped，绝不自动上传。
- `inspect_stage` 对 `validate`、`format_gate` 严格性不变；`format_gate` 只做本地格式打包，不调模型、不联网。
- 真实差异（如 q-5 语音 vs 词汇、q-11 日常活动 vs 影视戏剧）仍进人工队列，不自动消解。

## 如何验证
- 隔离临时批次（拷贝真实 19 卷到 /private/var 临时目录）跑 `do_review_gate` + `format_gate`：
  review_gate exit 0、needs_review/未放行；dependency allow_unqualified=True、strict=False；
  format_gate passed=True、产出 final_sha256。
- 真实 19 卷（插件后端自动续跑）终态：review_gate=needs_review(rr=False)、format_gate=completed、
  upload_gate/upload/publish_gate=pending；`07_review/upload-format.json` passed=True，
  final_sha256 与 review-gate 的 batch_sha256 一致；人工队列 12 项（均 knowledge 差异）。
- 回归：`test_review_consensus`(17) + `test_execution_state` + `test_manifest_atomic` +
  `test_review_rounds` 共 **94 通过 OK**。`test_delivery` 8 项失败为**改动前既有**的夹具漂移
  （原始 A 组同 8 项失败，改动未新增任何失败）。

## 回退方式
撤销上述 6 处改动即恢复「review_gate 有 open 项即卡住 format_gate」的旧行为；
`review_consensus` 的 rationale 补充可单独保留（纯修复能力受限误判，无副作用）。

## 追加（前端流程/排队区/动效，用户 2026-11-07 当面要求）
1. **流程图末节点改名**：`lib/client.js` `compactFlowStages` 最后一组 `上传与收尾` → **`进入人工审核`**
   （仍含 upload_gate/upload/publish_gate 三阶段，语义改为「打包完成→进人工审核」，自动流程终点）。
   流程图提示文案与说明同步改为「… → 上传格式检查 → 进入人工审核」「通过即打包成可上传批次并进入人工审核区，
   差异由人工复核后手动上传」。
2. **跑完自动离开排队区**：`workflow/execution_state.py` `local_complete` 改为按「本地各阶段已执行
   （允许 review_gate 的 needs_review+continue_unqualified 未放行交付）」判定完成；不再要求 review_gate
   `passed`。于是 `flow_done=True`，批次从「上传试卷」排队区移除、进入「试卷审核/待人工确认」。
   实测真实 19 卷：`final_format_complete=True`，upload 仍 pending（不自动上传）。
3. **运行中节点动效**：`lib/client.js` 当前/运行节点加 `.wf-node-pulse`（外圈脉冲放大淡出）+
   `.wf-node-dot`（中心点呼吸明暗）CSS 动画；`prefers-reduced-motion` 下关闭。

### 前端验证
- `node --check lib/client.js` 通过。
- 后端 Node 全量 `node --test backend/*.test.cjs`：**110 通过 0 失败**（含更新后的
  `upload-stage-flow.test.cjs` 断言：末节点文案「进入人工审核」、说明「通过即打包成可上传批次并进入人工审核区」）。
- Python：`test_execution_state` + `test_workbench_catalog_archive` + `test_workbench_review_status` **29 通过 OK**。

## 追加（上传服务修复：SSH 隧道/媒体部署/附件去重，用户 2026-11-07 报"点上传不成功"）
### 诊断链路（真实数据）
1. 首次报错 `07_upload/preflight.json = missing_api_key`：实为**后端被文件监视器反复重启**
   （`lib/index.js` watch `app/**/*.py`，一改即 800ms 后 `restartBackendSoon`），点击撞上重启窗口，
   子进程环境未就绪。SSH 隧道本身正常（`ssh_tunnel.ensure_upload_connection` 已起 8089，
   `/api/health` 200、`/api/me` 认证通过，密钥 `MUDU_API_KEY` 有效 len 45）。
2. 修好认证后暴露真问题：`06_batch/batch_meta.json` 附件 URL **混合且重复**（q-6 有 13 条：
   1 组本地 `01_source/media/image4.jpeg` + 3 组 `/source-media/<中文批次名>/…` 重复），
   导致 `media_inventory.referenced_media` 抛 `Attachment URL is not owned by this batch`，
   上传准备无法通过。
3. 远端实际部署为**内容寻址 flat-hash**（`/source-media/<namespace-sha>-<content-sha>.<ext>`，
   36 个文件均已在 `/home/ubuntu/mudu-quiz/static/source-media/` 存在），
   `07_upload/media-map.json` 即 name→flat-hash 权威映射（36 条）。
### 修复
- `06_batch/batch_meta.json`：按 `07_upload/media-map.json` 把每个附件 URL 规范为已部署的
  **flat-hash `/source-media/...`**，并按 (kind,name,placement,optionKey) **去重**（q-6 13→4）。
  备份：`/tmp/batch_meta.pre-flatmap.json`。
- `app/workbench/server.py` `_stage_upload_media`：新增**短路径**——当 `07_upload/media-map.json`
  存在且覆盖批次全部附件名时，直接复用该映射返回，**不再重复 SSH 部署**，也不再对已部署
  flat-hash 附件调用 `referenced_media`（该函数要求 `/source-media/<批次名>/` 前缀，与 flat-hash
  部署不兼容，是误报根因之一）。
### 验证（真实 19 卷，非临时副本）
- `format_gate.run` → passed=True。
- `upload.prepare(media_map=…)` → **passed=True，payload_sha256=1e6f7bbcb5e6264e，151600 bytes**，0 错误。
- `_stage_upload_media` 短路径返回 36 条映射、`prepare` 通过。
- 回归：后端 `node --test backend/*.test.cjs` **110 通过**；Python
  `test_media_inventory/test_media_transfer/test_upload/test_upload_content/test_upload_payload/test_format_gate`
  共 **172 通过 OK**。
### 边界
- 未执行最终 `upload` 提交（远端题库为外部不可逆操作，须用户点按钮/授权）；修复覆盖到"准备上传（冻结请求）"全部通过。
- `missing_api_key` 的根因（后端重启）已通过停止并行改动消除；若再次并发改 `app/**/*.py` 仍会触发后端重启，
  上传前应确保后端稳定。重复附件的写入方未 100% 定位到单一代码路径（已用去重+幂等部署兜住）。

## 追加（哈希过期收尾 + SSH 隧道健康检查修复，用户 2026-11-07 报"消费内容已变化 batch_meta"）
### 根因
此前手工改 `06_batch/batch_meta.json` 附件导致 underlines/validate/review 记录的哈希过期；且 `wire_format.normalize_item`
每次从现有 attachments append 不去重，反复 assemble 把 q-6~q-10 的图片附件堆成 4 倍（13→实际应 4 条）。
`batch-fill-state.json` 的 `finalized_bodies` 保留了**正确 3 条图片附件**，是权威真相。
### 修复
- `workflow/wire_format.py` `normalize_item`：有 media_map 时先清掉旧 image/audio 附件、由 [IMG]/audio_map 重建，最后按
  (kind,name,placement,optionKey) **去重**，幂等，不再累加。
- 以 `finalized_bodies` 为准修正 `batch.json` 附件 → `assemble_local.available=True` 触发**确定性重建**（`model_calls: 0`），
  再依序重跑 `meta → underlines → validate → review → review_gate → format_gate`，manifest 哈希全部重新对齐。
- `workbench/ssh_tunnel.py`：新增 `_endpoint_reachable()` 端到端 HTTP 探测，替换"只看端口 listening"的盲点——僵尸/悬挂
  socket 或 defunct ssh 不再被误判"已就绪"，会清理并重建隧道。
### 验证（真实 19 卷）
- 阶段终态：assemble/meta/underlines/validate/review/format_gate=`completed`，review_gate=`needs_review(rr=False)`，
  upload_gate/upload/publish_gate=`pending`；batch status=reviewing。
- `upload-format.json passed=True`；`batch.json` sha 与 `final_sha256` 前缀一致（aac25047）。
- `_stage_upload_media`+`prepare` → **passed=True**（152275 bytes）；`_check_upload_preconditions`（allow_unreviewed 确认）→ **PASS**。
- 回归：`test_upload/upload_content/upload_payload/wire_format/media_inventory/media_transfer/format_gate` 共 **179 通过 OK**；
  后端 node 测试 110 通过。
### 边界
- 未执行最终远端提交（用户确认"含未审核差异上传"后由本人点击）；review 因输入哈希变化被重新执行（确定性内容不变，仍 12 项
  知识点差异进人工）。SSH 隧道当前由手工进程维持，插件修复后下次点上传会自动按端到端探测重建。

## 追加（修复上传 `too many values to unpack (expected 2)`）
### 根因
上一步给 `_stage_upload_media` 加的「媒体已部署则复用 media-map」短路径，`return {n: deployed[n] ...}`
只返回单个 dict；而正常路径返回 `(mapping, source_hash)` 二元组，调用方 `_run_batch_upload` 用
`mapping, source_hash = staged` 解包 → 解包 dict 的键导致 `too many values to unpack (expected 2)`。
### 修复
`app/workbench/server.py` `_stage_upload_media` 短路径改为 `return mapping, hashlib.sha256(raw).hexdigest()`，
与正常路径同构。
### 验证
- `_stage_upload_media` 返回 tuple(len=2)，`mapping, source_hash = staged` 解包成功，36 条映射，source_hash 与 batch_meta 一致。
- 模拟上传前三步：隧道就绪 → staged 36 条 → `prepare passed=True` 且 source_sha256 匹配。
- 后端已重启加载新代码（start == server.py mtime）。
- 回归：后端 node 110 通过；Python upload/media/format 69 通过。
