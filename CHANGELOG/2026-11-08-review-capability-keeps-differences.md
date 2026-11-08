# 2026-11-08 修复图片能力提示吞掉真实标签差异（2015 卷第 6–10 题）

## 背景 / 触发

用户指出 2015 安徽卷听力短对话第 6 题及后续题的一审与原标签不同，却没有待审核标记。此前盲审隐藏标签误报修复后，错误地将 9 条队列认作完整差异集合；本次纠正该验证遗漏。

第 6 题原标签为 `[设题角度/听力/细节理解]`，一审为 `[主题/人与自我/生活与学习/学校生活, 设题角度/听力/细节理解]`，是明确的集合差异。模型理由同时写了“选项图片未实际查看，仅据占位和转写判断标签”。

根因：`review_consensus.compare` 已正确生成结构化 knowledge 差异，但 `review._is_capability_gap` 只按模型 rationale 判断能力受限；它在该理由看到图片、占位且 verdict=ok，就返回 True，没有尊重程序比较产生的 field/value 差异。结果实际差异被移入 informational，不再进入持久队列。第 6–10 题共 5 条同类漏标。

## 改了什么

- `app/workflow/review.py::_is_capability_gap`：对 `type/subtypes/knowledge` 且携带对应结构化 `round_sources.field/value` 的程序比较发现，直接保留真实差异，不能被审核理由中的图片/音频措辞降级。
- 保留原能力限制边界：仅“看不了图/听不到音频”的不确定，在没有程序比较差异时仍为 informational，不送人工。
- `app/workflow/test_review_consensus.py`：新增双轮、知识点/题型/子类差异附带媒体限制描述仍待审；真实差异写入持久 manual-review-queue；标签一致而只有媒体不确定仍 informational 的回归。
- `app/docs/review.md`、根 `AGENTS.md` 硬规则 6、`CHANGELOG/RULES.md` 固化能力限制与真实差异的优先级。

## 实际批次恢复与逐层验证

仅处理活动批次 `workflows/【木牍教育】15年安徽中考英语`。复用已有两轮判断，通过正式 runner invalidate/run 重新生成审核比较、队列和闸门，不修改原标签、审核响应或 manifest，也不重新调用模型。

- 一审 48 单元、二审 10 个抽样目标仍完整。
- 独立按原标签与各轮 assessment 逐项比较：14 条差异；修前队列 9 条，缺少 q-6/7/8/9/10。
- 修后 evaluate_rounds findings=14、持久队列=14，无缺项或多余项。
- 工作台 HTTP 接口：48 单元，needs_human=14；q-6–10 每题 needs_human=true、open_findings=1；q-1–5 为 false、0。
- 独立只读复核确认：48 行工作台投影中恰有 14 行待审，队列与预期签名完全一致，旧缺标签清理不再误过滤这些真实差异。
- 质量仍未放行；本地格式检查继续，不自动上传。

## 如何验证

- 六个相关套件：`workflow.test_review_consensus workflow.test_review_rounds workflow.test_review_rounds_recovery workflow.test_review_local_reuse workflow.test_review_legacy workflow.test_self_heal_actions`，170 项、19.515 秒，全部通过。
- 全量 workflow 在排除用户数据、隔离配置与锁且禁用真实模型命令的临时源副本运行：1679 项、90.969 秒，9 failures、0 errors、17 skipped。失败名称集合与先前同环境基线完全相同（新增失败 0）；遗留为 changelog 2、local_agent_transport 1、repair_feedback 3、supervisor 2、textbook 1。不能称全量通过。本次新增双轮媒体措辞差异、队列持久化与纯能力提示一致性回归均通过。

## 边界

不改变盲审隔离与提示，不向模型透露原标签或其它轮次判断；不豁免真实内容错误。能力限制本身不送人工，但不能成为删除结构化标签差异的理由。没有更改归档批次、其它年份或执行题库上传。

## 回退方式

回退分类器与测试的本次差异后，可通过正式 runner 重建派生文件；回退会重新暴露第 6–10 题漏标问题。保留原始两轮判断和历史队列，不得靠编辑状态台账回退为“已通过”。
