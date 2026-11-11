# AGENTS.md — 给所有接手本项目的 Agent

本文件是**项目根目录的总入口**。任何 Agent（DSH、Codex、Cursor、Copilot、CLI 或其它）接手本项目前先读这里。
用户当面或文字明确提出的要求**永远优先于本文件**；本文件与用户冲突时，按用户执行并在 `CHANGELOG/` 记一条。

本项目是「录题插件版」（md-upload-tiku）：把英语试卷整理为可审核、可上传的题库批次。
根目录名有时叫 `上传插件版`，实际包名见 `package.json`。

## 一、项目地图（先看这些，不要靠猜）

| 位置 | 作用 |
|---|---|
| `README.md` | 项目总览、安装、能力 |
| `AGENTS.md` | 本文件：Agent 总入口与硬规则 |
| `app/AGENTS.md` | 工作流**任务协议**细节（导任务/交回/闸门） |
| `app/docs/` | 各阶段规则与专项文档（extraction / review / upload 等） |
| `app/prompts/roles/` | 8 个内置子 Agent 的角色提示词（职责/权限/自检/出问题怎么办） |
| `app/workflow/` | 工作流实现（`contracts.py` 是 18 个阶段的唯一契约） |
| `backend/` | 路由脚本 `wf-router.sh`、`route_pick.py`、插件宿主桥接 |
| `lib/` | 宿主插件与前端（`client.js`、`index.js`） |
| `config/` | `model-router.json`（模型/子 Agent 路由）、策略配置 |
| `CHANGELOG/` | **项目变更记录**（见第三节，强制维护） |
| `ISSUES/` | **卡点台账**：`open/` 未修复待办清单，`fixed/` 已修复留档（见硬规则 24） |
| `workflows/` | 实际试卷批次产物（**用户数据，非代码**） |

改代码前先读对应 `app/docs/*.md`；`app/workflow/contracts.py` 是阶段与产物的唯一真相，改动前先对齐它。

## 二、硬规则（用户反复强调，必须遵守）

