# 2026-11-09 审核末片无法收尾：统一证据来源归一化与轮次提示契约

## 背景

用户反馈「【木牍教育】17年安徽中考英语」内部审核长时间重试，最后一片 q-94/q-95/q-96 始终未被认定完成，并出现输出缺少 findings/notes 的协议错误。

逐字段核对发现：前面第 2 片 q-31 的答案引文真实存在于 `01_source/附录.txt`，模型误引为 `01_source/正文.txt`。`_round_checks` 按真实引文自动纠正了安装记录的路径；留存响应及其摘要仍保留原路径。最后一片合并时，来源复核将留存响应与安装记录直接比较，因此报 `Review execution checks mismatch`。正确的第 4 片也无法消除前片的路径差异，继续调用模型没有意义。

前期将换 task_id 认定为根因的假设已被排除；换 ID 是失效收据后重新导出的结果，不是这次字段不一致的起点。

另一个独立问题是轮次任务附加了普通审核角色的输出契约：头部要求 status/artifacts/notes/findings，角色末尾却要求 findings/checked_units/unchecked，形成矛盾。

## 改了什么

- `app/workflow/review.py`：提取 `_evidence_source` 为唯一证据路径解析器，继续要求路径属于绑定输入、引文真实存在；校验及来源复核共用。来源复核只在深拷贝的比较视图中归一化路径，兼容已经生成的旧记录。响应文件、摘要、收据、事务不重写，其余判断字段仍严格比较。
- `app/workflow/agent_roles.py`：轮次任务使用独立的 checks/assessment 角色段，不再混入普通审核产物的 findings/checked_units/unchecked 说明；普通审核角色保持原样。
- `app/workflow/review_rounds.py`：在模型可见提示中提供完整四根键 JSON 结构示例；checks 仍必须覆盖实际目标，严格解析器不补造字段。
- `app/workflow/test_review_rounds.py`：新增四分片回归，模拟第二片引错附录路径、先保留已完成二审，再完成最后一片；验证原响应不变、两轮均完成、篡改判断仍不通过。新增两轮提示词完整 envelope 与角色契约检查。

本次是既有协议的缺陷修复，不提高 PROMPT_VERSION 或修改通用角色文件，避免仅因输出说明修正令已有完整审核证明失效。

## 真实批次复验

通过 `review.evaluate_rounds` 的原来源检查确认：labels 48/48、sample 10/10 均已执行完成。通过 `agent._run_review_rounds` 正式入口登记审核交付，并运行 `runner review-gate` 刷新闸门；期间禁止模型调用，未重新生成题文或标签，二审原记录逐字段相等。

当前 review=completed，review_gate=needs_review；17 项真实标签差异完整保留，required_unchecked=0。诊断无内部审核失败。未手改 manifest、binding、receipt，未上传或发布。

## 验证

- 最终专项（审核、恢复、来源证明、严格传输、角色、变更记录规范）：141 项通过，包含增强后的四分片回归、未绑定来源及伪造引文拒绝、纠正来源之外的判断篡改拒绝。
- 最终全量 workflow：1953 项，1934 项通过、17 项跳过、2 项因沙箱禁止 ps 的 PermissionError 报环境错误；无断言失败。两项环境错误均为 test_local_agent_ownership 进程所有权用例。

## 边界与回退

不覆盖失败收据、不允许不同响应覆盖已消费任务、不修改审核结论，不把差异当作通过。不存在于绑定原文的引文仍拒绝。回退本提交即可恢复代码；运行时审核证明仍保留，旧比较器会再次报告来源路径不一致。
