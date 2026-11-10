# 2026-11-10 修复知识点标签阶段的两个卡点

## 背景

用户反馈：刚才的试卷任务在「知识点标签」阶段出现两个卡点。核查 `【木牍教育】22年安徽中考英语` 的自愈记录，两次都是 `action=rejudge_knowledge`、`status=refused`，且都报同一条错误：

`KNOWLEDGE_REPAIR_FAILED: [Errno 2] No such file or directory: .../06_batch/batch_meta.json`

两次触发分别是：① 知识点代理输出 JSON 语法错误（缺引号）；② group-6-7「听力长对话理解」组级缺必带维度「主题」。根因有三层：

1. `self_heal_actions.knowledge_repair_targets` 硬读最终产物 `06_batch/batch_meta.json`，而知识点阶段在 assemble 之前运行，该文件还不存在 → 自动修复每次都崩，变成“卡点”。
2. 自愈状态 `self-heal-status.json` 写入后被固定为 `refused`；即使知识点阶段随后在普通重试中成功，界面仍显示旧的“拒绝/卡点”（陈旧）。
3. 知识点角色提示词没有强调“整组题型必须包含必带维度”，导致 group-6-7 漏掉必带的「主题」。

## 改了什么

- `app/workflow/self_heal_actions.py::knowledge_repair_targets`：最终批不存在时，改为校验当前 `04_knowledge/knowledge.json`（配合 `03_types/types.json` 与确认结构）来定位非法单元；两者都不存在时返回空目标，不再抛 `FileNotFoundError`。知识点阶段的自愈因此不再崩溃。
- `app/workbench/server.py::advance_state`：仅当其 `stage` 仍未完成（completed/approved/skipped 之外）时才显示 `self_heal` 卡点；阶段已成功的陈旧自愈状态不再冒充当前卡点。
- `app/prompts/roles/knowledge.md`：新增硬性自检——整组题型必须包含 `references/type-knowledge-map.json` 中该题型 `dims` 列出的「主题」「语篇体裁」等必带维度，各取合法叶子（如「听力长对话理解」必须含「主题」）。
- 回归：`app/workflow/test_self_heal_actions.py` 新增知识点阶段无最终批时的目标发现与空目标用例；`app/workbench/test_upload_status.py` 新增“阶段完成后隐藏陈旧自愈、仍失败时保留”用例。

## 没改什么 / 边界

不放宽知识点校验（缺必带维度仍判非法），不猜标签、不自动改知识点；只是让自愈能正确运行、并清除陈旧展示。未改审核口径与上传边界。

## 验证

- `app/workflow/test_self_heal_actions.py`、`test_self_heal*`、`test_agent_tasks`、`test_agent_roles`、`workbench.test_upload_status` 共 174 项通过。
- 真实批次复核：`22年` 现 `04_knowledge/knowledge.json` 中 group-6-7 已含「主题」合法叶子；`/api/batches/.../pipeline` 的 `self_heal` 现返回 `null`（陈旧卡点消除）；知识、审核阶段均完成，仅剩真实标签差异待人工。

## 回退方式

回退本提交即可恢复原实现（会重新出现知识点阶段自愈崩溃与陈旧卡点）。
