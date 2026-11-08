# 流水线：18 个阶段与自动驱动

这篇管"一批卷子从原件到待上传草稿"的推进方式：有哪些阶段、每个阶段产出什么、自动执行器在哪里停下，以及哪些动作永远不允许自动化。

## 18 个阶段

阶段定义是封闭契约，写在 `workflow/contracts.py` 的 `STAGES` 里，顺序、依赖、产物都不可临时增删。类型只有四种：`agent`（由执行者或代理完成）、`command`（跑脚本）、`gate`（机器闸门，只能由检查结果放行）、`human`（人工确认，只能由人标记）。

| # | id | 做什么 | 产出 | 类型 | 依赖 | 停点 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `extract` | 来源、年份、地区、来源类型与原卷复合提取 | `01_source/metadata.json`、正文、结构与提取证据 | command | 原件（必有） | 确定性执行器 |
| 2 | `structure_gate` | 对照原卷逐题核题号、选项、材料边界和真实对话分组 | `01_source/structure.confirmed.json`、`07_review/structure-gate.json` | gate | `extract` | 证据不足则阻断并准备版面复核 |
| 3 | `preassemble` | 前置组装上传字段形状，立即建立同一批次 | `06_batch/batch.base.json`、可逐步填充的 `06_batch/batch.json` | command | `structure_gate` | 未判断标签留空；来源必须可回指 |
| 4 | `answers` | 逐题填入原答案或带来源证据的「无答案」决定 | `01_source_ans/answers.json` | agent | `preassemble`、结构确认 | 未逐题决定不能交付 |
| 6 | `audio` | 依据已验证题型切听力并填媒体引用 | `02_audio/audio_map.json` | agent | `structure_gate`、`preassemble`、`types` | 单元/类型绑定过期即拒绝；无音频明确跳过 |
| 5 | `types` | 填目录题型、子类、词数并确认原文完整性 | `03_types/types.json` | agent | `preassemble`、结构确认 | `contentComplete=false` 不交付 |
| 7 | `explanations` | 按Word原文逐字匹配解析/大意，确认缺失填占位 | `04_knowledge/explanations.json` | agent | `types`、`answers` | 来源唯一且精确绑定；不生成 |
| 8 | `knowledge` | 仅填知识点叶子/ID与适用教材映射 | `04_knowledge/knowledge.json` | agent | `types` | 只含标签；不读取答案或解析 |
| 9 | `assemble` | 确定性合流，按原题身份合入并校验全部上游数据 | `06_batch/batch.json` | agent/command | `answers`、`types`、`audio`（或有效跳过）、`explanations`、`knowledge` | 任一上游未交付/不合规/过期即等待 |
| 10 | `meta` | 归一难度与分类，并逐字段回填同批次 | `06_batch/batch_meta.base.json` | command | `assemble` | 不改题文与知识标签 |
| 11 | `underlines` | 补画线并生成最终上传序列化视图 | `06_batch/batch_meta.json` | command | `meta` | 无 DOCX 保留原有内容 |
| 12 | `validate` | 本地体检与原卷逐题对账 | `07_review/validation-report.json` | gate | `underlines` | 失败保留质量问题 |
| 13 | `review` | 两轮内容与知识点审核 | 分轮记录与差异队列 | agent | `validate` | 复用未变单元 |
| 14 | `review_gate` | 内容审核结果归集 | `07_review/review-gate.json` | gate | `review` | 质量结论如实保留 |
| 15 | `format_gate` | 最终本地上传字段/格式检查 | `07_review/upload-format.json` | command | `review_gate` | 无模型、无网络；错误不伪称通过 |
| 16 | `upload_gate` | 上传确认 | 台账与冻结绑定 | human | `format_gate` | 专用上传入口 |
| 17 | `upload` | 写入远端上传暂存 | `07_upload/receipt.json` | command | `upload_gate` | 严格冻结请求与远端验证 |
| 18 | `publish_gate` | 发布确认 | 台账 | human | `upload` | 发布仍走专用入口 |

