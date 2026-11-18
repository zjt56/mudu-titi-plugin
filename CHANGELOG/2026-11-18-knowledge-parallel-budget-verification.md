# 知识点并行预算故障与原生验收链路复验

## 背景与根因

批次 `workflows/【木牍教育】17年安徽中考英语` 的原生知识点任务 `7007c4e63c044dd1a25991bf3cbba480` 同时包含 48 个单元，工具调用数达到 40 的配置上限，未提交候选即失败。原有分片策略位于 `local_agent.main`，并行入口 `auto._parallel_ready_agents` 直接调用 `local_agent.invoke`，绕过分片。任务没有调用类别轨迹，不能据此断言各类工具占用比例。

共享工作区修复已让并行原生知识点复用 `label_execution.run`，按预算收窄任务，保留单元快照，最终仍经整批校验及协议导入；预算没有提高。实卷分片进一步暴露 `content_agent._validate` 逐单元校验时未收窄题型上下文，造成合法 8 单元候选被误报题型表“48 行 / 确认 1 个”；现已将知识点的题型上下文过滤到当前单元，与解析使用相同规则。

## 本次补充验证

新增 `app/workflow/test_knowledge_parallel_budget.py`，直接调用真实并行入口与有界执行器，保留原生部分候选语义校验、任务导出、单元快照、最终验收和正式协议导入。只替换模型产出以及会话 admission/route 守卫；不依赖真实模型或用户批次。

- 48 单元、`label_split=false`、原生工具预算 40：实际 6 次调用，每次 8 个单元，无遗漏重叠，最终交付 48 条知识点。
- 第 2 片模拟预算失败：不安装部分正式产物；续跑只调用该片的 8 个单元，其他 40 个复用并重验快照。
- 在隔离副本换回 `HEAD` 的原 `auto.py`，第一条回归真实失败于 `48 not less than or equal to 8`，证明覆盖原故障入口。
- 隔离副本执行新链路回归、`test_local_agent`、`test_label_execution`、`test_unit_tasks`、`test_content_agent`、`test_auto_upstream_cooldown` 及两条相关 `test_runner_auto` 用例：137 项，1 项跳过，无失败。
- 更广的 193 项执行中有 5 条原有调度断言失败；换回修改前 `auto.py` 单独执行该模块 57 项，同样 5 条失败，均涉及独立 `content_snapshot/meta/underlines` 调度预期。本次不改变这些独立分支规则。

实卷保留候选只读复验中，3 个 8 单元候选通过当前原生校验；其余候选存在空知识点或实际无效目录路径，仍被原规则拒绝。模拟测试和单片复验不等于整卷知识点、审核或上传已完成，实际阶段以正式导入器回执为准。

## 边界与回退

未提高工具预算，未裁决审核差异，未上传发布，未直接改写批次产物或 manifest。本次新增回归与本文可独立撤回；运行修复的回退见 `2026-11-18-native-label-partition-budget.md`，并包含相应 `auto.py`、`label_execution.py`、`content_agent.py` 的知识点分片改动。当前工作区有其他会话改动，未将其混入提交。
