# 2026-11-09 审核响应 JSON 结构错误给出可修复提示

## 背景 / 触发

批次 `【木牍教育】17年安徽中考英语` 的两轮审核停在
`Invalid final JSON response: Expecting ',' delimiter: line 1 column 1753 (char 1752)`。

只读排查多个失败尝试的 `final.txt` 发现：模型返回的 JSON **在闭合处少一层对象 `}`**——写成
`..."subquestions":[]}}]},"notes":[],"findings":[]}`，正确完整结构应为 `...}}]}},"notes"`（`checks` 数组 `]` 后应是
`review_round` 的 `}`，这里并入了 `review_round` 的 `}` 却漏了 `artifacts` 的 `}`）。内容完整，只是括号少/错位，
解析器按协议判为失败。多次重试模型仍以同样方式写错，属确定性输出格式问题。

## 改了什么

- `app/workflow/review_rounds.py`：审核重试在 `error_code == 'response'`（JSON 协议错误）时，给下一次重试追加更具体的结构指引，而不是只回显解析器的“Expecting ','”：要求只输出一个完整 JSON 对象、四个根键齐全、每个 `{`/`[` 都有配对的 `}`/`]`，并点明“根级 notes/findings 之前漏写 artifacts 的 `}`”这一真实形态。并行分片与单轮两条重试路径都覆盖。
- `app/workflow/test_review_rounds.py`：新增 `test_response_json_error_feedback_guides_retry`，验证首次 JSON 协议错误后，重试 prompt 含结构修复指引并能通过。

## 没改什么 / 边界

- **不改解析器、不放宽审核 JSON 严格性**：既有契约明确“审核响应必须是精确合法 JSON，不自动补齐/提升”（`test_review_json_is_not_repaired_or_hoisted`、`test_auth_quota_format_and_bare_substrings_do_not_retry` 仍通过）。因此不做“自动修括号”，避免把截断或错误响应改写为看似合法内容。
- 不改审核口径、质检结论、上传边界；不伪造审核数据。重试预算与“同错升级”规则不变，本改动只提高重试时的信息量，不改变失败仍可上报的语义。

## 如何验证

- 真实四处错误响应（`round-58f…`/`round-7d5…` 各尝试）确认形态为“少一层对象 `}`”；说明重试反馈中的指引对得上真实错误。
- `workflow.test_review_rounds`、`test_review_rounds_recovery`、`test_review_consensus` 共 132 项通过（含新增用例）。
- 全量 `workflow`：1948 项，仅 2 项既有 `test_local_agent_ownership` 因沙箱禁止 `ps` 报环境错误，无断言失败。

## 回退方式

`git revert` 本提交即可；仅回退重试反馈文本，无数据结构迁移。
