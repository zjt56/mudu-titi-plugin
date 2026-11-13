# RULES — 规则与强调台账

## 2026-11-13：AI 运维必须自动接收报错并闭环处理

用户明确要求：把批次报错交给 AI 运维后，运维应自动读取具体错误证据、分析原因、执行已授权的实际修复、运行原失败检查复验并续跑，不停在建议或等待用户重复点击。显式 `runner run` 的非零失败在执行锁释放后自动调用 `operations.handoff_if_available`；运维长测试/安装请求必须有动作级等待窗口但仍受会话硬截止、取消和未知回执保护；investigation 升级 repair 时重置调查读取预算。自治安装仅在用户通过可信本地配置接口明确授权且源码根匹配时启用，仍须隔离工作区、测试、计划、CAS 安装、重启及原检查复验；不自动上传、发布、改审核差异或改锁定控制面。流水线自行通过旧故障时可结案，但必须记录 `resolution_source=pipeline_progress` 且界面显示“流水线已复验”，不得冒称 AI 运维修复完成。落地：`app/workflow/runner.py`、`lib/operations-agent.js`、`app/workflow/operations_development.py`、`app/workflow/operations.py`、`lib/client.js`；验证见 `CHANGELOG/2026-11-13-ai-operations-autonomous-closure.md`。

## 2026-11-13：短文填空图注空与视觉栏目身份一致性

批次11（StarterUnit3Welcome第三课时）答案填充卡点修复的经验固化：①短文填空/选词填空材料行里
「题号+图片+括号提示」`1.[IMG …](tree)`、`2.[IMG …](/aːnt/)` 是**合法图注空（子题号本体）**，
必须与 `<u>…</u>` 文本空一起收全，否则结构漏题、答案附录键无法绑定；孤立 `N.[IMG …]`（无括号提示）
仍不计，且「题号在前」的文本空只在同步练习组题类型下合并收集，避免非同步卷把材料行普通数字误当题号。
②视觉整页栏目归属与结构/答案身份必须一致：有印刷标题的大题即使匹配视觉栏目，也以标题派生身份
（真实课时 context + 全局 occurrence）为准，栏目只作附加证据，不得把 context 写成 `visual-column:<id>`
或 occurrence 重置为 1；标题里的装饰性 `[IMG …]` 属版面噪声，确认结构/绑定比较时同源剥离。
③视觉栏目调用遇 provider abort/超时/截断/参考答答案区未建题良性 issue 属可重试或良性，不硬判失败、
不换模型；模型把 printed_numbers 写成数字串、response 含 tab 控制字符属格式差异，归一化容忍但
不补造/不伪造坐标。④阅读理解题组末尾 ☆拓展设问（词数限制的简答子题）无 A–D 选项，E20 选项条数
基准不应误判，但缺选项的选择题仍报错。落地：`extract.blank_numbers`/`preview._weak_numbers`
（IMAGE_HINT_BLANK）、`visual_columns`（_benign_issue/_quote_eq/strict=False/重试/复用）、
`structure_gate._proven_scoped_restart`、`question_identity.assign_scoped`、`upload_content` scoped
标题比较、`audit_type_layout` E20；验证见 `CHANGELOG/2026-11-13-image-hint-blanks-and-visual-column-robustness.md`。

## 2026-11-13：无题号与栏目边界按通用来源规则修复

用户要求后续没有题号或其他栏目板块均按同类规则处理。无号题须由当前大题的完整正文和明确作答区域证明；连续独立作答行分别建单元，自然换行只有在唯一末尾作答区域及连续正文可证明时合窗。候选必须覆盖所有重建窗口；内部别名不作印刷题号，上传范围仍由来源证明投影为 `null/null`。图片栏目用当前 Harness 模型读取完整可见标题，不限“熟词生义”；普通文字只认明确栏目包装或既有标记，栏目不决定题型、题数或答案，并与 A/B 板块身份分离。混题、缺正文、边界不清或陈旧视觉证据仍拒绝。落地：`question_content.unnumbered_text_responses`、`structure_gate`、`structure_ai`、`lesson_bands` v3；根规则9与 `app/docs/sync-practice.md`。验证详见 `2026-11-13-generic-unnumbered-features.md`。

## 2026-11-12：Harness 子智能体用于运行时提取与匹配

用户明确要求用于插件运行时，并授权按评估方案实现：知识点、复杂答案/解析来源查证、前置组装题型、结构整卷取证和图片补证。模型提交候选，由现有程序验收；大段规则与答案即使从首轮提示压缩也必须能通过只读快照工具取得。恢复、预算、取消、输入绑定及删除批次的子会话清理一并实现。此为既有原文保真、独立审核、模型严格路由和不自动上传等规则的落地，不放宽质量或上传边界。落地与实测结果见 `2026-11-12-harness-content-agents.md`。

## 2026-11-12：Word图片板块由视觉辅助，答案按同源结构绑定

用户明确“培优”即B能力提升，要求视觉辅助Word内容提取，并直接落地速度、质量、准确性及答案匹配优化。正文A/B横幅为图片，不能从答案区文字、文件名或宽高比猜正文分界。视觉读取真实图片，按原件/正文/所有候选媒体及当前模型路由绑定边界；结构、草稿、预览与答案共同消费，原文/原号/有效ID保留。答案必须证明课时/专题/板块/大题与全题号覆盖；错scope或已有陈旧/有问题视觉证据不回退数字串答。多图批量和字节+路由缓存，失败明确延期，视觉不编题不编答案。落地：lesson_bands、visual_transport、wf-vision、structure_gate/question_draft/upload_content与消费链路测试；根规则27补充。当前来源证明完整ABCD的纯选项语音组可保留原本空的子题干，缺来源/少选项/改文仍拒绝。