要点：

- `preassemble` 即建立真实批次；后续步骤按字段填充。`meta`/`underlines` 生成同一批题目的归一化上传视图，体检和最终格式检查读取该视图。
- `validate` 运行目录、排版、表格与源头逐题对账；不能调低原文保真检查换取通过。
- 审核后新增 `format_gate`，只做本地上传格式检查，不触发模型。上传队列仅在当前最终文件通过该检查后结束；二审完成不再提前移出队列。
- [上传 API 对接](upload-api.md)仍在真实提交前校验实时远端目录、媒体和冻结请求；本地格式通过不等同远端提交批准或发布。普通 run/auto 不自动提交远端。


调度只并发**依赖已经交付且消费者校验通过**的阶段。第一波答案与题型可并行；音频必须等待题型完成，并将题型/结构摘要绑定到切片证据；下一波解析（依赖题型、答案）与知识点（仅依赖题型、只输出标签）可并行。答案、解析与知识标签由 `assemble` 唯一确定性合并，合并成功后才允许 meta 等下游读取。

## 结构归类与确认（第 2 阶段）

`extract` 只负责保留原始文字、版面、图片、表格和页/行证据；题号、题干、材料、选项、答案/解析归属和题组边界由结构 AI 主路径判断。执行 `run`/`auto run` 时，`workflow/structure_ai.py` 先生成带原文行号与 `source_span` 的候选结构，再由 `workflow/structure_gate.py` 做安全校验和证据留痕。

确定性规则只作为证据整理和模型不可用时的保底视图，不再把结构猜测本身当作题目语义结论。模型输出不满足题号覆盖、原文连续片段或边界校验时丢弃候选，保留原始提取结果并把原因写入报告；不得凭确定性切题结果伪造题干或选项。

`structure_gate` 是自动机器闸门，不需要人工点击确认，但结构候选可交付不代表题干/选项完整，也不代表后续质量通过。

执行 `run`/`auto run` 时由 `workflow/structure_gate.py` 自动完成：

1. **去噪**：`workflow/denoise.py` 把广告、扫码、水印、页眉 logo、分隔线、页码等与题目无关的
   行整行剔除，并记入 `structure-gate.json` 的 `removed_count` 供审计，不静默删除。
2. **元信息抽取**：卷名、年份、地区、考试时长、总分、整卷作答说明抽到 `meta` 字段
   （`structure.confirmed.json` 的 `meta`），题目正文里一律不再保留。题库入库时这些值走专门字段。
3. **按题型结构化**：独立题（单项填空、单词拼写、完成句子等）一题一单元逐题呈现；组合题
   （完形、阅读、任务型阅读、听力信息转换、补全对话等）以题组为一个单元整体呈现，共享材料只出现一次。
4. **解析版答案分离**：正文里夹带的答案、解析先由确定性规则识别（同题号紧邻短答案、
   `【解析】/【答案】` 行、题号区间答案汇总、`参考答案/答案与解析` 标题后的整段答案等），
   移入附录；规则拿不准的重复题号再由 `workflow/structure_ai.py` 交给模型判断：
   逐题混排时返回具体答案行，整卷后附答案时返回连续答案区间。输出必须逐字引用原行、
   编号必须在前面出现过、区间内不能有问句/选项/填空行；校验通过后带 `skip_lines`
   重跑确定性结构化，并记录到 `01_source/structure.ai.json`。模型不可用、输出无法校验
   或没有重复题号时不调用，闸门仍按原规则运行，绝不因 AI 静默放行。
5. **机器自检**：`source_present`、`sections_present`、`coverage`（题号全覆盖无重复）、`denoise`
   （切片不残留卷头）、`grouping`（题组题号属于合法大题）。任一失败即 `blocked`，列出阻断码
   （`SOURCE_EMPTY`、`NO_SECTIONS`、`MISSING_NUMBERS`、`DUPLICATE_NUMBERS`、`RESIDUAL_HEADER`、
   `GROUP_OUT_OF_SECTION`）。

