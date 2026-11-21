# 流水线：19 个阶段与统一问题处理

这篇管"一批卷子从原件到待上传草稿"的推进方式：有哪些阶段、每个阶段产出什么、自动执行器在哪里停下，以及哪些动作永远不允许自动化。

## 19 个阶段

阶段定义是封闭契约，写在 `workflow/contracts.py` 的 `STAGES` 里，顺序、依赖、产物都不可临时增删。类型只有四种：`agent`（由执行者或代理完成）、`command`（跑脚本）、`gate`（机器闸门，只能由检查结果放行）、`human`（人工确认，只能由人标记）。

| # | id | 做什么 | 产出 | 类型 | 依赖 | 停点 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `extract` | 来源、年份、地区、来源类型与原卷复合提取 | `01_source/metadata.json`、正文、结构与提取证据 | command | 原件（必有） | 确定性执行器 |
| 2 | `structure_gate` | 对照原卷逐题核题号、选项、材料边界和真实对话分组 | `01_source/structure.confirmed.json`、`07_review/structure-gate.json` | gate | `extract` | 证据不足则阻断并准备版面复核 |
| 3 | `preassemble` | 切题后建立来源对象，并在同一阶段判题型、子类、词数与完整性 | `06_batch/batch.base.json`、`03_types/types.json`、逐步填充的 `06_batch/batch.json` | agent（内含确定性组装） | `structure_gate` | 来源对象与题型均验收才交付；不重新编写原题 |
| 4 | `types` | 兼容题型交接校验，只读检查前置组装的结果 | `03_types/types.json`（复用组装产物） | command | `preassemble`、结构确认 | 不调用模型、不重复判型；不完整不能交付 |
| 5 | `answers` | 逐题填入原答案或带来源证据的「无答案」决定 | `01_source_ans/answers.json` | agent | `preassemble`、结构确认、`types` | 未逐题决定不能交付 |
| 6 | `audio` | 依据已验证题型切听力并填媒体引用，独立作业 | `02_audio/audio_map.json` | agent | `structure_gate`、`preassemble`、`types` | 单元/类型绑定过期即拒绝；无音频明确跳过 |
| 7 | `explanations` | 按Word原文逐字匹配解析/大意，确认缺失填占位 | `04_knowledge/explanations.json` | agent | `preassemble`、结构确认、`types` | 消费原答案材料，与答案填充并行；来源唯一且精确绑定，不生成 |
| 8 | `knowledge` | 仅填知识点叶子、ID与证据，不做教材匹配 | `04_knowledge/knowledge.json` | agent | `structure_gate`、`preassemble`、`types` | 不读取并行答案或解析产物 |
| 9 | `content_snapshot` | 复制前置原文基线，供并行整理使用 | `06_batch/content.snapshot.json` | command | `preassemble` | 快照不代表填充完成或质量通过 |
| 10 | `meta` | 从内容快照生成难度与分类视图 | `06_batch/batch_meta.base.json` | command | `content_snapshot` | 与答案、解析、知识点填充并行，不改题文与知识标签 |
| 11 | `underlines` | 从元数据视图补原卷画线标记 | `06_batch/batch_meta.json` | command | `meta` | 无 DOCX 按可选规则跳过，汇总消费基础视图 |
| 12 | `assemble` | 在画线后按原题身份汇总固化全部文字填充 | `06_batch/batch.json`，同步完整最终视图 | agent（确定性执行） | `preassemble`、`answers`、`types`、`knowledge`、`explanations`、`underlines` | 消费画线后视图；上游未合格不能合流；不等待听力 |
| 13 | `validate` | 对完整汇总后的最终视图做内容体检与原卷逐题对账 | `07_review/validation-report.json` | gate | `assemble` | 不消费尚未汇总的中间视图；失败保留质量问题 |
| 14 | `review` | 两轮内容与知识点审核 | 分轮记录与差异队列 | agent | `validate`、`audio` | 复用未变单元 |
| 15 | `review_gate` | 内容审核结果归集 | `07_review/review-gate.json` | gate | `review` | 质量结论如实保留 |
| 16 | `format_gate` | 最终本地上传字段/格式、难度及严格来源检查 | `07_review/upload-format.json` | command | `review_gate` | 无模型、无网络；错误不伪称通过 |
| 17 | `upload_gate` | 上传确认 | 台账与冻结绑定 | human | `format_gate` | 普通本地自动推进跳过，提交走专用入口 |
| 18 | `upload` | 写入远端上传暂存 | `07_upload/receipt.json` | command | `upload_gate` | 普通本地自动推进跳过；提交严格冻结请求与远端验证 |
| 19 | `publish_gate` | 发布确认 | 台账 | human | `upload` | 普通本地自动推进跳过，发布仍走专用入口 |