## 2026-11-11：OCR 识别错误先查官方参数，不针对单页自行重排

- 用户纠正：“它返回来的有问题，那说明就是识别的问题，你不应该自己再去修复这种东西，因为你下一篇内容又不一样了，你怎么修复？”并要求查官方识别设置。
- OCR Markdown 沿用服务阅读顺序；通用修复保留脱敏原始响应、全部块字段及坐标/序号，不能把只保存文字当成版面识别完整接入。识别错序明确保留，禁止单页硬编码行置换或按坐标自排来冒充通用识别修复。当前临时行重排模块、映射及集成均撤销，批次经正常runner恢复原始OCR顺序后复验，结构仍阻断，未伪称完成。
- 官方 jobs 文档的 optionalPayload 引用 VL 参数表，包含 useLayoutDetection、layoutThreshold、layoutNms、layoutUnclipRatio、layoutMergeBboxesMode；三个预处理/图表开关只是官方示例，不能当作全部能力。本地/自部署默认与托管1.6实际默认须分开核实，不擅发未文档参数或擅换模型。
- 落地：根规则28、paddle_jobs.py、extract.py、structure_ai.py、sync_practice.py、app/docs/extraction.md；测试保留真实OCR错序失败与有效候选诊断，不按单页移行造通过。

## 2026-11-11 编号空格/空位号不是印刷题号（用户要求同类错误沉淀为约束）

- 用户原话：“将以上出现的问题，以代码规则和提示词增强为约束，避免下次继续犯同类错误以及类似错误。”
- 背景：批次03完形材料行内 `<u>　n　</u>` 的 `</u>.` 句点让“数字+句点”锚点误命中，报 SOURCE_DUPLICATE_NUMBERS/QUESTION_NOT_FOUND；补全对话 `Tony.1.<u>` 空位因题号在空位前定位不到第1题。
- 规则：结构锚点先按库侧空档写法 normalize_blank_text 规范化再识别印刷题号；_weak_numbers 需认“题号在空位前”的对话空位（N.　　/ Xxx.N.____）。编号空格/空位号绝不与下方 ( )n. 选项并列重复计数，也不因紧随文本句点而漏题。
- 落地：`structure_gate._scope_anchors`、`preview._weak_numbers`；回归 `test_structure_gate` 两条；根规则9。

## 2026-11-11 原卷合法空/图形态不算内容缺失（用户要求沉淀为约束）

- 用户原话：“将以上出现的问题，以代码规则和提示词增强为约束，避免下次继续犯同类错误以及类似错误。”
- 背景：两次同类卡点——结构闸门把“无题号无图片的题组大题”误判 SOURCE_SECTION_UNCOVERED；前置组装把“空白作答网格（答案在附录）/纯图片题”的 contentComplete=false 当硬阻断。
- 规则：结构确认、前置组装（含题型）、体检等任一环节，都不得把①纯图片题（除图片/表格标记外无文字题干）②空白作答网格（表格全空、答案在卷末附录可按题号对上）③整节无印刷小题号也无图片但有正文 当成内容缺失而阻断。判定缺失前必须先排除这三类；contentComplete=false 只能基于真实截断/缺选项/题干错配，并说明缺了什么文字。
- 统一口径：`app/workflow/question_content.py::legitimate_source_shape`（布尔封装 `capability_limited_source`），结构闸门 `structure_gate._build_scoped_units` 与前置组装 `agent_tasks._validate(types)`、`accept_result` findings 降级共用同一分类，不得各处各写一套；提示词 `app/prompts/roles/preassemble.md`、`types.md` 同步要求先排除三类再判 false。

## 2026-11-11：远端课时标记规则按本插件边界落地

按用户要求对照 Mudu-English-Upload 的课时配方，来源/新考法与无年份栏目（传统文化、安徽人文、榜样人物、经典谚语、熟词生义等）必须完整保留到实际题干或材料；不能用全批同年/地区 token 或 source_section 元数据假作已交付。可靠 scoped 身份下核对所属单元及子题，旧协议不猜归属。远端自造答案/范文、自动教材挂接及辨音逐题与用户当前规则冲突的做法不引入；只匹配知识点、语音按整节 group、上传需授权的边界不变。落地：sync_practice、audit_source_marks、课时提示词及回归；根规则25。

## 2026-11-11：未知来源字段只在 meta 视图省略，来源不参与内容审核

来源/年份缺证据保持未知，meta 最终视图省略空白与「未提供」，只同步同节点已有实值别名；真冲突和非法实值仍由硬校验拒绝，不从父题或日期猜测。仅来源元数据变化不应触发标签/内容重审；题干、材料、答案、知识点和真实来源证据的绑定继续严格，在途精确哈希不放宽。落地：metadata_policy、assign_meta、review 语义绑定及回归；根规则6/26。

## 2026-11-11：语音组题的原生图片、公共导语和来源证明

课时实际组装复验发现：图片已转换为 `placement=question` 原生附件，空 `material` 不证明图片丢失；公共作答导语必须按当前大题来源逐字保留。组装、结构体检与上传格式检查共用当前来源证明，仅认可已验证的语音共享图/完整同节练习和无印刷号纯图内部别名形态；不得编造“见附图”等文本，不改组单，不普遍豁免 A8/A9。无目录上下文的裸数据仍严格校验，真实缺图、跨节、过期来源及普通缺题干继续阻断。落地：`question_draft.py`、`shared_rubric.py`、`phonetic_structure.py` 及结构/发布/上传格式消费者；根规则27。

