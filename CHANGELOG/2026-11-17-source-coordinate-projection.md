# 2026-11-17 来源坐标投影修复

- 修复 `extract.infer_structure()` 在答案附录被剥离后，结构分区末尾仍引用原始全文行号的问题。
- 结构中的章节、题组和题目定位现在投影到最终 `正文.txt` 的行号；答案附录诊断行保留为诊断坐标。
- 回归验证：`workflow.test_extract.StructureCandidateTest.test_structure_coordinates_project_from_full_text_to_split_body` 与 `workflow.test_source_coordinate_projection` 通过。
- 批次 `【木牍教育】19年安徽中考英语` 的正文仍为 315 行；结构闸门重跑通过，产出 48 个单元且无阻断项。该批次已有历史上传，新增 `content_snapshot` 按保护规则跳过，后续依赖阶段不继续重算。
