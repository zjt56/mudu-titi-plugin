# RULES — 规则与强调台账

## 2026-11-11 单课时来源识别与原件识别边界

- 用户要求：后续一个课时一批次；识别年级、版本、单元、课时作为来源，例如“七上人教版课时A计划 Unit 1 课时1”。
- 规则：以真实原件名/路径与独立章节标题为证据；uint 规范为 Unit，Starter Unit 区分；整册多课时只生成册级来源；不猜系列、不从批次名取证，已有有效非空值保留。
- 同步正文中的重置题号、无序号标题与中段范文不能将后续正文误切入附录。保留原号与来源行号；当前下游全局数字题号限制不得被删校验掩盖。
- 落地：根 AGENTS 规则26、`app/workflow/metadata_ai.py`、`sync_practice.py`、`extract.py`、`structure_ai.py`、`agent_tasks.py`，回归及边界见 `2026-11-11-sync-source-recognition.md`。

## 2026-11-10 首个阻断异常即交 AI 运维分析（用户纠正）

- 用户原话：“一旦这种异常抛出，AI运维就能立马去做分析、做判断，然后给出修改建议或者方向。”
- 规则：宿主在线且模型配置有效时，首个真实确定性阻断异常就在安全检查点建单，让出故障阶段，执行锁释放后由运维接手；不再等普通自愈失败数轮。先记录检查项、原因/假设、证据和修改方向，在面板显示建议；再按授权修复和复验。同证据保持事件身份，不重复建单烧模型。
- 边界：正常执行、审核差异、限流/额度等待不触发；在线心跳过期、关闭或不可用继续原自愈；不放松写锁、来源校验及上传边界。
- 落地：`operations.service_heartbeat/handoff_if_available/_discover`、`auto._advance_impl/_result`、宿主可用性报告与运维建议面板；记录 `2026-11-10-operations-first-blocker.md`。

## 2026-11-10 无任务也展示完整流程图、节点/连线自绘且自适应（用户强调）

- 用户原话：“即使没有任务的状态下，它流程图也是全量展示的，只是没有工作状态的显示而已……方便对外演示”；随后要求“把流程图给它高度做高一点，目前太拥挤了”“用 SVG 去绘画这个流程图，同时绘画几种状态……运行中像以前一样闪烁”“这个样式自适应宽度……根据页面的变化自适应”。
- 规则：无任务（含首次进入、本轮全部完成）时仍渲染十二类完整依赖图，只去掉进度数字、状态文字、动效和“继续处理”按钮等运行态；不得用“暂无流程/本轮结束”整块替换。节点用自绘 SVG 表达已完成/未运行/运行中三态，运行中保留脉冲外圈+呼吸圆点闪烁；连线按状态分已走过实线、流向运行中流动虚线、未走浅虚线。整图不固定像素宽高，随容器宽度等比自适应。
- 追加：连线改为一条线首尾相连（每行 3 个共 4 行蛇形），不画多条分支；整图缩小到约 3/4 宽度，随容器等比自适应。
- 落地：`lib/client.js`（compactFlowStages overview 分支、节点/连线样式、`--wf-flow-node-*` token、`wf-flow-dash`）；回归 `backend/upload-stage-flow.test.cjs`、`backend/upload-flow-redesign.test.cjs`；效果图 `artifacts/flow-node-states.svg`。

## 2026-11-10 十二类流程与悬浮子流程（用户确认）

- 用户确认分类：内容提取、结构确认、组装判型、听力处理、答案填充、解析填充、知识标注、汇总整理、本地体检、内容审核、格式检查、审核上传。替代此前九类与十节点上限。
- 全部子流程只在鼠标悬浮文字层显示，逐条附真实状态；主图不常驻子步骤。未执行/缺少当前证据为待处理，运行、失败、需重跑与明确跳过保留真实状态，不能一律显示已完成。
- 内容提取及元信息识别在交付前结束；组装判型包含题型交接。之后听力、答案、解析、知识点具备并行资格，解析消费原卷/答案材料，不等待答案填充产物。
- 文字三路完成即可汇总、体检；只有内容审核等听力。图中连线表达依赖，不把四路画成串行；重试仍回原分类显示。来源、审核差异、人工上传发布边界不变。
- 落地：contracts、auto/runner、stage_preflight、substeps、server.stage_cards、client.compactFlowStages 与真实依赖连线；记录 `2026-11-10-twelve-phase-workflow.md`。

## 2026-11-10 没有试卷任务时自动巡检必须暂停（用户强调）

- 用户原话：自动巡检在没有试卷任务的时候要暂停，但目前没有暂停。
- 规则：监督器只在存在未完成本地批次（或正被占用）时运行；全部批次已完成/已到远端上传边界时必须暂停等待唤醒，不能按固定间隔空转。监督器自身派发不得清空已提交的重试预算，否则 no_progress 永不累计、永远不会进入 exhausted。
- 落地：supervisor.needs_attention/has_pending_work、watch() 的 wake/pause_poll、server._wake_supervisor、server._dispatch_supervised(reset_budget=False)；记录 `2026-11-10-auto-supervisor-idle-pause.md`。

## 2026-11-10 审核附件与题目附件分离（用户指向官方文档）

- 用户原话：了解题库服务器附件上传要求，准备把 Word 和 PDF 作为附件一起上传到批次；随后给出 `POST /api/uploads/{staging_id}/attachments` 协议并指向 `http://101.43.1.57/docs/#format`。
- 规则：审核 Word/PDF/图片走暂存附件接口，创建暂存必须人工会话，至少一个附件才能确认；追加非幂等，响应丢失先核对暂存列表名称/数量；题目 JSON 的 `attachments` 仍只允许 image/audio。审核结算后清理审核附件只能由人工会话显式调用，不自动上传、发布或清理。
- 落地：`workflow/upload_client.py`、`workflow/upload.py`、`docs/upload-api.md`；记录 `2026-11-10-review-attachment-upload.md`。

## 2026-11-10 加强解析防遗漏提示词并写入代码规则

- 用户原话：“加强提示词约束并写进代码规则，确保后续不会出现”。
- 防遗漏约束必须进入实际完整任务、文件协议、自动/局部分片提示，不能只写在说明文档；已有原文不能填「无」，遗漏码须按题号局部修复复验，不改无关标签或跳检查。图标/方括号/括号标题由同源代码规则识别；source_ref 与直抄/体检不能使用不同识别口径。大意禁止按列表顺序分配，冲突不能伪装缺失。
- 落地：content_rules.RULES、unit_tasks、roles/explanations.md、content_labels、content_fragments、upload_content；实际提示构建及引用展开回归见 `2026-11-10-explanation-omission-contract.md`。根 AGENTS 规则22同步，书面表达例外保持。

## 2026-11-10 原解析直抄也须真实交付，不能由说明文字跳过检查

