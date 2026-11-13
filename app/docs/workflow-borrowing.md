# 从历史卡点出发借鉴成熟工作流：流程、效率、分工与质量

研究日期：2026-11-12。范围：本项目当前源码、ISSUES/CHANGELOG 历史记录，以及 Coze Studio、Eino、LangGraph、Coze Loop、Temporal 的官方源码与 Anthropic 官方工作流文章。

这是研究与实施建议，不是已实施报告。本轮只修改文档；未跑真实批次、调用录题模型、更新安装、迁移数据或提交上传。历史台账中的通过数字只属于原修复记录，不冒充本轮复验。仍在 open 的记录仅说明台账状态，未对当前批次重新判故障。

## 1. 结论与对前两轮的纠正

适合本项目的路线是：保留固定业务 DAG，在既有任务协议和交付验收上补齐契约一致性、模型资源预算、按交付调度、来源及派生视图的一致性、历史故障评测。

前两轮存在几项过度推断，本文取代旧方案：

| 旧说法或建议 | 本轮核实与修正 |
|---|---|
| 没有版本绑定、恢复、评测 | 已有原件/任务/规则摘要、导入幂等、有效分片复用、候选复验和大量回归。缺少的是统一观察与按同一数据集比较的实验报告，不是从零开始。 |
| 审核/上传都应建成等人工的中断节点 | 本地自动运行不新增人工停点。审核差异是正常产出，保留待裁决；上传/发布仍由用户显式动作决定，不借研究改变边界。 |
| 定义全局哈希一变就让所有结果作废 | 按阶段、消费者、单元和字段投影判断影响。在途任务/原件/媒体/上传冻结仍精确绑定；技术性重写不能引发全卷无谓重跑。 |
| 增加 checkpoint 快照后恢复 manifest | 不引入第二份可覆盖真实交付的状态。先复验已有收据、产物和有效分片，再协调恢复；不能靠恢复旧快照制造完成。 |
| 硬链接源批次做沙箱 | 硬链接共享 inode，不能证明隔离。采用独立复制或可靠 COW，并隔离输入路径、缓存、锁、模型路由与上传能力；先用合成夹具。 |
| 所有阶段产物路径不得重复 | 现有 preassemble 与 types 合法交接同一题型产物。定义检查须允许声明的共享/交接，不能用“一律不重复”拒绝现有契约。 |
| 盲审多数票、反复优化到一致可以提高质量 | 不自动裁决真实差异，不以差异数清零为目标。技术性错误可局部修复；语义差异完整交付。 |

现有基础：[阶段契约](../workflow/contracts.py)、[输入与交付台账](../workflow/manifest.py)、[任务协议](../workflow/agent_protocol.py)、[执行投影](../workflow/execution_state.py)、[分片归并](../workflow/unit_tasks.py)、[来源原生对象](../workflow/source_objects.py)。

还需纳入同日新增的[原生内容子智能体](harness-content-agents.md)：持久任务队列、冻结快照、受控取证工具、同任务恢复、精确回执、候选验收和终态保护已经存在。不能再以“只有一次性大prompt/没有受控工具/没有恢复”描述现状。文档及变更记录的模拟回归不等于实卷效果；本轮也未测真实模型质量。

## 2. 实际阅读依据及可迁移边界

外部使用官方来源。GitHub 按下列 revision 固定，避免未来 main 变化使结论无法复核。未在本地部署或运行这些外部项目；读接口只能证明能力边界，不能声称完成验证其端到端实现。

