# 表格作答空位的最终上传副本题干适配

## 触发

12课时Starter Unit3第四课时Project最终format_gate报sec-7f100795c4cef82a-u43/6–10 MISSING_STEM。原表格与子题均完整，本地来源证明已通过，但最终API映射仍要求结构化子题有非空stem。2026-11-13匿名GET当前公开developer.md：文档要求子题题号/题干/答案，但未规定空档stem具体写法。本补丁为来源保真的最小兼容适配，并无认证远端暂存实测结论。

## 修复

upload_payload在原始单元（编号/canonical转换前）执行完整table_blank_stem_numbers来源匹配，再只从对应原生table proof marker中唯一截取原题号+下划线作答空位，使用已有normalize_blank_text转换成6.______等。仅给API副本的空stem填此原文片段，非空stem不覆盖；不复制整cell、不生成题干或答案。本地batch与batch_meta只读。无proof、错scope/身份、缺表格/子题/空位或重复空档仍保留MISSING_STEM及原硬校验。

转换以TABLE_BLANK_STEM_PROJECTED warning留档unit/source_ref/table_number/source_line；format规则摘要加入table_structure与question_content。未知字段、原号覆盖、答案、catalog、媒体/冻结等全部校验保持。6项审核差异不裁决或清理。

## 验证

Python相关129项全部通过（6.681秒）：上传副本、最终format、编号、表格来源。涵盖原始输入不变、五个精确原空档、无proof/partial/错scope/重复/普通题干缺失拒绝、非空不覆盖与marker重复拒绝。

正式runner run --stage format_gate --force exit0，manifest format_gate=completed，upload-format.json passed=true/errors=[]，5个projection记录均TABLE2/正文52行。修前备份workflow/recovery-evidence-20261113-format-stem；修后batch.json、batch_meta.json、manual-review-queue.json、review-gate.json SHA全部不变。review_gate仍needs_review，质量未放行。原运维启用恢复与故障复验见现场回执。未调用上传/发布端点。

## 回退

回退upload_payload与format规则摘要改动，保留当前原文、5项来源证明和历史报告；不得删除审核差异或直接改manifest制造完成。公开契约若变化需重新核对，不把本地format passed当远端已接收。
