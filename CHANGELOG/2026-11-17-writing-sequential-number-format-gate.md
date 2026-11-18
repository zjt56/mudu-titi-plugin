# 书面表达顺承题号不应阻断最终格式闸门

## 背景
批次 `【木牍教育】16年安徽中考英语` 的最终格式检查把 `Ⅺ.书面表达` 的 `QUESTION_NUMBER_INFERRED` 候选 `[96]` 当成阻断错误。原卷前一题为 95，书面表达没有阿拉伯印刷题号；当前确认结构已绑定为唯一 `single_writing_section`。

## 改动
- `app/workflow/final_audit.py` 新增只读证据判定：唯一的 `single_writing_section` 书面表达单元，且本地顺承编号等于前序题号最大值 + 1 时，保留 `QUESTION_NUMBER_INFERRED` 证据为 warning，不升格为 format_gate error。
- 不修改原文、`printed_numbers`、来源绑定或上传编号投影；仅避免把合法的本地顺承编号误判为印刷号冲突。
- `app/workflow/test_final_structure_evidence.py` 增加回归测试。

## 验证
- `workflow.test_final_structure_evidence workflow.test_format_gate workflow.test_upload_payload workflow.test_upload_numbering`：118 项通过。
- 目标批次 `【木牍教育】16年安徽中考英语` 的 `format_gate` 重跑通过：`07_review/upload-format.json` `passed=true`、`errors=[]`；原诊断保留为 warning，说明“已按上一题号顺承为本地编号，非印刷题号”。
- 未执行上传或发布。
