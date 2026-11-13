# 2026-11-12：无号古诗来源与课时局部题号证明

## 背景与证据

目标批次为 `workflows/12七上人教版课时A计划StarterUnit3Welcome!第四课时Proj`。只读其正文、原始正文、结构候选与结构闸门报告，在系统临时目录复制文字/候选后运行真正的 `structure_gate.run_gate`，复现五项阻断：古诗无号节没有单元、正文年龄15误入声明题号、两个合法跨大题重号仍保留为阻断。本次没有写入或重跑该真实批次，没有操作运维修复 workspace。

## 修改

- `app/workflow/extract.py`：同行后续小题共用 `source_anchors.printed_tokens` 的真实列边界。普通正文空格后的 `15.She` 不再被当小题；保留原有答题空档与递增序列条件。
- `app/workflow/question_content.py`：来源形态分类增加 `unnumbered_table_response`。仅认可完整节窗口、非空且闭合的表格、表格外明确答题线、无图片及无印刷锚点的来源，不认可纯空表格或残缺表格。
- `app/workflow/structure_gate.py`：无号 standalone 古诗保留完整来源，使用内部别名 `[1]`，同时明确 `printed_numbers=[]`、`number_binding=unnumbered_section`、`original_number=None`、`printed_number=False`。不把空 numbers 一概放行。跨大题 restart 逐项重建当前标题、出现序次、来源范围和 scoped 身份；本节或参与同号的前序节有来源错误时仍阻断。合法证明不再依赖无关节整体通过。
- `app/workflow/test_structure_source_shape_regressions.py`：9项专用临时夹具测试，覆盖古诗保真、年龄数字、同行并排题/对话空位、独立消解restart、过期occurrence、同窗重号、空/残表/无正文、禁止跨大题借号。

## 验证

- 专用8项与相关闸门、来源、身份及同步规则共70项：通过，4项按既有条件跳过。
- 最终9项专用回归通过；新增子进程执行真正脚本 `extract.py` 的 `__main__` 入口，禁模型但保留原生Word提取与解析，验证无package上下文下导入有效、年龄不入题号、真实并排题仍完整。新导入使用绝对 `workflow.source_anchors`（主agent集成修正）。
- 扩充确定性回归共147项：通过，4项跳过；包含 `test_extract.StructureCandidateTest`、对话空位、课时提取、结构闭包、来源结构、原生无号、结构闸门、scoped身份、内容形态与同步集成。
- 额外跑入作文编号推断套件时共179项出现1个既有失败：`test_native_extraction_warning_cannot_prove_number`。将 HEAD 的原始 `structure_gate.py` 在临时module载入单跑，同样失败；其已有调用 `allow_warnings=True` 与测试期望冲突。本次未修改该逻辑。
- 从目标批次只读复制正文，在临时批次重新 `infer_structure` 后运行真闸门：`passed=true`、`blockers=[]`、8个单元。保留古诗整表和答题线，任务阅读只含真实1–10。
- 保留旧 structure 候选的临时副本仍拒绝已存的虚假15；闸门不静默改写旧提取候选。
- `git diff --check`：通过。

## 边界与回退

未生成答案、未补造印刷题号、未跨材料绑定、未改用户原件或批次、未安装/提交。旧批次需要由主agent通过正式提取阶段重建候选，再复验结构，不能手写候选或manifest造完成。恢复本次三个实现文件对应的差异并移除专用测试即可回退；不得覆盖其他并行修改。