- 触发：2024 卷 Word 以图片夹住“解析”“文章大意”作为标题，直抄漏识别却写入占位并登记完成。
- 既有保真/来源规则的落地：图标文字标题同样有效；已有原解析不能当缺失；大意绑定完整题号集合，不按顺序分配。直抄和模型产物共用来源/遗漏检查和通用任务绑定导入，“按题号直接抄录”仅为说明。下游元数据须实际同步新解析/大意，不能只凭结构合法或旧完成状态复用。
- 位置：explanations_direct / content_rules / agent / handoff / manifest；案例与验证：`2026-11-10-explanation-icon-label-and-delivery.md`。

## 2026-11-10 审核上次改动并加强提示词约束

- 用户要求：“审核上面改动，加强提示词的约束”。审核差异保护必须进入实际一审/二审专用提示，而不只写在角色文件；审核/自愈/运维不得靠改正式标签、resolve/dismiss、换恢复动作或重审到一致消除差异。
- 0 卡点、空队列和旧执行标记不证明当前审核/最终格式工作完成；当前执行失败、输入失效必须保留真实证据。差异码即使含 field=knowledge/producer_stage，也不作为内容生产者故障定位依据。
- 落地：agent_roles.section、prompts/roles/review.md、agent_tasks.RULES[review]、self_heal._plan/_producer_for_issue、lib/operations-agent.js、diagnose 摘要及历史错误处理；记录见 `2026-11-10-review-difference-audit-prompts.md`。

## 2026-11-10 用户选择的模型必须保存在设备本地（用户反馈“我选的是官方，却显示额度用尽”）

- 触发：用户选择的官方模型被仓库内 `config/model-router.json` 的检出/重置覆盖，路由回落到 WorkBuddy 中转并报“额度已用尽”。
- 规则：模型选择属于设备本地设置，必须存 `~/.dsh/plugin-state/md-upload-tiku/model-router.json`；仓库 `config/model-router.json` 只作默认模板（default 为空）。升级、检出、重新克隆不得覆盖用户选择。
- 显式模型仍严格优先，损坏必须报错不静默替换；AI运维顺序 `roles.operations` → 用户 default → Harness。
- 落地：`lib/index.js::ensureRouterFile/readRouter`、`backend/wf-router.mjs`、`backend/wf-router.sh`、`app/workflow/local_agent.py::_router_file`；记录 `CHANGELOG/2026-11-10-model-selection-persistence.md`。

## 2026-11-10 书面表达不按客观题校验（用户明确）

- 原话：“书面表达题型没有确定的答案，它是作文，可能有范文与怎么写作的类似指导，请你不要以常规题目校验答案，如果没有范文和解析都是正常的，只要题目存在就行了，不要严格校验这么多。”
- 书面表达不要求答案/解析/文章大意逐字绑定原文；缺失填「无」属正常；有范文/写作指导只提示、不阻断。
- 禁止要求或编造书面表达答案，禁止自写范文冒充原文；题目/题干仍必须存在。
- 只放宽书面表达；其他客观题保持严格。
- 落地：`agent_tasks._validate`（answers 覆盖与绑定豁免）、`upload_content`（书面表达只 warning）、`check_answers`、`check_explanations`、`publish_check`；提示词 `content_rules.RULES`、`RULES["answers"]`、`roles/explanations.md`、`roles/answers.md`；根规则 23；记录 `CHANGELOG/2026-11-10-writing-not-objective.md`。

## 2026-11-10 内容来源优先级：Word ＞ PDF ＞ 空占位（用户明确）

- 原话：“无论是试卷还是答案解析，文章大意等，都以world为准，如果world没有，优先取PDF，如果还是没有，就留空的占位符。”
- 答案、解析、文章大意一律以 Word 原卷/答案材料为准；Word 有的一律用 Word，PDF 不得覆盖、纠正或改写。
- Word 确实没有该题/该篇内容时，才用随附 PDF/OCR 提取原文兜底并记录来源；两者都没有时解析/大意填「无」、答案记缺失并留证据。
- 禁止生成、改写、跨材料借用或用 AI 内容充当原文。
- 提示词与代码必须同时落地：`content_rules.RULES`、`RULES["answers"]`、`RULES["assemble"]`、`roles/explanations.md`、`roles/answers.md`；`explanations_direct._pdf_sources`、`upload_content` 附件兜底；根规则 4/22。
- 记录：`CHANGELOG/2026-11-10-content-source-priority.md`。

## 2026-11-10 排队区严格串行：前一份未完成不得启动后一份（用户明确）

- 原话要点：“前一个试卷遇到卡点或者问题时，排队区的其他试卷就自动开始任务了，这是错误的逻辑，必须等到上分任务全部完成且从排队区移除后才可以启动”。
- 规则：同一时刻只允许「排队区」里最靠前的一个未完成批次运行；其余批次必须等待，直到前面的批次 `flow_done`（或已上传）并移出排队区。前一份卡住/需要人工时，后续批次保持等待，不自动开始、不因此消耗模型。
- 落地：`app/workbench/server.py::_queue_batch_order`/`_queue_head`/`_waiting_for_predecessor`（`_launch_advance` 与 `_advance_worker_loop` 双重闸门）；前端 `lib/client.js` 显示“等待前一份…”。回归 `workbench.test_upload_status`；记录 `CHANGELOG/2026-11-10-strict-queue-serialization.md`。

## 2026-11-10 原解析溯源与模型 JSON 结构必须稳健（用户要求彻底修复并加强提示词）

- 原话要点：“彻底修复此类 bug，并加强提示词的说明”。
- 规则：①原解析/原大意/书面表达【思路点拨】等合法原文，字段标题（【解析】【答案】【分析】【详解】【思路点拨】【文章大意】）由程序统一剥离后再溯源，严格与非严格口径一致；真正改写/半截/跨材料仍拒绝。②模型输出只允许一个根 JSON 对象，多余 `}`/`]` 在非严格阶段可去掉后重解析（能解析成完整对象才接受），严格审核不修补；`valid+valid` 拼接仍拒绝。③各阶段提示词必须显式写明上述 JSON 单对象纪律、标签剥离、书面表达思路点拨用法、整组题型必带维度。
- 落地：`app/workflow/content_fragments.py`、`upload_content.py`、`local_agent.py`、`content_rules.py`、`agent_tasks.py`、`app/prompts/roles/explanations.md`、`knowledge.md`；回归 `test_upload_content`、`test_content_fragments`、`test_local_agent`；记录 `CHANGELOG/2026-11-10-thorough-fix-explanation-and-json.md`。

## 2026-11-10 上传不校验已发过的试卷、删除必须干净（用户明确）

- 原话要点：“不去校验之前已经发过的试卷，也不留存对应的记录，只要删除批次了，就删除干净”。
- 规则：上传去重不得比对或复用已提交云端（上传阶段 completed/approved，含待确认）的试卷；重新上传应作为新任务出现。仅对仍在处理中（未发）的同内容批次复用，避免重复跑模型。
- 删除批次仍走 `purge`，永久清除 `workflows/<批次>`、`inbox/<批次>`、回收归档、音频状态/全局镜像、调度与内容缓存，不留残余；自动流程不得自行删除用户试卷。
- 落地：`app/workbench/server.py::_batch_already_sent`/`_existing_upload_batch`/`purge_batch`；回归 `workbench.test_upload_status`；记录 `CHANGELOG/2026-11-10-skip-sent-papers-dedupe.md`、`CHANGELOG/2026-11-10-duplicate-upload-visible.md`。

