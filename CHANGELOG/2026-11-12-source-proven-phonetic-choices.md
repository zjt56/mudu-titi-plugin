# 2026-11-12 原件仅选项语音组题的消费闭环

## 触发

真实隔离「八上01」辨音组题有5个子题，每题只有A–D选项，无独立子题干；draft已保留完整大题标题stem、原始选项与身份，material为空。既有 `phonetic_structure.shape` 仅认可非空子stem，导致payload误报GROUP_INCOMPLETE/MISSING_STEM。

## 改动

仅修改 `app/workflow/phonetic_structure.py` 的shape边界，并补 `test_phonetic_structure.py`：

- 先保留既有material/materialBlocks、完整身份、来源span、标题stem、子题number/question_id/stem/options的逐项严格相等检查。
- 当前verified原件类型必须是语音题，原material为空，每个原子stem均为空，且每子选项必须按顺序完整A–D、每项文字非空，才能认定choices_only。
- 此时返回(material_optional=True, alias_children=True)，供现有consumer允许原件本来不存在的材料和子stem为空；不生成任何题干。
- 缺选项、改选项、改身份、生成子stem、原件本身缺项、没有当前来源证明均拒绝。

## 边界

未修改upload_payload，未扩大题型规则，未生成题干/答案，未补伪来源或标签。证明仍来自当前原件重建，普通非语音题、来源不一致或内容不完整不能复用此例外。未提交Git。

## 验证

- `WF_STRUCTURE_AI=0 WF_STRUCTURE_REVIEW=0 PYTHONPATH=app python3 -m unittest workflow.test_phonetic_structure workflow.test_native_unnumbered_structure workflow.test_upload_payload`：97项全部通过。
- 新回归完整走原文→infer_structure→scoped units→question_draft→verified_groups→upload_payload，断言没有GROUP_INCOMPLETE/MISSING_STEM，wire子stem继续为空；同时缺项/改文/ID/生成stem负例均拒绝。
- 主Agent全量反馈测试复用输入对象受到consumer原地规范化影响；测试保存不可变baseline，每次正负payload调用仅传fresh deepcopy，并断言原source row与各负例均保持不变。只修测试隔离，不改consumer。实际 `/Users/zjt/.local/bin/python3.12 -m unittest` 同上3模块97项全部通过（1.123秒）。
- 真实隔离八上01只读重建：`sec-d2476f8caa40c5a3-u48`，shape=(True,True)，payload目标错误GROUP_INCOMPLETE/MISSING_STEM均为空；未改批次数据。

## 回退

还原shape中choices_only判据与新增回归即可；其他消费者和用户数据无迁移。