要点：

- `preassemble` 即建立真实批次；后续步骤按字段填充。`content_snapshot` → `meta` → `underlines` 与文字填充分支并行，`assemble` 等文字填充与画线均可交付后再合流，体检和最终检查消费完整的最终视图。
- `validate` 只做内容、结构、来源与标签体检（题号覆盖、原文保真、答案/解析/标签、原卷逐题对账）。`audit_type_layout --content-only` 保留选项完整性等硬检查；完整题型版面的重复显示、文本、空档、画线、表格、完成句子格式与难度一致性后移。`upload.local_preflight` 不在 validate 调用，报告明确登记延期检查；不能调低内容检查换取通过。
- `format_gate` 是唯一的本地上传格式闸门：纯格式脚本与完整题型版面观察只作 warning；`check_difficulty` 保持阻断，最终字段、身份、题号、标签、媒体、序列化、`build_payload` 与严格来源核对也保持阻断。不触发模型、不修改批次数据，错误不伪称通过。上传队列仅在当前最终文件通过该检查后结束；二审完成不再提前移出队列。
- [上传 API 对接](upload-api.md)仍在真实提交前校验实时远端目录、媒体和冻结请求；本地格式通过不等同远端提交批准或发布。普通 run/auto 不自动提交远端。


调度只并发**依赖已经交付且消费者校验通过**的阶段。前置组装在同一阶段完成来源对象与题型判断；题型不完整时整个前置组装不能交付。随后听力、答案、解析、知识点可并行；`content_snapshot` 也可从已交付的 `preassemble` 独立生成，再运行 `meta`、`underlines`。音频消费已验收题型并绑定题型/结构摘要；解析消费提取阶段准备的原卷/答案材料，不依赖并行答案 JSON。`types` 只保留无模型兼容检查点。`assemble` 等待已交付的文字三路、题型和画线结果，显式消费画线后的 `batch_meta.json`，确定性合并并同步完整最终视图，随后 `validate` 才能执行。一个分支阻断不冻结无依赖的分支，也不能借并行消费过期、缺失或不合格输入。

自动并行推进时，文字任务发出后由原调度线程运行已就绪的内容快照、元数据和画线命令，模型候选仍由原导入器验收。最终汇总继续等待文字分支和画线的合格交付。对于前置组装管理的批次，本地体检兼做审核前交付检查，复用最终格式的字段和身份检查，并提前阻断已经确认的题干/选项来源差异。软格式提示、难度一致性、目录快照及媒体部署仍走后续既有检查。

自动并行模型任务在执行前保存原任务身份，返回后即原子写入候选。若进程在导入前退出，下一轮自动推进先用原任务导入器校验现存候选和当前输入。锁繁忙时保留候选，只等本地导入。监督器在新候选出现时可提前发起一次本地复验，模型冷却继续在调用入口生效。无法验证原任务身份、输入已变化或候选不完整时，保留原证据并按对应阶段的现有规则处理。

判型与知识点分片按小题数和提供给该单元的材料量平衡连续分片，保留原题组、输入和单元数上限。判型和文字填充在有并发槽位时优先派发输入较大的分片。原生代理另外记录入队、宿主领取和开始时间，可将宿主等待与运行时长分开。两轮审核按轮次交错派发，每轮大分片优先，验收仍遵循原绑定顺序。各分片尝试在既有运行目录记录队列等待、调用耗时和验收耗时，重试按尝试号区分。既有并发上限和模型预算继续按设备配置生效。

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

通过即写 `01_source/structure.confirmed.json` 并进入前置组装判型；不通过保留候选、原件证据和具体阻断码，由既有自愈链按来源与影响范围诊断、修复、复验。无依赖的分支可继续，有依赖的阶段必须等当前来源和消费者校验通过；题号归属、图片理解或边界不明不能静默放行，也不能编造原文补齐。

## 卡点分类与前置预检

