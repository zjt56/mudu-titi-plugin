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
| 2026-11-08 | [2026-11-08-explanation-binding-fix.md](2026-11-08-explanation-binding-fix.md) | （中间方案，已被 direct-copy 取代）放宽解析严格来源校验的历史留痕 |
| 2026-11-08 | [2026-11-08-format-hints-not-blocking.md](2026-11-08-format-hints-not-blocking.md) | 格式小问题（下划线/空档/表格写法）只提示不阻断；题号题干对不上等实质错误仍阻断 |
| 2026-11-08 | [2026-11-08-review-difference-kept.md](2026-11-08-review-difference-kept.md) | 更正：一审/二审与原标签不一致必须待审核；移除把真实差异当“过期结论”的过滤 |
| 2026-11-08 | [2026-11-08-preview-keeps-group-material.md](2026-11-08-preview-keeps-group-material.md) | 预览切片不再丢题号前的表格/材料（2024 卷首篇阅读只剩选项的根因） |
| 2026-11-08 | [2026-11-08-explanation-reference-authority.md](2026-11-08-explanation-reference-authority.md) | 解析来源唯一性：PDF/OCR 副本不再与 Word 权威来源竞争；闸门诊断指明具体检查项 |
| 2026-11-08 | [2026-11-08-normalized-stage-fingerprints.md](2026-11-08-normalized-stage-fingerprints.md) | 阶段复用用规范化输出签名，消除 JSON 序列化噪声导致的重跑；精确绑定与真实内容验收保留 |
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