## 2026-11-09 AI 运维必须能真正接手（用户质疑“从来没有工作过”）

- 触发：用户反馈 AI 运维从未工作。核查：服务 `available=true, enabled=true`，但 `incidentCount=0`，所有批次 `operations.json` 的 `incident=null`。
- 根因：`9387566` 把 self_heal 签名改为含“重试证据”（输入/原卷/代码指纹），`operations._discover` 仍用旧公式 `sha256(stage+detail)` 重算，二者永远不相等，任何真实 `repair_failed` 都不会被识别。
- 规则：运维事件身份必须以 self_heal 实际记录的事件 signature 为准，不得在其它模块自行重算另一套签名；新增/修改签名公式时必须同步所有消费方并补跨模块回归。
- 运维仍只升级“已真实失败过自愈”的当前卡点；`review_gate` 差异、限流/额度、正常运行不得当成故障。
- 落地：`app/workflow/operations.py::_discover`；回归 `workflow.test_operations::test_discovery_accepts_self_heal_evidence_signature`；记录 `CHANGELOG/2026-11-09-operations-never-triggered.md`。

## 2026-11-09 答案不因无害小符号卡住（用户明确）

- 原话要点：“对答案的要求不要这么苛刻……即使他加了两个这种小符号，他也不影响答案的正确性……下游的这种校验不要这么严”。
- 答案填充与下游装配共享格式归一化，排除加粗/画线、空白换行、直弯引号、全半角符号、备选分隔空格及句末句号造成的误判；保留原答案，不因此转人工或重跑。
- 数字、小数点、正负号、否定词、选项和真实词句变化仍参与校验。格式等价不等于语义判断，也不授权编造缺失答案。
- 落地：根规则9；`workflow/agent_tasks.py::_answer_comparison_text`、`_validate`、`_provenance`；`docs/pipeline.md`；`CHANGELOG/2026-11-09-answer-format-tolerance.md`。

## 2026-11-09 删除批次必须彻底删除（用户明确）

- 原话要点：如果删除该批次的事件后，要彻底的删除，不要在本地留有类似的痕迹。
- 规则：用户明确删除某批次时，必须删除 `workflows/<批次>`、`inbox/<批次>`、同批次 `.workbench-trash`/`.trash`/`archives` 归档和内存调度状态；不得用“可恢复归档”冒充删除，不得保留回执、日志、缓存副本。
- 边界：只由用户显式删除触发；自动流程仍不得自行删除用户试卷；普通 `archive` API 的可恢复行为保留给旧接口，但工作台“删除批次”按钮改走 `purge`。
- 落地：`app/workbench/server.py::purge_batch`、`lib/client.js`；根 `AGENTS.md` 硬规则21。

## 2026-11-09 模型列表/路由只能出现真正可调用的 provider（NO_ADAPTER 卡点修复）

- 触发：批次【木牍教育】18年安徽中考英语 前置组装失败，stderr 实为 `dsh: NO_ADAPTER: no adapter registered for provider "magpie"`，界面却显示「Agent subprocess failed…重跑一次通常即过」。
- 路由 profile 只复制 base profile 的 `llm-pi-ai` / `llm-deepseek` 条目；宿主独有的中转 provider（WorkBuddy `magpie`、账号目录）不在其中，选中必然失败。
- 模型列表不得列出路由 profile 不能服务的 provider；`wf-router` 调用前必须校验 provider 在生成的路由 profile 中，否则带可选列表直接报错，不得静默换模型，也不得把配置错误伪装成阶段产物不合格。
- 落地：`lib/index.js::servableHostModels`、`backend/wf-router.mjs::overlayProviders`；测试 `backend/wf-router-native.test.cjs`、`backend/model-list.test.cjs`；记录 `CHANGELOG/2026-11-09-router-provider-guard.md`。

## 2026-11-09 模型不固定：每台设备跟随自己的 Harness 模型（用户明确）

- 原话：“模型不是固定的，是随着不同设备自由选择他们harness的模型”。
- 仓库 `config/model-router.json` 的 `default` 保持为空，不再把任何厂商/中转写死为默认。
- 路由在 `default` 为空时读取本机 `profiles/<profile>/cordis.patch.yml` 的 `agent-default-model`（顺序：`DSH_PROFILE` → `desktop` → `wf-deepseek-fast` → `wf-deepseek`），跟随该设备 Harness 菜单当前模型。
- 插件设置里显式指定的 `default` / `stages` / `roles` 仍然严格优先；显式配置存在但不完整或损坏时必须报错，不得静默换成其他模型。
- 缓存与分片快照的模型身份也绑定该 profile 文件，换模型后不会复用旧结果。
- 插件不需要用户填写模型 API Key：模型凭据由 Harness 提供。初始化引导不得要求“填 API Key”，默认直接路由到 Harness 当前模型；无显式 `agent-default-model` 时取本机 Harness profile 第一个可服务模型兜底。
- 跟随 Harness 默认模型时也必须做“可服务性”过滤：provider 必须在该 profile 的 `llm-pi-ai`/`llm-deepseek` 声明中，否则跳过（WorkBuddy 的 magpie 在 headless 里 NO_ADAPTER）。全部不可用时明确要求用户选模型，不静默换模型。
- 落地：`backend/route_pick.py`、`backend/wf-router.mjs`、`lib/index.js::servableHostModels/harnessDefaultModel`、`lib/client.js`、`app/workflow/local_agent.py::_part_model_binding`；根规则8。

## 2026-11-09 题型判断真正纳入前置组装（用户明确）

- 原话：“按照我的要求修改，题型放在组装流程中”；“在切题的过程中顺便判断了题型”。
- 前置组装同一阶段建立来源对象并完成目录题型、子类、词数和完整性判断；来源与题型共同验收后交付下游，不能只合并流程图名称。
- 后续答案、听力和知识点可并行，解析等待答案；听力仅阻塞内容审核，不阻塞汇总和本地体检。
- `types` 保留为无模型的兼容交接校验和显式旧任务/局部修正身份，常规新批次不额外判型；局部修正须同步共享交付摘要，避免假过期。
- 落地：根规则20；`workflow/preassembly.py`、`contracts.py`、`agent_tasks.py`、`local_agent.py`、`agent_protocol.py`、`handoff.py`、`manifest.py`；回归 `workflow.test_preassembly`。

## 2026-11-09 快速纠错：同证据不得原样重试，代码缺陷走隔离修复（用户授权「可以」）