工作台诊断会为每个自身卡点附带 `failure_class` 与 `next_action`，把“代理输出格式失败、输入过期、确定性闸门失败、审核质量差异、缺必需输入、等待上游”分开显示。下游仅等待上游时不再伪装成新的失败。上游交付不满足时区分“尚未完成执行”“已执行但质量/交付未满足”和“已执行但当前产物/消费者校验未满足”，审核已执行不等于质量放行。**审核差异是产出，不是卡点**：`review_gate` 为 `needs_review + execution_completed + continue_unqualified + release_ready=false` 时，一审/二审与原标签的差异属于设计状态，诊断显示为待人工判断（`failure_class=quality_differences`，不阻断手动上传），不要求自动修正、不自动改标签/改判通过；只有 `blocked` 的确定性阻断项（标签体检、流程图、审核协议失败等）才是卡点。

最终格式脚本通过后仍须通过来源固化钩子；`upload-format.json` 的 `passed:true/errors:[]` 只证明该报告的格式检查，不覆盖台账中后续固化失败。诊断保留实际固化异常和上游交付条件，来源变化错误列出首个冲突题组及字段。来源解析器补回原卷导语等对象变化时，通过受控 `preassemble` 刷新来源基线并复验；同一来源切片、媒体、题号及子题身份未变时保留已有填充，真实正文编辑冲突仍拒绝，不能以空 errors 绕过固化。

`validate` 不运行或额外生成上传本地预检；内容检查以外的上传适配集中在 `format_gate`。最终来源审计对托管批次要求当前正文、提取结构和确认结构完整，重新执行已确认单元的原卷核对与 `upload_content.inspect_content(strict=True)`，来源缺失、答案/解析不符、审计未完成均保持阻断。只有明确的图片/音频能力限制可作 informational；实际比较出的选项、标签或内容差异不能因此降级。独立 payload 工具没有来源上下文时明确返回 `not_applicable` 与 `unchecked`，不声称做过原卷核对。

这些核对的范围是**确认单元与已绑定内容**，并不证明独立的原件逐页完整覆盖，也不证明浏览器最终渲染。报告仍明确保留 `independent_original_page_coverage`、`rendered_preview` 为未检查边界；缺来源上下文时连 `source_fidelity` 也未核验。不能把机器报告通过写成“独立全原卷复核完成”或“预览无遗漏”。远端目录、媒体可访问性、冻结文件一致性仍由 `prepare-upload`/`verify` 在真实上传前复核。

各检查、体检和最终报告用统一 `findings` 汇总问题，至少包含错误码、检查/脚本、完整原因、阻断属性、严重性与阶段，保留报告路径和已有的单元身份。完整 stdout/stderr、全部失败行和结构化证据留档，展示尾部摘要不替代证据；不得从打印题号猜造 `unit_id`。缺输入、超时、无法执行和未解释的非零退出均如实失败。软 warning 不变成硬阻断，审核差异保持待判断；这些 findings 供同一既有自愈链消费，不另建修复队列。

规则文件按阶段分层绑定：解析/知识点/审核只绑定自身语义规则；装配版式或上传约束变化不会让无关阶段的模型任务无条件过期。真正相关的输入、产物和目录变化仍会使阶段失效并要求重跑。

## 前置标准题目对象与局部更新

结构确认后立即执行 `preassemble`：从 Word 原文和切题边界生成源对象，真正写入 `06_batch/batch.json`，并在同一阶段结合题干、选项、大题标题和共享材料判断题型、子类、词数与内容完整性。确定性代码建立正文与来源绑定，模型只填写题型标签，不能重新抄写或改动题目。题型结果通过目录、覆盖与完整性检查并回填同一批对象后，前置组装才完成。每个独立题/题组使用稳定 `unit_id`，题干、选项、共享材料、表格、子题、媒体按最终字段形状前置组装。`batch.base.json` 是不随标签填充变化的来源基线，`workflow/batch-fill-state.json` 记录各字段填充进度；这些都不冒充审核通过。

答案、题型、知识点、解析/大意、媒体和元信息产物被校验接受后，按来源单元身份逐字段回填同一批次；跨大题同号不按全局数字猜配。未判断字段保持空；确认原件没有相应内容后沿用既有「无」「未提供」等占位规则，有原文则逐字填写，不生成解析或大意。多附件先按内容分角色，同角色内 Word 优先。**每份 PDF（包括 Word 配套 PDF）都必须整卷 OCR 并保存来源绑定的 JSON＋Markdown**，不受文字层质量或 OCR 总开关影响；任务等待、失败、空文字或缺当前结果时提取不通过。OCR 原顺序与版面证据保留；同一正文的 OCR 不覆盖 Word，Word 确实缺少的解析/大意才按附属 PDF 原文兜底并记来源。同源副本只作佐证，不参与唯一绑定竞争。

