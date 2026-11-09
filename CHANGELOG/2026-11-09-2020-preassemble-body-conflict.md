# 2026-11-09 2020 安徽卷预装配正文冲突修复

## 背景

用户反馈批次【木牍教育】20年安徽中考英语在 preassemble 报 `Batch fill正文 conflict for group-6-7; existing content preserved`。批次目录为 `C:\Users\Administrator\.dsh\wf-plugin-data\workflows\【木牍教育】20年安徽中考英语`。

## 改了什么（诊断与修复）

只修复当前批次数据，未改实现或 manifest。逐个比较 batch.json 受保护正文与 batch-fill-state.json 的预期正文，并用当前 question_draft 验证已存基底仍与原卷绑定一致。共发现并恢复 4 个单元：

- group-6-7、group-8-9-10、group-11-12-13-14-15 的 material 被写入子题题干与选项，预期共享材料为空；恢复 material，保留完整子题。
- group-16-17-18-19-20 表格的一个空单元格少了 `items: []`；确认仅此空字段差异后恢复。

在工作流 agent 写锁下，重新检查输入快照，备份 batch.json、batch.base.json、batch-fill-state.json 和逐项 before/after，再原子写入 batch.json。未改答案、标签或填充状态，不制造阶段完成态。

备份：`workflow/repairs/body-conflict-20261109-112347/`（位于上述批次目录）。

## 验证与边界

- 写入前验证 4 个冲突单元的当前 draft 与受保护基底一致；其他未预期差异会触发断言并拒绝写入。
- 修复后 `batch_fill._managed` 完整读回校验通过。
- 通过工作台 advance API 续跑，11:24 实批 preassemble、answers、types、audio、explanations、knowledge、assemble、meta、underlines 均 completed。
- 整卷尚未完成：validate 当前因 `audit_batch_integrity`、`check_explanations` 失败，后续审核 pending。此次修复只证明上述正文冲突解除，不代表内容质量通过。
- 无代码变更；验证使用实批状态与受保护正文校验，未新增实现回归测试。未自动上传或发布。

## 回退

如需恢复，必须先停下该批次写入、核对备份后新增的合法产物，再事务恢复备份；不能直接覆盖续跑后的数据或编辑 manifest。
