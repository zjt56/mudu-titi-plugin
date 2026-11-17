# 2026-11-17：前期少卡点：内容完整性后移、不可消费的结构自动重跑

## 背景/触发

用户明确要求：前面流程不要有太多卡点，具体的格式校验与正确性验证放到后面的流程完成。
真实批次 `【木牍教育】2024中考英语试卷` 长时间停在 `structure_gate`：阶段台账显示
`completed`，但 `01_source/structure.confirmed.json` 仍是旧版重复题号产物，
`preassemble` 的消费者校验 `Confirmed units have duplicate question numbers` 失败，
调度只显示“等待上游”，无法自行恢复。另有 `types/preassemble` 因
`contentComplete=false` 提前阻断，消耗模型轮次但不产生下游动作。

## 改了什么

- `app/workflow/manifest.py::is_stale`：`structure_gate` 标记完成时额外做一次消费者核验
  （`agent_tasks.confirmed_units`）；结构确认产物缺失、重复题号或身份不可解析时返回
  stale，调度会自动失效并重跑结构闸门，而不是让下游永久等待。
- `app/workflow/agent_tasks.py::_validate`（types/preassemble）：`contentComplete=false`
  不再提前抛错；保留原值并写入 `warnings`/`notes`（“内容完整性未确认，已后移最终闸门核对”），
  不伪造为 true。
- `app/workflow/final_audit.py`：新增 `content_completeness_errors()`；最终闸门读取
  `03_types/types.json`，对真实缺内容（非合法图片/空白网格）报
  `CONTENT_COMPLETENESS_UNVERIFIED`，把正确性校验放到最终阶段。
- `app/workflow/manifest.py`：`upload` 已完成的旧批次自动把新增 `content_snapshot`
  标记为 `skipped（历史批次已提交远端）`，不再让已上传/已暂存批次显示“可执行快照”。
- `app/workflow/runner.py`：`audio` 返回 `needs_review`（exit 3）时只记录待复核，
  继续执行解析、知识点、快照、元数据、画线、组装等不依赖听力的独立阶段；review
  仍因 audio 未完成而等待。
- `app/workflow/agent_tasks.py`、`app/workflow/handoff.py`：`assemble` 的来源审计问题
  保留为 notes/候选，不提前让模型重试；`handoff` 对 assemble 延后内容阻断。最终
  `validate`/`final_audit` 继续按原卷逐题核对并阻断。
- `app/workflow/test_agent_tasks.py`、`test_preassembly.py`：同步新语义；新增
  `app/workflow/test_early_defer_policy.py` 覆盖内容延后与结构 stale。
- `AGENTS.md` 硬规则 9 同步：内容完整性、难度一致性、重复显示后移最终闸门；前期不阻断、不伪造。

## 没改什么 / 边界

- 来源缺失、题号/身份冲突、消费者不可解析等实质错误仍阻断；结构闸门只是不再把所有
  结构质量警告当阻断。
- `contentComplete=false` 不会被改写成 true，也不会跳过最终核验；最终闸门仍会失败。
- 不改用户原卷、答案材料、上传边界；不自动上传或发布。

## 如何验证

- 真实批次 `【木牍教育】2024中考英语试卷`：强制重跑 `structure_gate` 后 34 单元
  （独立21/组合13）通过，`preassemble`、`types` 均完成。
- `workflow.test_agent_tasks` 63 项通过；`workflow.test_early_defer_policy` 2 项通过；
  `workflow.test_format_gate` 27 项通过；`workflow.test_agent_tasks`+`test_early_defer_policy`+
  `test_format_gate` 合计 93 项通过。
- 真实批次 `【木牍教育】2024中考英语试卷` 继续跑：`answers` 完成、`audio` 待复核不阻断文字链、
  `assemble` 确定性完成；`validate` 在来源逐题核对处阻断（正确性已后移到该阶段）。
- `test_preassembly` 仍有 5 个旧夹具/期望失败：`test_fresh_scheduler_delivers_types_before_three_label_workers`
  仍假设只有 `types` 命令、`test_invalid_labels_never_complete_or_unlock_downstream` 仍按
  `contentComplete=false` 阻止，另 3 个 late-audio 夹具未构造新的
  `content_snapshot→meta→underlines→assemble` 顺序；属于测试同步债务，保留待统一修复。

## 回退方式

分别回退 `manifest.is_stale` 的结构消费者检查、`agent_tasks` 的 contentComplete 延后分支
及 `final_audit.content_completeness_errors` 即可；不影响已存在批次数据。