- 用户原话要点：要实现快速纠正卡点；授权按建议补齐故障证据包、重复失败升级、局部重生成、隔离代码修复/测试/受控更新/续跑。
- 卡点处理必须指出具体失败检查（报告、错误码、检查名、题号），不能只写"校验不通过"；`diagnose` 返回自包含证据包。
- 同一修复/续跑策略在**相同输入、证据与插件代码**上重复失败时，后端必须拒绝原样重试并升级为代码修复；输入或代码真实变化后才允许再试。限流、网络、超时等瞬时错误不在此列。
- 未知程序缺陷不得靠反复重试冒充修复：只能在隔离工作区复现、修复、跑测试、生成安装计划，再由受控宿主确认安装并单次重启；安装成功不等于故障恢复，必须重启后重新认领并用原失败检查与消费者链复验。
- 局部重生成只缩小重跑范围：仅内容阶段（答案/解析/题型/知识点/听力），只带本批次确认题号；未指定题号时只用证据中真实出现的单元，定位不到就拒绝，不得整卷重跑；上传/发布边界与确定性阶段永不适用。
- 隔离工作区不是操作系统沙箱；生产安装必须显式确认且宿主设置 `MD_OPERATIONS_INSTALL=1`，默认不可用。`lib/*.js`、配置、数据结构只产出补丁，不得宣称自动生效。
- 不自动上传、不发布、不改归档批次；真实卷只在明确授权下执行。
- 落地：`app/workflow/operations.py`、`app/workflow/operations_development.py`、`lib/operations-agent.js`、`app/docs/harness-operations.md`；测试 `workflow.test_operations`、`workflow.test_operations_development`、`backend/operations-agent.test.cjs`。

## 2026-11-09 共享导语归属修正（审核后修复）

- 原文坐标必须以已验证绑定的 line_basis 为准，不混用原始/去噪行号。
- 仅继承同节标题之后、首篇材料之前的完整公共导语；限定题号的指令不传播，本组专属要求优先。
- 公共导语缺失须由原文审计检出，预览和上传载荷同样保留。修正此前“标题之前”的错误表述。
- 落地：`shared_rubric.py`、`question_draft.py`、`question_content.py`、`preview.py`；根规则14。

## 2026-11-09 共用大题题干不得只挂在第一组（用户强调）

- 原话：“完型填空的AB，以及阅读理解的几篇文章都是共用一个题干内容的，请完善对应的部分内容，确保这部分的题干不丢失。”
- 完形 A/B、阅读各篇共用同一大题的作答导语；后一组的来源窗口只含自己的文章，必须补回同一大题标题之前的原文导语，留空视为丢失。
- 补入的导语只写 `stem`，不得据此截断材料；`A`/`B` 分段标签和文章正文必须原样保留。原文没印导语的试卷保持为空，不生成文字。
- `第一节`/`第二节` 是分段标记：识别时去前缀匹配，原文行按原样保存。
- 落地：根 AGENTS.md 硬规则14附注；`app/workflow/question_draft.py::_section_rubric`、`question_content.py::SECTION_LEAD`；`app/docs/workflow-regressions.md`；回归 `workflow.test_question_draft`；详见 `CHANGELOG/2026-11-09-shared-section-rubric.md`。

## 2026-11-08 Harness 运维接手必须保留现有功能（用户明确）

- 原话：“可以，你帮我去做一个这样的一个功能，前提不修改现有的一个已有的功能，你帮我完善这个。”
- 新增Harness运维会话为附加层；原录题、自愈、审核及上传入口保留，关闭或不可用不替换旧执行器。只有真实失败才升级，不能把标签差异或正常长时执行当故障。
- 处理记录必须区分已接手、已执行动作、待续跑、验证成功及需深入修复；只有当前产物经真实检查才可认定恢复。未知请求先查回执，不重放；写入绑定批次身份、接手代次和执行锁，不自动上传发布。
- 落地：AGENTS硬规则18；`app/workflow/operations.py`、`lib/operations-agent.js`、`lib/operations-settings.js`、原页面附加运维区域；`app/docs/harness-operations.md`。

## 2026-11-08 表格空列与格式诊断规则固化（用户强调）

- 原话：“将这个修复的规则写到插件里，避免下次出现此类问题。同时提交到GitHub。”
- Word 表格必须保留原件 `grid_before/grid_after` 明确记录的行首/行尾空白位置，转换为空单元格，不为凑行宽猜改 `rowspan/colspan`。存量恢复须有原件摘要匹配、唯一来源绑定及旧投影逐项相等的证据，经事务备份、来源审计与格式复验后安装。
- 格式诊断以结构化 `errors` 为本地失败依据，显示错误码与题组；`deferred/warnings` 不得误当阻断。报告缺损才回退日志的结构化错误，知识点目录快照/媒体部署等远端准备校验仍保留。
- 落地：根 AGENTS.md 硬规则16/17；`app/workflow/question_draft.py::_source_table_rows`、`self_heal_actions.py::_prepare_tables`、`diagnose.py::_format_detail`；`app/docs/workflow-regressions.md`；回归 `test_question_draft/test_self_heal_actions/test_diagnose`；详见 `CHANGELOG/2026-11-08-format-table-grid-diagnosis.md`。

## 2026-11-08 能力提示不得吞掉真实标签差异（2015 卷第 6–10 题）

- 用户纠正：一审与原标签不同却没有待审核标记，第 6 题及后续多题均如此。
- 程序按 type/subtypes/knowledge 的结构化值比较出的真实差异必须进队列；不能因审核者理由提及图片未看、音频未听、占位等，把该差异降为 informational。能力受限豁免只针对限制本身。
- 验证必须对照整卷全部比较结果、持久队列与实际工作台待审状态，不能只验证少量正常题或只报队列条数。
- 落地：根 AGENTS.md 硬规则 6；app/workflow/review.py::_is_capability_gap；test_review_consensus；CHANGELOG/2026-11-08-review-capability-keeps-differences.md。

## 2026-11-08 盲审隐藏标签不得误报缺失（2015 卷）

- 用户反馈：“一审和原标签都是一致的，但还是被打上待审核标签”，要求找出原因并根除。
- 盲审不公开原标签，模型只独立填 assessment，差异比较由程序负责；隐藏字段缺失不能成为人工发现。此类无依据结论由程序拒收并自动补审，原始响应保留，不改写成 ok。
- 标签一致但有真实答案/解析/原文问题仍须送审；真实知识点/题型/子类差异始终保留。修复后须重建已有派生队列，避免仅改提示而界面仍读旧误报。
- 落地：根 AGENTS.md 硬规则 6；app/workflow/review_rounds.py::_round_prompt（review-prompt-v10）；app/workflow/review.py::_reject_blind_label_absence；app/docs/review.md；CHANGELOG/2026-11-08-blind-review-label-absence.md。

> 用户强调过、反复强调过、以及新定的硬规则，全部记录在此。新增/修订规则时追加一行，并同步 `AGENTS.md` 第二节。
> 「原话要点」尽量保留用户当时的表达，避免转述失真。