执行关系为：内容提取（含材料角色、每份 PDF 整卷 OCR、卷头识别）→ 结构确认 → 组装判型；已交付 `preassemble` 一路经 `content_snapshot` → `meta` → `underlines`，另一路经题型交接启动听力/答案/解析/知识点并行。画线与文字三路交付后在 `assemble` 合流固化 → 本地体检 → 内容审核 → 最终格式与严格来源检查 → 专用审核上传入口。听力不阻塞快照、元信息、画线、文字填充、组装或体检；只有内容审核等待听力。主图固定十二类，内部子流程只在悬浮层显示真实状态，连线表达真实依赖。晚到听力若变更正式媒体，事务性同步各派生视图的音频附件、保留非音频字段；只有此前当前且同步后消费者验收通过的视图才更新交付摘要。最终视图变化使旧体检失效，进入审核前实际重跑体检；不重跑答案/解析/知识点，不改旧报告冒充通过。

标签模型默认以一题或一个题组为一片（`label_part_units=1`），与审核分片大小独立。可以唯一回指当前原文且模型身份可观察时，按本片题文、对应标签/答案、规则目录和模型配置复用快照；在途任务仍核对全部原始输入。托管批次后置 `assemble` 仅对同一批对象汇总固化并走原有整批保真校验；发生冲突时保留现有题目对象等待局部修正，不回退模型整批重组。没有托管状态的历史批次保留原有装配路径。来源元数据及报告簿记变化只做确定性字段合并。

自动恢复采用两层指纹：任务导入和上传冻结继续校验原始字节；完成结果复用比较该阶段真正消费的内容。JSON 缩进、键顺序和生成时间不使内容失效。词数、难度变化不重新打知识点；题型/子类、题干、选项或答案真实变化才更新相关工作。原始标签难度保留为首轮结果，不成为审核完成前提。

修复阶段时先只重排当前阶段，保留其它产物；它交付后，再逐个检查消费者是否真的接收了变化。未修好的上游会阻止依赖任务提交，但不会先把整条链的已完成结果作废。审核按独立题/共享材料题组保存语义摘要和真实执行来源：题干、选项、答案、解析、知识点或相关媒体改变时补对应单元；共享材料改变补对应题组；来源、难度、分类和报告时间变化不补审。无法可靠限定范围的附录或媒体变化仍保守扩大范围。

显式 `invalidate` 仍代表要求整链重置；正常恢复不再使用该操作。旧记录只有原始摘要时，先按旧摘要验证；没有可证明的逐题审核来源时不能直接认定旧记录有效。上传批准仍绑定最终文件的精确摘要，内容冻结保护不因缓存复用而放松。

## 工作台继续处理回执

流程右上角「继续处理」提交后立即显示本批次的请求状态。后台 `started:false` 不能当作启动成功：等待前卷时显示 `waiting_for/reason`，已排队、正在执行、另一执行器占用分别说明；后台拒绝或执行结果中的错误在流程区直接显示。请求完成立即刷新列表，后续沿用统一进度轮询，不再以 `running:false` 把排队误认为结束，也不额外启动长时间私有轮询。

同批次在途点击去重；回执超过15秒或连接中断时，只读 `/pipeline` 核对是否已在队列或执行中，不自动重发。如果仍无法确认，明确显示结果未知。排队顺序、执行锁、真实阶段闸门及上传授权边界保持原有规则。

## 持续自动修复与执行验证

真实工作台服务启动时会启动 `workflow.supervisor.WorkflowSupervisor`，默认每 30 秒检查活动批次，服务退出时停止；只导入模块不会启动线程。监督器只扫描活动 `workflows`，不恢复归档，也不调用上传或发布。每批 `workflow/supervisor.json` 保存重试时间和无进展预算，重启后继续；不再依赖短暂的 7 轮巡检或用户反复点继续。执行中的批次/模型/CLI 锁不会被重复接管。

