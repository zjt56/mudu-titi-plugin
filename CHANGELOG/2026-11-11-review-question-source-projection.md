# 2026-11-11 审核题目来源元数据投影

## 背景（触发）

课时批次通过正常 meta 投影清除子题旧来源占位后，`source_type`、`year` 和难度已被审核语义投影排除，但子题顶层 `source` 未排除，导致 5 个题组的已完成审核单元绑定变化，重新导出审核任务。实际差异包含 19 个子题来源占位删除，以及 46 处缺省难度由缺失补为“适中”，不能描述为仅修改来源。

## 改了什么

- `app/workflow/review.py`：`semantic_review_value` 增加显式题目上下文，仅在已识别的题目/子题行忽略展示来源 `source`；审核单元与按单元选出的正式行使用该上下文。普通证据/正文嵌套对象的 `source` 仍参与语义绑定。
- `app/workflow/test_review_local_reuse.py`：覆盖父子题来源、来源类型、年份与缺省难度变化保留单元绑定；题干、答案、知识点真实变化使对应单元失效；来源元数据变化仍拒绝在途任务的旧精确字节哈希。

## 没改什么 / 边界

未放宽在途任务精确输入哈希、原件/媒体证明和上传冻结；未修改真实批次或审核判断，未调用真实模型、未停止进程、未提交或上传。此修复不迁移历史绑定，也不恢复已退役未裁决发现；历史发现恢复由独立的 ingest 修复负责，不能把存档当作活动待审队列。

## 如何验证

独立外部 TEMP/TMP/TMPDIR、用户配置目录及离线 `MUDU_AGENT_CONFIG`，`PYTHON=python`，所有临时批次位于仓库外：

- `workflow.test_review_local_reuse`：14/14 通过，无失败/错误/跳过。
- `workflow.test_review_rounds`：85/85 通过，无失败/错误/跳过。
- `workflow.test_stage_semantics`：27/27 通过，无失败/错误/跳过。
- 合计 126/126 通过；`git diff --check` 通过。

日志位于 `C:\Users\Administrator\AppData\Local\Temp\tiku-review-source-projection-20261111`，分别为 `local-reuse-final.log`、`review-rounds.log`、`stage-semantics.log` 及同名 JSON 汇总。

## 回退方式

仅回退本条记录对应的 `review.py` 显式题目来源投影及两条回归测试；保留并行工作和历史审核证据，不改写真实批次台账。
