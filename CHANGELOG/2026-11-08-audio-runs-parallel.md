# 2026-11-08 听力并行：只有内容审核必须等听力

## 背景 / 触发

用户反馈：听力（切段/转写）耗时最长，之前 `assemble` 依赖 `audio`，导致听力没跑完
解析、知识点、组装全被挡住。用户要求：答案/解析、题型/知识点、来源/难度等可以
**与听力并行**推进；只有**内容审核**这一步必须等听力跑完才进入。

## 改了什么（app/workflow/contracts.py）

- `assemble.depends_on`：`("answers","audio","types","knowledge","explanations")`
  → **移除 `audio`**，改为 `("answers","types","knowledge","explanations")`。
  组装本身不读音频内容（音频只由 audio 阶段写入 attachments，缺映射时按
  “待部署媒体”记录在 notes，不会因缺音频失败）。
- `review.depends_on`：`("validate",)` → **`("validate","audio")`**。
  审核要读 `02_audio/full-transcript.json` 核对听力语义，因此唯一必须等听力的
  下游节点是内容审核。

结果：`answers → types → explanations/knowledge → assemble → meta → underlines →
validate` 可与 `audio` 并行跑完；`review` 在这些完成**且** audio 完成后才开始。

## 没改什么 / 边界

- 不改审核口径、不改上传边界、不改任何题目数据。
- 听力仍然必须执行（有音频时不会被跳过）；只是不阻塞其它阶段。
- 显式 `02_audio/SKIP_LISTENING`（无音频/用户跳过）时 audio 为 skipped，视同已完成，
  review 照常进行。

## 如何验证

- 依赖图：`upstream_of('assemble')` 不含 audio；`upstream_of('review')` 含 audio；
  内存探针“audio pending 时 assemble 依赖满足、review 依赖不满足”。
- 回归：`test_runner_auto.test_audio_needs_review_does_not_block_assemble_but_blocks_review`
  断言 audio 待复核时 `assemble` 照常执行、`review` 等待且 `unmet_dependencies` 含 audio。
- 相关套件：`test_runner_auto / test_auto_audio / test_audio_skip / test_assemble_local /
  test_review_rounds / test_agent_protocol / test_agent_entry / test_progressive_pipeline /
  test_delivery / test_agent_tasks` 全绿（252 项，单独复核 review_rounds 64 项）。
  注：并行跑整套时 review_rounds 3 个用例因共享临时目录竞态偶发报错，单独/与其相邻
  套件一起跑均通过，与本次依赖改动无关。

## 回退

还原 `contracts.py` 的 `assemble`/`review` 的 `depends_on` 两行即可；不要回退
`AGENTS.md` 规则 15。
