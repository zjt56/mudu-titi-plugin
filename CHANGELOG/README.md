# CHANGELOG — 项目变更记录制度

本目录专门记录「录题插件版」的一切重要变更与规则强调。**改完就记，不等提醒。**

## 文件约定

| 文件 | 内容 |
|---|---|
| `README.md` | 本文件：制度说明 + 变更索引 |
| `RULES.md` | 规则/强调台账：用户强调过/反复强调过/新定的硬规则，逐条留痕 |
| `YYYY-MM-DD-<slug>.md` | 每次重大改动一份记录 |

**重大改动** = 影响运行行为、模型路由、子 Agent 提示词契约、数据结构/产物形状、审核口径、上传边界。
小修（注释、补测试、typo）可并入当天的 `-misc` 或省略。

## 一条记录至少写清

1. **背景/触发**：用户提了什么、哪里坏了；
2. **改了什么**：文件 + 关键行为变化；
3. **没改什么 / 边界**：避免被误读为"全自动通过"；
4. **如何验证**：测试名与结果数字（写实测数字，不写"已测试"）；
5. **回退方式**（如有）。

## 何时必须记录

- 用户提出新的硬要求，或纠正了我们的做法；
- 修掉一个卡点（卡点修复必须能指向测试）；
- 调整了模型路由、子 Agent 提示词、阶段契约；
- 改变审核范围或上传边界。

## 变更索引（新→旧）

