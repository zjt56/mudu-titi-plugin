# 录题流水线审查（2026-11-06）

审查对象：`HEAD 01f62ec`（包含 `d45b843` 的“模型优先/弱闸门”改造）。
审查方法：只读代码 + 对 `workflows/2026安徽中考听力材料` 实测；**以代码实际行为为准**，不以历史记忆或文档描述为准。

---

## 0. 结论摘要

### 与预期一致

1. 缺答案不再阻断：答案可以是空映射，只提取材料里真实存在的答案。当前批次已提取 1~80 题答案。
2. 缺解析不再阻断：没有原解析就填「无」，不生成、不追究。
3. 结构不再由正则硬切后“切不准就整段否定”：提取只保留文本/导语/材料，结构闸门对听力类批次把边界疑点降为警告。
4. 有阶段级轮询与增量补缺：上游完成后会触发下游；已完成的题目不会被整批重做（在增量逻辑生效的路径上）。

### 与预期不一致（会继续造成“每份卷子都卡”）

| 编号 | 问题 | 直接后果 |
| --- | --- | --- |
| A1 | `meta` 与 `underlines` 共用 `06_batch/batch_meta.json`，交付哈希每次轮询都比对 | 每次推进都可能把 `meta`→`validate`→`review` 整条链判失效，**无限循环** |
| A2 | 子类型缺失只在打标签入口放宽，`validate`/`review_gate` 仍用 `check_question_labels` 强制 | 你仍会看到“主类必须填写目录子类”的阻断，只是从 `knowledge` 挪到了体检/审核闸门 |
| A3 | 输入哈希不再参与失效判断，但成功执行阶段也不会自动失效下游 | 上游真变了，下游可能继续用旧产物（另一个方向的不一致） |
| A4 | 零人工模式把“闸门未通过”直接标成 `completed` | `validate`、`review_gate` 实际永不阻断；审核没做也能走到上传前 |
| A5 | `no answer/structure` 时 `assemble` 仍硬依赖 `02_audio/audio_map.json` 等全部上游产物 | 音频阶段“有候选但无正式产物”时，组装直接 `task not ready` 卡住 |
| A6 | 增量补缺与交付哈希互相冲突 | 补一次字段，可能触发下游全量失效；或反过来下游不更新 |
| A7 | `stage_gaps` 把“难度”当缺口 | 没有题干也要求补难度，白跑两次模型；计数器成功后不清零 |
| A8 | `assign_meta` 缺 `batch.json` 时写空 `batch_meta.json` 并返回成功 | 制造“假完成”，之后真批次出现又触发哈希失效 |
| A9 | `process()` 会扫描 `workflows/` 下所有已有批次 | 已完成批次每次 `auto run` 都被重新 reconcile，放大 A1 |
| A10 | `docs/pipeline.md` 与代码已经不一致 | 你看到的规则说明和实际行为对不上，正是“记忆混乱”的来源 |

**最可能的“每次上传都卡、下游全部重跑”的根因是 A1**：`meta` 和 `underlines` 同时声明产出 `06_batch/batch_meta.json`。`underlines` 改写这个文件后，下一次 `advance` 的 `reconcile_deliveries` 发现 `meta` 的产物哈希变了，就把 `meta` 判为 `stale` 并级联失效 `validate`、`review`、`meta` 重跑；`meta` 重跑又覆盖掉画线结果，`underlines` 再跑……如此往复。已用最小脚本复现：

```
invalidated: ['meta', 'validate']
{'meta': 'invalidated', 'underlines': 'invalidated', 'validate': 'invalidated', 'review': 'pending'}
```

---

## 1. 十六个阶段：实际做什么、产物、卡点

类型：`command` 跑脚本；`gate` 只读检查；`agent` 调模型/代理；`human` 人工。