完整路径沿用同一套 `self_heal`、监督器与受控运维链：检测确定性阻断或无进展 → 汇总既有报告的完整 findings、来源证据和尝试历史 → 选择受限动作 → 实际应用 → 原检查复验 → 继续原有流水线，不新增平行修复队列。模型诊断使用当前显式 Stage 路由，不静默换模型；无关独立分支继续执行，依赖分支必须等合格交付。审核差异、正常等待和限流不冒充质量故障；同输入同策略重复失败不能原样空转，按现有预算与受控运维规则升级或延期。模型冷却只延后相关模型调用，不冻结可执行的本地检查。

实际动作包含：有原文证明的上传格式修复、源表格恢复、已登记文件状态更新、当前阶段重试，以及携带真实错误反馈的受影响题目重试。真实修复会备份、守卫原始输入、原子安装并读回校验，失败回滚；不编造缺失原文、答案或解析，也不把质量失败改成通过。纯格式等价修复可重用已实际通过的原验证与审核证据，仍独立复检来源和上传形状；内容恢复引入变化则重新执行相应检查。

`workflow/repair-feedback/<stage>.json` 的严格反馈只进入对应阶段和受影响分片。其它题目的精确提示与有效缓存保留；在途任务仍校验全部原始输入。`workflow/self-heal.json` 区分 `retry_scheduled`、`repair_applied`、`retry_verified`、`retry_failed`，排队不冒充修复成功；小型 `self-heal-status.json` 用于界面显示“AI 判断／局部修复／校验／稍后重试”，不新增主流程节点。后台 `/api/health` 返回实际监督线程存活与最近巡检时间。

自愈返回 `refused` 后，当前阶段的串行与并行入口均保持拒绝；旧输入记录或音频能力跳过不能覆盖这次决定。主流程保留具体原因并继续无依赖分支。`defer_and_continue` 的期限及输入/原件/代码证据保存在原自愈事件中，跨调度读取有效等待，到期或实际证据变化后才重新准入，不能在同一轮立即重派。复验先检查上游稳定交付，再判当前阶段真实执行结果；审核已执行的差异独立记为 `retry_quality_differences`，不当成修复失败或质量通过。

模型调用返回明确的凭据拒绝时，失败阶段保留在批次台账中，并停止本阶段的模型重派和自动代码修复。调度继续运行可交付的独立分支。监督器在只剩此类等待时不消耗无进展预算，发现可信模型路由、配置或 Harness 本机凭据文件版本变化后重新准入。批次只记录路由摘要、文件版本信息和错误分类。

元数据和画线交付使用同一套来源字段投影。来源占位、年份字符串和数字的等价值按元数据规则归一化，实质不同的来源别名和正文变化仍拒收。音频映射保留已验证的本地文件路径，批次附件从首次交付起使用所属批次的标准媒体地址，重复验收同一切片不会再因地址转换触发体检。

## 阶段状态

台账里每个阶段只有九种状态：

| 状态 | 含义 |
| --- | --- |
| `pending` | 未开始 |
| `running` | 正在跑 |
| `completed` | 完成且产物已记录哈希 |
| `approved` | 人工确认通过 |
| `needs_review` | 审核差异或执行/交付未满足，须结合 `execution_completed`、质量和消费者校验判断 |
| `blocked` | 确定性阻断或缺少必要条件，进入既有诊断修复链 |
| `failed` | 执行失败 |
| `invalidated` | 当前阶段实际消费内容或交付发生变化，按范围更新 |
| `skipped` | 按规则跳过（如没有音频） |

`completed`、`approved`、`skipped` 是终态标记，消费者仍须验证实际交付及当前输入；旧文件存在不能代替合格交付。`failed`、`blocked`、`invalidated` 先按影响范围修复、复验，有依赖的消费者等待，无依赖分支继续。`needs_review` 的审核差异不自动消除，也不等于执行失败或质量通过。

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
│  ├─ batch.base.json         # 前置原文基线
│  ├─ content.snapshot.json   # 并行整理的来源快照，不含完整文字填充
│  ├─ batch_meta.base.json    # 元数据基础视图
│  ├─ batch.json              # 按身份逐步填充，assemble 后完整固化
│  └─ batch_meta.json         # 画线后视图；assemble 同步完整填充供后续消费
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
3. 没有 `batch.json` 时按后缀和文件名登记候选：`.docx`/`.doc`/`.pdf` 为文档，含“答案/answer/key/解析”的文件名仅辅助初始路径选择；`.mp3`/`.wav`/`.m4a`/`.aac` 为音频。正式正文/答案/解析角色仍由提取阶段按实际内容识别，不能用上传顺序、扩展名或文件名代替。
4. 目标批次没有 `workflow/manifest.json` 时，调用 `runner` 的初始化流程登记输入。