通过即写 `01_source/structure.confirmed.json` 并进入题型判定；不通过停下等人工或重跑，
**不会**因题号归属、图片理解的语义歧义被机器静默放行——机器拿不准的会以阻断码暴露出来。

## 卡点分类与前置预检

工作台诊断会为每个自身卡点附带 `failure_class` 与 `next_action`，把“代理输出格式失败、输入过期、确定性闸门失败、审核质量差异、缺必需输入、等待上游”分开显示。下游仅等待上游时不再伪装成新的失败。

`validate` 阶段会额外写入 `07_upload/local-preflight.json`：这是离线本地预检，不访问远端、不冻结文件、不批准上传，但会提前检查最终批次、标签目录、解析来源、媒体引用和 payload 大小。远端目录、媒体可访问性、冻结文件一致性仍只由 `prepare-upload`/`verify` 在上传前复核。

规则文件按阶段分层绑定：解析/知识点/审核只绑定自身语义规则；装配版式或上传约束变化不会让无关阶段的模型任务无条件过期。真正相关的输入、产物和目录变化仍会使阶段失效并要求重跑。

## 前置标准题目对象与局部更新

结构确认后立即执行独立 `preassemble`：从 Word 原文和确认结构生成源对象，再真正写入 `06_batch/batch.json`，不等标签完成。每个独立题/题组使用稳定 `unit_id`，题干、选项、共享材料、表格、子题、媒体按最终字段形状前置组装。`batch.base.json` 是不随标签填充变化的来源基线，`workflow/batch-fill-state.json` 记录各字段填充进度；这些都不冒充审核通过。

答案、题型、知识点、解析/大意、媒体和元信息产物被校验接受后，按唯一题号/单元逐字段回填同一批次。未判断字段保持空；确认原件没有相应内容后仍沿用既有「无」「未提供」等占位规则，有原文则逐字填写，不生成解析或大意。OCR 只作版面辅助，不覆盖 Word 原文。Word 为主卷时，配套 PDF 明确登记为 `structure_reference`，只帮助判断题号、题型和层级；可读页使用原生文字层，不强制整卷重做 OCR。PDF 辅助获取失败只保留诊断，不把已成功的 Word 提取判成失败。同题解析以 Word 正文/附录为准，PDF OCR 的重复内容不制造“来源不唯一”冲突。

执行顺序为：复合提取 → 确认题目结构 → 前置批次组装 → 答案/音频/题型填充 → 解析与知识点填充 → 汇总固化和元信息补全 → 体检与内容审核 → 最终上传格式检查。答案、音频、题型可在前置组装后独立推进。

标签模型默认以一题或一个题组为一片（`label_part_units=1`），与审核分片大小独立。可以唯一回指当前原文且模型身份可观察时，按本片题文、对应标签/答案、规则目录和模型配置复用快照；在途任务仍核对全部原始输入。托管批次后置 `assemble` 仅对同一批对象汇总固化并走原有整批保真校验；发生冲突时保留现有题目对象等待局部修正，不回退模型整批重组。没有托管状态的历史批次保留原有装配路径。来源元数据及报告簿记变化只做确定性字段合并。

自动恢复采用两层指纹：任务导入和上传冻结继续校验原始字节；完成结果复用比较该阶段真正消费的内容。JSON 缩进、键顺序和生成时间不使内容失效。词数、难度变化不重新打知识点；题型/子类、题干、选项或答案真实变化才更新相关工作。原始标签难度保留为首轮结果，不成为审核完成前提。

修复阶段时先只重排当前阶段，保留其它产物；它交付后，再逐个检查消费者是否真的接收了变化。未修好的上游会阻止依赖任务提交，但不会先把整条链的已完成结果作废。审核按独立题/共享材料题组保存语义摘要和真实执行来源：题干、选项、答案、解析、知识点或相关媒体改变时补对应单元；共享材料改变补对应题组；来源、难度、分类和报告时间变化不补审。无法可靠限定范围的附录或媒体变化仍保守扩大范围。

