# 2026-11-17：卡点修复目标与前期延期优化

## 背景/触发
用户要求减少不必要卡点，把卡点转成明确修复目标并加快前期流程；同时不能把题目内容、来源、身份或上传硬校验降级为假通过。

## 改动
- `app/workflow/diagnose.py`：每个诊断条目增加只读 `repair_target`，包含 `target`、`acceptance`、`scope`、`can_defer`；可区分确定性阻断、上游等待、可选输入和审核差异。
- `app/workflow/contracts.py`：阶段契约增加 `defer` 与 `optimization` 元数据，并提供 `defer_reasons()`、`optimization_hints()`、`stage_metadata()`；答案、听力、解析、知识点等独立分支保持并行，外部等待与最终格式工作明确延期。
- `app/workflow/test_contracts.py`、`app/workflow/test_diagnose.py`：增加结构化目标、延期语义和依赖不变回归；测试 fixture 显式按 UTF-8 读取 JSON，避免 Windows 默认编码造成假失败。

## 边界
本次只优化调度/诊断表达和延期元数据，不改变内容、结构、题号、来源、标签、审核差异或上传格式硬校验；审核差异仍不自动改判，外部等待仍不是成功状态，格式最终闸门仍必须通过。

## 验证
- `python -m unittest workflow.test_contracts`：4 tests OK。
- `python -m unittest workflow.test_diagnose`：50 tests OK。
- `python -m unittest workflow.test_self_heal workflow.test_self_heal_verification`：24 tests OK。

## 回退
回退 `contracts.py` 的新增元数据、`diagnose.py` 的 `repair_target` 只读投影及对应测试/变更记录即可；不涉及用户批次数据迁移。