| # | 阶段 | 实际动作 | 产物 | 硬卡点 |
| --- | --- | --- | --- | --- |
| 1 | `extract` | 读原件；DOCX 原生/PDF 逐页/图片 OCR；去噪、提取元信息、切大题；材料分类（每份上传文件一份文字层）；答案文件单独提取 | `01_source/正文.txt`、`structure.json`、`extraction.json`、`metadata.json`、`07_review/extraction-report.json`、`01_source/materials/*.txt` | 原件缺失；正文为空；OCR 未配置（图片/扫描件） |
| 2 | `structure_gate` | 先跑结构 AI 复核（可选）；再确定性结构化：去噪、元信息、题号/题组、导语、覆盖自检 | `structure.confirmed.json`、`structure-gate.json` | 正文空、无 sections、题号重复/未覆盖、原文题号重复、题组越界、切片残留卷头。**含听力的大题会把计数不符、原文未定位题号、单元空、导语不一致降为警告** |
| 3 | `answers` | 提取答案卷全文（`01_source_ans/正文.txt`），模型输出答案/证据/难度 | `01_source_ans/answers.json` | 答案值必须逐字出现在答案材料中；难度只能覆盖已答题目。允许空答案；允许 1~80 等超出当前结构的题号 |
| 4 | `audio` | 自动切段（WhisperX）→ `segment-evidence.json` → 模型映射 `audio_map.json` | `02_audio/audio_map.json` | 未 verified 的切片；无已切音频；切段运行时未安装；音频缺失则跳过 |
| 5 | `types` | 模型判主类/子类、词数、内容完整性 | `03_types/types.json` | 必须精确覆盖全部单元；主类必须是目录值；`words`/`contentComplete` 必须存在；不允许额外字段。**子类缺失在模型优先模式下只记 warning** |
| 6 | `explanations` | 模型逐单元提取原解析/文章大意；没有填「无」 | `04_knowledge/explanations.json` | 覆盖率；解析必须能在材料中逐字找到（占位「无」除外）；证据非空 |
| 7 | `knowledge` | 模型打知识点（目录叶子），解析/答案由导入器注入 | `04_knowledge/knowledge.json` | 覆盖率；知识点必须是合法叶子；整组题型的子题 knowledge 必须为空；子题号必须精确匹配；解析/答案字段不得与上游不一致 |
| 8 | `assemble` | 模型合并结构+答案+解析+知识点+题型为正式批次；导入前做来源保真审计、标签审计、5 项格式预检 | `06_batch/batch.json` | **依赖全部上游产物存在**（含 `audio_map.json`）；单元/题号/分组必须与确认结构一致；来源切片保真（题干/材料/选项/表格/画线/图片）；标签合法；格式预检 5 脚本 |
| 9 | `meta` | 按特征补难度与分类；已有模型难度则保留 | `06_batch/batch_meta.json` | 正常无阻断；缺 `batch.json` 时写空占位并返回成功（见 A8） |
| 10 | `underlines` | 从 DOCX 字符格式补 `<u>`，**原地改写 `batch_meta.json`** | `06_batch/batch_meta.json`（同一文件） | 非 docx 跳过；画线映射缺失则失败。与 `meta` 共享产物 → 见 A1 |
| 11 | `validate` | 18 项本地脚本体检 + 原卷逐题来源审计 | `07_review/validation-report.json` | 除 `check_text`、`check_fidelity` 外均阻断；`batch_meta.json` 必须存在；来源审计必须通过 |
| 12 | `review` | 一审全量 + 二审抽 20%（整组题型整组抽）；两轮必须是不同执行、绑定同一输入计划、逐目标有判断/理由/原文引证 | `review-rounds.json`、`content-review.json`、`metadata-review.json`、`manual-review-queue.json`、`unchecked.json` | 缺轮次/绑定不符/证据引不到原文/覆盖不全 → `REVIEW_ROUND_UNCHECKED`；差异 → `REVIEW_ROUND_FINDING`；任一未解决都阻断闸门 |
| 13 | `review_gate` | 汇总阻断发现、人工队列、必检未完成、标签体检、流程图检查 | `07_review/review-gate.json` | 任一非空即不通过。零人工模式下该结果被直接标 `completed`（见 A4） |
| 14 | `upload_gate` | 人工确认上传目标与冻结请求 | 台账 | 必须人工；零人工模式标 `skipped` |
| 15 | `upload` | 提交冻结请求到远端暂存 | `07_upload/receipt.json` | 需批准+冻结；零人工模式 `skipped` |
| 16 | `publish_gate` | 人工发布 | 台账 | 永远人工；零人工模式 `skipped` |