| 日期 | 记录 | 摘要 |
|---|---|---|
| 2026-11-09 | [2026-11-09-preassembly-types.md](2026-11-09-preassembly-types.md) | 题型真正并入前置组装交付；历史题型检查点无模型，局部修正共享签名与真实运行进度同步 |
| 2026-11-09 | [2026-11-09-model-default-follow.md](2026-11-09-model-default-follow.md) | 修改全局默认模型时，仍跟随默认的子 Agent 与审核轮次一起切换；显式指定的保持不变 |
| 2026-11-09 | [2026-11-09-operations-recovery-contracts.md](2026-11-09-operations-recovery-contracts.md) | 修复发布标记、回滚事务与路径边界、重启后二次修复、重生成结算；五个内容阶段统一局部任务与导入 |
| 2026-11-09 | [2026-11-09-parallel-stage-display.md](2026-11-09-parallel-stage-display.md) | 流程进度显示：并行阶段同时显示处理中，完成态不再被压回（只改显示） |
| 2026-11-09 | [2026-11-09-upstream-review.md](2026-11-09-upstream-review.md) | 拉取2f51429并评估工作流与运维变更；发现反馈契约、局部重生成、发布标记及回滚事务等高优先级问题 |
| 2026-11-09 | [2026-11-09-answers-example-missing-field.md](2026-11-09-answers-example-missing-field.md) | 答案阶段提示词示例补回必填 `missing`，并在报错里点名缺失键/题号；修复2017卷在该阶段无限重试，47+63项测试通过 |
| 2026-11-09 | [2026-11-09-model-list-host-providers.md](2026-11-09-model-list-host-providers.md) | 设置页模型下拉补上宿主实际可用的 provider 与账号目录模型，不再只正则解析两个补丁块 |
| 2026-11-09 | [2026-11-09-upload-local-settings.md](2026-11-09-upload-local-settings.md) | 经运行中插件设置接口保存本机上传参数与私钥，凭据仅存宿主私有目录并收紧 ACL；无代码改动 |
| 2026-11-09 | [2026-11-09-operations-fast-repair.md](2026-11-09-operations-fast-repair.md) | 故障证据包、同证据重复失败升级、局部重生成、隔离代码修复与受控安装、发布标记、宿主单次重启与失败自动还原、跨轮次预算与人工出口；Python 71项、JS 45项测试通过 |
| 2026-11-09 | [2026-11-09-answer-explanation-local-repair.md](2026-11-09-answer-explanation-local-repair.md) | 答案/解析有界分片、失败单元修复、来源片段复制与及时交付，保留整批验收 |
| 2026-11-09 | [2026-11-09-finalization-failure-diagnosis.md](2026-11-09-finalization-failure-diagnosis.md) | 空格式 errors 保留真实固化异常；来源冲突定位题组和字段，区分执行与质量交付；55项通过 |
| 2026-11-09 | [2026-11-09-server-contract-alignment.md](2026-11-09-server-contract-alignment.md) | 混合 ID 批次逐题加前缀、保留自定义 ID；每题附件上限20，187项回归通过 |
| 2026-11-09 | [2026-11-09-review-pdf-evidence-binding.md](2026-11-09-review-pdf-evidence-binding.md) | 审核计划补齐提示词已提供的OCR与来源角色摘要，保持原文证据和过期检查 |
| 2026-11-09 | [2026-11-09-operations-model-diagnostics.md](2026-11-09-operations-model-diagnostics.md) | 运维失败保留脱敏 cause，不被摘要覆盖；42项相关测试通过，真实TRANSPORT根因仍待证据 |
| 2026-11-09 | [2026-11-09-url-source-and-image-label-validation.md](2026-11-09-url-source-and-image-label-validation.md) | 修复网址误脱敏与图片段落字母误报；106项回归通过，2020安徽卷本地体检通过 |
| 2026-11-09 | [2026-11-09-table-empty-items-body-equivalence.md](2026-11-09-table-empty-items-body-equivalence.md) | 表格空 items 与缺失等价，真实正文与跨度变化仍阻断；83项回归通过 |
| 2026-11-09 | [2026-11-09-shared-rubric-review-fixes.md](2026-11-09-shared-rubric-review-fixes.md) | 修正共享题干坐标、归属、多行和前缀，打通原文审计与预览链路 |
| 2026-11-09 | [2026-11-09-shared-section-rubric.md](2026-11-09-shared-section-rubric.md) | 完形 A/B、阅读各篇补回同一大题导语，`第一节/第二节` 前缀可识别，材料与 A/B 标签保留 |
| 2026-11-09 | [2026-11-09-assembly-empty-material.md](2026-11-09-assembly-empty-material.md) | 组装格式转换保留来源绑定的空材料，避免复制子题；相关85项通过、5项跳过 |
| 2026-11-09 | [2026-11-09-source-material-recovery.md](2026-11-09-source-material-recovery.md) | 修复Windows事务临时文件权限兼容；不新增无实际需要的恢复接口 |
| 2026-11-09 | [2026-11-09-2020-preassemble-body-conflict.md](2026-11-09-2020-preassemble-body-conflict.md) | 2020 安徽卷恢复 4 处有来源证明的正文差异，预装配复验通过并推进至体检 |
| 2026-11-09 | [2026-11-09-assemble-preassemble-dependency.md](2026-11-09-assemble-preassemble-dependency.md) | 组装显式等待当前前置对象合格，保留听力并行；8项回归通过 |
| 2026-11-09 | [2026-11-09-question-id-paper-prefix.md](2026-11-09-question-id-paper-prefix.md) | 修复多卷上传 question_id_exists：本地 q-N 按批次自动加年份+哈希前缀，2015 存量暂存已修 |
| 2026-11-09 | [2026-11-09-upload-attachment-placement.md](2026-11-09-upload-attachment-placement.md) | 修复逐题确认 invalid_request：材料图 placement=material 归一为 question，服务端兼容旧暂存 |
| 2026-11-09 | [2026-11-09-operations-read-during-worker.md](2026-11-09-operations-read-during-worker.md) | 批次执行时 AI 运维只读已持久化快照，不再因共用执行锁报错 |
| 2026-11-09 | [2026-11-09-windows-native-router.md](2026-11-09-windows-native-router.md) | Windows agent 桥改用 Node 原生 router，直接调用 dsh.cmd，不再依赖 /bin/sh |
| 2026-11-09 | [2026-11-09-console-encoding-stage-wedge.md](2026-11-09-console-encoding-stage-wedge.md) | 控制台GBK回显不再崩：stdio改replace、阶段子进程UTF-8、执行器崩溃标failed不再卡running |
| 2026-11-09 | [2026-11-09-review-card-freshness.md](2026-11-09-review-card-freshness.md) | 审核卡片区分"已完成/记录待更新/未执行"，旧协议记录自动真实补审，48项回归通过 |
| 2026-11-08 | [2026-11-08-harness-operations.md](2026-11-08-harness-operations.md) | 附加Harness运维会话、持久故障处理记录、受控修复及真实复验，保留已有流程 |
| 2026-11-08 | [2026-11-08-image-attachment-preserve.md](2026-11-08-image-attachment-preserve.md) | 修复组级/子题图片附件在组装与沿用中被丢弃（上传 SOURCE_IMAGE_MISSING），23卷审计 0 issues |
| 2026-11-08 | [2026-11-08-format-table-grid-diagnosis.md](2026-11-08-format-table-grid-diagnosis.md) | 修复Word空白网格列丢失及格式诊断误读deferred，2017卷体检和最终格式已通过 |
| 2026-11-08 | [2026-11-08-source-type-select.md](2026-11-08-source-type-select.md) | 修改标签的来源类型改为目录下拉选择，具体来源名称保留文本输入 |
| 2026-11-08 | [2026-11-08-writing-preview-no-duplicate.md](2026-11-08-writing-preview-no-duplicate.md) | 书面表达预览只补切片之前的导语，修复题干/要求/配图重复，原始与正式数据不变 |
| 2026-11-08 | [2026-11-08-review-capability-keeps-differences.md](2026-11-08-review-capability-keeps-differences.md) | 修复图片能力提示吞掉程序标签差异；2015 卷第 6–10 题补回待审，整卷队列 14 条逐项一致 |
| 2026-11-08 | [2026-11-08-blind-review-label-absence.md](2026-11-08-blind-review-label-absence.md) | 修复盲审隐藏标签被误判缺失；无依据结论自动局部补审、2015 卷重建队列并保留真实差异 |
| 2026-11-08 | [2026-11-08-explanation-binding-fix.md](2026-11-08-explanation-binding-fix.md) | （中间方案，已被 direct-copy 取代）放宽解析严格来源校验的历史留痕 |
| 2026-11-08 | [2026-11-08-format-hints-not-blocking.md](2026-11-08-format-hints-not-blocking.md) | 格式小问题（下划线/空档/表格写法）只提示不阻断；题号题干对不上等实质错误仍阻断 |
| 2026-11-08 | [2026-11-08-review-difference-kept.md](2026-11-08-review-difference-kept.md) | 更正：一审/二审与原标签不一致必须待审核；移除把真实差异当“过期结论”的过滤 |
| 2026-11-08 | [2026-11-08-audio-runs-parallel.md](2026-11-08-audio-runs-parallel.md) | 听力与解析/知识点/组装并行；只有内容审核等听力完成 |
| 2026-11-08 | [2026-11-08-preview-keeps-group-material.md](2026-11-08-preview-keeps-group-material.md) | 预览切片不再丢题号前的表格/材料（2024 卷首篇阅读只剩选项的根因） |
| 2026-11-08 | [2026-11-08-explanation-reference-authority.md](2026-11-08-explanation-reference-authority.md) | 解析来源唯一性：PDF/OCR 副本不再与 Word 权威来源竞争；闸门诊断指明具体检查项 |
| 2026-11-08 | [2026-11-08-normalized-stage-fingerprints.md](2026-11-08-normalized-stage-fingerprints.md) | 阶段复用用规范化输出签名，消除 JSON 序列化噪声导致的重跑；精确绑定与真实内容验收保留 |
| 2026-11-08 | [2026-11-08-serial-batch-queue.md](2026-11-08-serial-batch-queue.md) | 多份试卷上传改为全局串行排队（不并行） |
| 2026-11-08 | [2026-11-08-group-material-table-span.md](2026-11-08-group-material-table-span.md) | 修复表格型阅读题组 material 丢失（A8） |
| 2026-11-08 | [2026-11-08-table-responsive-wrap.md](2026-11-08-table-responsive-wrap.md) | 表格自适应宽度并自动断行，取消横向滚动 |
| 2026-11-08 | [2026-11-08-table-image-in-cell.md](2026-11-08-table-image-in-cell.md) | 表格图片与文字放回同一单元格 |
| 2026-11-08 | [2026-11-08-table-row-images-tiny.md](2026-11-08-table-row-images-tiny.md) | 表格图片按行归位并缩为极小缩略图 |
| 2026-11-08 | [2026-11-08-preview-table-image-and-size.md](2026-11-08-preview-table-image-and-size.md) | 修复表格内图片预览缺失，并缩小原卷图片显示 |
| 2026-11-08 | [2026-11-08-review-stale-snapshot-fix.md](2026-11-08-review-stale-snapshot-fix.md) | 修复审核旧快照和盲审脱敏导致的误报待审 |
| 2026-11-08 | [2026-11-08-batch-specific-blocker-report.md](2026-11-08-batch-specific-blocker-report.md) | 卡点诊断和继续处理错误必须明确显示具体批次 |
| 2026-11-08 | [2026-11-08-source-boundary-fix.md](2026-11-08-source-boundary-fix.md) | 来源结构按 source_span 审计选项，修复 18 项结构边界回归 |
| 2026-11-08 | [2026-11-08-performance-cleanup.md](2026-11-08-performance-cleanup.md) | 低风险哈希提速、行内选项兼容与本地产物忽略 | 
| 2026-11-08 | [2026-11-08-onboarding-wizard.md](2026-11-08-onboarding-wizard.md) | 新增「初始化引导」页：六项体检 + 听力模型一键安装/更新 |
| 2026-11-08 | [2026-11-08-listening-no-phonetics.md](2026-11-08-listening-no-phonetics.md) | 听力关键词语选择不再标「语音」知识点（语音是语音题考点） |
| 2026-11-08 | [2026-11-08-audio-fastpath-and-br-newline.md](2026-11-08-audio-fastpath-and-br-newline.md) | 修复听力切段快路脱敏误判、装配 `<br>` 换行被 B10 拦 |
| 2026-11-08 | [2026-11-08-whole-paper-single-pass.md](2026-11-08-whole-paper-single-pass.md) | 标注阶段改为整卷一次分析（不再一题一片），提示词瘦身，knowledge 209→69s、explanations 296→80s |
| 2026-11-08 | [2026-11-08-simplify-hash-checks.md](2026-11-08-simplify-hash-checks.md) | 简化哈希/过期校验让流程逐步填充、不再反复卡；题号/答案/格式正确性关卡保留 |
| 2026-11-08 | [2026-11-08-relax-strict-handoff.md](2026-11-08-relax-strict-handoff.md) | 移除逐交接点严格校验与语义指纹，改为轻量哈希+末次整体验收，解决反复卡顿 |
| 2026-11-08 | [2026-11-08-explanation-direct-copy.md](2026-11-08-explanation-direct-copy.md) | 解析/大意改为按题号从 Word 原文直接抄，不调模型、不严格溯源，修复卡顿与重跑 |
| 2026-11-08 | [2026-11-08-explanation-source-noise.md](2026-11-08-explanation-source-noise.md) | 解析来源校验适配 OCR 噪声与答案/解析分行，修复 2023 卷 explanations 连锁重跑 |
| 2026-11-07 | [2026-11-07-upstream-downstream-handoff.md](2026-11-07-upstream-downstream-handoff.md) | 上游必须通过下游交接验收才允许推进：统一 handoff、答案缺失语义、Word 权威来源、音频版本绑定、自愈定位 |
| 2026-11-07 | [2026-11-07-review-diff-package-upload.md](2026-11-07-review-diff-package-upload.md) | 审核差异包与上传确认相关记录 |
| 2026-11-07 | [2026-11-07-answer-blank-fix.md](2026-11-07-answer-blank-fix.md) | 书面表达无答案被误判 ANSWER_INPUT_ERROR；空答案按无答案处理 |
| 2026-11-07 | [2026-11-07-perf-review-page.md](2026-11-07-perf-review-page.md) | 审核页/切题加载慢：只读缓存，总览 10.6s→0.87s，切题 2.0s→0.96s |
| 2026-11-07 | [2026-11-07-runtime-core-refactor.md](2026-11-07-runtime-core-refactor.md) | 保留规则与界面的底层兼容重构：共享状态、公平调度、子进程锁、自愈原故障复验 |
| 2026-11-07 | [2026-11-07-agents-and-changelog.md](2026-11-07-agents-and-changelog.md) | 建立根目录 AGENTS.md 与 CHANGELOG 制度（含制度自测 `workflow.test_changelog_policy`）|
| 2026-11-07 | [2026-11-07-subagent-roles.md](2026-11-07-subagent-roles.md) | 内置 7 个子 Agent 角色化提示词 + 按角色独立路由 |
| 2026-11-07 | [2026-11-07-never-park.md](2026-11-07-never-park.md) | 任何单点失败不得停住流程：上游重推/延后并继续 |
| 2026-11-07 | [2026-11-07-fix-stem-table-queue.md](2026-11-07-fix-stem-table-queue.md) | 修复装配改写题干、信息转换表格空档、排队区进度分母 |