显式 `invalidate` 仍代表要求整链重置；正常恢复不再使用该操作。旧记录只有原始摘要时，先按旧摘要验证；没有可证明的逐题审核来源时不能直接认定旧记录有效。上传批准仍绑定最终文件的精确摘要，内容冻结保护不因缓存复用而放松。

## 持续自动修复与执行验证

真实工作台服务启动时会启动 `workflow.supervisor.WorkflowSupervisor`，默认每 30 秒检查活动批次，服务退出时停止；只导入模块不会启动线程。监督器只扫描活动 `workflows`，不恢复归档，也不调用上传或发布。每批 `workflow/supervisor.json` 保存重试时间和无进展预算，重启后继续；不再依赖短暂的 7 轮巡检或用户反复点继续。执行中的批次/模型/CLI 锁不会被重复接管。

完整路径是：检测失败或无进展 → 当前配置模型根据诊断、错误日志、相关原文与尝试历史选择动作 → 受限本地执行器应用 → 重新校验 → 继续原有流水线。`self_heal` 使用显式 Stage 标记走当前模型路由，不静默换模型。限流冷却在模型诊断前检查；暂时服务失败会退避后再试，同错长期无进展进入长冷却并再次探测，不会无限高频调用或永久丢下任务。

实际动作包含：有原文证明的上传格式修复、源表格恢复、已登记文件状态更新、当前阶段重试，以及携带真实错误反馈的受影响题目重试。真实修复会备份、守卫原始输入、原子安装并读回校验，失败回滚；不编造缺失原文、答案或解析，也不把质量失败改成通过。纯格式等价修复可重用已实际通过的原验证与审核证据，仍独立复检来源和上传形状；内容恢复引入变化则重新执行相应检查。

`workflow/repair-feedback/<stage>.json` 的严格反馈只进入对应阶段和受影响分片。其它题目的精确提示与有效缓存保留；在途任务仍校验全部原始输入。`workflow/self-heal.json` 区分 `retry_scheduled`、`repair_applied`、`retry_verified`、`retry_failed`，排队不冒充修复成功；小型 `self-heal-status.json` 用于界面显示“AI 判断／局部修复／校验／稍后重试”，不新增主流程节点。后台 `/api/health` 返回实际监督线程存活与最近巡检时间。

## 阶段状态

台账里每个阶段只有九种状态：

| 状态 | 含义 |
| --- | --- |
| `pending` | 未开始 |
| `running` | 正在跑 |
| `completed` | 完成且产物已记录哈希 |
| `approved` | 人工确认通过 |
| `needs_review` | 需要人工再看 |
| `blocked` | 被阻断，等外部条件 |
| `failed` | 执行失败 |
| `invalidated` | 当前阶段实际消费内容或交付发生变化，按范围更新 |
| `skipped` | 按规则跳过（如没有音频） |

只有 `completed`、`approved`、`skipped` 算完成。`failed`、`blocked`、`invalidated` 都必须处理后才能继续。

## 批次目录

每次通过上传界面或快捷命令创建独立批次：原始附件、提取文本、代理结果、校验报告和交付记录都保存在该批次目录中。收件箱扫描创建的新批次也会复制附件到批次目录；历史批次继续使用已有路径，不自动移动。新批次同名时自动加后缀。多个音频均会保存并登记；系统分别转写并依据完整、唯一且顺序正确的题号证据对应题组，分别切段后汇总。证据不足、题组对应冲突或转写失败时保留待处理状态，不猜测对应关系。不支持的格式会明确拒绝。

