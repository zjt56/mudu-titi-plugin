# 2026-11-07 上游产物必须满足下游交接契约

## 背景 / 触发

用户要求：**全面倒查下游所有产物、字段、数据格式与校验需求，上游必须符合下游要求，否则不允许下游流程推进**；不能出现“上个流程没完成，下个流程已启动”。同时要修复 2019 安徽卷暴露的连锁失败：答案空值误报、解析在 Word/PDF 之间来源不唯一、知识点依赖隐藏、听力切片复用旧题型、自愈回退到错误上游。

用户后续明确：**不再验证当前 2019 批次**（将删除重跑）。因此本次不做任何批次重跑、改写或验证；2019 目录保持原样。

## 改了什么

### 1. 与消费方一致的依赖契约（`app/workflow/contracts.py`）

- `types` 提前到 `audio` 之前；`audio.depends_on` 增加 `types`（切段器实际读取题型）。
- `explanations.depends_on` 增加 `answers`（解析阶段实际读取答案）。
- `knowledge` 只依赖 `structure_gate / preassemble / types`，**只输出标签**；不再在导入时强制读取答案/解析。

### 2. 统一交接验收（新增 `app/workflow/handoff.py`）

- `inspect_stage()`：逐阶段复用消费者校验（题号/子题覆盖、目录叶子、Word 来源、切片 binding、最终批次 schema + label audit + 原卷来源审计、报告 sha 对应当前最终文件）。
- `require_stage()`：所有交付登记前必须通过。
- 依赖状态判定因此不再只看 `completed` 或“文件存在”。

### 3. 调度与状态（`auto.py`、`manifest.py`、`execution_state.py`、`agent_protocol.py`、`runner.py`）

- `manifest.dependency_satisfied(name, allow_unqualified=...)` 固定按严格交付验收判定：上游必须终态、无重建标记、无过期、且通过 `inspect_stage`。
- `execution_state.executed()` 不再把 `needs_review + continue_unqualified` 当作执行完成；投影只在自身与全部上游完成时计为 done。
- 删除零人工下“部分产物自动接受为 needs_review 并继续”的路径（auto 两处 + 增量补缺上限一处）。
- `auto` 并行批在启动前再次核对依赖并把未就绪阶段留给下一个拓扑轮次，避免“并行先行、交回才发现过期”。
- 解析/知识点任务导出前，`_collect` 不再注入答案/解析给知识点，知识点指纹移除答案依赖。
- `runner` 进入 `run` 前先 `reconcile_deliveries`；`validate` 不再回写批次产物（表格恢复前移到汇总）。
- `agent_protocol._preconditions` 使用完整上游集合与严格交付判定；未就绪直接 `blocked`，不导出任务。

### 4. 缺失语义与答案（`agent_tasks.py`、`empty_answers.py`、`optional_inputs.py`、`runner.py`）

- 答案契约改为 `{answers, evidence, missing}`：每个确认题号必须给原文答案或**带缺失证据的“无答案”决定**，二者不相交；答案必须逐字绑定答案材料。
- 无答案来源时由 `empty_answers.write_empty_answers` 生成覆盖全部确认题号的确定性空决定（结构未确认时不允许生成）。
- `has_answer_source()` 只在答案文件里出现明显答案行/答案标题时才认为存在答案材料，避免把“附录有正文”误判成有答案。

### 5. Word 权威来源与解析恢复（worker 完成，`upload_content.py`、`content_rules.py`）

- 新增 `restore_source_content`：仅当候选逐字匹配唯一的补充附件（OCR 文本）、其规范化形式又唯一对应 Word 权威片段时才恢复为 Word 原文；模型自行改标点仍被拒绝。
- 第 96 题类写作“思路点拨”只在权威来源之间计冲突，补充附件重复不制造伪冲突。
- strict 来源失败改为错误，不再同时输出“不阻断”警告。

### 6. 音频版本绑定（worker 完成，新增 `app/audio/binding.py`、`app/audio/consistency.py`）

- 切段只消费 `confirmed_units` + 校验通过的 `types`；binding 覆盖完整确认单元与题型/子类。
- 切片证据记录 `unit_binding` 与 `file_sha256`；`pair_is_consistent` 在题型/成员变更、覆盖缺失、文件变化时拒绝旧证据。
- `auto._auto_prepare_audio` 复用证据前先做一致性校验。

### 7. 自愈按真实生产者回退（worker 完成，`self_heal.py`）

- 以结构化错误字段/检查器映射实际生产者；未知错误不再盲退 `assemble`/`knowledge`。
- 依赖未满足视为等待，不写 `failed` / 假完成。

## 没改什么 / 边界

- 不自动上传/发布；`upload_gate/upload/publish_gate` 仍不自动执行。
- 不放宽质量与原文保真校验；`review_gate` 与 `format_gate` 语义不变。
- 2019 批次未重跑、未修改、未验证。

## 如何验证

- 全量工作流回归 **122 个测试模块全部通过**（`python3 -m unittest workflow.test_*` 逐模块，0 失败 0 错误）。
- 其中核心修复专属新增/更新覆盖：
  - `test_agent_tasks` / `test_content_labeling` / `test_knowledge_content_fallback`：答案逐题覆盖与缺失决定、知识点只交标签、Word 权威来源恢复、子题覆盖校验迁移到解析校验器。
  - `test_audio_segment` / `test_audio_boundary_truth` / `test_audio_stage_fastpath` / `test_auto_audio` / `test_paper_audio`：切片 binding、文件哈希、题型版本失效、组题父音频绑定、空结构安全降级。
  - `test_agent_protocol` / `test_runner_auto` / `test_stage_semantics` / `test_execution_state` / `test_delivery`：严格交付验收、needs_review 不再算完成、跳过阶段在输入变化后失效、依赖未满足只等待。
  - `test_self_heal_gaps` / `test_self_heal_runtime` / `test_self_heal_verification`：按结构化错误定位生产者、未知错误延后不盲退、质量阻断不放行。
- 后端 JS `node --test backend/*.test.cjs`：**110 项全部通过**。
- `node --check lib/index.js lib/client.js` 通过；`py_compile` 全部工作流/音频模块通过。

## 回退

依赖与验收均为新增/收紧，可回退为 `contracts.py` 原依赖 + 移除 `handoff` 调用；不涉及数据迁移。