| 来源 | 本轮实际读到的内容 | 借鉴与限制 |
|---|---|---|
| [Coze Studio 执行接口](https://github.com/coze-dev/coze-studio/blob/fefb05ff27be1da939612fbf9faf5db62583b8ae/backend/domain/workflow/component_interface.go) | 单节点执行、恢复、取消、执行历史、按允许旧状态更新、按执行与恢复事件尝试加锁 | 统一执行身份、查询与并发提交条件；接口声明不等于已验证其存储锁实现。 |
| [Eino taskManager](https://github.com/cloudwego/eino/blob/4a4b7186693d9805539c7f0c36db7d03a3485c6a/compose/graph_manager.go#L361-L407) 与 [runner](https://github.com/cloudwego/eino/blob/4a4b7186693d9805539c7f0c36db7d03a3485c6a/compose/graph_run.go#L267-L320) | needAll/waitAll 与 waitOne 分支；完成结果进入下一轮任务计算 | 可借鉴完成即检查下游；不是所有图都默认 eager，也不是允许部分结果冒充完整阶段。 |
| [LangGraph executor](https://github.com/langchain-ai/langgraph/blob/b4cb0f19e3d8984e8a57f85667b8d1bec018f0ab/libs/langgraph/langgraph/pregel/_executor.py#L143-L177)、[runner commit](https://github.com/langchain-ai/langgraph/blob/b4cb0f19e3d8984e8a57f85667b8d1bec018f0ab/libs/langgraph/langgraph/pregel/_runner.py#L575-L615) | 实例级 max_concurrency/Semaphore；按 task.id 保存写入、异常和中断 | 借鉴统一实际执行入口与保留已完成任务；该信号量不是跨主机/跨进程全局预算，fatal 取消兄弟任务的语义也不能照搬。 |
| [Temporal RetryPolicy](https://github.com/temporalio/sdk-python/blob/4733537c4a1d523fa1f0081c18512d895f4f88b8/temporalio/common.py)、[Activity 生命周期](https://docs.temporal.io/activity-execution) | 退避、尝试上限、不可重试错误类型；执行与尝试、取消的区分 | 借鉴错误分类及幂等恢复。源码默认 maximum_attempts=0 表示不限次，这不适合本项目；抖动需本项目另行实现，不能说该 dataclass 自带 jitter。 |
| [Coze Loop 数据集版本](https://github.com/coze-dev/coze-loop/blob/36b9796878604bc87e0fadd047dc27e89dfe8c26/backend/modules/evaluation/domain/entity/evaluation_set_version.go)、[评估记录](https://github.com/coze-dev/coze-loop/blob/36b9796878604bc87e0fadd047dc27e89dfe8c26/backend/modules/evaluation/domain/entity/evaluator_record.go)、[实验实体](https://github.com/coze-dev/coze-loop/blob/36b9796878604bc87e0fadd047dc27e89dfe8c26/backend/modules/evaluation/domain/entity/expt.go) | 数据集/schema 版本；记录关联实验/运行/条目/评估器版本/Trace；Score 与 RunError、耗时、Token 分开 | 最值得迁移的是“同样例、同评价口径、不同实现”的对比。expt 大文件仅阅读返回的实体相关段落，未审计完整实验调度。 |
| [Anthropic Building effective agents](https://www.anthropic.com/research/building-effective-agents) | workflow/agent 区别，程序 gate，sectioning，orchestrator-workers，evaluator-optimizer，工具接口防错 | 固定录题流程适合预定义步骤；动态规划只用于未知故障诊断。文章发布于2024-12-19，不用它断言2026年的SDK现状。 |

Dify 在前轮读过 human_input_policy/node_execution_process_data。本轮不把人工表单列为优先方向。n8n/FlowGram 仅为前期浏览，不把未核实细节列为本轮结论。

## 3. 历史卡点 → 根因 → 借鉴机制

下表是故障模式映射，不是重新打开已修问题。同类模式用一个案例代表，不把条目数当故障发生频率；occurrences 与跨设备历史覆盖并不完整。

| 历史证据 | 暴露的机制问题 | 成熟机制与本地落点 |
|---|---|---|
| [答案示例缺 missing、反复尝试](../../CHANGELOG/2026-11-09-answers-example-missing-field.md) | 示例、角色、校验器不同源 | 执行前编译契约；扩展现有 EXAMPLE_REQUIRED，检查字段及组/子题层级语义。 |
| [无限重派、headless 卡死](../../ISSUES/fixed/2026-11-11-knowledge-局部重判被拒后-supervisor-无限重派,反复拉起-headless.md) | 确定性故障反复派发，多个并发池叠加 | 有界 retry policy + 实际调用总预算；保留已修失败 key 与并发降低，补跨入口准入。 |
| [旧绑定模型调用浪费](../../ISSUES/fixed/2026-11-11-验收刷新输入后仍以旧绑定重试模型.md) | 输入已改变仍使用旧任务重试 | 调用前绑定校验已有；重试应先复验候选与当前输入，不能删精确哈希省事。 |
| [解析器摘要改写共享草稿、答案在途失效](../../ISSUES/fixed/2026-11-12-解析器摘要刷新共享草稿导致答案在途绑定失效.md) | 共享可变输入与消费者绑定粒度不一致 | 尝试输入不可变；语义等价重建不改在途字节；真实变化才按依赖失效。 |
| [新标签未到最终视图、幻影 KNOWLEDGE_REQUIRED](../../ISSUES/fixed/2026-11-12-知识点局部重生成后派生批次仍为旧标签,最终格式检查报幻影-KNOWLEDGE_REQUIRED.md) | 上游交付与派生视图的新鲜度没贯通 | consumer-specific 摘要与统一交付钩子；补源产物→组装→meta→预览→payload端到端断言。 |
| [来源占位清理误触发重审](../../ISSUES/fixed/2026-11-11-来源占位清理被审核绑定误当内容变化触发重审.md) | 技术元数据变化被当作审核内容变化 | 区分原件字节、内容语义、展示投影；仍保留 evidence.source；未决差异迁移必须守恒。 |
| [相同原解析多位置被当冲突](../../ISSUES/fixed/2026-11-11-相同原解析重复位置误判来源不唯一并反复调用模型.md) | 位置唯一与内容等价混淆 | 同来源身份内等价引用复用；直抄优先，模型只定位歧义，不改写正文。 |
| [图片板块/同行子题未贯通消费者](../../ISSUES/fixed/2026-11-12-图片板块截窗与同行语音小题未贯通结构和答案消费者.md) | 不同组件各自解释作用域 | 已有 scoped-v1、视觉边界、source_objects；补每层消费者同源测试，不让各角色重猜。 |
| [PDF块丢弃、候选整批归零](../../ISSUES/open/2026-11-11-PDF-OCR版面被丢弃且课时重号候选整批归零.md) | 来源证据丢失、失败单元影响整个候选集 | 保留服务原序/坐标，按稳定unit隔离诊断；有效候选留证但不伪装整批交付。仍open，未复验现批次。 |
| [租约互毁](../../ISSUES/fixed/2026-11-11-运维租约被并行后端互毁,每轮-claim-后被-process_replaced-打回.md) 与 [未知回执隔离](../../ISSUES/fixed/2026-11-10-历史运维动作回执未知导致接手隔离.md) | 活跃所有者和死进程混淆；结果未知时存在重复写风险 | Coze式条件更新/按执行查询，加现有租约核验与幂等键；未知先对账，不重发、不重建批次掩盖。 |
| [列表请求52秒断连](<../../ISSUES/fixed/2026-11-11-审核页试卷列表加载超时(-api-batches-52s-断连).md>) | 每轮展示重复扫描、哈希和深拷贝 | 已有快照与缓存优化；继续最小依赖投影、轻列表/重详情，不在轮询里重跑验收。 |
| [审核显示、缓存和prompt版本不一致](../../CHANGELOG/2026-11-12-review-status-root-cause-hardening.md) | verdict、执行完成、差异状态、完整路径比较、冻结prompt不同口径 | 三类状态分开，单一差异投影；新任务绑定实际prompt；复用已验证检查，不重新“投票”。 |
| [表格单元格图平台无法表达](../../ISSUES/fixed/2026-11-12-格式闸门确定性失败-表格单元格内图片平台契约无法表达.md) | 平台能力限制与内容错误混在一个失败通道 | 依照已有明确口径保留附件与显式warning；不让模型重复尝试修复平台能力，不泛化豁免嵌套表格/真实错位。 |

## 4. 流程：从批量等待改为有效交付驱动

### 已经有的

[contracts](../workflow/contracts.py) 的 assemble 不依赖 audio，review 等 validate 与 audio；[auto](../workflow/auto.py) 713附近已开独立音频future。不能把现状说成全串行。

### 已证实的代码风险：限流拦错层

[auto.py](../workflow/auto.py) 851–855 对 kind=AGENT 先挡冷却，但 [agent.py](../workflow/agent.py) 370–383 的托管 assemble 是本地确定性合并，答案/解析等也有 deterministic preflight。也就是说，“代理阶段”不等于“当前需要调用模型”。现有 test_runner_auto 已覆盖 COMMAND/GATE，尚需覆盖 AGENT 内的本地路径。

建议先给已有直抄/preflight/assemble执行机会，只在实际invoke前申请模型许可及检查对应路由冷却。不要让一个模型的429冻结所有本地进度。

验收：冷却期间三路文字已交付仍能 assemble→meta→underlines→validate；可直抄答案0模型调用；确需模型的任务按retry_at等待。记录冷却引入的本地额外等待时间，目标为0。

### 需要时序回归确认的风险：并行波次尾部屏障

[auto.py](../workflow/auto.py) 398–418 虽 as_completed 后即导入，但函数要等整个线程池结束才返回；主循环930附近同步调用。若慢audio进入这一波模型调用，文字完成后仍可能晚启动assemble。独立音频预处理已规避部分情况，不能声称所有音频路径都阻塞。

借鉴Eino waitOne后重新计算下一批任务：保留单一导入/记账者，每次真实交付后重检消费者依赖。第一步只优化完整阶段交付；不开放“半组通过即整阶段完成”。

验收：用event挂起audio，完成answers/explanations/knowledge；在释放audio前观察assemble和validate启动，review仍不能启动。重启恢复、失败旁支和取消也用时序事件测试，不能靠sleep碰运气。

收益须分开测：文字可用时间通常会改善，但若听力占总关键路径，整批总时间未必同比缩短。目标结构为：前置耗时 + max(听力耗时, 三文字分支最大耗时+本地汇总体检耗时) + 审核及最终格式耗时；这是依赖图分析，不是本轮实测性能承诺。

## 5. 效率：统一调用预算，减少重复工作

### 5.1 主机预算 + 实际路由预算 + 原有分片限额

现有auto阶段池≤4，label_execution/local_agent还各有限制；原生宿主[content-agent.js](../../lib/content-agent.js)又有maxConcurrent=2，以及轮次、工具和任务时间预算。不能说没有限并发，也不能机械估计4×part_concurrency。可核实的是各执行入口预算并非同一个许可源；是否同时运行并造成超载，以及Harness更底层是否另有限额，均需观测。原生路径与CLI路径应分别量测，不把旧CLI的过载直接套到新原生管理器。

借鉴LangGraph在执行器处gate。先记录两种执行方式的实际并发；如果存在跨入口超额，再补适合本插件多进程的共享许可：

- 实际模型子进程共享主机总许可；provider/model/凭据引用维度另设路由许可，不保存凭据明文。
- 本地解析、验收、导入不占模型许可；听力CPU/OCR网络资源分别设置上限，不共用一个大锁。
- 基于现有锁与租约风格实现持有者身份、超时回收；不能只加进程内Semaphore就称跨进程有效。
- 先保守静态上限，再测资源。按unit_id稳定小批分片，并按提示长度/媒体量控制大小，失败不重跑已验单元。
- 公平性先用轮转/等待年龄度量；本轮没有实测证据证明当前存在饥饿，不先宣称需要复杂优先队列。

验收：两个后端、多个阶段/分片并行，真实invoke峰值≤总预算；被取消/崩溃持有者不泄漏许可；排队任务不持批次写锁；记录RSS、headless峰值、吞吐、队列P95。增加并发必须有数据证明收益。

### 5.2 直抄、引用和模型职责

现有answers_direct、explanations_direct、label_execution已经尝试确定性路径。应统计“为何没有直抄成功”，而非再加模型角色。

程序做：原生提取、解析引用、ID回填、范围校验、合并、归一和格式投影。模型做：需上下文理解的边界/题型判断、知识点选择、独立assessment、未知技术故障定位。模型不代写原卷答案/解析。

上下文按稳定来源对象和完整题组打包：包括公共导语、所属课时/板块/大题、候选答案窗口、合法目录；不能为减少token把必要归属证据截掉。现有原生内容工具已支持分页读来源与目录、查询单元和查看白名单图片，优先测量其“读取了哪些窗口、是否重复读取、是否漏必要归属”。大目录优先确定性按类型维度裁剪，歧义保留候选而非选“最近合法叶子”。

缓存记录实际路由、源/媒体字节、prompt与验收规则；只复用当前仍通过消费者校验的结果。独立盲审不复用另一轮判断；冷缓存/热缓存分别测，避免旧转录造成测试假快。

## 6. 分工：角色权限、交付和规则要同源

### 当前明确的两处矛盾

1. [knowledge角色](../prompts/roles/knowledge.md) 第19行要求阅读类每个子题标“设题角度”，但 [agent_tasks](../workflow/agent_tasks.py) 的规则及1395–1408附近校验要求whole-unit子题标签为空；角色第16行和常见错误也要求整组标注，正文内部就不一致。
2. [answers角色](../prompts/roles/answers.md) 第11行允许answers/evidence/difficulty，漏掉当前校验器要求的missing；已有示例键检查并没有消除此权限正文漂移。

建议先以现有题型知识映射/目录和阶段契约为权威，将required_fields、allowed_fields、组/子题层级、必需维度生成到schema、示例和角色权限段；职责与故障处理正文仍可手写。测试实际构建出的完整/文件协议/分片/局部修复/轮次专用prompt，不只检查某个常量。

不要让模型角色自行决定扩大范围、写正式文件或修改台账。沿用任务包与importer：worker只交候选；程序验证来源、覆盖、目录、未影响基线后提交。组装保留确定性职责，不增加一个“优化正文”的模型步骤。

| 工作 | 唯一负责者/执行方式 | 交付条件 |
|---|---|---|
| 原件、表格、媒体对象 | 原生解析/OCR保留器 | 字节、对象身份、服务原序与坐标可追溯 |
| 题组与题型 | preassemble及确定性验收 | 完整来源窗口、稳定unit_id、题型合法 |
| 原答案/解析 | 直抄优先，模型辅助定位 | 当前scope引文/坐标、覆盖与缺失判定合格 |
| 知识点 | knowledge角色 | 路径/ID/维度/层级/覆盖合格 |
| 内容归并 | importer/assemble | disjoint写范围、保留未影响基线 |
| 独立审核 | 两轮独立assessment + 程序比较 | 技术协议合格，真实差异不丢、不自动改标 |
| 运维 | 故障定位和受控局部修复 | 技术故障、范围、预算、基线、复验证据齐全 |
| 平台格式/上传 | 最终adapter与用户授权入口 | 保留前期内容体检边界，上传授权不被研究重定义 |

验收：故意构造上述规则冲突，必须在调用模型前失败（调用数0）；合法阅读组空子标签接受，越层打标拒绝；missing不违反角色权限。

## 7. 质量：分别衡量执行、保真和语义差异

学习Coze Loop把Score和RunError分开：调用失败、解析失败不应伪装成内容评分低；任务执行成功也不等于内容无误。

本项目保留四种输出：execution状态、内容/来源验收、独立assessment与差异、格式/平台能力。needs_review必须带语义：真实quality_differences是正常产出，协议失败/未完成才需技术修复。

建议建立分层验证：

1. 模型调用前：字段、示例、角色权限、目录、目标覆盖、当前绑定可用；确定性错误0调用。
2. 接收时：来源scope、答案/解析遗漏、题号、组/子题完整性、知识点合法性、输出自相矛盾。
3. 归并后：未影响单元不变，派生视图摘要匹配，晚到音频和局部标签贯通最终消费者。
4. 独立审核：盲审给assessment，程序比较；不以“verdict ok”替代标签一致，不以多数票裁决。
5. 最终适配：遵守新规则33，前期只做内容体检；平台格式与能力适配集中format_gate，不让画线/空档等软提示触发模型重跑。

知识点evidence的本地_evidence分支目前只要求非空字符串列表；全项目已有独立语义审核，不能据此说“没检查知识点”。可先离线量测引文命中、坐标可解析、当前scope归属，确认现状后再决定是否改变生产验收；本轮不增加阻断条件。

### 历史故障评测集

已有大量单测，补的是跨层故障集和同条件实验：每个案例关联ISSUES id、最小可公开/脱敏输入、预期不变量、必到消费层、禁止行为、相关测试入口。避免依赖已归档真实批次和机器私有配置。

首批建议覆盖：分段下划线+合并表格、课时重号、图片板块、合法空白/图片组、同文多位置、Word缺失PDF兜底、答案missing契约、盲审隐藏字段、局部标签传播、技术等价改写、并发429、未知回执/崩溃恢复、UI完整路径集合比较。

- 同时放“应接受”和“必须拒绝”的成对样例：同文等价 vs 跨大题借用；排版等价 vs 否定词变化；图片能力warning vs 丢题；部分候选保留 vs 全批伪完成。
- 确定性套件默认0网络/0模型，使用独立临时锁和缓存命名空间；不清用户全局缓存。不让全量测试意外读取本机生产模型配置。
- 模型实验另启，明确实际路由、prompt/数据/评估器版本、缓存模式及费用；记录逐例原始结果和运行错误，不只一个总分。
- 一轮技术修复后复验原候选，合格就复用；真实差异不进入“优化到一致”循环。

指标：原文/选项/材料保真率、题号/身份覆盖、答案与解析来源命中、误阻断率、实际错误漏检率、未裁决差异保留率、模型调用/合格交付单元、候选复用率、耗时P50/P95、峰值资源。真实错误漏检率需标注真值，只对已构造/审定案例报告；不能拿“无错误报告”当正确率100%。

硬目标：基线保护、差异保留、身份覆盖、禁止重复动作这类不变量在回归中100%满足。耗时与成本先建立基线，不能承诺“必降50%”；审核差异数量不设越低越好的KPI。

## 8. 可靠恢复：分类、候选复验与条件提交

借鉴Temporal策略，但复用现有self_heal/operations，不增加第三套重试循环：

| 分类 | 动作 | 模型是否重调 |
|---|---|---|
| 429/短暂网络失败 | 对应路由退避，遵守retry_at，继续本地和其他可独立分支 | 有预算、冷却结束且绑定仍当前时才允许 |
| schema/prompt/validator确定性缺陷 | 保存候选和诊断，修契约/代码后先复验候选 | 相同条件禁止原样重调 |
| 来源歧义/越scope/缺证据 | 回到具体来源对象/受影响unit定位，保留范围外基线 | 不能通过扩大到整卷或编造填空解决 |
| 正常标签/答案等质量差异 | 保存差异并交付展示 | 不为清零差异重调或自动resolve |
| 平台能力限制 | 按明确既有口径输出能力warning/error | 不用模型“修复”平台协议 |
| 结果未知/提交中崩溃 | 按request_id先查receipt/产物，核对所有者与尝试身份 | 未知不盲目再写 |

操作身份建议在已有回执上关联batch_instance、stage、unit_ids、attempt/task_id、input_binding、action_type、owner/lease。旧worker迟到提交须被现有锁加尝试绑定拒绝；若增加lease generation，必须在写侧验证，不能只有心跳字段。

同一失败预算不能被时间戳、日志措辞、尝试ID变化重置。心跳只证明活着，真实进展看新增有效单元/已验分片/阶段交付；长时间无进展应给出具体失败检查和恢复动作，不把“已安排重试”显示为恢复。

## 9. 数据与展示：减少解释分叉、避免新状态真相

已存在source_objects按原件/有序结构路径/内容摘要绑定表格和单元格；不再建议从扁平Markdown重猜Word合并。scoped-v1与视觉板块证据也应由不同消费者共同使用。PDF输入遵守当前规则28：含PDF整卷OCR留JSON作为版面证据；Word仍内容权威，服务阅读顺序保留，不在本地手工重排。

建议三类摘要各司其职：精确字节绑定、消费者语义摘要、展示投影键。不能把所有摘要都改为“去标签去空白”：这可能消掉有效结构或实质差异；同样不能把日志时间/样式变化扩散为全卷失效。

性能方面，列表不重算整卷验收；展示只消费已验证记录及最小依赖投影。已有列表快照和内容键缓存，应补每次真实字段变化触发的定向失效测试。重型详情按需读取；列表与详情采用同一差异计算函数，避免完整路径/集合的规则分叉。

统一观察只在既有回执/日志上补关联字段：run/attempt/task/unit/source_ref、执行方式、实际路由、排队/调用/验收/导入耗时、错误分类、是否可重试、产物/消费者摘要。优先让diagnose与列表使用这些字段，不先堆一套无消费者的JSON。

## 10. 实施顺序与停止条件

| 批次 | 最小范围 | 对应故障 | 验收/停止条件 |
|---|---|---|---|
| P0-A 契约同源 | 两处明确角色矛盾 + 构建prompt一致性检查 | missing/whole-unit层级冲突 | 合成漂移0模型调用即拒，合法任务不误拒 |
| P0-B 冷却下沉 | deterministic preflight/assemble先行，模型invoke再准入 | 限流冻结本地路径 | 冷却中本地链可完结，模型调用受限 |
| P0-C 总资源预算核对 | 统一观察原生maxConcurrent与CLI各池；证实跨入口超额再共享许可 | headless过载/重复派发 | 双后端/不同transport峰值可解释，退出回收、无锁饥饿 |
| P1-A 交付再调度 | 消除一个有时序证据的波次屏障 | 慢旁支拖延文字可用时间 | assemble早于audio结束，review仍等待 |
| P1-B 消费链不变量 | 现有失效/交付钩子贯通，技术等价不重写 | 幻影旧标签/在途失效/丢材料 | 源→组装→meta→展示→payload同时核验 |
| P1-C 历史故障实验 | 版本化小案例集、离线报告、正反对照 | 修一处另一处再坏/测试假快 | 确定性无外调；同样例可比较质量/成本 |
| P2 观察与调优 | 关联既有回执、队列等待/资源/复用指标 | 诊断只见末尾错误/列表慢 | 面板与diagnose实际消费，记录不提供放行权 |

每步独立验证、独立改动记录；首次实现先补失败用例再改对应路径。优先守住内容和未影响基线，前期不重复平台格式检查。动态跨unit流式交付、全局定义版本迁移、通用工作流替换和新人工中断均后置：当前收益/迁移风险证据不足。

这轮未改运行代码，因此这些项均为建议，不标“已修复”，不关闭ISSUES，不重跑历史批次。

## 11. 防停摆专项补查（2026-11-12 20:58起）

用户进一步要求研究“经常卡住”的流程问题。本节只读核对现代码，未运行故障注入或真实批次。因此下列是静态可构造的失败窗口，不等于证明某个当前批次正因它卡住。

### 11.1 还需要学习的机制

- **控制器持续对账**：Kubernetes controller 持续核对期望与实际状态，适合迁移为“队列非空→必须有有效worker或明确等待原因”的不变量，不要求引入Kubernetes。[官方源码文档](https://github.com/kubernetes/website/blob/main/content/en/docs/concepts/architecture/controller.md)
- **存活、就绪、进展分层**：进程存在不证明任务能推进；不宜一超时就重启整后端。先探测推进线程、监督tick、任务截止、有效产出，再安全回收对应执行单元。[Kubernetes probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/)
- **分层截止时间**：Temporal区分排队、单次执行、总执行、心跳超时。我们已有HTTP及原生任务预算，应补“排队未认领”“worker认领后无进展”“未知结果对账”等阶段的截止和责任者，不把所有等待统一算模型超时。[官方超时文档](https://docs.temporal.io/encyclopedia/detecting-activity-failures)
- **未知结果的对账状态机**：AWS幂等设计用调用方请求ID表达操作意图；本项目已有精确task/receipt，应在请求应答丢失后持续只读核对，而非盲重发或一次查询失败就永久放弃。[AWS Builders Library](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)
- **无丢失唤醒与失败注入**：线程标志不等于线程存活；Event清理顺序会影响唤醒可靠性。优先用可控event/fake clock构造竞态，不靠真实试卷反复碰运气。[Python Event语义](https://docs.python.org/3/library/threading.html#event-objects)

### 11.2 当前最值得验证的四个窗口

| 优先级/静态窗口 | 触发链及现有防线 | 最小优化与离线验收 |
|---|---|---|
| **P0：队列有任务但worker根本没启动** | server.py 1099–1105先把`_advance_worker_running=True`，再Thread.start；若start抛异常，无句柄/回滚。队列1191已保留；下次同名请求1170–1171因已queued提前返回，无法走1193的ensure。宿主仅看后端进程活着，不会修这个状态。正常执行finally已会清running，但无法覆盖未启动。 | 保留worker句柄，核对is_alive；启动失败回滚标志；遇已queued也修复worker存在性。模拟start首轮抛错，再恢复，断言旧队列被消费、无重复执行。必须同时覆盖启动异常与同名去重分支。 |
| **P0：原生任务认领成功，但响应丢失** | content-agent.js 201–203需claim应答成功才设claimed；catch/finally令本地记录settled；430对records已有task永远跳过。后台可能仍为claimed，无模型会话直到总deadline；Python队列已有绑定和终态保护，不能抹掉重建任务解决。 | 增加claim_unknown/reconciling状态；只读确认任务owner/session/receipt，再恢复同一任务，预算不重置。若服务无法证明原owner，等待有界租约或明确失败，不能偷抢。模拟POST已落盘后丢应答，断言能收敛且0重复claim副作用/0重复模型会话。 |
| **P1：暂停检查与wake清理之间丢信号** | supervisor.py 651判断无工作，656再clear；若其间入队并wake，clear可清掉刚到的信号；657会睡至pause_poll，默认600秒。现有“已paused再wake”测试不覆盖这个窗口。 | clear-before-check并重检谓词，或用Condition/代际计数；加确定性竞态测试，入队后不等600秒兜底。事件只是加速，周期对账仍保留。 |
| **P1：工具结果未知，首轮回执查询又失败** | content-agent.js 140–145将POST未知设outcomeUnknown，256–267只查一次receipt，finally settled，430阻止后续poll继续处理。若后台accepted已落盘，Python invoke仍可能独立推进，不能说必卡下游；若未完成则可能等到总deadline。 | 保留只读对账队列，短暂GET失败后退避查询，恢复后收敛；不重放submit、不自动新建模型。模拟POST已提交、GET前两次失败后成功，断言一次提交并恢复准确终态。 |

现有测试覆盖“不重发POST”和“第一次GET就查到accepted”，尚不能证明“查询也短暂失败后自动恢复”。这些新场景应先作为失败用例，不先认定生产修复已完成。

### 11.3 继续核验的两条监督策略

1. supervisor.py 553–560先按批次`max(reservation, provider冷却, self_heal重试)`返回，564才识别新增独立就绪工作。即便下一层auto允许本地路径，监督层仍可能不派发。应按实际执行需求判断路由冷却，禁止绕过模型retry_at；测试冷却中的COMMAND/GATE和确定性AGENT可完成。
2. `_transient`把config/spawn/timeout也列为外部短暂失败，长期相同配置错误可能一直被视为waiting_external。须区分已证实瞬时网络故障与不会自行变化的配置/执行器缺陷；不要把真实429记卡点，也不要把固定无效配置无限延期。正常waiting/busy应带wait_kind、等待对象、下次检查、责任者，不按普通失败累加预算。

### 11.4 恢复动作必须有唯一责任者

建议在已有状态上明确：宿主看门狗修后端退出；监督器核对就绪任务/worker；执行器负责单次任务和取消；self_heal/operations处理有具体检查的技术故障；receipt reconciler处理结果未知。各层共享尝试身份和预算，不能都各自“再重试一次”。

健康信息应区分backend_alive、supervisor_last_tick、worker_alive、queue_oldest_age、当前wait_kind、last_verified_progress、deadline_at、next_check_at与recovery_owner。心跳不能当完成证据；状态不一致时先读收据与现有产物，不写完成态。

**多批次严格串行是已有明确要求**，见[严格队首规则](../../CHANGELOG/2026-11-10-strict-queue-serialization.md)。不建议跳过卡住队首、并行启动后一批来掩盖问题。优化范围是修队首的卡点、单批次内并行以及明确等待原因；前文公平性建议仅适用于同批次资源分配，不改变跨批顺序。

### 11.5 实施与测试顺序修订

先做四类故障注入：线程启动失败、认领应答丢失、提交应答及回执查询接连失败、空闲转活跃时丢唤醒；随后补监督冷却与无进展检测。每例同时断言：有界自动恢复、模型调用不重复、未影响基线不变、旧worker不能覆盖新状态、真实审核差异不被清除、用户移出/删除的批次不被复活。

这些是控制流可靠性问题，优先级应高于新增通用Trace界面或改画布；仍不替代前述契约同源与内容质量修复。