### 推进（run）

以 `STAGES` 的真实依赖选择可运行工作，并把每个未满足条件的具体原因写入现有队列状态。某阶段失败或等待只限制其依赖消费者；其它输入当前且消费者校验通过的独立分支继续，不能按线性顺序遇到首个阻断就冻结整批：

| 情况 | 当前阶段处理 | 回执类别 |
| --- | --- | --- |
| 上游未交付、过期或消费者校验未通过 | 等待具体上游，不消费不合格产物 | 依赖等待 |
| 自定义桥未配置或通用任务等待接单 | 保留任务及缺少的执行条件，由已配置执行者续接 | `agent` / `review` |
| 代理桥或普通命令失败 | 保留完整失败证据，进入既有诊断、自愈与复验 | `failed` |
| 机器闸门未通过 | 保留全部阻断项，限定影响范围修复 | `gate` |
| 审核已执行但有真实差异 | 保留差异队列，不自动裁决或重生成 | `quality_differences` |
| 普通本地推进遇到上传确认、上传、发布 | 按本地规则 skipped，不提交远端 | 专用上传入口另行授权 |

终态阶段仍按当前输入与交付核验后复用；`invalidated` 按实际影响范围重新执行。缺执行条件与来源证据不能伪写为成功，也不能等待一次人工“通过”代替程序验收。

### 已交付产物的受控复用

早期独立的 `workflow.redeliver` 命令已移除。阶段产物因表现层变化被标为过期时，统一由 `python -m workflow.runner run --batch-dir <批次>`（或自动推进）按当前输入指纹与消费者校验重新执行或复验；共享草稿解析器回执稳定性见下节。

### 共享草稿的解析器回执稳定性

`question_draft.ensure` 每次完整重建并核对原始来源、媒体及诊断；若旧 `content_sha256` 自洽、输入键集相同、仅 `parser:*` 的摘要值改变，而其余完整对象和证据全等，则保留旧对象及原始序列化字节，不把新解析器摘要伪写到旧回执。原文、OCR、结构、媒体、对象或诊断发生真实变化，以及解析器键增删、旧回执损坏时仍刷新草稿。在途任务继续使用原始字节 SHA-256 严格检查，不使用语义等价替代；旧候选只能重新绑定当前任务并通过全部消费者校验后交付。

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
7. 在本地流水线中跳过 `validate` 直接进审核，或伪造 `review_gate` 放行；用户手动上传的独立入口仍按既有 validate 历史通过与上传硬校验规则执行，不要求审核差异自动消失。

上游实质变化必须检查受影响消费者，不能只补一个“通过”；正常恢复先修当前阶段、再由消费者判断是否失效，不能因时间戳或 JSON 排版变化整链重跑。显式整链 `invalidate` 的范围由 `contracts.py` 的依赖图与 `EXTRA_INVALIDATION` 推导；以下列出关键本地消费者，后续审核和上传链仍由契约继续传播：

| 改了什么 | 关键受影响消费者 |
| --- | --- |
| `extract` / `structure_gate` | 前置组装、各填充分支、快照整理链、汇总、体检与审核 |
| `preassemble` | `types`、各填充分支、`content_snapshot` → `meta` → `underlines`、`assemble`、`validate`、`review` |
| `types` / `knowledge` | 相关填充消费者、`assemble`、`validate`、`review`；不反向失效独立快照整理链 |
| `content_snapshot` | `meta`、`underlines`、`assemble`、`validate`、`review` |
| `meta` / `underlines` | `assemble`、`validate`、`review`（`meta` 还影响 `underlines`） |
| `assemble` | `validate`、`review`；不反向重跑 `meta` 或 `underlines` |

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
| `workflow/contracts.py` | 19 个阶段、依赖、产物、失效关系 |
| `workflow/auto.py` | 扫描 inbox、初始化、推进、写 `_queue.json` |
| `workflow/runner.py` | 各阶段实际执行、人工标记、闸门判断、上传阻断 |
| `workflow/manifest.py` | 台账读写、哈希、状态推导、失效传播 |
| `workflow/checks.py` | 内容/最终格式检查分层调度、完整输出与 findings |
| `workflow/findings.py` | 统一问题归集，保留证据和既有身份，不另建修复队列 |
| `workflow/content_snapshot.py` | 并行整理的前置内容快照 |
| `workflow/final_audit.py` | 最终严格来源核对及未检查边界 |
| `workflow/format_gate.py` | 最终上传适配、难度硬检查及来源审计归集 |
| `workflow/review.py` | `review_gate` 的放行规则 |
| `workflow/independent_review.py` | 独立复核的快照与签名校验 |

