# 2026-11-12 同行印刷题号与音标紧贴选项修复

## 背景（触发）

真实隔离课时「七上13」结构闸门出现三类确定性错误：`3.Emma is in Class 2.` 内部的 `2.` 被识别成重复题号；Pronunciation 末尾缩写题每行双列1–10仅生成左列；`/e/A.eye` 中首个A选项遗漏，造成BCD不完整。

## 改了什么

- 新增 `app/workflow/source_anchors.py`，只认行首数字、Tab/全角空格或至少双ASCII空格后的列题号；普通英文单空格后的班级/编号数字不作为印刷题号。保持同一行实际重复token，不用数字去重掩盖重复。
- `structure_gate._scope_anchors`、`question_identity.printed_anchors/numbered_projection` 复用同一边界口径，不改 `preview._weak_numbers` 或 extract。
- `structure_gate._build_scoped_units` 为同行独立题保存0-based、end-exclusive `source_span.start_offset/end_offset`，`start_line==end_line`。只对同一行且偏移区间不交叠的两题豁免行重叠错误，其他来源边界检查保留。UID/QID加入列偏移区分身份。
- `question_content` 按精确偏移读取同行题源；选项正则允许紧贴短IPA音标后的A标记，不普遍放开URL/path斜杠边界。
- 添加Class 2伪锚点、双列10题、IPA五种元音选项与URL非选项测试。
- 后续PDF fixture反馈：模型第12单元72–80行跨越74行真实大题标题，但因LaTeX初字母空位4/7/8未识别而提前QUESTION_NOT_FOUND，遮住原跨节诊断。`structure_ai._sanitize_classified` 改为在合法span后先验证真实标题跨节，再扫描题号，保留GROUP_CROSSES_SECTION及原始单元索引/坐标。不放宽空位锚点、不接受坏候选。新增锚点完全不可识别时仍保持跨节诊断回归。

## 边界

未扩大题型规则、未生成答案、未改manifest。`question_draft` 的offset来源绑定由主Agent并行实现，本次只涉及锚点、身份、内容解析及结构闸门的最小列边界支持。既有单全角空格双题重复仍应判为重复。

## 验证

- `PYTHONPATH=app WF_STRUCTURE_AI=0 python3 -m unittest workflow.test_question_identity_structure workflow.test_question_content workflow.test_structure_gate workflow.test_question_identity workflow.test_question_identity_answers`：69项，65通过、4项既有跳过、0失败。
- 真实隔离七上13：`WF_STRUCTURE_AI=0 structure_gate.run_gate(base,output_dir=<独立临时目录>)` → passed=true、blockers=[]；source_number_coverage/grouping/source_content全部passed。单元26，其中standalone24、group2。未直接改manifest。
- 独立输出目录：`/var/folders/sf/9tmftxkx0n7bg6qx2n_k99_00000gn/T/lesson13-anchor-gate-9wqisll1`。
- 直接边界检查：`1.A　1.B` 保留两次1；`3.Emma is in Class 2.` 仅题号3；`1.I’m___\t2.it’s___` 保留1、2。
- 后续PDF诊断复验：独立TMPDIR隔离共享agent测试锁，`workflow.test_pdf_lesson_structure workflow.test_structure_ai workflow.test_question_identity_structure workflow.test_structure_gate` 64项全部通过。首次未隔离运行因fake local-agent协议测试等待共享锁10秒而失败1项；隔离后0.408秒全过，未放宽或跳过测试。

## 回退

还原上述parser/身份/闸门定向改动及新增source_anchors，保留原件与批次数据不变；同行偏移字段的消费需与主Agent的question_draft绑定支持同时回退。
