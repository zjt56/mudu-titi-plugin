# 卡点：标签审核响应将notes和findings嵌套入artifacts导致整轮未交付

- id: 2026-11-11-标签审核响应将notes和findings嵌套入artifacts导致整轮未交付
- status: fixed
- batch: 02七上人教版课时A计划StarterUnit1Hello!第二课时Pronun
- batch_dir: workflows/02七上人教版课时A计划StarterUnit1Hello!第二课时Pronun
- stage: review
- check: REVIEW_RESPONSE_ENVELOPE
- created: 2026-11-11T20:39:40+08:00
- last_seen: 2026-11-11T20:39:40+08:00
- occurrences: 1
- fixed_at: 2026-11-11T20:42:48+08:00
- fix_commit: 1b38b41之后的运行恢复记录；无代码改动，见CHANGELOG/2026-11-11-review-candidate-envelope-recovery.md
- verification: 同一labels原任务9/9checks通过本地消费者，import completed=true；labels/sample执行完成unchecked=[]；3项open差异保留，review_gate=needs_review execution_completed continue_unqualified；format报告passed=true/errors=[]且当前消费者passed；validate19项全通过，无上传receipt
- title: 标签审核响应将notes和findings嵌套入artifacts导致整轮未交付

## 现象（卡点）

labels两次候选均含9个真实check但顶层缺notes/findings；二审sample已接受，labels仍REVIEW_ROUND_UNCHECKED，不能称审核执行完成

## 具体检查与判定

- 失败检查：REVIEW_RESPONSE_ENVELOPE
- 判定口径：候选实际将空notes/findings放进artifacts，审核根对象必需status/artifacts/notes/findings，不能因候选存在即跳验证；最末候选还有1条图片单元原文引语不能命中当前来源
- 修复方向：保留9条原判断与分歧，仅确定性提升现有同名元数据到正确层；复用原任务当前绑定复验，已有8条有效，失败1条按原审核target局部补证；不改判断/标签或差异队列

## 修复记录

- 修复方式：仅将原候选已有notes/findings移回根对象，图片题quote按第58行恢复漏抄的中字；9条verdict/assessment/rationale不变，原绑定当前，通用审核导入接受后正常执行review_gate与format_gate
- 修复提交：1b38b41之后的运行恢复记录；无代码改动，见CHANGELOG/2026-11-11-review-candidate-envelope-recovery.md
- 复验证据：同一labels原任务9/9checks通过本地消费者，import completed=true；labels/sample执行完成unchecked=[]；3项open差异保留，review_gate=needs_review execution_completed continue_unqualified；format报告passed=true/errors=[]且当前消费者passed；validate19项全通过，无上传receipt
- 标记时间：2026-11-11T20:42:48+08:00