## 难度与教材匹配口径

答案阶段的难度只保留 `容易/适中/困难` 主观等级；`difficultyEvidence` 不再是必填字段，也不要求模型编写解释性难度证据。实际题干/选项缺失时难度可以留空，不得凭题型硬猜。

答案来源比对和下游装配共用格式归一化：忽略 Word 加粗/画线等排版标记、空白换行、直弯引号、全半角符号、备选答案斜杠旁空格及答案末尾句号。这些不影响内容的差异不作为失败或人工判断项，原答案字段与原件不被改写。换行前后的内部句号保留，不能因来源换行而单边删掉。上下标保留其含义：`10<sup>2</sup>` 与 `10²` 等价，但不等于 `102`；下标同理。归一化后没有实际内容的非空输入（如 `**.**`）必须拒绝，明确的缺失占位仍按原口径处理。词句、大小写、数字、小数点、正负号、否定词、选项和内部标点仍参与比较，不能把实际改错的答案当作格式差异放行。

教材匹配现已暂停（2026-11-11 用户要求）：当前只做知识点标签匹配，所有来源均省略本地 `textbook`，不向知识点任务提供教材目录。旧候选教材字段不参与交付与组装；知识点标签、证据和覆盖仍严格验收。

## 阶段交付清单

每个批次的 `workflow/delivery.json` 记录阶段声明产物、SHA-256、交付状态和消费关系。阶段状态、执行完成、产物交付、质量结论与放行分别记录：`delivered` 表示声明产物存在且指纹匹配；`delivered_unqualified` 表示已有执行产物但质量或交付条件仍未满足；`quality_status`/`release_ready` 单独表示质量和放行状态。`completed` 或文件存在均不能替代消费者对当前输入与交付的校验；零人工模式不能把质量失败改写成通过，也不能把未合格候选交给依赖方。

`content_snapshot` 从已交付 `batch.base.json` 建立 `content.snapshot.json`；`meta` 消费该快照写入 `batch_meta.base.json`；`underlines` 消费元数据基础视图写入 `batch_meta.json`，无 DOCX 时保留原有内容。这条并行来源投影不要求尚未交付的答案、解析或知识点。`assemble` 等文字填充与画线交付后合流，把已接受填充同步到 `batch.json`、`batch_meta.base.json`、`batch_meta.json` 等实际消费者视图，并保持来源、身份、标签与非目标字段。旧批次缺基础文件时按依赖重新执行相应阶段，不让体检误读未汇总快照。

## 材料分类与对齐（2026-11 重设计）

结构阶段不再“一上来按题号硬切、切不准就整段否定”。`workflow/material_classify.py` 配合提取链逐份读取 Word 原文或 PDF 整卷 OCR 内容，判定文件角色（`paper` 正文 / `answers` 答案 / `explanations` 解析 / `listening_script` 听力录音材料 / `mix` 混排 / `other`），结果写入 `01_source/material-roles.json`；识别出的答案材料写入 `01_source_ans/正文.txt` 供答案阶段使用，独立听力录音材料则由 `workflow/listening_script.py` 消费并在 `upload_content` 答案/解析锚点解析时隔离。同角色内 Word 优先，不能以 PDF 原生文字层代替必需 OCR，不能按上传顺序把第二份文档直接认作答案。模型不可用时确定性规则只作证据或保底，来源身份不明、PDF OCR 未完成或消费者验收失败仍如实保留未完成状态。

多篇材料（A/B/C/第X篇…）的阅读/对话分区由确定性规则给出边界，`material_classify.partition_paper` 可在有模型时确认每篇包含的题号（严格校验恰好覆盖全部题号才采信，否则回退规则）。段落标签带插图（如 `A[IMG image11.jpeg]`）也按材料标识别，避免首篇材料漏切导致整段被当成一个题组。
