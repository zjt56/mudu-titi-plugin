# 批次 35 选项来源窗口与审核协议修复

日期：2026-11-17
批次：`35七上人教版课时A计划Unit4MyFavouriteSubject第二课时S`

## 背景/触发

用户报告该批次在填充汇总与固化阶段失败。组题 `sec-df4064aca73afa50-u76` 的 1、2 题出现 8 条 `OPTION_CONTENT_MISMATCH`：题干解析把后续第 3 题的图片选项误绑定到前两题，导致原卷中的文字选项只剩 `A.`、`B.`、`C.`、`D.` 标签。
随后审核二审的独立题抽样又发现模型回显了空知识子题列表，违反独立题协议的 `subquestions=[]` 形状。

## 改了什么

- `app/workflow/question_content.py` 增加 `_question_window`，统一确定当前题目的来源窗口。
- `app/workflow/question_draft.py` 将图片选项探测限制在当前题窗口内，避免后续题目的图片选项污染前题；`app/workflow/test_question_content.py` 覆盖同一行/跨行文字选项与图片选项。
- `app/workflow/batch_fill.py` 在同一来源片段的解析刷新中保留已填答案、标签和用户已接受的规范化正文，并同步新的来源身份；刷新判定以托管旧 source base 为基准，避免规范化对象被误判为新源变更。
- `app/workflow/review.py` 对独立题抽样返回的全空知识子题做确定性归一化；含真实知识点的非空子题仍严格拒绝。
- `app/workflow/test_review_evidence_scope.py` 增加上述两种审核协议回归测试。

## 没改什么 / 边界

- 不改原卷文字、不把图片选项伪造成文字；第 3 题的图片继续作为选项附件保存。
- 不自动改动 17 项真实知识标签差异；`review_gate=needs_review` 保留给人工审核。
- 不自动上传或发布批次。

## 验证

- `workflow.test_question_content workflow.test_question_draft workflow.test_batch_fill workflow.test_review_evidence_scope`：107 项通过，4 项跳过，0 失败。
- 批次真实复验：assemble、meta、underlines、validate、review、format_gate 均完成；`07_review/validation-report.json` 通过；review 执行完成但 review_gate 保留 17 项真实知识标签差异；未上传。

## 回退

回退本次代码提交即可；批次修复前备份位于 `%TEMP%\\backup-batch35-20261117`。