```text
workflows/<批次>/
├─ attachments/              # 原卷、配套文档、音频及上传描述
│  ├─ source.docx / source.pdf
│  ├─ answers.docx / answers.pdf（可选）
│  ├─ attachment_001.docx / .pdf（可选，其他配套文档）
│  ├─ audio.mp3（可选，或其他支持的音频格式）
│  └─ batch.json             # 原始文件名、类型及上传信息
├─ workflow/
│  ├─ manifest.json          # 台账：阶段、哈希、人工决策
│  ├─ run-log.jsonl          # 执行日志
│  ├─ decisions.jsonl        # 人工决策审计
│  └─ logs/                  # meta.log、upload.log
├─ 01_source/
│  ├─ 正文.txt
│  ├─ structure.json          # 结构候选
│  ├─ structure.confirmed.json# 自动结构化的确认视图（去噪/元信息/单元）
│  └─ inventory.json          # 题号清点
├─ 01_source_ans/answers.json
├─ 02_audio/audio_map.json
├─ 03_types/types.json
├─ 04_knowledge/knowledge.json
├─ 06_batch/
│  ├─ batch.json              # 组装的中间批次
│  └─ batch_meta.json         # 加完难度分类的最终校验对象
├─ 07_review/
│  ├─ validation-report.json
│  ├─ content-review.json
│  ├─ metadata-review.json
│  ├─ manual-review-queue.json
│  ├─ unchecked.json
│  ├─ review-gate.json
│  ├─ decisions.jsonl
│  ├─ report.json / report.md / notification.txt
│  └─ independent-plan.json / independent-records.json
├─ 07_upload/receipt.json
├─ candidates/units/<unit_id>.json   # 审核台写回的单元级标签
└─ media_upload/                     # 待上传媒体
```

没有 `05_` 段位：难度与分类写进 `06_batch/batch_meta.json`，不单独占一级目录。

## 自动执行器的行为

`python -m workflow.auto run` 是一个"扫一遍、推一步、记下来"的循环，不做业务判断。

### 扫描（scan）

1. 遍历 `inbox/` 下每个子目录。
2. 若有 `batch.json`，按其中的 `workflow_id`、`year`、`region`、`subject`、`source`、`answers`、`audio`、`docx` 初始化。
3. 没有 `batch.json` 时按后缀猜：`.docx`/`.doc`/`.pdf` 为原卷；文件名含"答案/answer/key/解析"的判为答案卷；`.mp3`/`.wav`/`.m4a`/`.aac` 为音频。
4. 目标批次没有 `workflow/manifest.json` 时，调用 `runner` 的初始化流程登记输入。

### 推进（run）

按 `STAGES` 顺序逐个看，遇到第一个"过不去"的阶段就停下，把原因写进队列：

| 情况 | 停下的原因文案 | `action` |
| --- | --- | --- |
| 上游还没完成 | `等待上游：<stage>` | `agent` |
| 人工闸门 | `等待人工确认（<标题>）` | `human` |
| 自定义桥未配置当前代理阶段 | `等待代理执行（<标题>）` | `agent` |
| 通用任务等待接单或代理需要补充证据 | `代理任务等待接单、补充证据或处理` | `review` |
| 代理桥命令失败 | `代理桥执行失败` | `failed` |
| 普通命令失败 | `脚本执行失败` | `failed` |
| 机器闸门没通过 | `闸门未通过` | `gate` |
| 上传阶段 | `上传须人工通过专用命令显式确认` | `human` |

已经在终态的阶段直接跳过；`invalidated` 的阶段会被重新执行。

### 已交付产物的受控复用

阶段产物因为“表现层改写”被标成过期时（例如空档写法、表格 variant、换行归一化改变了 `06_batch/batch.base.json`，而 Word 原文与所有原始证据文件逐字节未变），重新调用模型只会得到同一批标签。`python -m workflow.redeliver --batch-dir <批次> --stage <阶段...>` 提供一条受控的替代路径：

1. 只接受填充阶段（`audio`/`types`/`explanations`/`knowledge`），终态阶段原样返回。
2. 产物必须与交付时记录的哈希一致；交付后被改动则拒绝。
3. 记录的全部原始输入必须仍一致，只允许 `06_batch/` 表现层对象变化；任何正文、附录、结构、题型、材料或音频字节变化都会拒绝。
4. 托管的逐题来源绑定（Word 片段哈希与媒体哈希）必须与当前构建完全相同。
5. 产物先通过该阶段现有的完整校验，才登记交付。

