# 2026-11-12 续行右栏图片小题的识别投影

## 背景

用户报告06七上人教版课时A计划StarterUnit2KeepTidy!第二课时Pro的结构闸门仍被NUMBERING_RESTART/GROUP_BOUNDARY_REVIEW阻断。实际报告还包含第2、4题QUESTION_NOT_FOUND。两题位于第Ⅱ题的前题续行右栏：`c<u>空格</u>/eɪ/ke\t2.[IMG ...]`。印刷锚点已经找到右栏题号，但内容消费的project_numbered仅在同一行有至少两个token时拆分，因此一个右栏token加无号续行的形态没有投影，校验误报缺题并使重编号无法消解。

## 修改

- source_anchors.project_numbered：没有行首题号时，保留右栏题号之前的原续行并单独投影真实列题号。没有列锚点或单个行首题号时原样保留。普通单ASCII空格Class 2.不作为列边界。只改变解析视图，不改正文、来源顺序、题号或媒体。
- test_source_continuation_columns：真实版式字符串覆盖投影、逐子题图片/音标归属，以及infer_structure→结构闸门→draft→source_slice/source_blocks→payload消费链路；证明2/4可见且前题/后题文字不串。
- 正式runner复验指定06批次，未编辑manifest或用mark伪造通过。四条NUMBERING_RESTART和一条GROUP_BOUNDARY_REVIEW经全部题号/材料独立覆盖校验被解决，结构闸门passed，5个组题。草稿无issue，23答案全部来源校验通过，然后继续正常工作流。

## 边界

未删除重编号/题组完整性检查，未只按消息文字压成warning，不覆盖模型来源失败，未更改图片识别/答案内容。只处理用户指定批次，未自动上传发布。

## 验证

- Python3.12、独立TMPDIR：新增3测试及6个受影响模块合计84 tests，OK，4 skipped（21.895s）。
- 指定真实批次正式runner `--stage structure_gate --force`：通过，5组；23答案逐题validate_scoped_answer通过。
- 后续续跑结果与最终回归追加在交付记录。

## 回退

回退source_anchors.py的本轮解析投影hunk和新增回归。原始Word/正文未改，不回滚真实已通过的来源校验状态。