| 日期 | 规则要点（含原话关键句） | 落地位置 |
|---|---|---|
| 2026-11-06 | **零人工本地执行**：全程不得人工干预/判断/选择，不得因待复核、待核验、人工确认停住。 | `app/workflow/auto.py` ZERO_TOUCH；本 AGENTS 硬规则 1 |
| 2026-11-07 | **卡点必须真自愈**：AI 自己诊断原因、实际执行修复、复验和续跑；不能只加修复函数或提示、仅原样重试后停住，也不能等用户反复点继续。 | `app/workflow/self_heal.py`；硬规则 2 |
| 2026-11-07 起反复强调 | **任何单点失败都不能让整个项目停住**（答案提取、装配、任一步都一样）；出问题要自己再看一遍、修一下，不能就这样停了。 | `self_heal.UPSTREAM`、`defer_and_continue`、`supervisor`；硬规则 2 |
| 2026-11-07 | **子 Agent 各管一块 + 自带脑子检查**；角色化提示词与按角色独立选模型。 | `app/prompts/roles/`、`config/model-router.json` `roles`、`backend/route_pick.py` |
| 2026-11-07 | **排队区进度按可见流程图节点显示**，不出现内部 18 阶段导致的"十几/18"。 | `lib/client.js` `compactProgress`；硬规则 7 |
| 2026-11-07 | **重大改动与规则强调都必须落记录**：不要说反复叫去做，有重大变更也要记下来。 | 本 `CHANGELOG/`；硬规则第十条附注 |
| 2026-11-07 | **上游交付必须满足所有下游契约**："上游的东西必须符合下游的东西，否则上游的就不会让下游去推进流程；不要上个流程没完成，下个流程就启动"。未完成、字段/格式/覆盖不合格、输入或绑定过期均不得视为可交付；无关分支仍可继续。 | `workflow/contracts.py`、`handoff.py`、`manifest.py`、`agent_protocol.py`、`auto.py`；硬规则 2 |
| 既有 | **不自动上传/发布**：本地运行到 format_gate 为止，远端提交须明确授权。 | `auto.py` ZERO_TOUCH；硬规则 3 |
| 既有 | **原文保真**：题干/选项/材料须等于原卷；Word 与 PDF 并存以 Word 为准。 | `question_draft.py`、`assemble`；硬规则 4 |
| 既有 | **空白语义**：未判定留空；确认不存在保留占位，不得一律清空。 | `content_rules`；硬规则 5 |
| 既有 | **审核只收两类差异**（知识点/基本差异）；难度、来源、分类、选题、词数不进审核；能力受限只作提示。 | `workflow/review.py` MANUAL_REVIEW_SCOPE；硬规则 6 |
| 既有 | **模型不静默兜底**：配置缺失/损坏/缺默认必须报错。 | `backend/route_pick.py`；硬规则 8 |
| 既有 | **质量≠执行**：已执行有差异不等于通过，`release_ready` 须真实审核。 | `manifest`；硬规则 9 |
| 2026-11-07 | **本次重构范围强调**：之前已添加的规则不要修改，不让用户重复处理；现有工作流、功能、界面保留，只做插件底层重构与优化。本条记录实施边界，不修订既有业务硬规则。 | [底层重构记录](2026-11-07-runtime-core-refactor.md)；规则与界面 SHA-256 保护、局部恢复与原故障复验回归 |
| 2026-11-08 | **过期/未完成审核不阻碍上传**：批次产物“过期/内容变化”与“未完成审核/有审核差异”都不阻碍上传到题库服务器；只要本地体检（validate）曾通过即可上传，内容正确性由用户自己把关（标签/结构等硬校验仍保留）。“这个过期状态不阻碍批次上传”“未完成审核也不影响批次上传到题库服务器”。 | `workbench/server.py::_check_upload_preconditions`；根 `AGENTS.md` 硬规则 3 |
| 2026-11-08 | **哈希不得成为序列化噪声卡点**：工作流不得因 JSON 空白、键顺序或时间戳差异反复失效/重跑；模型限流不得冻结不依赖模型的本地阶段或独立分支。精确哈希保留给在途任务输入绑定、原件/媒体证明、音频一致性及上传冻结，不得用放宽安全边界制造假完成。 | `workflow/manifest.py` 规范化阶段产物签名；`workflow/stage_semantics.py`；`workflow/auto.py` 冷却隔离；根 `AGENTS.md` 硬规则 2；`CHANGELOG/2026-11-08-normalized-stage-fingerprints.md` |

## 2026-11-08 卡点反馈必须标明具体批次（用户强调）

- 原话要点：2014 年试卷本来已经跑完，但错误报告没有精准指到具体试卷，反馈后又把 2014 批次拉出来重跑；后续卡点必须指出具体批次。
- 落地：`app/workflow/diagnose.py` 增加 `batch_name`，诊断摘要带批次名；`lib/client.js` 卡点条显示批次名，继续处理失败提示带批次名；根 `AGENTS.md` 硬规则 11。
- 边界：只改善错误定位和反馈，不改变自动推进、重试、自愈或阶段规则；泛化反馈不得猜测目标批次。

## 2026-11-08 听力题不标语音知识点（用户强调）

- 原话要点：听力关键词语选择题每道都冒出「单字母在单词中的语音变形」，
  但听力题不该有这个考点，语音应该是语音题的考点。
- 落地：`app/references/type-knowledge-map.json` 的「听力关键词语选择」去掉 `语音` 维度，
  只留 `词汇 + 答案考点`；`app/skills-reference/type-knowledge-map.md` 同步。
- 边界：`语音题` 仍标语音；其它题型未动。
| 2026-11-08 | **重复来源不参与唯一性竞争**：同一份卷被重复提取的 PDF/OCR 附件副本（`material-roles.json` 中 `content_authority=false`）只作佐证，不得让逐字解析被判“来源不唯一”；权威原文内部同题两份不同解析仍算冲突。**卡点说明必须给出具体检查项与判定口径**，不能只写“按阻断项修复”。 | `workflow/upload_content.py` `reference_only_sources`；`workflow/diagnose.py` `CHECK_HINTS`；根 `AGENTS.md` 硬规则 2/4；`CHANGELOG/2026-11-08-explanation-reference-authority.md` |
| 2026-11-08 | **有差异就必须待审核**：一审/二审与原标签的知识点/题型不一致即真实差异，必须进人工队列；不得用“审核判断≠当前正式标签”判过期而静默丢弃（用户原话：有差异就待审核，没差异默认正常）。图片/音频能力受限仍只作 informational。 | `workflow/review.py` 移除 `_obsolete_label_finding`；`workflow/ingest.py` 只保留“标签已补齐”清理；根 `AGENTS.md` 硬规则 6；`CHANGELOG/2026-11-08-review-difference-kept.md` |

## 2026-11-08 原卷预览图片显示规则（用户强调）

