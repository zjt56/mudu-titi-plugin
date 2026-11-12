# 答案阶段整体匹配与坐标验收

## 背景（触发）

当前小课时批次只有 24 个题目，但答案阶段首轮按分片调用模型，验证失败后修复轮又把失败单元拆成一题一调用。由于答案值已经匹配正确、仅 evidence 使用了人类描述而不是机器可解析坐标，同一确定性校验错误被重复模型调用放大，造成长时间运行。

## 改了什么

- `app/workflow/label_execution.py`：answers 阶段在首轮和修复轮都尽量保留全部待处理单元的共同上下文；仅在输入预算不足时由 `build_parts` 按大小切分。explanations 等阶段继续使用原有分片上限。
- `app/workflow/unit_tasks.py`：答案分片提示明确要求先整体阅读正文与答案材料，并要求每条 `evidence` 含相对文件路径/行号或唯一别名/行号；自然语言只能作为补充。
- `app/prompts/roles/answers.md`：补充同样的机器坐标契约和重复题号按大题/课时整体匹配要求。
- `app/workflow/test_answer_scope_blank_evidence.py`：确认不合规坐标仍由正式导入路径按同值、同范围原文确定性补齐；确认答案阶段模拟调用只产生一个覆盖全部单元的整体请求；同时固定测试读取 JSON 使用 UTF-8。

## 边界

本次没有放宽来源范围、答案值校验、题号身份或板块证据要求；错误答案、跨大题借用和无法唯一绑定的来源仍失败。也没有改写当前批次的 manifest、正式答案产物或上传边界。

## 验证

`PYTHONPATH=app python -m unittest workflow.test_label_execution workflow.test_answer_scope_blank_evidence workflow.test_scoped_lesson_answers workflow.test_answer_lesson_bands`：58 项通过。

`git diff --check`：通过（仅报告既有工作区文件的换行提示）。

## 回退

回退 `label_execution.py` 的 answers 分片上限改动、两处答案提示词改动及对应测试/本记录即可；不涉及用户批次数据。