全部条件都是确定性的、只读的；登记仍走正常的 `record_delivery`，并在 `workflow/redeliver-log.jsonl` 留下证据。它不是审核、质量通过、上传或发布，也不改变原文。--dry-run 只报告能否复用。

不满足任一条件时命令拒绝并给出具体原因，此时必须真正重跑该阶段。

### 队列文件 `_queue.json`

每轮结束写 `workflows/_queue.json`，供人和审核台看"现在卡在哪"：

```json
{
  "generated_at": "2026-01-01T12:00:00",
  "batches": [
    {
      "batch": "某批次",
      "batch_dir": "workflows/某批次",
      "status": "reviewing",
      "stopped_at": "review_gate",
      "action": "gate",
      "reason": "闸门未通过",
      "report": "07_review/report.json",
      "digest": "无误 40 / 有疑问 3 / 阻断 1 / 未核对 2"
    }
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `batch` | 批次名（目录名） |
| `batch_dir` | 批次绝对或相对路径 |
| `status` | 批次当前总状态 |
| `stopped_at` | 停在哪一个阶段 |
| `action` | 停下的类别：`human` / `agent` / `gate` / `failed` |
| `reason` | 给人看的原话 |
| `report` | 通报文件路径（可选） |
| `digest` | 四态统计一行话（可选） |
| `report_error` / `error` | 生成通报或推进时的异常（可选） |

## 代理桥 `workflow/agent-bridge.json`

`agent` 阶段默认使用[通用 Agent 接口](agent-interface.md)，无需指定厂商的 CLI。没有本机配置时导出任务包并等待接单；Agent 交回结果后，由导入器校验、保存产物并登记台账。有本机 CLI 配置时，同一入口使用任意命令或 DSH/Codex 的可选适配器。

桥文件可覆盖默认阶段调用，是阶段 id 到命令参数数组的映射，例如：

```json
{
  "types": ["{python}", "-m", "workflow.agent", "run", "--stage", "types", "--batch-dir", "{batch_dir}"]
}
```

规则：

- 桥命令只接受非空数组，不作为 shell 字符串执行。`{python}` 使用工作流当前解释器。
- 自动调用不替 Agent 批准机器或人工闸门；文件交接仅允许导入器登记通过校验的代理阶段产物。
- 退出码 0 仍须有全部声明产物才完成；代理退出码 3 表示等待接单或复核，其他失败如实记录。
- 没有桥文件时使用通用默认入口，不默认查找 DSH。自定义桥明确遗漏某个阶段时，保留等待代理行为。
- 原卷 `extract` 已有复合提取执行器，不需要配置代理桥。

可选覆盖示例在 `workflow/agent-bridge.example.json`。换电脑或交给其他 Agent 时先读根目录 `AGENTS.md`。

## 禁止事项

以下动作任何自动化、脚本、代理都不得执行：

1. 把 `upload_gate`、`publish_gate` 标成 `approved`（`structure_gate` 已是自动 gate，不得手工标记）。
2. 手工把 `gate` 类阶段标成 `completed` / `approved` / `skipped`（`runner.py` 直接拒绝）。
3. 跑上传、调发布接口、删除草稿。
4. 修改批次产物（`batch.json`、`batch_meta.json`、各类 review 文件）来"让它看起来通过了"。
5. 在审核文件里编造核对人、证据或签名。
6. 用 `--allow-missing` 在没有产物时强记人工确认。
7. 跳过 `validate` 直接进审核，或跳过 `review_gate` 直接确认上传。

改了上游就必须让下游失效重跑，不能只补一个"通过"。上游改动范围由 `contracts.py` 的 `EXTRA_INVALIDATION` 决定，例如：

| 改了什么 | 连带失效 |
| --- | --- |
| `extract` | `types`、`knowledge`、`assemble`、`meta`、`validate`、`review` |
| `types` | `knowledge`、`assemble`、`meta`、`validate`、`review` |
| `knowledge` | `assemble`、`meta`、`validate`、`review` |
| `assemble` | `meta`、`underlines`、`validate`、`review` |
| `meta` | `underlines`、`validate`、`review` |

## 常用命令

```powershell
python -m workflow.auto scan --inbox inbox
python -m workflow.auto run  --inbox inbox --once
python -m workflow.auto run  --inbox inbox --interval 60 --limit 3 --stop-at review_gate
python -m workflow.runner status   --batch-dir workflows/<批次>
python -m workflow.runner run      --batch-dir workflows/<批次>   # structure_gate 为自动闸门，随 run 一起执行
python -m workflow.runner mark     --batch-dir workflows/<批次> --stage upload_gate --status approved --actor <人名>
python -m workflow.doctor
```

退出码：`0` 成功或已停在人工节点，`1` 闸门未通过，`2` 用法或前置条件错误。

## 相关代码

| 文件 | 作用 |
| --- | --- |
| `workflow/contracts.py` | 18 个阶段、依赖、产物、失效关系 |
| `workflow/auto.py` | 扫描 inbox、初始化、推进、写 `_queue.json` |
| `workflow/runner.py` | 各阶段实际执行、人工标记、闸门判断、上传阻断 |
| `workflow/manifest.py` | 台账读写、哈希、状态推导、失效传播 |
| `workflow/checks.py` | `validate` 阶段的 16 项体检调度 |
| `workflow/review.py` | `review_gate` 的放行规则 |
| `workflow/independent_review.py` | 独立复核的快照与签名校验 |

## 难度与教材匹配口径

答案阶段的难度只保留 `容易/适中/困难` 主观等级；`difficultyEvidence` 不再是必填字段，也不要求模型编写解释性难度证据。实际题干/选项缺失时难度可以留空，不得凭题型硬猜。

教材匹配只适用于来源类型为「同步练习」或「单元测验」的材料，并按教材版本、册次、单元、课时/Section 精确匹配。普通中考真题、模拟卷和来源不明试卷不强制教材匹配，`textbook` 应省略，不因缺少教材映射阻断流程。

## 阶段交付清单

每个批次的 `workflow/delivery.json` 记录阶段声明产物、SHA-256、交付状态和消费关系。`completed` 只表示阶段质量通过且产物已安装；`delivered` 表示声明产物存在且指纹匹配；`delivered_unqualified` 表示执行产物已交付但质量仍待复核/失败；`quality_status`/`release_ready` 单独表示质量和放行状态。零人工模式可以继续生成本地候选，但不能把质量失败改写成质量通过。

`meta` 写入 `batch_meta.base.json`；`underlines` 读取基础批次并独立写入 `batch_meta.json`，无 DOCX 时保留原有内容。旧批次缺基础文件时重新执行相应阶段。

## 材料分类与对齐（2026-11 重设计）

结构阶段不再“一上来按题号硬切、切不准就整段否定”。`workflow/material_classify.py` 先把每份上传文件的**文字层**各自提取出来，判定文件角色（`paper` 正文 / `answers` 答案 / `explanations` 解析 / `mix` 混排 / `other`），结果写入 `01_source/material-roles.json`；明确上传的答案卷会在该阶段直接提取到 `01_source_ans/正文.txt`，供答案阶段使用。模型不可用时用确定性规则（题号+选项 vs 题号+答案标记）兜底，只留证据、不阻断流程。

多篇材料（A/B/C/第X篇…）的阅读/对话分区由确定性规则给出边界，`material_classify.partition_paper` 可在有模型时确认每篇包含的题号（严格校验恰好覆盖全部题号才采信，否则回退规则）。段落标签带插图（如 `A[IMG image11.jpeg]`）也按材料标识别，避免首篇材料漏切导致整段被当成一个题组。