- 原话要点：表格里图片要和那段文字在同一个单元格里，不要分开；表格要自适应宽度、自动断行，不要左右滑动；原图（尤其表格内图）要缩得非常小。
- 落地：
  - `app/workbench/server.py::source_blocks` 按单元格切分，`cellImages[行][列]=[图片名]`，图片留在原地；`attach_media` 为 `cellImages` 补 URL。
  - `lib/client.js` 表格 `width:100%; table-layout:fixed`，单元格 `overflow-wrap:anywhere; word-break:break-word; white-space:normal`；`.wf-table-imgcell` 不再 `nowrap`；表格外容器去掉 `overflow-x:auto`；正文图 `width:100%; max-width:200px`；表格内缩略图极小（约 36×28）。
  - 图片能力受限仍只作 informational，不进人工、不阻断。
- 关联记录：`CHANGELOG/2026-11-08-table-image-in-cell.md`、`2026-11-08-table-responsive-wrap.md`、`2026-11-08-preview-table-image-and-size.md`。

## 2026-11-08 题组材料必须在题号前完整绑定（用户强调）

- 原话要点：把这类题目产生的问题写进修复规则，避免下次再出现（2024 卷 group-56-57-58 阅读理解因表格材料丢失报 A8「题组 unit_kind=group 但 material 为空」）。
- 根因：`question_draft._bound_source` 只对 `span.basis==ai_classified` 走整窗绑定；本卷 basis 为 `cleaned_source`，退回按 stem 唯一匹配定位。stem 只含题干（从首个题号起），定位把题号前的文章/表格材料切掉，group 的 material 变空。
- 规则：group（题组）绑定来源时必须使用**完整 confirmed span 窗口（材料 + 题目）**，定位 stem 只能证明其在窗内，不得据此缩短范围、丢弃题号前的材料（文章/表格/导语）。`source_span.basis` 为 `ai_classified` 或 `cleaned_source` 均属可信边界。standalone 仍只绑定自身题干。
- 落地：`app/workflow/question_draft.py::_bound_source` 增加 group span 整窗分支；`CHANGELOG/2026-11-08-group-material-table-span.md`。
- 边界：只修 group 材料绑定；不改题目内容/答案/标签；表格材料用 `[TABLE ...]` 原样保留，A8 要求 group 有 material + subquestions。
| 2026-11-08 | **格式小问题不阻断流程**：用户原话“标点符号呀、下划线呀，这些在流程通过性上都是小问题，只要不是大错误就行”；只有题目/题干/题号对不上才是大问题。据此把下划线/答题线占位、全角空档、`<br>`、首字母间距、表格列数/空档写法等降为 `[W…` 提示，不阻断 validate。 | `app/scripts/_common.py::report`（软提示不阻断）；`scripts/06-upload/check_underline.py`、`check_tables.py`、`check_text.py`、`audit_sentence_blanks.py`；`workflow/checks.py` blocking=False；根 `AGENTS.md` 硬规则 9；`CHANGELOG/2026-11-08-format-hints-not-blocking.md` |
| 2026-11-08 | **本规则写入程序**：上述“格式小问题不阻断”不是只在文档里说明，而是落在检查器代码——`app/scripts/_common.py::report` 的软提示机制、`check_underline`/`check_tables`/`check_text`/`audit_sentence_blanks` 的 `[W…` 前缀、`workflow/checks.py` 的 `blocking=False`。用户要求：“写到程序的规则里面”。 | 程序落地位置同上；`CHANGELOG/2026-11-08-format-hints-not-blocking.md` |
| 2026-11-08 | **预览必须完整展示题组材料**：用户反馈 2024 卷第一篇阅读“正文没了只剩选项”，实质是预览切片从首个题号开始截取、把题号前的 `[TABLE 2]` 海报材料丢掉（批次数据本身完整）。规则：预览片段必须包含题号前的全部共享材料且顺序在题目之前，不能因段落标签 A/B 或`※Notes：1.…2.…` 弱题号截断表格。 | `app/workflow/preview.py::slice_range`；`workflow/test_source_structure.py` 回归；根 `AGENTS.md` 硬规则 14；`CHANGELOG/2026-11-08-preview-keeps-group-material.md` |
| 2026-11-08 | **数据完整≠展示完整**：用户指出 2024 卷首篇阅读正文丢失未被检查出来。规定“内容丢失”类修复必须逐层验证（批次字段 → 预览切片 `slice_range` → `source_blocks` → payload），只验证一层不算通过；并必须补该链路回归测试。 | `app/workflow/test_source_structure.py` 新增切片回归；根 `AGENTS.md` 硬规则 14；`CHANGELOG/2026-11-08-preview-keeps-group-material.md` |
| 2026-11-08 | **听力并行、仅内容审核等听力**：用户要求听力耗时长，不要卡住后续；答案/题型/解析/知识点/组装及 meta/underlines/validate 都可与听力并行推进，只有内容审核（review）必须等听力跑完（审核要读听力全文转写核对语义）。| `workflow/contracts.py`：assemble.depends_on 移除 audio，review.depends_on 加 audio；`test_runner_auto` 用例更新；根 `AGENTS.md` 硬规则 15；`CHANGELOG/2026-11-08-audio-runs-parallel.md` |
| 2026-11-09 | **版本控制只在 master 一条线**：用户明确“不要加分支，直接在 master 上提交后续”。不新开 `develop`/`feature`/`hotfix` 分支，后续改动直接提交到 `master` 并推 `origin`；动手前先 `git fetch` 确认远端未领先，禁止 `push --force`。 | 本地与远端分支均只有 `master`（`git branch -a`）；根 `AGENTS.md` 五、协作提示末条；`CHANGELOG/2026-11-09-console-encoding-stage-wedge.md` 后续提交即按此执行 |
| 2026-11-09 | **附件 placement 只能是题库四值**：用户反馈题库「上传暂存 → 逐题确认」只报 `invalid_request`，根因是材料图片附件带 `placement=material`；题库只接受 `question/option/answer/explanation`，确认阶段整批 `400 invalid_attachment`。规则：生成线上载荷时 `material`/`stem` 必须归一为 `question`；任何新位置先确认远端确实渲染，不能把本地/装配内部位置直接上线。 | `app/workflow/wire_format.py`、`app/workflow/upload_payload.py`；回归 `workflow.test_upload_payload`、`workflow.test_wire_format`；`app/docs/upload-api.md`；`CHANGELOG/2026-11-09-upload-attachment-placement.md` |
| 2026-11-09 | **题目 ID 全局唯一、跨卷必须加前缀**：用户反馈 2014 卷确认后再确认 2015 卷报 `question_id_exists`。根因是本地 `q-N`/`group-N` 只在单卷内唯一，题库题目 ID 是全局主键；云端拒绝重复是正确行为。规则：上传冻结时对本地形态 ID 自动加 `<年份>-<批次哈希10位>-` 前缀，显式 ID 保持原样；同批次重传相同前缀属有意防重，替换旧卷先删旧草稿或另建批次。 | `app/workflow/upload.py::_auto_id_prefix`、`app/workflow/upload_payload.py::build_payload(id_prefix=...)`；回归 `workflow.test_upload`、`workflow.test_upload_payload`；`app/docs/upload-api.md`；`CHANGELOG/2026-11-09-question-id-paper-prefix.md` |
| 2026-11-09 | **提示词示例即契约（示例与校验器同源）**：用户要求“修复也是bug并写入代码规则”。根因是 `answers` 的示例漏了后来才设为必填的 `missing`，模型每轮照抄示例 → 首轮校验必失败 → 补字段的重试又出现 `evidence` 多一个题号 → 两次重试用尽回到 pending，再从同一份坏示例重来，阶段无限空转。规则：示例必须含校验器全部必填键，必填键变更须在同一次改动内同步示例/规则/回归；构建任务时 `EXAMPLE_REQUIRED` 自检缺键直接报错；校验报错要点名缺/多的键与不匹配题号；确定性失败不得靠重试消化，同一阶段连续多轮同一报错即按硬规则 2 升级修复。 | `app/workflow/agent_tasks.py::EXAMPLE_REQUIRED`、`_build_task_template`、`_fields`、`_numbers_preview`；回归 `app/workflow/test_agent_tasks.py::test_every_stage_example_satisfies_its_declared_contract`、`::test_answers_task_example_carries_every_required_field`；根 `AGENTS.md` 硬规则 19；`CHANGELOG/2026-11-09-answers-example-missing-field.md` |
| 2026-11-09 | **审核差异是产出，不是卡点**：用户原话「一审和二审产出的差异是我需要的，不是作为卡点……知识点差异是我们需要的，用来判断大模型和哪些头容易出错，从而为了引入人工判断」。一审/二审与原标签不一致的条目必须完整保留在 `07_review/manual-review-queue.json` 与 `review-gate.json` 的 `rounds.errors` 里，供工作台人工判断；不得自动 resolve、不得为了放行而改判通过或修改标签，也不得把 `review_gate=needs_review` 当成故障去“修”。 | `app/workflow/review.py::evaluate_rounds`（差异只登记不吞）；`runner._cmd_upload_locked` 的 `unreviewed_confirmation` 路径（手动上传不受差异阻碍）；根 `AGENTS.md` 硬规则 3/6；`CHANGELOG/2026-11-09-audio-liveness-and-assemble-audio.md` |
| 2026-11-09 | **切段探活以锁为准，组装不等听力（落码）**：Windows 上 `fcntl` 不可用，旧代码在 `except ImportError` 分支把「探不到」当成「在跑」，导致残留 `running` 状态压住重跑 15 分钟并让组装空转 15 轮。规则：切段是否在跑只认 `02_audio/.segment.lock` 的真实加锁探测（`segment.lock_is_held`），状态文件只作展示；无存活证据的 `running` 必须立即重置为可重跑；`assemble` 的 `_collect` 不得绑定 `02_audio/audio_map.json`，`review` 仍必须等听力。 | `app/audio/segment.py::lock_is_held`、`app/workflow/auto.py::_segment_lock_held`、`app/workbench/server.py::_audio_has_live_worker/_audio_reap_stale/audio_status`、`app/workflow/agent_tasks.py::_collect`；回归 `app/workflow/test_audio_liveness.py`；根 `AGENTS.md` 硬规则 15；`CHANGELOG/2026-11-09-audio-liveness-and-assemble-audio.md` |
| 2026-11-09 | **跨轮审核差异必须留档**：用户要的是一审/二审与原标签的差异（用来判断哪个模型头容易出错），所以轮次之间“出现又消失”的差异不能静默蒸发。规则：每次审核完成把当前 findings 追加进 `07_review/rounds-history.jsonl`（只追加、按 digest 去重、无完成轮次不写空快照）；队列重投影时被 `_superseded` 淘汰的未处理项必须先归档到 `07_review/retired-round-findings.json` 再出队；归档不改变闸门判定，也不自动 resolve。 | `app/workflow/review_rounds.py::record_rounds_history`、`write_summary`；`app/workflow/ingest.py::build_review_files`；回归 `workflow.test_review_rounds`（新增 3 条）；`CHANGELOG/2026-11-09-review-rounds-history.md` |
| 2026-11-10 | **并行模型调用必须让调度看见（Windows 共享锁槽）**：`local_agent` 在 Windows 用字节区间模拟 flock——独占锁 0..1023、每个并行调用各占 1..1023 中的一字节；只探第 0 字节会把「正在跑模型」判成空闲，导致同批次重复派发。规则：supervisor 探活必须覆盖共享槽位（独占锁整个区间），探测失败按忙处理；不得用平台差异当成放行理由。 | `app/workflow/supervisor.py::_agent_lock_busy`、`execution_busy`；回归 `workflow.test_supervisor::test_external_auto_runner_and_model_locks_prevent_dispatch`；`CHANGELOG/2026-11-10-windows-shared-agent-lock-probe.md` |
| 2026-11-10 | **审核差异在卡点诊断/运维自愈里也不是卡点**：用户原话「审核差异不应该作为卡点，你仔细阅读规则」。`review_gate` 的 `needs_review + execution_completed + continue_unqualified + release_ready=false` 是审核已执行、差异待人工的设计态，必须显示为 `quality_differences`（不计入“需处理”、不阻断手动上传），不得建议自动修正，也不得容忍运维 Agent 用局部重生成改标签/改判通过来消除差异；只有 `blocked` 的确定性阻断项才是卡点。 | `app/workflow/diagnose.py::_review_differences/_review_gate_blockers`、`app/workflow/operations.py::_regenerate_units`、`lib/operations-agent.js` 运维提示词；回归 `workflow.test_diagnose`（44）、`workflow.test_operations`（61）；根 `AGENTS.md` 硬规则 6；`CHANGELOG/2026-11-10-review-differences-not-a-blocker.md` |
| 2026-11-10 | **卡点必须落台账（ISSUES/）**：用户要求“每次插件的试卷流程遇到卡点时就记录下来，同时记录该卡点有没有被修复；本地创建一个待修复问题清单的文件夹，没有修复的放里面，已修复的标注已修复”。规则：卡点必须用 `python -m workflow.issue_log add` 登记到 `ISSUES/open/`（写明批次/阶段/失败检查/现象/判定口径/修复方向），修复并复验后 `fix` 移入 `ISSUES/fixed/` 并标注 `status: fixed` 与提交/复验证据；未复验不得标记已修复；同一「批次+阶段+检查」重复出现累加 occurrences，不重复建单；修好不删除记录。审核差异与限流等瞬时错误不算卡点。台账只写 `ISSUES/`，不改批次数据/manifest/收据/上传边界。 | `ISSUES/README.md`、`app/workflow/issue_log.py`、`app/workflow/test_issue_log.py`；根 `AGENTS.md` 硬规则 24 与项目地图；`app/AGENTS.md`「卡点台账」；`CHANGELOG/2026-11-10-blocker-ledger-issues.md` |
| 2026-11-10 | **硬规则必须落到代码，不只写在 AGENTS.md**：用户追问“agents 里面的硬规则是否在代码层面已经做约束”。逐条核对后确认两处只有文档：①规则 24 卡点台账没有调用方；②规则 7 流程图 ≤10 节点没有断言。规则：硬规则要么有校验/闸门/回归，要么明确标注为流程约定；能机器判定的必须补代码约束（本轮：自愈 `repair_not_applicable/repair_failed/retry_budget_exhausted/gate_retry_budget_exhausted` 与 supervisor `exhausted` 自动写批次内 `workflow/blockers.json`，`issue_log sync` 汇总进 `ISSUES/open/`；`test_flow_chart_nodes.py` 守住 ≤10 节点与阶段全覆盖）。 | `app/workflow/issue_log.py`、`self_heal.py`、`supervisor.py`、`app/workflow/test_issue_log.py`、`test_flow_chart_nodes.py`；根 `AGENTS.md` 硬规则 7/24；`ISSUES/README.md`；`CHANGELOG/2026-11-10-blocker-ledger-code-enforcement.md` |
## 2026-11-10：AI 运维按活动批次巡检并先诊断后介入

