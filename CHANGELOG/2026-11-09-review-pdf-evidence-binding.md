# 2026-11-09 审核绑定补齐已提供的 PDF OCR 辅助证据

## 背景
目标批次【木牍教育】20年安徽中考英语的两轮审核反复报 `Evidence must cite a bound batch input`。冻结提示词明确提供 `01_source/pdf-ocr.json`，任务级 input_hashes 也绑定其摘要，但嵌套审核 plan.input_hashes 没有该文件。模型引用真实且当前的 OCR 段落，因此被两个不一致的输入清单拒绝。

## 改了什么
- `app/workflow/review.py` 在 ROUND_INPUTS 尾部补齐 `01_source/pdf-ocr.json` 和同样已提供的 `01_source/material-roles.json`；前六项顺序不变，保持必需文件检查语义。
- `app/workflow/test_review_evidence_scope.py` 覆盖真实文件摘要、OCR 辅助引文、编造引文拒绝、内容变化使计划过期、旧任务哈希拒绝、缺失文件不绑定。

## 边界
OCR 仍是辅助材料，Word 原文优先；校验器仍要求当前绑定证据与可核验原文，不放宽审核通过条件。不修改真实批次、冻结任务或历史回执。旧任务必须通过正常新导出获得新绑定，不能补写旧计划。没有上传或发布。

## 验证
- 初次新测试把 OCR 当唯一原文证据，触发 `Every target needs verifiable source evidence`；已修正测试保留原文证据并附加 OCR，确认未放宽原文要求。
- 修正后的 evidence_scope 首次 5/5 通过；随后增加旧任务哈希与缺失文件拒绝检查。隔离锁目录重跑时确认旧任务按预期抛 AgentError(context_changed)，修正测试异常类型断言后，联合 `workflow.test_review_evidence_scope workflow.test_review_rounds` **70/70 通过**（17.541秒）。
- 首次联合测试 70 项：68 通过、1 失败、1 错误。错误为上述旧测试快照；失败为真实工作流占用全局代理锁导致 `lock_timeout`。已隔离 TEMP/TMP 重跑，最终结果见上。12:02 Workbench 只读状态仍为 reviewing、flow_done=false、uploaded=false；没有把代码回归通过当作真实审核通过。git diff --check 通过，仅有 CRLF 提示。

- 12:08 只读检查实际冻结任务 `workflow/review-bindings/round-686f1311b36946059bf87a7e951c57cf.json`：任务级有 OCR/roles，嵌套 plan 仍为旧清单；其回执 labels part_completed=1/part_count=3。说明在途进程使用旧计划，尚不能宣称真实任务应用了本补丁；没有改写在途绑定或抢占运行。

- 12:10 真实续跑复验：manifest.review completed，finished_at=12:07:32，notes=分轮审核：complete；`07_review/review-rounds.json` 新 binding=9f1be59fc22ef98757f489ea9e70dc593ceb838b4fe48a9ec9d2b1c319a06bdd 已包含 OCR 精确摘要，两轮均实际包含 OCR 引用及非空 execution_id；unchecked.json=[]。仍存在真实知识点差异，manual-review-queue 保留 open，不声称质量通过或放行上传。此前12:08查看的旧冻结任务不是当前合并轮次，因此以当前产物为准。

## 回退
回退 ROUND_INPUTS 新增两项与新增测试。现有冻结计划不修改。
