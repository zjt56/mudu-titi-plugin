# 2026-11-11：保留真实审核判断，修复候选协议层级与原文引文

## 背景

指定课时批次 `workflows/02七上人教版课时A计划StarterUnit1Hello!第二课时Pronun` 在答案及装配恢复、19项体检通过后，两轮实际审核中的sample已接受，labels连续两候选均因根对象缺notes/findings而拒收。独立运维报告误称审核差异状态已完成，但真实 `review.evaluate_rounds` 为 execution_completed=false、REVIEW_ROUND_UNCHECKED(labels)，不能把needs_review一律当差异。

## 改了什么

未触及代码。读取最新原候选 `workflow/review-attempts/round-9cc8377dfb0b4e65bf58973b687ac0c4-68a4f31e2e6248449f26d81221d75fac/response.json`：9条checks真实存在；原 `notes=[]/findings=[]` 误放入artifacts，仅将这些现成元数据移回根对象，review_round保持。

原文局部复验先证明8条check有效；图片填词check引用第58行漏抄「中」字，将quote从「补充第6题所缺的字母」恢复为真实同坐标的「补充第6题中所缺的字母」，不改该条uncertain或assessment=null，也不声称已看图。九条判据、verdict、assessment、rationale逐项保持原值。

使用原任务ID、原当前输入绑定与正常review导入器复验交回，未更改binding、receipt或manifest；正常auto执行review_gate与format_gate。

## 边界

不补写任何语义审核判断，不改verdict、不resolve差异、不改题型/知识点/答案。原候选完整保留。只有来源坐标明确且引文可逐字还原时修引文，不能用此法覆盖真实审核矛盾或跨题补证。3项真实/矛盾判断差异完整保留（含verdict=error却在rationale说ok的模型输出），供用户判断，不自动改为ok。

## 验证

- 修改前8条check本地有效、1条引语未命中；修引语后9/9原check通过相同 `_partition_check_issues`。
- 原输入 `_check_bound_inputs`、plan与prior-round绑定均当前；9条语义判据四元组（unit_id/verdict/assessment/rationale）修前修后完全相等。
- 通用 `agent_protocol.import_result` 处理原round任务，completed=true、round_completed=labels、quality_passed=false。
- `review.evaluate_rounds`：execution_completed=true、checked_rounds含labels/sample、unchecked=[]；review阶段completed。
- 正常review_gate执行完成但needs_review、continue_unqualified=true、release_ready=false，活动队列保留3项open（5项历史fixed条目仍在）。最终format报告passed=true、errors=[]，is_stale=false、handoff消费者passed=true。validate当前通过19项；批次pending_confirmation，无上传receipt。
- 未触及代码，无需跑测试；相关代码修复的297项及全量2590项结果见前两份当日变更。

## 回退

原候选与新交回文件都保留，不手改活动审核记录或清空队列。如需撤销这轮，应使用现有受控fresh-review入口另开真实审核代次，不伪造完成态或删除分歧。
