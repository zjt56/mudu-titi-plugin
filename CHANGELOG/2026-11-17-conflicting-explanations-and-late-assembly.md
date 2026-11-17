# 2026-11-17：冲突解析择一与画线后最终汇总

## 背景/触发
同一题在不同真实材料中出现两份不一致解析时，原实现把它当成确定性冲突并卡住；同时旧流程先汇总、后画线，后续汇总可能覆盖 `<u>...</u>` 标记。

## 改了什么
- `app/workflow/explanations_direct.py`：解析候选允许在同题真实来源冲突时择一，保留候选自身的来源坐标与证据，不拼接、不改写、不跨题借用。
- `app/workflow/upload_content.py`：保留逐字来源、scope/ownership 和上传保真校验；同题另一份异文解析不再清空当前真实候选。
- `app/workflow/contracts.py`：新增 `content_snapshot`，执行顺序调整为 `content_snapshot → meta → underlines → assemble → validate`；最终 assemble 显式依赖画线产物。
- `app/workflow/agent_tasks.py`、`runner.py`、`stage_semantics.py`、`manifest.py`：绑定画线后的 batch view、快照输入与失效传播，合法无 DOCX 跳过时允许基础视图作为未标记输入。
- `app/workflow/handoff.py`：交接时要求 `content.snapshot.json` 与 `batch.base.json` JSON 内容完全一致；元数据视图必须保持快照来源投影，画线视图只允许改变 `<u>...</u>` 标记；坏 JSON、篡改快照或题面字段不能仅凭文件存在完成阶段。
- `app/workflow/assemble_local.py`：最终确定性汇总读取 `06_batch/batch_meta.json`（无 DOCX 合法跳过时读取 base view），只覆盖题干、材料、表格、选项、附件、子题等来源字段，答案/解析/知识点仍以新验收产物为准。

## 边界
这不是自动裁决解析质量，也不放宽题号、来源、结构、标签或上传硬校验；择一必须来自当前题目真实来源，画线产物缺失且未明确跳过仍阻断。

## 验证
- `PYTHONUTF8=1 python -m unittest workflow.test_explanations_direct workflow.test_upload_content_equivalent_anchors`：39/39 通过。
- 核心集成回归（contracts、snapshot、delivery、stage semantics、assemble、agent tasks、解析冲突、上传等价锚点）：166/166 通过；补充快照篡改与 skipped-underlines 交接回归后 delivery/contracts/snapshot：18/18 通过。
- 扩展回归（含身份、答案、题型流水线、上传载荷、听力完整性）：262/262 通过；交接来源投影回归（含快照篡改、元数据/画线视图篡改、无 DOCX 跳过）：19/19 通过；变更模块 `py_compile`：通过；`git diff --check` 无空白错误。

## 回退
恢复 `contracts.STAGES` 的旧顺序和 `_unique(..., allow_conflict=False)`，并移除 `content_snapshot` 及其最终汇总输入绑定；不回退已有用户数据。