---

## 2. 阀门清单（哪些真的会挡）

### 2.1 会挡住的硬闸门

- `extract`：命令退出码非 0；退出 0 但缺声明产物。
- `structure_gate`：`SOURCE_EMPTY`、`NO_SECTIONS`、`DUPLICATE_NUMBERS`、`MISSING_NUMBERS`（覆盖）、`SOURCE_DUPLICATE_NUMBERS`、`GROUP_OUT_OF_SECTION`、`GROUP_CROSSES_SECTION`、`RESIDUAL_HEADER`、未证明的 `QUESTION_NUMBER_INFERRED`。
- `answers`：答案值不是材料原文、题号非数字、难度覆盖了未答题目。
- `types`：缺单元、主类非法、缺 `words`/`contentComplete`、额外字段。
- `explanations`：缺单元、解析不是原文且非「无」、证据为空。
- `knowledge`：缺单元、知识点非叶子、子题覆盖不符、改动解析/答案。
- `assemble`：上游产物缺失、结构不一致、来源保真失败、标签非法、5 项格式预检失败。
- `validate`：16 项阻断脚本任一失败；来源审计失败。
- `review`：轮次缺失/过期/证据不可核。
- `review_gate`：任何未解决发现/人工项/未检项。

### 2.2 只在“严格”路径阻断、在模型优先路径降为警告

- 题型子类缺失（`agent_tasks.py:560`，`WF_MODEL_FIRST=0` 时才抛错）。
- 知识点阶段重复的题型子类校验（`agent_tasks.py:866`，同上）。
- 结构闸门中的计数/覆盖/空单元/导语不一致（`structure_gate.py:389` 起，条件是大题里出现“听力”）。

### 2.3 只记录、不阻断

- 来源字段缺失 → `未提供` 占位。
- 图片/音频能力受限 → informational。
- `meta` 缺 `batch.json` → 空占位 + 成功。
- 零人工模式下：代理 `needs_review`（且全部声明产物都在）、所有 gate 未通过、人工闸门。

### 2.4 关键开关

- `WF_ZERO_TOUCH`（默认 `1`）：自动推进时把 `needs_review`/闸门失败/人工闸门统一转成继续。
- `WF_MODEL_FIRST`（默认 `1`）：打标签阶段的目录/子类问题转 warning；部分结果可导入。
- 两者独立。当前默认组合 = “全程不挡”。

---

## 3. 失效、交付、轮询、增量

### 3.1 失效判定（`manifest.py:342`）

一个已完成的阶段只在三种情况被认为过期：

1. 自身声明产物缺失；
2. 自身产物哈希与记录不符；
3. 被显式 `invalidate`，或上游被显式 `invalidate_downstream`。

**输入哈希已不再参与判断**。这样确实不会再因为“上游多了一条 warning”就全量重跑；代价是：如果上游产物真的变了，下游不会自动失效（A3）。

### 3.2 交付清单（`manifest.py:218`）

`workflow/delivery.json` 记录每个阶段的交付状态、产物路径/哈希、被谁消费。每次 `advance` 开头都会 `reconcile_deliveries`：

- 产物缺失/哈希不符 → 该阶段 `invalidated`，并级联失效全部下游；
- 否则标 `delivered`（历史批次会补登一次哈希）。

问题在第 1 条：它比对的是“manifest 里记录的产物哈希”，而 `meta`/`underlines` 共用一个文件（A1）。

### 3.3 轮询（`auto.py:776`）

- `poll_batch`：反复 `advance`，直到所有阶段终态，或达到 `--max-polls`。
- `auto run` 现在还会扫描 `workflows/` 下已有批次（`auto.py:816`），不只收件箱。
- 每轮都会重读 manifest、重新 reconcile、再按拓扑触发第一个未完成阶段。

