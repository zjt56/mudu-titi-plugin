# 原卷修改：解析与文章大意保存后真正进入正式批次

## 背景

用户此前要求「原卷修改里面也要加上文章大意的修改入口」，并在追问时明确选择
「保存即应用到正式批次（推荐）」——不是只存候选草稿。首版实现只把内容写进
`candidates/<unit_id>.json`，正式 `06_batch/batch_meta.json` 不变，上传读到的仍是旧值；
这既不满足选择，也会让审核页显示与上传内容不一致。

## 改了什么

- `app/workflow/manual_content.py`（新增）：`workflow/manual-content-edits.json` 人工编辑台账。
  `source_binding()` 记录当前 `01_source`（正文/附录/答案/确认结构/材料角色/附件）与
  `material-roles.json` 的 SHA-256；`prepare()` 生成待写入数据（只保留 `articleSummary`/`explanation`，
  绑定当前来源并带 `updated_by/updated_at`）；`load()` 严格校验结构，损坏即报错而不是静默清空。
- `app/workbench/server.py::apply_manual_content`：把人工改的解析/大意**同时**写进
  `04_knowledge/explanations.json`（组装交付要求与产物逐字一致）与 `06_batch/batch_meta.json`，
  并登记台账、重登记 `explanations` 输出摘要，再 `invalidate_downstream(assemble)` 让体检/审核/上传失效。
  只接受 `explanation`/`articleSummary`；材料/题干/选项/答案属于原文/答案投影，仍只存草稿并明确回报。
- `save_units` 改为**单次事务**：先取批次执行锁（auto 锁 + `.run.lock` + agent 锁，任一被占用即拒绝，
  不打断运行中的阶段），把所有待写文件（草稿、解析产物、正式批次、台账、manifest）交给
  `agent_tasks._install` 一次原子替换，失败整体回滚，不再出现"正式批次半写仍返回成功"。
  任何写入失败都直接报错，不再降级成"仅保存为草稿"。
- 并发与陈旧编辑保护：客户端只提交**改动过的字段**并附带 `expected`（打开时显示的正式值），
  服务端比对当前正式值，不一致即拒绝（"正式内容已变化，请重新打开本题"），避免旧弹窗覆盖新值；
  已批准上传/已上传的批次拒绝覆盖绑定内容；`explanations` 阶段未完成/过期时拒绝同步正式批次。
- `app/workflow/upload_content.py::manual_value`：严格校验对**人工台账中逐字相同、且来源绑定仍与当前
  `01_source` 一致**的解析/大意按用户确认接受，记 `MANUAL_EDITED_CONTENT` 提示；模型二次生成、
  与台账不一致、来源变化后的旧值、或本身带 `source_ref` 证据错误的文本仍按原口径拒绝。
  原始（未剥离听力稿前缀）值命中的手工解析也可接受；`restore_field` 不再把人工值恢复成原文。
- `app/workbench/server.py::candidate`：草稿现在只保留材料等草稿字段，不再冗余保存 `explanation/articleSummary`
  （正式值已直接写入正式批次），审核页与上传读到的都是同一份正式内容，不会再出现"草稿旧值遮盖正式值"。
- `lib/client.js`：原卷修改弹窗改为字段级差异提交（`buildContentPatch` + `contentOriginal` 基线，
  只发改动项与 `expected`），明确区分"已写正式 / 仅存草稿 / 正式未变化"；保存失败在弹窗内
  `saveError` 提示并保留输入，不再替换整页；保存期间锁住输入与退出，取消时放弃修改。

## 边界（重要）

- 只有解析与文章大意进入正式批次。材料/题干/选项/答案仍是草稿，必须走各自阶段或原卷修正流程，
  否则会破坏画线/元数据/答案投影；弹窗顶部已写明。
- 人工修改**不被伪装成原卷原件**：登记在 `manual-content-edits.json`，严格校验以
  `MANUAL_EDITED_CONTENT` 提示接受；听力录音材料完整性、AI 标记、原卷有解析却留空的遗漏检查不放宽。
- 保存后仍须重新体检/审核/上传（下游被失效）；不自动上传、不自动确认。

## 验证

- `app/workbench/test_unit_content_edit.py` **9 项通过**：解析/大意写入正式批次与解析产物；材料仍为草稿并回报；
  台账可重放且绑定当前来源；大意可清空；下游 assemble/validate/review/upload 失效而 explanations 保持完成；
  只改标签不碰正式内容；审核页读到新大意且不再留有冗余草稿副本；陈旧编辑（`expected` 不符）被拒；
  `_install` 写到一半失败时正式批次与解析产物完全回滚、台账与草稿均未落盘。
- `app/workflow/test_upload_content.py` **53 项通过**（含新增）：人工值可溯源接受并记提示；与台账不一致的改写仍拒绝。
- `app/workflow/test_assemble_local.py`、`test_stage_semantics.py`、`test_agent_tasks.py` 合计 176 项通过。
- 真实批次副本（`workflows/【木牍教育】20年安徽中考英语` 的临时拷贝，未改用户数据）验证：对一个完形组改文章大意、
  一个阅读组改解析后，`06_batch/batch_meta.json` 与 `04_knowledge/explanations.json` 同步为人工值，
  重跑 `assemble`（确定性、零模型）后仍是人工值并通过交付校验，`validate` 14 项全过；
  `upload_content.inspect_content(strict=True)` 0 error，仅 2 条 `MANUAL_EDITED_CONTENT` 提示。
  另注：若连 `upload_gate=approved` 的已绑定批次保存，会按设计拒绝覆盖（"已批准上传或已上传"）。
- `node --check lib/client.js` 通过；全量 `backend/*.test.cjs` + `*.test.mjs` **439 项，437 通过、0 失败、2 跳过**
  （含更新后的 `review-content-edit.test.cjs` 3 项、`review-content-preview.test.cjs`）。
- workbench 全量 101 项：新增 9 项全过；其余 4 失败 3 错误与干净 `HEAD db588c9` 基线一致（`test_findings_scoped_binding`、
  `test_manual_start_advance…`、`test_review_snapshot_open_count`、`test_work_hint_is_read_only…`、
  `test_source_slice_blocks_detail_and_http_keep_both_image_groups`、`test_snapshot_round_trips_for_the_same_fingerprint`），
  不是本次引入。

## 回退

还原上述文件的本次改动、删除 `app/workflow/manual_content.py`、`app/workbench/test_unit_content_edit.py` 与
`workflow/manual-content-edits.json`（若已生成）即可；正式批次另有 `.bak` 可回退到上一次内容版本。
