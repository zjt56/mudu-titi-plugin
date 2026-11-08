# 2026-11-08 阶段指纹规范化：避免序列化噪声反复卡流程

## 背景 / 触发

用户要求重新梳理插件流程，消除哈希指纹作为调度阻点，并让失败可以自主诊断、修复、复验和推进。此前虽有 `stage_semantics` 规范化 JSON 输出的实现，实际 `record_semantics` 却把原始 `output_hashes` 再存为语义输出，`is_stale` 仍按裸 SHA256 判断。因此 JSON 空白、键顺序或时钟字段变化仍能触发阶段失效，与既有规范化设计不一致。

## 改了什么

- `app/workflow/manifest.py`：`record_semantics` 记录 `stage_semantics.VERSION` 和规范化的 `output_signatures`；原始输入/输出哈希仍保留在台账用于审计。
- `app/workflow/auto.py`：限流冷却不再在批次入口提前返回；只跳过需调用模型的 agent 阶段，仍运行本地命令/闸门，并继续处理可执行的独立分支。AI 自愈模型调用也遵守同一冷却；本轮并行或串行调用新触发冷却后立即作用于其余 agent，避免继续撞限流。
- `app/workflow/test_runner_auto.py`：加入限流期间本地 validate 继续、review 依赖仍等待的调度回归。
- `app/workflow/manifest.py`：`is_stale` 与 `reconcile_deliveries` 对新版本记录按规范化输出签名比较；纯 JSON 序列化变化不再触发重跑，真实业务内容变化仍标记过期并要求重新校验。
- `app/workflow/test_stage_semantics.py`：将真实输出编辑测试明确为必须重开阶段，并验证 JSON 空白/键序/时钟变化可复用。
- 根 `AGENTS.md`、`CHANGELOG/RULES.md`：记录哈希用途边界，区分调度复用签名与精确安全绑定。

## 没改什么 / 边界

- 在途代理任务、原卷/媒体来源证明、音频映射一致性和上传批准/冻结仍使用精确绑定；这些哈希不能因调度卡顿而放宽。
- 真实产物字段变化、缺失产物、消费者 schema/来源/题号校验失败仍不得当作交付；自愈需要实际修复并复验，不能把 failed/needs_review 改成质量通过。
- 本地自动流程不自动上传或发布；此修复不重跑、不改写任何历史批次用户数据。
- “保证每份任意内容都通过”不作为实现目标：有真实内容缺失或不可修复错误时必须保留未完成状态，禁止编造内容。

## 如何验证

- 当前代码受影响回归：`workflow.test_delivery`、`workflow.test_stage_semantics`、`workflow.test_runner_auto` 共 `77/77` 通过（13.355 秒）。
- 补丁后关键定向回归：规范化指纹、真实内容改动失效、限流期间本地闸门继续，`3/3` 通过；四个改动 Python 文件 `py_compile` 检查通过。
- 修复前 `workflow.test_delivery` + `workflow.test_stage_semantics` 为 `34` 通过、`2` 失败；失败项是 JSON 序列化变化被原始哈希误判，以及真实产物变化测试期望与执行口径矛盾；修复后同模块与调度模块完整回归全绿。
- 全量 `workflow.test_*` 曾启动，但在补丁前载入旧模块且运行超过 20 分钟仍未完成，已终止；因此不报告全量套件通过。当前变更以受影响模块完整回归作为验证结果。

## 回退

如需回退，仅恢复 `manifest.py` 中 `record_semantics`、`is_stale` 对语义版本签名的写入与比较，并恢复对应回归断言；不得删除现有精确输入绑定、媒体校验或上传冻结校验。
