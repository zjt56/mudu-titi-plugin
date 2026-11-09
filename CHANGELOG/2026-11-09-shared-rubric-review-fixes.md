# 共享题干审核问题修复

## 背景

对 57d0d74 审核发现：原始/去噪坐标混用可选中未来导语；回溯会传播上一题组专属指令；多行只取末行；第一部分/加粗前缀匹配失败；审计和预览尚未接入。

## 改动

- 新增 shared_rubric.py：仅继承同节标题之后、首篇材料之前的完整公共阅读/完形作答说明，排除限定题号的组内指令和听力类型。支持部分/节、粗体标记。
- question_draft 按 _bound_source 已证明的 line_basis/start_line 选择原始或去噪文本，保留补入导语证据；本组原有导语优先。
- question_content 原文审计要求共享导语存在，缺失报 SOURCE_SHARED_RUBRIC_MISSING；合法共享的多行说明不再视为外来材料。
- preview.source_slice 接入同一归属规则，专属导语优先，不覆盖任务型阅读自己的要求。
- 回归包含原始坐标偏移、限定题号、完整多行、前缀，以及生成→原文审计→source_slice→source_blocks→payload 链路和删除检出。

## 验证

- 276 项通过：test_question_draft、test_wire_format、test_format_gate、test_upload_payload、test_review_rounds、test_writing_preview、test_batch_fill、test_assemble_local、test_stage_semantics。
- 2017、2024 两卷仅在内存中使用新草稿题干做原文审计，均 issues=[]；预览包含公共导语；删除后可检出。
- 真实批次文件未修改，未上传、未调用模型。保留工作区原有上传前缀/附件修改。

## 边界与回退

不迁移旧批次，不变更阶段依赖、审核结论或上传授权。回退此提交即可撤销新增共享导语规则及消费者集成。