### 3.4 增量补缺（`agent_tasks.py:125`、`auto.py:423`）

- `stage_gaps` 计算：答案缺哪些题/难度、题型缺哪些单元、解析缺哪些单元、知识点缺哪些单元、组装缺不缺文件。
- 已完成但仍有缺口 → 把该阶段改回 `pending`，最多 2 次；下游不主动失效。
- 模型上下文里会带 `incremental_gaps`，要求只补缺口。

现存问题：

- `answers` 的“难度缺口”在没有任何题干时也一直存在 → 每次都会补跑两次（A7）。
- `incremental_retries` 成功后从不清零，后续真正需要补缺时会被上限挡住。
- 增量补出的新产物如果改变哈希，下一轮 `reconcile` 又会级联失效下游（A6）。

---

## 4. 与预期不一致的逐项说明

### A1（最高优先级）共享产物导致无限失效

- 证据：`contracts.py:117` 与 `contracts.py:129` 都声明 `06_batch/batch_meta.json`；`manifest.py:238-248` 比对产物哈希；`manifest.py:261-267` 级联失效。
- 后果：每次 `advance` 都可能出现“上游已变化，本阶段产物失效，需重跑”（`diagnose.py:188` 的文案），并且 `meta` 重跑会覆盖画线、`underlines` 再补，循环。
- 建议：`meta` 声明自己的基线产物（如 `batch_meta.json` 的输入 `batch.json` 哈希），或让 `underlines` 产出独立文件/独立标记；reconcile 只对“本阶段独立产物”做哈希比对。

### A2 放宽不一致：入口松、出口紧

- 证据：`agent_tasks.py:560`/`:866` 在模型优先时只记 warning；但 `runner.py:586-590` 的 `label_audit.inspect_file` → `label_audit.py:27` → `taxonomy.check_question_labels`（`taxonomy.py:391`）仍然要求“主类必须填写目录子类”。
- 后果：用户仍会在 `validate`/`review_gate` 看到同样的错误，只是晚了几步。
- 建议：要么统一在 `check_question_labels` 增加“模型优先/缺失允许”参数，要么在打标签阶段就补齐子类（用模型兜底），不要两套口径。

### A3 输入变化不再失效

- 证据：`manifest.py:342-364` 只检查自身产物；`runner.py:965` 只有在 `is_stale` 为真时才 `invalidate_downstream`。
- 后果：重新 `extract` 或重新 `structure_gate` 后，下游可能继续用旧 `confirmed`/旧类型/旧批次。
- 建议：区分“交付级输入”（结构、答案、类型、批次）与“提示级输入”（warning、证据文本）。前者变化时按题号范围局部失效，后者不失效。

### A4 零人工把闸门失败标成完成

- 证据：`auto.py:654-664`：`if ZERO_TOUCH:` 直接 `touch_stage(..., COMPLETED)`。
- 后果：`validate` 报告写着失败、`review_gate` 报告写着阻断，但台账显示完成；最终可到 `upload_gate`（然后被跳过）。
- 建议：闸门未通过应标 `skipped` 或 `needs_review` 并保留报告，不要标 `completed`；否则“是否体检/审核过”无法从台账判断。

### A5 组装对上游产物是硬依赖

- 证据：`agent_tasks.py` 的 `_collect` 对 `assemble` 要求 `types`、`knowledge`、`explanations`、`audio_map`、正文、`structure.json`、`confirmed` 都存在；`merge` 阶段 `build_task` 因此返回 `needs_review`，导出即失败。
- 后果：音频只有候选切片、没有正式 `audio_map.json` 时，组装不会开始，整批停住。
- 建议：把 `audio_map` 也纳入“可选交接”：缺失时组装照常产出，听力单元挂空音频并记 warning。

### A6 增量与交付哈希冲突