1. **零人工本地执行**：全程不得出现人工干预、人工判断、人工选择；不得因"待复核/待核验/人工确认"停住。
2. **卡点必须自愈**：遇到问题由 AI 自行诊断、**实际修复**、复验、续跑；不能只加函数/提示、不能原样重试后停住、不能等用户反复点继续。单点失败不能阻塞无关独立分支；有依赖的下游只能在上游产物完整、格式正确、当前且通过消费者校验后推进。未完成、needs_review、缺失/过期/不合规产物不得视为交付；依赖等待不能伪装成功或质量通过。禁止编造内容蒙混过关。卡点说明必须指明具体检查项与判定口径（哪条检查、为什么判失败、怎么修），不能只写“按阻断项修复”；调度不得仅因 JSON 空白、键顺序或生成时间等序列化差异停住/重跑；模型限流只延后模型调用，不得冻结不依赖该模型的本地命令/闸门或独立分支；精确哈希仍用于任务在途输入绑定、原件/媒体证明与上传冻结，真实内容变化必须通过消费者校验并按影响范围处理。
3. **不自动上传/发布**：`upload_gate / upload / publish_gate` 本地一律 skipped，绝不自动提交题库；远端提交须用户明确授权。**但用户手动点上传时，产物“过期/内容变化”与“未完成审核/有审核差异”都不阻碍上传**（用户已多次强调）；只要本地体检 validate 曾通过即可上传，内容正确性由用户自行把关，标签/结构等硬校验仍在。
4. **原文保真**：题干/选项/材料/表格必须等于原卷切片；Word 与 PDF 同时存在时以 Word 原文为准，PDF OCR 只辅助层级与版面；但当 Word 确实缺少某题解析/答案或某篇文章大意时，可按随附 PDF/OCR 原文兜底并记录来源，两者都没有才留空占位。**同一份卷被重复提取成多份来源时（PDF 附件副本、OCR 副本、materials/*.txt），副本不得参与“唯一绑定”竞争**；`01_source/material-roles.json` 中 `content_authority=false` 的文件只作佐证，不构成冲突。权威来源内部同一题出现两份不同解析，仍必须判为冲突。
5. **空白语义**：未判定字段留空；确认原内容不存在时保留既有占位（无解析/未填写/未提供），不得一律清空。
6. **审核范围**：人工审核只收两类差异——知识点标签差异、基本差异（题型/子类/答案/解析/原文）；难度/来源/分类/选题/词数不进审核、不比对、不显示。图片/音频能力受限只作 informational，不进人工队列、不阻断；此豁免只针对能力限制本身，**程序已比较出的真实标签差异不得因审核理由提及图片/音频而被过滤**。**一审/二审与原标签不一致就是差异，必须进待审核**；不得以“审核判断与当前正式标签不同”为由把差异当过期结论丢弃。**盲审隐藏标签不等于正式标签缺失**：两轮只独立填写 assessment，由程序比较原标签；把刻意隐藏字段当缺失的结论必须自动补审，不得误入人工队列，也不得直接伪改为通过。标签一致时仍须保留真实答案/解析/原文错误。**差异是产出，不是卡点**：一审/二审与原标签不一致的条目必须完整留在 `07_review/manual-review-queue.json` 与 `review-gate.json` 的 `rounds.errors`，供工作台人工判断（用来发现模型/知识头的偏差）；禁止自动 resolve、禁止为放行改判通过或改标签；`review_gate=needs_review` 就是该设计状态，不得当成故障“修复”，也不阻断用户手动上传（规则 3）。**卡点诊断与运维自愈同样适用**：`review_gate=needs_review + execution_completed + continue_unqualified` 显示为“差异待人工判断”（`failure_class=quality_differences`），不计入“需处理”，也不得触发改标签/局部重生成；只有 `blocked` 的确定性阻断项才算卡点。落地：`app/workflow/diagnose.py`、`app/workflow/operations.py`。
7. **流程图固定 12 类**：内容提取、结构确认、组装判型、听力处理、答案填充、解析填充、知识标注、汇总整理、本地体检、内容审核、格式检查、审核上传。主图只显示分类，全部子流程及真实状态仅在悬浮文字层呈现，不常驻展开。箭头必须表达真实依赖，不能把并行分支画成串行；有实际工作才显示处理中，不能仅因依赖满足或旧文件存在而伪造子流程完成。
8. **模型不固定在仓库，也不静默兜底**：仓库 `config/model-router.json` 的 `default` 保持为空，每台设备自动跟随自己 Harness 当前选中的模型（`agent-default-model`），也可在插件设置里显式指定。显式配置的 provider/model 一定被严格使用；配置存在但损坏/不完整时必须报错，绝不静默换成其他模型。
9. **质量≠执行**："已执行，有差异"不等于通过；`review_gate` 不得因此放行；`release_ready` 只有在真实审核后才可能为真。
    **答案格式容错**：答案填充与下游比对使用同一归一化口径；加粗/画线标记、空白换行、直弯引号、全半角符号、备选分隔旁空格和句末句号不影响答案正确性时，不得造成失败、重跑或人工判断。保留原始答案，不擅改词句；数字、小数点、正负号、否定词、选项和实质内容变化仍校验。
    **格式小问题不阻断流程**：标点、全角空格、连续下划线/答题线占位、`<br>`、首字母与空档间距、表格列数/空档写法等格式表达问题只作 `[W…` 提示，不阻断 validate；只有题目/题干/题号对不上、答案或来源不符、结构错位这类实质错误才阻断。落地：`app/scripts/_common.py::report`、`workflow/checks.py`（画线/空档/表格/完成句子设为 `blocking=False`）。
10. **不动用户数据**：不重跑/改写已归档批次；不直接编辑 `workflow/manifest.json` 造完成态；不自动删除用户试卷。
11. **卡点必须指明批次**：任何卡点、待复核、失败或“继续处理”错误，必须同时显示具体批次名称和可定位的批次目录；收到泛化错误反馈时不得猜测或拉起其他批次。
12. **原卷预览图片显示**：表格单元格里的图片必须与该单元格文字在同一单元格内显示，不得拆到单独的行尾/额外单元格；表格必须自适应容器宽度并自动断行（`width:100%`、`table-layout:fixed`、`word-break:break-word`），不得因内容撑宽导致左右滑动；原图（尤其表格内图）缩得很小（表格内缩略图约 36×28，正文图 `max-width:200px`）。只影响预览渲染，不改题目数据。落地：`app/workbench/server.py::source_blocks`/`attach_media`、`lib/client.js` 表格样式。
13. **题组材料必须在题号前完整绑定**：group（题组）从原卷绑定来源时，必须使用**完整 confirmed span 窗口（文章/表格/导语 + 题目）**；定位 stem 只能证明其在窗内，绝不得据此从首个题号起截取、把题号前的材料丢掉（否则 group 的 `material` 为空、装配报 A8）。`source_span.basis` 为 `ai_classified` 或 `cleaned_source` 均属可信边界；standalone 仍只绑定自身题干。表格材料用 `[TABLE ...]` 原样保留。落地：`app/workflow/question_draft.py::_bound_source`。

14. **预览不得丢题组材料**：原卷预览按题号切出的片段必须包含题号之前的共享材料（文章、`[TABLE N]` 表格、`※Notes` 备注、分段 A/B 标签）；预览切片不得因“段落标签/弱题号”把表格或正文截掉，也不得把材料排到题目之后。只影响预览与只读核对，不改题目数据。落地：`app/workflow/preview.py::slice_range`。
    **数据完整 ≠ 展示完整**：修复“内容丢失”类问题必须同时验证①批次数据字段、②每一层消费/展示输出（预览切片、`source_blocks`、payload），只验证一层不算通过；并且必须补一条覆盖该链路的回归测试，避免同类问题再次漏检。
    **共用大题题干不得只挂在第一组**：完形 A/B、阅读各篇共用同一大题的作答导语；后一组来源窗口只含自己的文章时，必须依据已验证来源坐标补回同节标题之后、首篇材料之前的完整公共导语；含限定题号的组内导语不得传播，本组专属导语优先。生成、原文审计和预览使用同一归属规则，保留 `A`/`B` 分段标签与材料原文，禁止留空或编造；`第一节`/`第二节` 分段前缀只用于识别匹配。落地：`app/workflow/question_draft.py::_section_rubric`、`question_content.py::SECTION_LEAD`。

15. **听力并行、只有内容审核等听力**：听力（audio）切段/转写耗时最长，不得阻塞答案、题型、解析、知识点、组装（assemble）及后续 meta/underlines/validate。`assemble.depends_on` **不含 audio**；只有内容审核（`review`）显式依赖 audio（审核要读 `02_audio/full-transcript.json` 核对听力语义）。即：前置组装与题型交接完成后，听力、答案、解析与知识点可并行；解析只消费已准备的原卷/答案材料，不依赖答案填充产物。文字三路交付后汇总组装及体检可先跑完；到内容审核必须等听力完成。落地：`app/workflow/contracts.py`（assemble / review 的 `depends_on`）。

16. **Word 表格空白网格位置不得丢失**：转换表格时必须保留 `grid_before/grid_after` 明确记录的行首/行尾空白列，以空单元格表达，保持非空文字及 `rowspan/colspan` 不变；禁止为凑齐行宽猜改合并跨度。存量表格只在原件摘要匹配、源表唯一绑定、旧投影逐项相等时，通过有备份的事务恢复并复验；无证据的结构错误仍须如实报告。落地：`app/workflow/question_draft.py::_source_table_rows`、`app/workflow/self_heal_actions.py::_prepare_tables`。
17. **格式诊断必须区分失败与延期检查**：最终上传格式检查优先读取 `07_review/upload-format.json.errors`，显示错误码、题组标识和具体原因；`deferred/warnings` 不得冒充本地阻断。报告缺损时回退日志中的结构化错误，不能从末尾抓一句“必须”当失败原因。知识点目录快照/媒体部署等延期项仍须在真实上传准备时检查，不得为修本地卡点放宽远端校验。落地：`app/workflow/diagnose.py::_format_detail`、`app/workflow/format_gate.py`。

18. **Harness 运维为附加能力**：新增异常接手不得替换原录题、自愈、审核和上传功能；关闭或不可用时原流程继续。**运维在线时首个确定性阻断异常即在安全检查点建单并让出故障阶段，执行锁释放后接手分析，不等待普通自愈反复失败；首轮先落账原因、证据与修改方向并展示建议，再按授权修复复验。健康推进、审核差异和限流等待不触发此接手，同证据不重复建单烧模型。**运维只在批次身份、尝试和写锁有效时调用受控动作，未知请求先查回执，不盲重发。模型自述、会话空闲或安排重试不能当成恢复，必须以当前产物的真实复验为准。未知代码缺陷保留复现证据，不直接修改运行中的插件，不自动上传发布。**同一修复/续跑策略在相同输入与代码上重复失败必须拒绝原样重试并升级为隔离代码修复；代码修复只能在临时工作区进行，安装须显式确认且宿主设置 `MD_OPERATIONS_INSTALL=1`，安装后仍须重启、重新认领并用原失败检查复验。内容阶段出错只按受影响题目局部重生成（限答案/解析/题型/知识点/听力），未定位到题号就拒绝，不得整卷重跑或借机改动上传边界。** 落地：`app/workflow/operations.py`、`app/workflow/operations_development.py`、`lib/operations-agent.js`；说明见 `app/docs/harness-operations.md`。

19. **提示词示例即契约（示例与校验器同源）**：任务提示词里的示例 JSON 必须包含校验器要求的全部必填键；必填键/键集一旦变更（如 `answers` 的 `missing`），必须在同一次改动内同步示例、规则正文与回归测试，禁止示例比校验器旧。构建任务时由 `EXAMPLE_REQUIRED` 自检示例缺键并直接报错（不烧模型调用）；校验报错必须点名缺/多的键与不匹配的题号，让下一次重试能一次改对。**确定性失败不得用重试消化**：某阶段首轮就确定性失败的产物（示例缺字段、键集不符、覆盖不全）必须修提示词或契约，不得靠 `retries` 循环空转；同一阶段连续多轮同一报错即视为确定性卡点，按第 2 条升级为实际修复。落地：`app/workflow/agent_tasks.py::EXAMPLE_REQUIRED`、`_build_task_template`、`_fields`、`_numbers_preview`；回归 `app/workflow/test_agent_tasks.py::test_every_stage_example_satisfies_its_declared_contract`、`::test_answers_task_example_carries_every_required_field`。

20. **题型判断在前置组装内完成**：切题后结合题干、选项、大题标题和共享材料，在 `preassemble` 同一阶段产出并验收目录题型、子类、词数与完整性；不能仅合并流程图名称。来源对象和题型交接都合格才交付下游，随后听力、答案、解析和知识点可并行。`types` 标识只保留无模型的兼容交接校验及显式旧任务/局部修正入口，新批次不得再独立调用一轮题型模型。局部题型修正须同步其共享交付摘要，避免让前置组装永久过期。

21. **删除批次必须彻底删除，不留本地痕迹**：用户明确删除某批次时，必须删除 `workflows/<批次>`、`inbox/<批次>` 以及同批次的 `.workbench-trash`/`.trash`/`archives` 归档和内存调度状态；不得以“可恢复归档”冒充删除，不得留下回执、日志、缓存副本。该动作只由用户显式删除触发，自动流程仍不得自行删除用户试卷。落地：`app/workbench/server.py::purge_batch`、`lib/client.js` 删除入口。

22. **内容来源优先级（Word > PDF > 空占位）**：答案、解析、文章大意一律以 Word 原卷/答案材料为准；Word 确实没有该题/该篇内容时才用随附 PDF/OCR 提取原文兜底，并记录来源；两者都没有时解析/大意填「无」、答案记缺失。任何情况下不得用 PDF 覆盖已有 Word 内容，也不得生成、改写或跨材料借用。落地：`explanations_direct._pdf_sources`、`upload_content` 的 attachment 兜底；提示词 `content_rules.RULES`、`RULES["answers"]`、`app/prompts/roles/explanations.md`、`roles/answers.md`。**防遗漏交付约束**：`【解析】`、`[解析]`、`[IMG ...]解析[IMG ...]` 及对应文章大意标题使用同源代码规则识别，装饰图片不得作为缺失理由；裸图片不证明解析含义。大意按来源坐标与完整题号集合绑定，禁止按列表顺序分配。直抄、source_ref、模型字符串共用来源/归属/遗漏校验，说明文字不能豁免；`ORIGINAL_EXPLANATION_OMITTED` / `ORIGINAL_SUMMARY_OMITTED` 必须定位原文局部修复复验，禁止改完成状态、改无关标签或跳检查。组装及后续元数据/画线/最终产物须同步已接受内容；正文与展示消费链路都要验证。落地：`content_labels.py`、`content_fragments.py`、`content_rules.py`、`unit_tasks.py`、`handoff.py`、`manifest.py`；全量任务、文件协议、并行分片和局部修复提示词都须包含该约束，回归必须验证实际构建出的任务提示。书面表达仍按规则23。

23. **书面表达不按客观题校验**：书面表达是作文，没有确定答案；范文/写作指导可有可无。只要题目存在即可，不要求答案、解析或文章大意逐字绑定原文，缺失填「无」属正常；禁止自行撰写范文或讲解充当原文。若原卷确有【参考范文】/【思路点拨】则逐字保留，无法逐字定位只提示、不阻断。落地：`upload_content` 对 `书面表达` 只告警不报错、`agent_tasks` 答案覆盖豁免、`check_answers`/`check_explanations`/`publish_check` 书面表达豁免；提示词 `content_rules.RULES`、`RULES["answers"]`、`roles/explanations.md`、`roles/answers.md`。

24. **卡点必须落台账（`ISSUES/`）**：插件试卷流程每遇到一次卡点，必须用 `PYTHONPATH=app python3 -m workflow.issue_log add` 登记到 `ISSUES/open/`，写明**具体批次、阶段、失败检查、现象与判定口径、修复方向**（不得只写“按阻断项修复”）；修复并复验后用 `PYTHONPATH=app python3 -m workflow.issue_log fix --id <id> --resolution … --commit … --verify …` 移入 `ISSUES/fixed/`，自动标注 `status: fixed` 与修复提交/复验证据。禁止只把卡点留在对话里、修完不留档；未复验不得标记已修复；同一「批次+阶段+检查」重复出现由命令累加 `occurrences` 并更新 `last_seen`，不得重复建单；修好后不删除记录，留档供同类问题复用。审核差异（`review_gate=needs_review + execution_completed + continue_unqualified`）与限流/额度等瞬时错误**不算卡点**，不登记。**代码强制**：自愈判定 `repair_not_applicable`/`repair_failed`/`retry_budget_exhausted`/`gate_retry_budget_exhausted`、监督器判定「长时间无有效进展→exhausted」时，会自动写入批次内 `workflow/blockers.json`（同卡点累加、best-effort、不建幽灵批次）；用 `python -m workflow.issue_log sync --batch <批次目录>`（或 `--all`）汇总进 `ISSUES/open/`，按「批次+阶段+检查」去重。自动留档不等于已修复，仍须复验后 `fix`。台账只写 `ISSUES/` 与批次内 `workflow/blockers.json`，不得改批次数据、manifest、收据或上传边界。落地：`ISSUES/README.md`、`app/workflow/issue_log.py`、`app/workflow/self_heal.py`、`app/workflow/supervisor.py`；回归 `app/workflow/test_issue_log.py`。

25. **同步练习类的题型与组题判定必须走统一口径**：按课时编排的同步练习册/课时同步材料（每课时常分基础段与提升段、提升段题号从 Ⅰ 重新开始），题型、子类、组题/单独题三者一起判定，`unit_kind` 在结构确认阶段定死。口径以 `app/references/sync-practice-rules.json` 为唯一来源（来自 26秋《课时A计划》英语 RJ 七上·八上·九上 854 处大题实测 + 用户逐条确认），代码入口 `app/workflow/sync_practice.py`（`classify` / `classify_with_body` / `audit_units` / `prompt_block`）。规则要点：完形/阅读/任务型/选词/短文填空/教材语篇填空/补全对话/语音题整大题/看图题/四线三格/填词游戏 → group；单项填空/完成句子/单词拼写一句一空/翻译/改错/书面表达 → standalone；`☆拓展设问` 并入所属组题且不计入空数；`综合阅读提升`、`单元话题写作` 只是版块容器不计数。**仅适用于同步练习类材料**，单元卷/期中期末/月考/中考卷不得套用。结构确认阶段识别到同步练习特征时把口径追加进提示词，并用 `audit_units` 与原卷比对，差异只写 `01_source/structure.ai.classified.json` 的 `sync_audit` 留痕，**不得据此自动改题型/组单或放行闸门**。新增表述先在 JSON 加规则（顺序即优先级）再跑 `PYTHONPATH=app python3 -m workflow.sync_practice` 自检并补 `app/workflow/test_sync_practice.py` 用例。说明见 `app/docs/sync-practice.md`。

26. **课时同步来源按单课时识别**：后续按一个课时一批次录入，来源由原件证据识别年级册次、教材版本、系列、单元和课时，例如「七上人教版课时A计划 Unit 1 课时1」。`uint` 规范为 `Unit`，Starter Unit 与普通 Unit 区分；整册多课时只记册级来源，不用首课冒充整册。原件文件名与真实路径可取证，禁止从批次名猜测；证据不足留空、既有有效非空值保留。落地：`app/workflow/metadata_ai.py`、`app/workflow/test_metadata_ai.py`，说明见 `app/docs/sync-practice.md`。

27. **同课时重号按来源身份关联，保留原卷顺序**：同步练习新批次采用 `scoped-v1`；保留 `Ⅰ、Ⅱ、Ⅲ`、标题、基础/提升的出现序次及原小题号，不重编号。`section_id/unit_id/question_ids` 分别绑定大题、单元及子题，答案/缺失/证据用 question ID，解析/知识点/审核/预览/保存以 unit ID 加本单元原号定位；禁止全局数字同号兜底。A/B 同号须材料窗口唯一，无号图片题只用内部兼容别名且不伪装印刷题号。漏题、同窗重复、跨材料、来源不明仍阻断；原答案附录缺少大题对应证据不猜、不借、不伪记缺失。旧批次维持原协议且不改历史数据。落地：`app/workflow/question_identity.py`、结构/答案/解析/组装/审核/预览链路，说明见 `app/docs/sync-practice.md`。

> 以上是**当前生效**的硬规则集合。新增或修订硬规则时，必须同步更新本节**并在 `CHANGELOG/RULES.md` 追加一条**（见第三节）。

## 三、变更记录制度（强制）

项目根目录有 `CHANGELOG/`，专门记录本项目的一切重要变更与规则强调。**每次改完都要记，不要等人提醒。**

- `CHANGELOG/README.md`：记录制度说明 + 索引。
- `CHANGELOG/RULES.md`：**规则/强调台账**。凡是用户强调过的、反复强调过的、以及新定的硬规则，都追加一条（含日期、原话要点、落地位置）。
- `CHANGELOG/YYYY-MM-DD-<slug>.md`：**每次重大改动一份**（重大 = 影响运行行为/模型路由/提示词契约/数据结构/审核口径/上传边界）。小修（改注释、补测试、修 typo）合并进当天的 `-misc` 或省略。

一条改动记录至少写清：
1. **背景/触发**（用户提出了什么、哪里坏了）；
2. **改了什么**（文件 + 关键行为变化）；
3. **没改什么 / 边界**（避免被误读为"全自动通过"）；
4. **如何验证**（测试名与结果数字）；
5. **回退方式**（如有）。

触发记录的时机：用户提出新的硬要求、用户纠正我们的做法、修掉一个卡点、调整模型路由/提示词、改变审核或上传口径——**做完就写，不等下一轮**。

## 四、验证要求

- 改 Python 工作流：至少跑受影响的 `python -m unittest workflow.test_<area>`，重大改动跑全量 `workflow`（从 `app/` 目录运行，`PYTHONPATH` 指 `app`）。
- 改前端/路由：`node --test backend/*.test.cjs`。
- 改宿主插件：`node --check lib/index.js lib/client.js`。
- 在变更记录里写出实际通过数字，不要只写"已测试"。

## 五、协作提示

- 内置 8 个子 Agent（前置组装题型判定/答案/听力/题型兼容/解析/知识点/装配/审核）的角色规范在 `app/prompts/roles/`，每个都含"出问题怎么办"硬规则；改提示词时四个小节（职责/权限边界/脑子检查/出问题怎么办）必须齐全。
- 模型与子 Agent 路由在 `config/model-router.json`：`roles` 优先于 `stages`，`review` 轮次优先；选择逻辑在 `backend/route_pick.py`（有单测）。
- 不确定先读 `app/AGENTS.md` 与 `app/docs/`，仍不确定就在变更记录里写清未决点，不要自行放宽质量或上传边界。
- **版本控制在 `master` 上一条线推进**：不新开分支、不建 `develop`/`feature` 分支，后续提交直接落到 `master` 并推 `origin`（用户 2026-11-09 明确要求）。改动前先 `git fetch` 看远端是否领先，禁止强推。
