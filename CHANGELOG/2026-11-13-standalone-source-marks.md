# 无编号独立题的原卷提示标记保留

## 背景与触发

12课时Starter Unit3第四课时Project解除答案与表格A9卡点后，validate的audit_source_marks报[跨学科]缺失（D27/B9）。实际标记仅在古诗大题source_section标题，未进入题干/材料，不能按元数据存在就通过。

## 改了什么

question_draft仅在已验证scoped来源、完整合法无编号standalone形态下，重建当前大题标题实际位置/文本/section身份；实际标题带来源标记时将整行原文前置到stem，原诗与表格完整保留。数字题已有题干不重复，普通标题不泛补；错行/错scope/借别节标记拒绝。未放宽audit_source_marks或原文审计。仅question_draft.py+新test_standalone_source_marks.py，未扩其它生产实现。

## 验证

- 专项及来源/身份/预览相关83项通过；root另合并表格/结构消费者66项62通过4跳过（1.758秒）。
- 临时真实copy从旧逻辑正式finalized状态复现D27；新逻辑initialize仅刷新古诗UID，try_assemble通过。除该单元stem补同节完整标题，22答案/所有标签/两张materialBlocks字段严格等价，knowledge文件字节保持。
- 正式export preassemble+既有types候选新envelope import均completed，meta/underlines执行后do_validate=0、14检查0阻断、reconcile=[]；无伪改hash或manifest。
- 现场后台通过原受控链路自行重建：preassemble于07:11:51、assemble/meta/validate于07:11:52完成。实际stem包含原[跨学科]标题与整首古诗。answers完成时间仍06:50:36，knowledge仍前日23:21:44，标签未重生成。核验时review running，未打断/重复启动审核；最终07:15:35审核执行完成，保留6项真实差异(review_gate needs_review)，不宣称质量通过。format_gate另报5项MISSING_STEM（表格6–10空），已另立ISSUES/open核对平台空stem契约，不直接将本地豁免扩为远端放行，也不伪造题干。整卷尚未完成。

## 现场状态

修改期运维暂时关闭避免重复调查，正式恢复enabled=true且running/available/loopAlive=true；GET原validate事件resolved、verification.verified=true。health确认worker_alive=true、running_batches精确为本批、无running_without_worker。此补丁不自动上传发布，也不自动裁决审核差异。

## 回退

按本轮2文件差异回退；保留原来源、失败候选和已验证导入记录，不能直接清标记审计或改manifest造完成。卡点复验后用issue_log fix留档。