## 2026-11-11：先只匹配知识点，暂停教材匹配

用户原话（m06470）：「目前只做知识点标签的匹配，先不做教材方面的匹配，教材的还没搞好」。所有来源暂不提供教材索引、不生成教材位置；正常导入旧候选时省略本地 `textbook` 并留说明，组装不传播，原始候选保留。知识点目录叶子、ID、维度、题号覆盖及证据继续严格验收。教材目录和独立工具保留，恢复须用户另行明确；既有远端映射校验及上传授权不变。落地：`agent_tasks.py`、`assemble_local.py`、`batch_fill.py`、`substeps.py`、`app/docs/content-labeling.md`；根规则31。

## 2026-11-11 运维租约共存：并行后端不再互毁

- 用户报「worker_busy: 批次执行锁已占用; name=Error，AI运维无法正常处理」。取证：7 分钟内 22 次 `lease/process_replaced` + 21 次 claim、0 次完成。
- 同一数据根跑多个后端实例时，运维租约只因「到期」或「原执行进程已消失」释放；活着的并行实例持有效租约期间，本实例认领返回 409 `worker_busy` 等待，不得重置 `attempt_id/execution_process/queue_generation`，也不得写 `process_replaced`。
- 进程存活判定：Windows 用 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)+GetExitCodeProcess`；**禁用 `os.kill(pid, 0)`**（在 Windows 上等于 TerminateProcess）。
- `/api/health.busy` 纳入未过期运维租约，插件据此延后 .py 热重启（上限 30 分钟），避免保存代码打断在途运维。
- 宿主插件 JS 只有整进程重启才生效：看到裸 `name=Error`（无 `code=`）先核对运行中宿主进程的启动时间与修复提交时间，不要只改仓库。
- 落地：`app/workflow/operations.py`（`_pid_alive`/`_foreign_owner`/`leased`）、`app/workbench/server.py`（`_operations_leased`）、`app/docs/harness-operations.md`；验证见 `2026-11-11-operations-lease-co-driver.md`。

## 2026-11-11 整行标签不是孤立字段：画线恢复必须能进合并材料

- 用户要求把 12 年卷真正跑完（m03369 之后的续跑）。
- 原卷里「整段只有一条画线」的表格标签（`Testing Dates:`、`Homework:` 等）在组装后嵌进很长的 material，画线脚本原有的「整体相等即跳过」保护把这类标签一律丢掉，报 `UNDERLINE_MAPPING_MISSING` 并整阶段失败。
- 规则：整行纯文字标签（≥3 字符、无答题线）允许在合并段落中定位并恢复画线；含答题线的标签与单字标签仍拒绝嵌入匹配；多处命中仍判 `UNDERLINE_MAPPING_AMBIGUOUS` 并保持候选不变。判定口径 = 唯一性 + 同单元镜像，不靠「字段长度等于标签」。
- 落地：`app/scripts/06-upload/mark_underline.py`、`app/workflow/test_underline_layout.py`；验证见 `2026-11-11-underline-whole-line-label.md`。


## 2026-11-11 重复原文等价验收与确定性拒收停止模型空转

- 用户要求修复解析填充此类 bug，并加强代码和提示词约束。
- 同题、同来源身份中逐字相同而位置不同的原文按内容等价核验；source_ref 仍须命中真实坐标，异文冲突、跨大题借用与归属不明仍阻断，不按锚点数直接判冲突。
- 仅来源/校验器结构化确认 `retryable=false`、`failure_class=source_binding/validator_fault` 时停止对应单元重复模型，保存原候选与诊断；换上下文、反馈和执行轮次不得绕过。真实来源变化后重新绑定，代码修复先本地复验原候选。
- 正式产物缺失不证明模型没有产出；运维先查候选、验收错误和源坐标。所有内容续跑别名须限定受影响单元、保留已验证基线；不能通过动作名绕过局部修复。
- 唯一例外须同时成立：该阶段当前能由确定性直抄从原文整卷交付（有真实内容、不是占位「无」），才允许整阶段重跑一次——不调用模型、不能编造，所以不属于"整卷模型重跑"；占位或直抄失败时仍按无定位拒绝。
- 落地：根 AGENTS 规则4/19、`upload_content.py`、`label_execution.py`、`self_heal.py::_deterministic_delivery`、`private_label_baseline.py`、`operations.py`、解析角色与运维提示词；验证见 `2026-11-11-explanation-source-binding-recovery.md`。

## 2026-11-11 提取来源优先级与运维消息滚动

- 用户要求把 Word > 带文字层 PDF > 扫描件的提取规则加入插件代码；同时要求 AI运维消息固定下方、可以上下滑动，不能向上顶流程图。
- 同批 Word/PDF 原卷以 Word 为主源；PDF 合格文字层页原生读取，质量不足页在 OCR 已启用时兜底；原有答案/解析/大意缺失兜底仍按规则22执行，不覆盖已有 Word 内容。
- 运维消息区固定 132px、内部纵向滚动；工具栏与状态在滚动区外，重复提示合并并限制展示数量；上滑查看时保留滚动位置。
- 落地：`app/workflow/source_policy.py`、`extract.py`、`app/workbench/server.py`、`lib/client.js::OperationsPanel`；根 AGENTS 规则28/29。验证与边界见 `2026-11-11-source-policy-operations-log.md`。

## 2026-11-11 同课时跨大题重号保留原号、全链按来源身份关联

- 用户要求：按一个课时一批次，保留原卷 `Ⅰ、Ⅱ、Ⅲ` 的排序；同一课时不同大题及基础/提升段会重置小题号，不能全局按数字覆盖。
- 规则：同步新批次使用 `scoped-v1` 的 section/unit/question 身份；原标题、罗马序号、出现顺序及小题原号保留。答案、缺失及证据按 question ID，其他产物按 unit ID 与本单元原号关联；A/B 同号以真实材料窗口区分，禁止数字兜底。无号图题内部别名不显示为印刷号。
- 来源：答案/解析/大意必须有同一 scope 的实际原文证据；附录仅有同号但无对应大题/材料证据时拒绝猜配，也不能把已有未绑定答案伪记缺失。Word 优先、作文豁免与原文无则占位保持原规则。
- 边界：旧批次仍用旧协议，不改历史数据；漏题、跨窗、同窗重复和来源不明检查继续生效，审核差异/上传边界不变。
- 落地：根 AGENTS 规则27，`app/workflow/question_identity.py` 及结构、内容、审核、预览/保存链路；实现与验证见 `2026-11-11-scoped-question-identity.md`。

## 2026-11-11 AI 运维零人工自治修复与本地持久授权（覆盖旧逐次安装要求）

- 用户本轮要求：重设计 AI 运维，保留自愈，自动解决卡点，零人工接管；已明确授权本机自治修复。移除前台展示修改建议的要求，后台仍保留诊断证据和修改方向。
- 规则：运维服务在线时首个确定性阻断异常安全接手；用户明确授权且本地持久自治开关启用后，隔离复现→修复→测试→安装计划→安全安装→宿主重启→重新认领→原失败检查复验自动进行，不再强依赖逐次确认或 `MD_OPERATIONS_INSTALL=1`。配置 `<源码根>/.operations-autonomy.json` 默认关闭、绑定源码根、可信本地 API 原子写入，不向模型开放；关闭时兼容旧确认加环境变量通道。
- 保护：测试及变更测试执行证据、计划/基线哈希、保护文件、备份 CAS 安装与失败回滚仍生效；部署成功不等于原卡点修复。预算与同证据失败继续限制空转，不能靠新会话或清空计数绕过；未解决事件升级自治修复或等待真实输入/代码证据变化，不转人工终态、不假报成功。
- 边界：原录题与自愈保留，服务关闭/不可用仍走原流程；不自动上传/发布，不直接编辑用户数据/manifest，不裁决或自动消除审核差异。审核差异仍是产出，不是卡点。
- 落地：`app/workflow/operations.py`、`app/workflow/operations_development.py`、`lib/operations-agent.js` 及宿主发布协调器、前台运维状态；根 AGENTS 规则18、`app/docs/harness-operations.md`；变更与验证见 `2026-11-11-operations-autonomous-recovery.md`。本条覆盖 2026-11-10 的前台展示建议要求，以及旧代码安装须逐次确认/环境开关和预算耗尽转人工的要求；历史台账保留。

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
| 2026-11-13 | **视觉模型辨栏目归属与无号题**：“基础巩固、栏目提示、熟词生义、创新训练、拓展设问等……有的题目直接归属栏目，确实没题号，你需要辨别”。必须结合整页判容器/说明/独立题/附属设问；无号实际题保留无号，不猜挂前题、不补造编号。 | `source_pages.py`、`visual_columns.py`、结构/身份/来源复建；硬规则34 |
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

## 2026-11-11：课时同步卷的答案区、并排小题与审核页题型直显

用户原话（m05238）：「这个是同步课时题型的，然后跟之前中考试卷不一样，你需要依据同步课时这种题型的一个特点，再给他写一篇对应的匹配规则」；（m05271）：「这批试卷录入到试卷审核页面的时候，它那个题型啊，它应该直接展示题型，而不是说题干……按这种题就是你要匹配一下这个匹配规则」。两个卡点同一根因：课时卷的版式特征（每大题重排题号、并排小题、说话人前缀、图片小题、卷末紧跟答案区）没有确定性口径，结构闸门把答案行当正文题号全部阻断，审核页又拿大题标题充题型。落地：`app/workflow/sync_practice.py::answer_key_start`（把末节窗口截到「参考答案」之前）、`app/workflow/extract.py::same_line_numbers`/`DIALOGUE_QUESTION`/图片题 informational、`app/workflow/structure_ai.py::sync_subtype`（子类按本大题标题确定性回填）、`app/workbench/server.py::decorate_review_rows`（审核页只补缺失、不覆盖已判定）、`lib/client.js`（行与标题栏展示题型+子类）、`app/docs/sync-lesson-types.md`、根 `AGENTS.md` 硬规则 30。

## 2026-11-11：已验证题组图片不得被页眉启发式隐藏

用户指出课时前两题都是组题却看不到图片。有效 scoped 来源窗口已证明题图归属时，图片优先于首题号位置与宽高比启发式；显式排除仍优先，过期、冲突、跨单元归属不得提升。验收须贯通切片、blocks、detail JSON 与实际媒体接口，并保留题组、子类列表和无印刷号语义。落地：`app/workbench/media_audit.py`、`app/workbench/server.py`、`app/workflow/preview.py`、`workbench.test_media_scoped_preview`；根 `AGENTS.md` 硬规则 14。

## 2026-11-11：模型重试前重新核验在途输入

验收动作可能刷新派生输入；每次调用模型前必须重新检查初始精确绑定。发生变化立即返回 `context_changed` 并保留候选，不得沿用旧提示词再烧一轮模型，也不放宽哈希校验。落地：`app/workflow/local_agent.py`、`workflow.test_local_agent::test_validation_refreshing_draft_stops_retry_before_model_call`，延续硬规则 2/19。

## 2026-11-11：试卷名/来源按已识别元数据与原卷 Word 文件名补齐

用户原话：「还有这个试卷名，它现在来源显示是"未提供"。你正常以 Word 文档的文件名去修改一下，它其实就是这个名字的来源」。课时拆分件正文没有卷头，结构闸门原先把 meta 直接落成「未提供」，还经 runner 覆盖掉已识别值、写进每道题的 `filterMetadata`。规则：试卷名/来源按「已有值 → `01_source/metadata.json` 已识别值 → 原卷 Word/PDF 文件名 → 未提供」补齐，只补空缺不覆盖识别值；「未提供」只是展示占位，不写进题目字段，也不能凭空生成年份/省份/题型。落地：`app/workflow/metadata_policy.py::paper_meta/resolved_paper_meta`、`structure_gate.py`、`runner.py`、`assemble_local.py`、`batch_fill.py`、`app/workbench/server.py::paper_info`、`workflow.test_metadata_source_name`；根 `AGENTS.md` 硬规则 26；验证见 `2026-11-11-metadata-source-name.md`。

## 2026-11-11：课时拆分件的提取口径同源（课时标题变体 / 版块边界 / 提示标记保真）

用户原话（m03765）：「了解该项目中关于课时的注意事项以及分类等条件，完善该插件对应课时的提取规则」（对方仓库 Mudu-English-Upload）。规则：课时标题认 `第一课时`、`第1课时`、`第1节课`、`课时1`、`Period 1`、`Lesson 1`，课时后可直接跟 `Section A`（不要求空格）；`Reading Plus`、`Project`、`Reflecting`、`Pronunciation`、`综合阅读提升`、`单元话题写作` 与课时平级，只能作上下文并结束上一大题窗口，不得当大题或并进共享材料；试卷卷头出现单元测试/期中/期末/月考/模拟时仍不套用同步口径。原卷方括号提示标记（`[2025·六安月考]`、`[2023·安徽]`、`[新考法·…]`、`[情境题]`、`[改编]`）属于原文，必须原样留在题干或材料，`sync_practice.audit_source_marks` 只写 `structure.ai.classified.json` 的 `sync_marks` 留痕（上传前再由 `scripts/06-upload/audit_source_marks.py` 复核），不改题干、不放行闸门；`type_kind` 覆盖目录全部 27 个主类。落地：`app/workflow/sync_practice.py::_LESSON/is_section_title/source_marks/audit_source_marks/prompt_block`、`app/workflow/structure_ai.py::auto_classify`、`app/references/sync-practice-rules.json`、`app/workflow/test_sync_lesson_extraction.py`、`app/docs/sync-practice.md`（第九节）；根 `AGENTS.md` 硬规则 25；验证见 `2026-11-11-sync-lesson-extraction-rules.md`。

## 2026-11-11：技术性绑定变化不得让真实未裁决差异消失

用户原话（m11332 要点）：「审核出来的 11 条差异我还没裁决，不能因为清理元数据占位就把它清掉」。旧实现按「全局 input binding 变了」把未裁决发现一律归档出活动队列，一次来源占位清理就让 11 条真实差异从人工队列蒸发，还白烧一遍模型。规则：来源/年份等**题目来源元数据**不参与审核语义绑定（`evidence.source` 等真实来源证据仍参与）；历史摘要只要精确等于当前投影、旧投影当前值或仅回放已知「未提供」子题来源这三种之一，就算内容等价，可复用既有两轮结论，不重跑模型。因绑定过期退休、内容证明等价且仍未裁决的差异必须放回活动队列，保留原 `round_sources` 与理由、换新 `finding_id` 防撞号并幂等去重；内容真变、证明不了、已裁决、其他退休原因一律仍归档。落地：`app/workflow/review.py::acceptable_unit_bindings/_unit_binding_matches`、`app/workflow/ingest.py::_restore_equivalent_retired/build_review_files`、`app/workflow/test_ingest_binding_equivalence.py`；根规则 6/9；验证见 `2026-11-11-review-binding-equivalence-restore.md`。

## 2026-11-11：课时编排自检（缺单元名 / 空课时 / 课时跳号）

用户原话（m04567 要点）：「查看该技能对于课时录入规则的补充和约束，运用到该该插件，补强代码约束和提示词增强」。规则：课时级错误必须报出具体课时名与行号，不得静默丢课时——①课时标题之前没有 `Unit N` 记 `LESSON_UNIT_MISSING`，不得沿用上一单元或从批次名猜；②课时与下一课时/答案区之间没有任何大题标题记 `LESSON_EMPTY`；③同一单元课时序号不连续记 `LESSON_NUMBER_GAP`。课时序号认 `第一课时`/`第1节课`/`课时3`/`Period 2`/`Lesson 4`（中文数字含十二、二十一）。结果写入 `structure.sync_practice.lesson_audit` 只作留痕，不改题型/组单、不放宽闸门。课时录入约束文字唯一来源是 `sync-practice-rules.json` 的 `lesson_constraints`，`prompt_block()` 直接消费（每课时必须有单元名与至少一个大题、序号连续；版块与课时平级；材料带齐题号时不重复生成同号子题；单元话题写作第1步词汇积累/思维导图不是题）。落地：`app/workflow/sync_practice.py::_section_number/_lesson_number/lesson_constraints/audit_lesson_shape`、`app/workflow/extract.py::infer_structure`、`app/references/sync-practice-rules.json`、`app/workflow/test_sync_lesson_shape.py`、`app/docs/sync-practice.md`（第九节）；根 `AGENTS.md` 硬规则 25；验证见 `2026-11-11-sync-lesson-shape-audit.md`。

## 2026-11-11：题型/子类以桌面《英语题型汇总.xlsx》表一为最终口径

用户原话（m05969）：「这个对应在表一做了修改，请你也做对应的改动」。规则：表一（`题型汇总`）改了题型/子类/组单，插件规则必须逐行跟上，不能拿逐处明细或旧先例顶替；表一与插件目录冲突时先报冲突再落码，不静默改写。2026-11-11 表一 15 行改判：一句一空语法功能词填空（人称代词、do/does、介词、连词、How/What、if/whether、关系代词）与 can/can't/could/couldn't 的适当形式由 `单词拼写/句意` 改为 `选词填空/多句`（前者仍单独题、后者仍组题）；看图补全句子（图片提示/家谱/地图/图示/图片信息）由 `单词拼写/看图填词` 改为 `完成句子/补全句子`（组题）；短文后完成思维导图由 `任务型阅读/信息摘录` 改为 `阅读综合`。`完成句子` 的 `补全句子` 子类是本地补入 `filter-framework.json` 的目录项（平台 1.0 框架原无），平台拒收时体检 A7 会明确报错，只能由用户裁决退回 `看图填词` 或改投 `情景运用`，不得静默改回别的题型。`sentence-picture` 必须排在 `spell-picture` 之前（规则顺序即优先级）。落地：`app/references/sync-practice-rules.json`、`app/references/filter-framework.json`、`app/workflow/test_sync_practice.py::TestWorkbookSheetOneAlignment`、`app/docs/sync-practice.md`（第三节）、`app/docs/sync-lesson-types.md`（5.1）；根 `AGENTS.md` 硬规则 25；验证见 `2026-11-11-sheet-one-type-alignment.md`。

## 2026-11-11：平台侧题型目录登记「完成句子/补全句子」

用户原话（m06231）：「平台侧也加上这个」。规则：桌面表一新增/改判的子类，本地
`app/references/filter-framework.json` 与平台服务器 `static/filter-framework.json` 必须**两侧同时登记**；
只改本地会让平台 `validate_upload_question_types` 与本地 A7 双双拒收（`invalid_question_subtype`）。
平台改动须先备份到 `/home/ubuntu/mudu-quiz/backups/`、记录改前改后 sha256，并在 `CHANGELOG/` 留下
回退命令；目录文件每次校验都重新读取，改完无需重启 `mudu-quiz.service`，但 `static/app.js` 的目录
缓存戳要同步升级，否则后台下拉仍按旧目录渲染。落地：`static/filter-framework.json`、
`static/docs/index.html`、`static/app.js`（平台侧）；`app/references/filter-framework.json`、
`app/docs/sync-practice.md`、`app/docs/sync-lesson-types.md`、根 `AGENTS.md` 硬规则 25（插件侧）；
验证见 `2026-11-11-platform-catalog-sync.md`。

## 2026-11-11：自愈重试预算不得被易变 detail 清零（防整机卡死）

用户原话：「我启动了那个任务，让我电脑卡死了两次」「修复这个bug，让我电脑直接卡死了」。规则：自愈的重试预算 signature 必须只键于**稳定**维度（阶段 + 归一化失败类别 + `_retry_evidence` 的真实输入/代码指纹），**不得拼入易变的原始 detail**（分片集合、被杀子进程 `exit -N`、分片顺序、时间戳、行号、内嵌 evidence JSON）；否则每次整阶段重跑的 detail 微变都会把 `attempts` 清零，令确定性卡点无限整阶段重跑、并发拉起大量 headless 进程打满内存/CPU，直接违反硬规则 2「不得因序列化差异停住/重跑」与硬规则 19「确定性失败不得用重试消化」。落地：`app/workflow/self_heal.py::recover`（signature 用 `_failure_class_key`）、`_failure_class_key`；`app/workflow/local-agent.json` 的 `part_concurrency`/`review_part_parallel` 从 16 降到 3（本机安全并发，别拍 16）；回归 `app/workflow/test_self_heal.py::test_volatile_detail_does_not_reset_retry_budget`。验证见 `2026-11-11-self-heal-retry-budget-loop.md`。

## 2026-11-11：课时录题版面/选项/空壳子题的硬检查（吸收远端课时技能细节）

用户原话：「学习该技能中关于课时这方面的一些录题的一些 Skill，一些提示词，一些约束，怎么提取的，怎么让大模型去做，然后强化到这个插件中，特别是一些细节方面的东西」（对方仓库 Mudu-English-Upload `main@abf7333` v0.3.37）。规则：课时作业「一错就整屏变形」的字段写法必须由**确定性体检**兜住，不能只靠提示词口头约束——①选项字母必须 A→… 连续、不跳号不重复（`A/B/D/D` 这类）；②完形填空单元级 `options` 必须留空、选项挂子题（C11）；③选词/短文/教材语篇填空的子题**不得带选项**（C12）；④组题材料已带齐题号（`__N__`/`　N　`/`（N）`/`N.`）时，子题 `stem` 只能是空壳（纯 `______` 或纯题号），写整句或重复题号即返工（D7/SHELL_SUB）。落地：`app/scripts/06-upload/audit_type_layout.py`（新检查，本地体检阻断项）、回归 `app/workflow/test_audit_type_layout.py`。提示词侧：`app/workflow/sync_practice.py::prompt_block` 下发版面规约段；`app/prompts/roles/knowledge.md` 补按题型维度约束（阅读/任务型/读写综合每题一个设题角度、完形/语法选择只挂体裁+主题、填空拼写类不挂主题）。源参考：`app/skills-reference/workbook-recipes.md`（课时逐题型配方节选）。**不引入远端 F7「缺答案/解析/大意自己补并标 AI」生成规则**（与用户「缺就不生成、填无」口径冲突）。验证见 `2026-11-11-lesson-layout-hardening.md`。

## 2026-11-11：继续处理区新增「删除」入口，可停运行中批次并清缓存

用户原话：「像这种流程中的任务，我需要加一个删除任务，因为有的我不想让它跑了，直接给它删除，同时清空它留在这里的缓存，就放在继续处理那边。」规则：删除入口放到流程图（继续处理区）头部「继续处理」按钮旁；对**正在运行/排队**的批次也能删——先写批次内取消标记 `workflow/.delete-requested` 并移出推进队列，推进器在**下一阶段边界**干净退出、释放执行锁（绝不阶段中途强杀、不留半写产物），停稳后再走 `purge` 彻底删目录并清缓存；排队/等待中的批次移队列后直接删；若 `DELETE_STOP_WAIT_SECONDS` 内停不到阶段边界则**拒绝删除并保留数据**（不删运行中的批次）。删除仍只由用户显式操作触发、需输入完整批次名，自动流程不删用户试卷。落地：`app/workflow/auto.py::cancel_requested/request_cancel/cancel_marker_path/clear_cancel`（`_advance_impl` 阶段边界检查）、`app/workbench/server.py::delete_batch/_cancel_running_batch` 与 `/api/batches/<name>/delete` 路由、`lib/client.js` 继续处理区「删除」入口、`app/workflow/test_workbench_delete_batch.py`；根 `AGENTS.md` 硬规则 21。验证见 `2026-11-11-continue-area-delete-batch.md`。

## 2026-11-11：知识点「knowledge 非空」卡点属代码误判，按组题口径修复并强化维度/路径约束

用户原话：「卡点诊断 批次：01七上…StarterUnit1Hello!第一课时Sectio … 知识点标签匹配：阶段失败 原因：knowledge must be a nonempty list of nonempty strings」「需要，并且把这个错误也加强到代码层面，然后增强约束」。规则：**整组题（有子题或 unit_kind=group）的知识点以组级为准，子题 knowledge 可空**——`agent_tasks._validate` 的 knowledge 与 assemble 两处前置校验都改用 `whole_unit_type(...) or is_group`，与权威校验 `taxonomy.check_question_labels` 的 `child_of_whole` 同口径；「语音题」是整组题（仅组级「语音」维度），子题留空正确，不得据此判失败。**维度范围代码强制**：完成句子/改错只 词汇·语法（禁语用/主题）、单词拼写 词汇·语法·语音、单项填空 +语用、补全对话 体裁·主题·语用、完形/语法选择/书面表达 仅 体裁·主题，越界即 `超出允许维度/范围` 阻断。**路径笔误可定位**：未解析知识点路径由 `taxonomy._closest_leaf` 报最近目录叶子提示（只提示不自动纠正、不放宽）。**装配/恢复同口径**：`restore_source_stems` 对材料已带齐空位题号的组题（补全对话等）子题题干强制 `______`，不抄后一行对白。落地：`app/workflow/agent_tasks.py::_validate(knowledge/assemble)`、`RULES["knowledge"]`、`app/workbench/taxonomy.py::_check_labels/_closest_leaf`、`app/workflow/question_content.py::restore_source_stems/_material_blank_numbers`、`app/prompts/roles/knowledge.md`；回归 `test_agent_tasks::test_phonetic_group_accepts_empty_child_knowledge`、`test_label_audit::test_completing_sentence_rejects_pragmatic_dimension/test_unknown_theme_path_reports_closest_leaf_typo_hint/test_phonetic_group_child_knowledge_may_be_empty`、`test_question_draft::test_shared_blank_dialogue_children_keep_shell_stem`。验证见 `2026-11-11-knowledge-phonetic-group-and-typo-hints.md`。

## 2026-11-12：整卷题号不得用「上传单元序号」推断；独立题补 location「第N题」

用户原话要点：「这是为了避免把『第几个审核/上传单元』误当成『原卷题号』。正确修复需要给 2024 试卷这些单元补上 numberStart / numberEnd。」规则：线上整卷档案的 `numberStart/numberEnd` 只认原卷印刷题号；页面在没有真实题号时回退显示 `paperOrder` 是设计，不得据此反推题号。插件侧：独立题（无子题、legacy 整卷批次）`location` 为空时按结构确认的 `number` 补成「第N题」，供远端 `inferred_paper_numbers` 建立范围；`书面表达`（无印刷数字题号，`number` 是装配续号）与 scoped 课时批次（同号分节重复）不补，已有 `location` 不覆盖。落地：`app/workflow/upload_payload.py::_Builder.item()`、`app/docs/upload-api.md`、`app/workflow/test_upload_payload.py`；线上 2024 卷数据修复见 `2026-11-12-2024-paper-number-ranges.md`，代码固化见 `2026-11-12-upload-paper-number-location.md`。

## 2026-11-12：审核页知识点标签差异只展示末级

用户原话：「试卷审核页面的知识点标签差异那里，标签只展示到子级，上一级都不需要展示，简洁一点。」规则：审核详情左栏「知识点标签」三行（原标签/一审/二审）正文只显示末级短名，完整路径放进 `title` 悬浮可查；**差异判定仍比较完整路径集合**，同名叶子不同分支不得因短名相同而漏判。落地：`lib/client.js::cmpRow/labelAuditPanels`、`backend/review-results.test.cjs`；验证见 `2026-11-12-review-knowledge-leaf-only.md`。

## 2026-11-12：上传题号必须显式写原卷范围，location/paperOrder 不再代替

用户原话要点：「开发者文档新增了关于题号的规范，请阅读并设计修改内容」「将本机目前所有批次都进行对应的修改」「不在放在排队区修改，你直接自己后台改」。规则：`items` 保持原卷单元顺序；独立题顶层 `numberStart/numberEnd` 为同一原卷数字题号，题组为全部唯一子题号的连续闭区间，经来源证明无印刷数字题号的单元显式 `null/null`；`location` 只作来源追溯，`paperOrder` 只是单元序位，均不能代替结构化题号。唯一投影在 `app/workflow/upload_numbering.py::project_batch`：有号只认当前确认结构与原文锚点；无号只认可重建的 `single_writing_section`/`unnumbered_section` 证据，禁止按题型、空 `printed_numbers`、内部续号或图片题别名猜 null；scoped 课时跨大题同号按各自身份保留原号，不全局去重、不重编号。显式范围形状非法或与来源冲突即阻断。存量迁移只改派生范围字段，先备份、原子写入、只允许范围变化、逐批复验；已批准上传的最终 `batch_meta.json` 字节不改写、不伪造批准、不自动重传。无号题组子题的远端 `number=null`/缺省契约未核实，本地保留内部别名和原答案整串，仅写 `UNNUMBERED_SUBQUESTION_CONTRACT_UNRESOLVED` 诊断，不把内部 `1` 或新聚合「1.」冒充印刷号。落地：`app/workflow/upload_numbering.py`、`numbering_migration.py`、`upload_payload.py`、`format_gate.py`、`manifest.py`、`stage_semantics.py`、`review.py`、`assemble_local.py`；根 `AGENTS.md` 硬规则 32；验证见 `2026-11-12-question-number-upload.md`。

## 2026-11-12：上传 PDF 一律整卷 OCR，JSON 作 Word 版面参考

用户原话：「有PDF文件上传时，需要调用OCR识别的功能，产生的JSON文件作为Word的版面参考辅助」；随后确认选择「有 PDF 就整卷 OCR 出 JSON」。规则：只要批次输入含 PDF，无论 OCR 总开关是否关闭、文字层是否合格、Word 是否为主源，都调用识别服务整卷识别，产出 `01_source/pdf-ocr.json`（含页、版面块、坐标、阅读顺序与警告）作为 Word 的版面参考；Word 原文仍是内容权威，`extract._pdf` 只在文字层质量不足的页用 OCR 文字替换，不覆盖 Word 正文；阅读顺序由识别服务负责，不手工重排。落地：`app/workflow/pdf_ocr.py::run`（`force_ocr=True, mandatory_pdf=True`）、`app/docs/extraction.md`「PDF 版面证据」、根 `AGENTS.md` 硬规则 28；验证见 `2026-11-12-pdf-ocr-full-layout.md`。

## 2026-11-12：前期只做内容体检，格式与上传适配集中到最终闸门

用户原话：「目前该流程各个阶段的校验有哪些，感觉有点严了」，随后给出「按照以上进行修改」，采纳
「前期流程要快、少做格式校验；最终对接上传时一次性适配」的改造建议。规则：`validate` 只跑内容/
结构/来源/标签类检查（`checks.CONTENT_CHECKS`）与原卷逐题对账，不再调用 `upload.local_preflight`；
文本/空档/画线/表格/完成句子等格式表达脚本（`FINAL_FORMAT_CHECKS`）只在 `format_gate` 报告，
`[W…` 提示不得当成阻断。`assemble` 的前置预检只保留 `check_structure.py` 与
`audit_type_layout.py --content-only`（结构与选项完整性），不再因空档/画线/表格/重复显示触发模型重试。
`format_gate` 是唯一本地上传格式闸门：格式检查结果写 `report["format_checks"]` 与 warnings，不改
`errors/passed`；真正的阻断仍由 `upload_payload.build_payload` 的 identity/numbering/label_audit/
content shape/catalog/media/table/schema 校验负责。**没有把内容、结构、来源、标签校验降级**；D7 子题
重复整句仍属内容形状错误，继续在 validate 阻断；格式归一化不得改写原始批次内容凑通过，远端目录/
媒体/冻结仍由 prepare-upload 与 submit 校验。落地：`app/workflow/checks.py`、`runner.py::do_validate`、
`assemble_precheck.py`、`format_gate.py::_run_final_format_checks`、`substeps.py`、
`app/scripts/06-upload/audit_type_layout.py`、`app/docs/pipeline.md`、根 `AGENTS.md` 硬规则 33；
验证见 `CHANGELOG/2026-11-12-early-content-late-format-gates.md`。
