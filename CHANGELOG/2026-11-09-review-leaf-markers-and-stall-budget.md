# 2026-11-09 审核：目录叶子标注 + 同错重试预算，止住 20 分钟空转

## 背景 / 触发

用户反馈 `【木牍教育】17年安徽中考英语` 的内容审核跑了约 20 分钟仍无结果。只读排查确认：

- **不是全量重跑**：一审按 48 单元分片（`review_part_parallel=16`），失败后走 `_repair_round` 只重审失败靶点（实测 repair 只提交 `q-96` 一题），二审 `sample` 已真实通过 10/10 并保留。
- **真正原因**：同一题 `q-96`（书面表达）的知识点路径反复写错——模型把 `电子邮件` 当成 `应用文`/`新媒体语篇` 的子级，写成 `语篇体裁/应用文/电子邮件`（目录中不存在）；而正确叶子是 `语篇体裁/新媒体语篇/电子邮件`。同一确定性错误每次 repair 都复现，加上每轮回退会重新导出新 task id，导致约 20 分钟、70 次模型调用空转。
- 目录叶子边界对模型不透明：`应用文`、`新媒体语篇` 是分支，`电子邮件` 是 `新媒体语篇` 下的叶子，但供给目录只给 `id/name/path`，模型无从判断“到此为止”。

## 改了什么

- `app/workbench/taxonomy.py`：新增 `knowledge_catalog_for_prompt()`；为目录每个节点补 `leaf` 与 `leafTerminal`（“叶子：到此为止，不得再拼接子级”/“非叶子：需继续选到叶子”）。只加展示标注，不改路径或内容。
- `app/workflow/review.py::compact_knowledge_catalog`：返回的每个点同样补 `leaf/leafTerminal`（含类型裁剪与回退全量两条路径），保证盲审 prompt 里的目录自带宽叶子边界。
- `app/workflow/agent_tasks.py`：把 `references/knowledge-catalog.json` 供给 review/assemble 等阶段时，逐点写入 `leaf/leafTerminal`。
- `app/workflow/review_rounds.py`：
  - 审核提示词新增“【叶子即终点·必须遵守】”，明确 `leaf=true` 只能原样照抄、不得再拼子级；并点明 `电子邮件` 属于 `语篇体裁/新媒体语篇/电子邮件`、不是 `语篇体裁/应用文/电子邮件`。
  - 新增**跨调用同错预算**：`workflow/review-stall.json` 按 `(round_id, 错误文本)` 累计失败次数；每轮成功即清除；达到 `review_stall_strikes`（默认 `max(review_max_attempts*2, 3)`）就停止原样重试，返回带具体错误的 `blocked`，不再无限换 task 重跑。
- 回归 `app/workflow/test_review_rounds.py`：新增“同错预算到上限转 blocked 且不空转”“审查目录含 leafTerminal 标注”用例；更新两处目录相等断言为“忽略 leaf 标注后内容一致”。

## 没改什么 / 边界

- 不改审核口径与判定：真实标签差异仍进人工队列，`review_gate` 不因格式缺项放行；`leaf` 标注只影响展示，不改变目录合法路径集合。
- 不改“按题局部补审”设计：仍只重审失败靶点、保留已通过的 checks 与二审结果。
- 同错预算只对**确定性重复错误**生效（同 round + 同错误文本）；限流/额度、输入变化（context_changed）走各自既有分支；正常一次通过不会触发。

## 如何验证

- `workflow.test_review_rounds`（含新增 2 用例并更新 2 处目录断言）、`test_review_rounds_recovery`、`test_review_consensus`、`test_review_evidence_scope`、`test_review_local_reuse` 共 151 项通过。
- `workbench.test_list_projection`、`workflow.test_upload_content`、`workflow.test_agent_tasks` 共 103 项通过。
- 全量 `workflow`：1951 项，仅 2 项既有 `test_local_agent_ownership` 因沙箱禁止 `ps` 报环境错误，无断言失败。
- 真实目录核对：`语篇体裁/新媒体语篇/电子邮件`（leaf=True）为合法叶子；`语篇体裁/应用文/电子邮件`、`主题/人与社会/社会交流与文化/文化交流` 均不在目录，印证原失败是模型拼错分支。

## 回退方式

`git revert` 本提交即可；`review-stall.json` 为新增运行时账本，删除无副作用。目录内容与合法路径集合不变。