用户要求无活动试卷时 AI 运维不巡检；有卡点时先找出具体问题，再依据当前任务方向决定观察、续跑、局部修复或隔离代码修复。落地：`lib/operations-agent.js` 的工作状态门控与诊断阶段、`app/workbench/server.py` 的 `/api/operations/work-state`、`app/workflow/activity.py` 的真实执行生命周期、`lib/client.js` 的分状态显示和防旧响应覆盖。无活动批次不创建运维会话；未知回执、审核差异和等待依赖不直接修复或宣称完成。
| 2026-11-10 | **写盘替换必须容忍 Windows 共享冲突**：目标文件被只读句柄短暂占用时 `os.replace` 会报 `WinError 5`，一次失败不得中断整轮审核；替换需有界重试，耗尽后仍抛原始错误，禁止静默改成成功。 | `app/workflow/agent_protocol.py::_replace_atomic`、`_atomic`；回归 `workflow.test_agent_protocol::AtomicReplaceRetryTest`、`workflow.test_review_rounds`；`CHANGELOG/2026-11-10-atomic-replace-windows-share-violation.md` |
| 2026-11-10 | **运维接手口径必须与自愈的“卡住”判定同源**：`self_heal.BLOCKER_OUTCOMES`（修复不可用/修复失败/重试预算耗尽/闸门预算耗尽）是唯一的卡点集合，运维建单不得只认其中两种；仍在推进的 `retry_scheduled`/`upstream_escalated`/`content_retry_with_feedback` 不建单。 | `app/workflow/operations.py::_discover`；回归 `workflow.test_operations::test_discovery_accepts_every_recorded_blocker_outcome`、`::test_discovery_ignores_retries_still_in_progress`；`CHANGELOG/2026-11-10-operations-discovery-blocker-outcomes.md` |
| 2026-11-10 | **重启后端不得打断在途工作**：模型调用与阶段推进跑在后端子进程树里，回收后端会 `taskkill /T` 杀掉整棵树；`.py` 热重启、手动重启、健康探测失败都不得在 `busy` 时直接回收。规则：后端 `/api/health` 必须暴露 `busy`；重启前查 `busy` 并在空闲后再重启（上限 30 分钟，超时强制），健康探测失败须再给更长窗口才判死；发布安装的宿主重启不受此限。 | `app/workbench/server.py::work_in_flight`、`lib/index.js::backendBusy/restartShouldDefer/deferRestart`；回归 `workbench.test_health_busy`、`backend/backend-watch.test.cjs`；`CHANGELOG/2026-11-10-no-restart-mid-flight.md` |
| 2026-11-10 | **环境中断不等于内容失败**：自愈诊断在调用模型前先落 claim（防重复诊断），若进程被整树杀掉，事件会停在 `diagnosing` 且无 `finished_at`；同一签名下超过 60 秒的残留 claim 必须收尾为 `diagnostic_interrupted`（写明不是该阶段内容失败），并退回它占用的重试预算，否则面板永远显示“正在判断卡点”、预算被环境问题吃掉。真语义失败仍占预算、仍落 ISSUES、仍交运维。 | `app/workflow/self_heal.py::_close_interrupted_diagnosis`、`recover`；回归 `workflow.test_self_heal::test_interrupted_diagnosis_is_closed_without_consuming_the_budget`；`CHANGELOG/2026-11-10-interrupted-diagnosis-and-upload-busy.md` |
| 2026-11-10 | **确定性直抄先于模型**：只要该阶段存在来源坐标可绑定的确定性路径（如解析/文章大意直抄），无论整批还是 scoped 局部修复都必须先走它；直抄判不了才回模型，且回模型不得伪装成功。局部修复不得因“范围小”绕开确定性路径重复烧模型。 | `app/workflow/agent.py` 的 explanations 快路径；回归 `workflow.test_direct_explanations_delivery::test_scoped_repair_also_uses_model_free_direct_copy`；`CHANGELOG/2026-11-10-scoped-direct-copy-and-healing-state.md` |
| 2026-11-10 | **展示投影不得当数据来源**：工作台卡片/预览把知识点缩成「二级/末级」短名只是展示；标签核对、修改与保存必须读批次里的目录全路径。合并行汇总与批次标签时不得把短名当独立标签（否则同一叶子显示两遍、被判目录外、并在保存时污染批次）。 | `app/workflow/preview.py` 行的 `knowledge_projection`；`app/workbench/server.py::drop_short_knowledge_copies`、`label_payload`；回归 `workflow.test_workbench_catalog_archive`；`CHANGELOG/2026-11-10-label-dialog-knowledge-duplicates.md` |
| 2026-11-11 | **同步练习类的题型与组题判定必须走统一口径（落码）**：用户原话「这个规则添加到该项目的插件代码，并注重是同步练习类和课时同步的规则」。把 26秋《课时A计划》英语 RJ 七上·八上·九上（学用）正文 854 处大题 + 用户逐条看图确认的结论固化为唯一口径：题型/子类/组题单独题一起判定，`unit_kind` 在结构确认阶段定死；完形/阅读/任务型/选词/短文填空/教材语篇填空/补全对话/语音题整大题/看图题/四线三格/填词游戏 → group，单项填空/完成句子/单词拼写一句一空/翻译/改错/书面表达 → standalone，`☆拓展设问` 并入所属组题且不计入空数，`综合阅读提升`/`单元话题写作` 只是容器。只适用于按课时编排的同步练习册，单元卷/期中期末/月考/中考卷不得套用；结构确认阶段把口径注入提示词并用 `audit_units` 与原卷比对，差异只写 `sync_audit` 留痕，不得据此改题型/组单或放行闸门。 | `app/references/sync-practice-rules.json`、`app/workflow/sync_practice.py`、`app/workflow/structure_ai.py::_classify_prompt/auto_classify`、`app/workflow/test_sync_practice.py`、`app/docs/sync-practice.md`；根 `AGENTS.md` 硬规则 25；`CHANGELOG/2026-11-11-sync-practice-rules.md` |