- 证据：增量重跑成功后 `_record_success` 更新 `output_hashes`，但下游已完成的记录不变；若下游被 `reconcile` 判失效，则全量重跑。
- 后果：要么下游旧、要么下游全重跑，没有“按题号合并”的中间态。
- 建议：交付记录里保存“按题号的产物指纹”，增量只更新受影响题号的指纹，reconcile 按题号判断，而不是整文件哈希。

### A7 增量缺口定义过宽

- 证据：`agent_tasks.py:125-145` 把 `difficulty` 全缺算缺口；`auto.py:423-447` 最多重试 2 次。
- 后果：只有听力稿、没有题干的卷子，答案阶段会白跑两次难度判定；`incremental_retries` 不清零。
- 建议：难度只在“有题干/选项”的单元上算缺口；成功后把计数清零。

### A8 `meta` 空占位造成假完成

- 证据：`assign_meta.py:121-130` 缺 `batch.json` 时写 `{"items":[],"warnings":[...]}` 并 `return 0`。
- 后果：`meta` 标 `completed`；之后 `assemble` 产出真批次，`meta` 产物哈希变化 → A1 式级联。
- 建议：没有 `batch.json` 时返回非 0 让阶段标 `skipped`，不要写空产物。

### A9 每次 `auto run` 重扫旧批次

- 证据：`auto.py:816` 扫描 `workflows/` 下所有含 manifest 的目录并 `advance`。
- 后果：已完成批次也会被 reconcile；与 A1 叠加时，每次启动都在“失效—重跑”之间循环。
- 建议：只轮询“未完成或被显式标记待推进”的批次；已完成批次跳过，除非 `--force`。

### A10 文档与代码不一致

`docs/pipeline.md` 仍写着：

- `explanations` 依赖 `types`、`answers`；`knowledge` 依赖 `types`、`answers`、`explanations`；`assemble` 依赖 `audio`、`types`、`knowledge`（代码已改为：解析/知识点只依赖 `types`；组装依赖 `audio`、`types`、`knowledge`、`explanations`）。
- 结构闸门“任一失败即 blocked”（代码对含听力批次大量降级为 warning）。
- 状态表“`invalidated` 必须处理后才能继续”（零人工模式会继续）。
- `validate` “18 个配置项”（代码 `checks.py` 当前 18 项，但语义已变）。

建议：文档顶部加“以 `contracts.py` 与本文审查为准”，并尽快同步这三处。

---

## 5. 需要你拍板的 5 个政策

| 编号 | 决策 | 选项 |
| --- | --- | --- |
| D1 | 总策略 | (a) 全程模型优先、闸门只记录（当前默认） (b) 分阶段：打标签宽松、体检/审核严格 (c) 全部严格 |
| D2 | 结构闸门放宽范围 | (a) 仅“纯听力材料”批次放宽 (b) 任何含听力的整卷都放宽（当前） (c) 只放宽题号边界，不放宽覆盖/空单元 |
| D3 | 审核网关 | (a) 允许零人工完成（当前） (b) 只允许“能力受限”不阻断，真实差异仍阻断 (c) 必须两轮真实审核才放行 |
| D4 | `batch_meta.json` | (a) 维持 `meta`/`underlines` 共用一个文件 (b) `underlines` 独立产物 (c) `meta` 记录基线哈希，reconcile 忽略下游改写 |
| D5 | 上游变化 | (a) 不失效（当前） (b) 整阶段失效 (c) 按题号范围局部失效（推荐） |

---

## 6. 建议的收敛顺序

1. 先修 A1（共享产物 + reconcile），否则后面所有“重跑/卡住”都会反复出现。
2. 再统一 A2（入口与出口同一套标签规则），否则“放宽”只是把卡点后移。
3. 明确 D3：零人工是否允许“未审核就完成”；这决定 A4 是修还是保留。
4. 修 A5（组装允许缺 `audio_map`）与 A7/A8（避免白跑和假完成）。
5. 最后同步 A10 文档，避免规则记忆继续分叉。

在 A1/A2 未修之前，不建议继续增加新的自动放行规则——否则“哪些问题真的被检查过”将无法从台账回答。
