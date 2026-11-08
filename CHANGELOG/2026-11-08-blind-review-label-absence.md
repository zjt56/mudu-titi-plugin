# 2026-11-08 修复盲审把隐藏标签当成缺失、正常题误入待审核

> 后续纠正：本记录当时的 9 条队列不代表完整差异集合；另有图片能力提示误吞第 6–10 题真实差异。后续已补回，当前 2015 卷队列为 14 条。见 [真实差异保留修复](2026-11-08-review-capability-keeps-differences.md)。本记录保留当时的过程证据。

## 背景与证据

用户反馈 2015 年安徽卷“一审和原标签都是一致的，但还是被打上待审核标签”。只读核对 `workflows/【木牍教育】15年安徽中考英语`：旧队列共 43 条（30 条 labels 判断、13 条 knowledge 比较），一审 48 个 check 中有 30 个 error，全部声称最终批次未见 type/subtypes/knowledge；其中 20 个 assessment 与原标签完全相同，另 10 个同时存在真正标签差异。

根因是 `review_rounds._partition_contexts` 正确地隐藏现成标签，但 `_round_prompt` 又要求“审核最终批次字段是否为空”“提出题型/知识点差异前引用当前值”。模型将刻意隐藏误判为正式数据缺失；`review._round_checks` 将该 error 原样投影成发现，`manual-review-queue.json` 持久保留，工作台正常展示该错误结论。之前 ingest 只按“草稿…为空/未完成标注”清理，不能解决实际根因，也无法匹配本次“最终批次未见…”措辞。

## 改了什么

- `app/workflow/review_rounds.py`：删除矛盾的当前标签引用要求，明确两轮都使用去标签视图；模型独立填 assessment，程序比较原标签。PROMPT_VERSION 从 v9 升为 v10，旧提示缓存和逐单元绑定按既有契约失效。
- `app/workflow/review.py`：新增 `_reject_blind_label_absence`。非 ok 判断若明确把被隐藏的标签字段当缺失，抛出 `BLIND_LABEL_ABSENCE`，沿用现有目标级 partial/repair 路径实际补审；不把 error 改成 ok、不按标签相同静默删除错误。匹配仅限标签字段列表与缺失声明直接相连，排除否定、合规留空表述，避免误伤“知识点一致，但答案缺失”等实质错误。
- `app/workflow/test_review_rounds.py`、`test_review_consensus.py`：覆盖双轮提示与去标签、一致仍有答案错误、真实差异保留、缺失结论原样留证、仅补坏目标且保留有效目标、否定与真实内容缺失。
- `test_review_legacy.py` 与 `test_self_heal_actions.py`：历史 v9 迁移测试显式固定 v9 夹具，避免把新版本当成旧契约；生产 `review_legacy.SUPPORTED_CURRENT_CONTRACT` 仍只接受原 v9，不放宽为新提示背书。
- 同步 `app/docs/review.md`、根 `AGENTS.md` 硬规则 6、`CHANGELOG/RULES.md`。

## 2015 卷实际恢复

通过正式 runner invalidate/run 路径仅重做该活动批次的审核；提示契约升级后真实执行一审 48 单元、二审 10 个抽样目标，没有手改 manifest、原任务、回执或审核判定。新两轮 58 个 check 均为 ok，但程序比较仍产生 9 条真实 knowledge 差异。已有 reconcile 路径退休旧派生发现并保留历史，队列从 43 条变为 9 条，30 条标签缺失误报不再活跃。

重新执行 review_gate/format_gate：审核执行完成、9 项差异待人工、未放行；本地最终格式检查完成。实际工作台 HTTP 接口验证：48 单元、96 小题、needs_human=9、flow_done=true、uploaded=false；q-1/q-2 无 finding；group-80-81-82-83 保留原标签=一审但二审不同的真正差异。

## 验证

- 初轮四个相关审核套件共 131 项通过。
- 最终扩展回归：`workflow.test_review_consensus workflow.test_review_rounds workflow.test_review_rounds_recovery workflow.test_review_local_reuse workflow.test_review_legacy workflow.test_self_heal_actions` 共 167 项，19.726 秒，全部通过。
- 全量工作流在隔离临时源副本执行（不复制用户 workflows，独立 HOME/TMPDIR、独立锁、禁用真实模型命令），并用相同设置对照 Git HEAD：最终全量 1673 项、90.643 秒，9 failures、0 errors、17 skipped；失败名称集合与基线完全相同，无新增失败。基线也有 9 failures，不能称全量通过。遗留项包括 CHANGELOG 老记录/索引 2 项、transport 并发 1 项、repair_feedback 3 项、supervisor 2 项、教材 PDF 版面证据 1 项。初跑提示升级暴露的旧 v9 测试夹具问题已显式固定版本并在最终回归消除，生产兼容边界没有放宽。
- 工作台接口额外验证：旧误报题 q-1/q-2 findings 为空，真实差异 q-11 与二审分歧 group-80-81-82-83 findings 保留；两轮执行完整且 unchecked=0、release_ready=false。

## 边界

不改题目/原始标签/答案/解析，不重跑归档或其他年份，不自动上传。标签相同不等于内容审核通过：真实答案、原文、解析错误与 uncertain 仍须保留；原标签=一审但二审不同仍是差异。原始审核响应和人工决定不伪造、不删除。此次采用真实重审而不是靠正则把旧错误判为通过。

## 回退

回退上述提示、校验器与测试改动会重新暴露旧问题；如需回退，必须同时回退提示版本并通过正式审核续跑恢复派生文件。旧轮次与队列退役证据保留在批次 workflow/review-history、原任务/结果/事务目录及 07_review/retired-round-findings.json；不得通过编辑 manifest 或回执制造完成态。
