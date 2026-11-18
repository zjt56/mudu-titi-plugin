# Git 历史与架构审计（2026-11-17）

## 结论与审计边界

本仓库在 2026-11-04 至 2026-11-17 的短期高频演进中，从独立录题插件发展出跨平台模型路由、原生内容子智能体、PDF 整卷 OCR、两轮独立审核、来源身份绑定和受控运维恢复。当前的复杂度既包含有意保留的兼容、安全和用户数据边界，也存在已经失去运行用途的清单、失实的能力声明及配置处理缺口。不能仅按文件年龄、名字相似或新实现已出现就整块删除旧代码。

前一轮审计基准为 [`26c407e8ffabf23a3112cd002c9b07d7c44ac8c5`](https://github.com/zjt56/mudu-titi-plugin/commit/26c407e8ffabf23a3112cd002c9b07d7c44ac8c5)；本次复核推进到当前 `HEAD d21584dc0265fcf449692e132cd0e44e93102972`。审计阅读了全部可见提交的索引，并对主要子系统执行路径、关键提交补丁和当前调用关系作定向复核；**并非逐行审阅全部 353 个提交的每一份补丁**。本文区分历史 HEAD、当前工作区修复和未解决风险，不把尚未提交的修复写成已发布版本。

本轮历史调查未修改文件、分支、提交、stash、设备配置或用户批次；随后仅按父任务要求新增本文。工作区中其他修改属于父任务或原有工作，不归因于本审计。本文不执行 OCR、ASR、模型调用、上传、批次推进或安装。

## 1. 可见历史、分支与 PR 证据

| 检查 | 结果与限制 |
|---|---|
| 浅克隆 | `git rev-parse --is-shallow-repository` 为 `false`。 |
| 主历史数量 | `git rev-list --count master` 为 **353**，包含可达合并历史。 |
| 所有引用数量 | `git rev-list --all --count` 为 **356**；额外对象来自 Codex 检查点与 stash 链，不是额外三次产品交付。 |
| 起点 | [`e5f9cd8db6ab26463692577b7e5ad5870acded9b`](https://github.com/zjt56/mudu-titi-plugin/commit/e5f9cd8db6ab26463692577b7e5ad5870acded9b)，2026-11-04 11:33:28 +08:00。 |
| 前一轮基准 HEAD | `26c407e8ffabf23a3112cd002c9b07d7c44ac8c5`，用于对比本次 11-17 后续收敛。 |
| 当前 HEAD | `d21584dc0265fcf449692e132cd0e44e93102972`，2026-11-17 15:24:05 +08:00。 |
| 分支与标签 | 可见分支为 `master`、`origin/master`，没有 tag。审计开始时二者相同，ahead/behind 为 `0/0`。 |
| 远端新鲜度 | 当前工作树检查显示 `master` 与 `origin/master` 同为 `d21584d`，ahead/behind 为 `0/0`；本次未执行新的 `fetch`，因此只代表本地已知远端快照。 |
| 主要真实合并 | `fd2f915a801be4091dd74da43b52dcc8736884a0`（11-10，同步远端 master）；`08b2cc913954a5e4d3c30071e7a62d8db2457a09`（11-13，OCR/版面投影与本地课时绑定修复）。 |
| stash | `9fe9edce908e5c97b9121c2142506ec4f9f61fac` 是 stash 伪合并，不能算作 PR；`d138114bb20f02f4ac1f94fcc8f8191e51589718` 是其 untracked-files 根。现存 safety stash/autostash 未清理。 |
| PR 搜索 | GitHub API `repos/zjt56/mudu-titi-plugin/pulls?state=all` 返回空；提交消息也未查到 `Merge pull request` 等 PR 标记。 |
| PR/CI 限制 | 未取得 PR diff、review、批准或 PR CI 证据。部分早期访问受限；定稿时 pulls 页面可返回 HTTP 200，但工具只返回截断的站点导航，未提供 PR 条目，重复 GitHub PR 搜索仍为空。没有跟踪的 `.github/`，但这不证明没有外部 CI。不得写成“历史绝无 PR”或“已通过 PR 审核”。 |

历史显示主要采用直接提交推进；2026-11-09 的 `e4da4b4` 记录 master 单线协作要求。现有提交消息和变更记录可以证明作者记录的意图及当时声称的验证，不能替代当前测试结果。

前一轮审计记录的日期计数曾是 345 个可达对象；该数字随 11-17 后续提交和检查点变化，当前计数见第 2.1 节，不再把旧数字当成当前历史总量。

## 2. 主要演进时间线

日期均为 2026 年、+08:00；链接指向精确提交。部分提交覆盖多个子系统，标题不是其全部补丁的设计说明。

| 日期 | 提交 | 机制、当前意义 |
|---|---|---|
| 11-04 | [`eacfc6231b01c336389e6d3dc14fc2d0e7066206`](https://github.com/zjt56/mudu-titi-plugin/commit/eacfc6231b01c336389e6d3dc14fc2d0e7066206) | 独立项目化、试卷数据路径隔离、修正旧路径。 |
| 11-04 | [`157de78fa4d569ba64f66403debd4ecf708497e0`](https://github.com/zjt56/mudu-titi-plugin/commit/157de78fa4d569ba64f66403debd4ecf708497e0) | 可分发性改造：打包 `app/`、Python 自动探测、机器配置排除、数据外置及旧目录兼容。早期 PDF 降级设计后来受到强制 OCR 规则约束。 |
| 11-04 | [`1380752cb2249dde6e3bb7d667175c34a9263a70`](https://github.com/zjt56/mudu-titi-plugin/commit/1380752cb2249dde6e3bb7d667175c34a9263a70) | 盲审协议、审核分片、预绑定和断点恢复。 |
| 11-05 | [`fef6968ecf2eb1785537b9f56088e824857b0b42`](https://github.com/zjt56/mudu-titi-plugin/commit/fef6968ecf2eb1785537b9f56088e824857b0b42) | WhisperX 听力适配、解析/大意缺失规则；同一提交将三轮审核收敛为现行两轮。 |
| 11-05 | [`12fc49873c04ea458b3549c7b8c4cd9d740cfb56`](https://github.com/zjt56/mudu-titi-plugin/commit/12fc49873c04ea458b3549c7b8c4cd9d740cfb56) | 多语种听力、并行审核。 |
| 11-06 | [`25077c51a0f66baa167d10f2e3dec1e5123fb3bf`](https://github.com/zjt56/mudu-titi-plugin/commit/25077c51a0f66baa167d10f2e3dec1e5123fb3bf) | 批次隔离、OCR 与审核流程，宿主 OCR/题库设置也在此阶段形成。 |
| 11-06 | [`6a87c0e4729bf009d9d63c75be396f831a234ee9`](https://github.com/zjt56/mudu-titi-plugin/commit/6a87c0e4729bf009d9d63c75be396f831a234ee9) | 上传设置、可选输入、题库连接完善。 |
| 11-06 | [`1145db12bef202339c05ced20463ebe95390d849`](https://github.com/zjt56/mudu-titi-plugin/commit/1145db12bef202339c05ced20463ebe95390d849) | 试卷识别及并行流程重构。 |
| 11-08 | [`41e92f58943d8cec5fab9663eb237b6686077cc6`](https://github.com/zjt56/mudu-titi-plugin/commit/41e92f58943d8cec5fab9663eb237b6686077cc6) | 自治推进增强、宿主 release coordinator；旧 `UPSTREAM` 清单也始于此。 |
| 11-08 | [`85421d73b17da21dd2bcff175a6d94d1c5894cd4`](https://github.com/zjt56/mudu-titi-plugin/commit/85421d73b17da21dd2bcff175a6d94d1c5894cd4) | 用户手动上传不被过期/审核未完成阻碍；不能由此推导自动上传获授权。 |
| 11-08 | [`02cf0a6140a5d3f8fc6444d594e656993c05bf44`](https://github.com/zjt56/mudu-titi-plugin/commit/02cf0a6140a5d3f8fc6444d594e656993c05bf44) | 格式小问题降为提示，内容实质错误保持阻断。 |
| 11-08 | [`a911893a381afa5b767ec6aeb320453b7000654b`](https://github.com/zjt56/mudu-titi-plugin/commit/a911893a381afa5b767ec6aeb320453b7000654b) | 听力并行，只有内容审核显式等 audio。 |
| 11-09 | [`5fbf5c615d950acb9d30e4eab2a73754baae4914`](https://github.com/zjt56/mudu-titi-plugin/commit/5fbf5c615d950acb9d30e4eab2a73754baae4914) | 附加式 Harness 运维接手，原录题/自愈功能继续。 |
| 11-09 | [`0e863c249be5ae3b29c234f3029bffefc1ab0699`](https://github.com/zjt56/mudu-titi-plugin/commit/0e863c249be5ae3b29c234f3029bffefc1ab0699) | Windows 原生 Node router；保留 POSIX shell/Python 路由链。 |
| 11-09 | [`2657460db83800bbb1de323751befa2c7acce0af`](https://github.com/zjt56/mudu-titi-plugin/commit/2657460db83800bbb1de323751befa2c7acce0af) | 题型判断真正并入 preassemble，旧 types 仅兼容交接/局部修正。 |
| 11-09 | [`10294b712dba6cdc6e8b628c6120203bc0f86cdb`](https://github.com/zjt56/mudu-titi-plugin/commit/10294b712dba6cdc6e8b628c6120203bc0f86cdb) | 仓库 default 清空、设备跟随 Harness 当前模型，损坏显式 default 不静默替换。 |
| 11-09 | [`b48cab489f33b1fafb3dd5caa739e4a57aa2b4e5`](https://github.com/zjt56/mudu-titi-plugin/commit/b48cab489f33b1fafb3dd5caa739e4a57aa2b4e5) | 运维恢复事务与内容局部再生契约。 |
| 11-10 | [`d3051eccee23d6e56eb74a6aa178c74d9266c8a0`](https://github.com/zjt56/mudu-titi-plugin/commit/d3051eccee23d6e56eb74a6aa178c74d9266c8a0) | 模型选择存设备本地，解决 checkout 覆盖选择后落到其他 provider/额度路径的问题。 |
| 11-10 | [`18775acd4c301c461222423f946c91f711f85eca`](https://github.com/zjt56/mudu-titi-plugin/commit/18775acd4c301c461222423f946c91f711f85eca) | 审核差异从诊断/运维卡点中剥离。 |
| 11-11 | [`f18cbe5c07e786c339877da794b3023b0621903f`](https://github.com/zjt56/mudu-titi-plugin/commit/f18cbe5c07e786c339877da794b3023b0621903f) | 持久自治授权、验证后的恢复续跑。 |
| 11-11 | [`c03fdff0e9a2909f9ef4b26cb57acc8c31bf2e62`](https://github.com/zjt56/mudu-titi-plugin/commit/c03fdff0e9a2909f9ef4b26cb57acc8c31bf2e62) | 暂停本地自动教材匹配；目录和远端 `textbookMappings` 契约保留。 |
| 11-11 | [`1e57d17419fdd587bac19e6f73546063c6fabb97`](https://github.com/zjt56/mudu-titi-plugin/commit/1e57d17419fdd587bac19e6f73546063c6fabb97) | 来源占位技术清理不触发重审；旧未裁决差异回活动队列。 |
| 11-11 | [`eb34ccf193e3dbe45e0fd17024672b802ff2818d`](https://github.com/zjt56/mudu-titi-plugin/commit/eb34ccf193e3dbe45e0fd17024672b802ff2818d) | 列表冷投影性能优化：绑定哈希去重、进程外快照、摘录缓存；不能把展示快照当正式验收收据。 |
| 11-11/12 | [`498f19e246cee219ab26681ec7987057d8e31ca9`](https://github.com/zjt56/mudu-titi-plugin/commit/498f19e246cee219ab26681ec7987057d8e31ca9)、[`3cc97413204720721f46ecdc76c66903baec33b4`](https://github.com/zjt56/mudu-titi-plugin/commit/3cc97413204720721f46ecdc76c66903baec33b4) | 宿主视觉旁证；Word 嵌入图停止逐图本地 OCR，改视觉复核。提交所称“4分钟→0.08秒”是历史测量，本审计未复测。 |
| 11-12 | [`6f6f3c2ea54bd8c2c77eb9b8960edc046281ec28`](https://github.com/zjt56/mudu-titi-plugin/commit/6f6f3c2ea54bd8c2c77eb9b8960edc046281ec28) | 原卷显式题号范围、受控存量迁移和审核语义绑定。 |
| 11-12 | [`5259d35eb9f453f9b8453345becac62ee50b4209`](https://github.com/zjt56/mudu-titi-plugin/commit/5259d35eb9f453f9b8453345becac62ee50b4209) | 后端退出看门狗；表格单元格图片能力缺口改非阻断 warning，结构/归属错误仍阻断。 |
| 11-12 | [`f312f3d359aafe2d217cf1a6bc883ccda63f6d9c`](https://github.com/zjt56/mudu-titi-plugin/commit/f312f3d359aafe2d217cf1a6bc883ccda63f6d9c) | 原生内容 Agent 与多项工作区改动的混合提交（116 文件、5990 插入/324 删除）；同时意外把仓库模型 default 写回固定值。 |
| 11-12 | [`3c04ed7ff01b0c059c08d6a8b6e129f4941b14b5`](https://github.com/zjt56/mudu-titi-plugin/commit/3c04ed7ff01b0c059c08d6a8b6e129f4941b14b5) | 已删除误提交的九个 `.tmp-*` 排查脚本，不能再列为当前待删除文件。 |
| 11-13 | [`ec328986dda33e1f266281ee16501ba8652e2a42`](https://github.com/zjt56/mudu-titi-plugin/commit/ec328986dda33e1f266281ee16501ba8652e2a42) | 仓库卫生回归、文末空行与临时路径断言修正。 |
| 11-13 | [`19a22fc0e563dd413d3a352029cc8365cd2f1115`](https://github.com/zjt56/mudu-titi-plugin/commit/19a22fc0e563dd413d3a352029cc8365cd2f1115) | 整页栏目归属、无印刷号实际题的来源证明链。 |
| 11-13 | [`46ba946ed47fae39f4f07345618660d33e4a7e5f`](https://github.com/zjt56/mudu-titi-plugin/commit/46ba946ed47fae39f4f07345618660d33e4a7e5f)、[`6bb9a420dd062e5c03263a56100768d5326f1453`](https://github.com/zjt56/mudu-titi-plugin/commit/6bb9a420dd062e5c03263a56100768d5326f1453)、[`47ec3270341a7e2214a9f5fc7a517764f89abbcd`](https://github.com/zjt56/mudu-titi-plugin/commit/47ec3270341a7e2214a9f5fc7a517764f89abbcd) | 宿主服务注入声明、运维自动接收与重新认领，说明宿主协议胶水仍在主动修复。 |
| 11-13 | [`b19d1e3864fe0a671f59bac7dc9a7601a0a9fad5`](https://github.com/zjt56/mudu-titi-plugin/commit/b19d1e3864fe0a671f59bac7dc9a7601a0a9fad5)、[`a7836bd28b5d463f499cf2fc58a4c8e9a4c609fd`](https://github.com/zjt56/mudu-titi-plugin/commit/a7836bd28b5d463f499cf2fc58a4c8e9a4c609fd) | 多附件先判角色、同角色 Word 优先；每份 PDF 整卷服务 OCR，任一失败不得标 extract 完成。 |
| 11-13 | [`9701316c6aedd70fedc07c527b8bf9332cb6160d`](https://github.com/zjt56/mudu-titi-plugin/commit/9701316c6aedd70fedc07c527b8bf9332cb6160d) | 审核单元缓存改内容摘要，修 NTFS 惰性 stat 复用旧内容。 |
| 11-14 | [`c432de94decf60e75be84464a19a0364079b6ce3`](https://github.com/zjt56/mudu-titi-plugin/commit/c432de94decf60e75be84464a19a0364079b6ce3)、[`5cb36fef9d789badb40b0960d613d1302ea2df0d`](https://github.com/zjt56/mudu-titi-plugin/commit/5cb36fef9d789badb40b0960d613d1302ea2df0d) | OCR POST 前持久回执、未知提交对账、续查与复用；artifacts key 固定 POSIX 修 Windows 回执。 |
| 11-16/17 | [`ff7d6b8ba5239c4bf109ad5b405f9c3a1a4cd857`](https://github.com/zjt56/mudu-titi-plugin/commit/ff7d6b8ba5239c4bf109ad5b405f9c3a1a4cd857)、[`26cd468657a3e50a08a2cbd8e5578541f4508238`](https://github.com/zjt56/mudu-titi-plugin/commit/26cd468657a3e50a08a2cbd8e5578541f4508238)、[`26c407e8ffabf23a3112cd002c9b07d7c44ac8c5`](https://github.com/zjt56/mudu-titi-plugin/commit/26c407e8ffabf23a3112cd002c9b07d7c44ac8c5) | 课时答案区、表格图片小题号、身份刷新、选项窗口及统一边界审计继续收敛，不能宣称来源归属已彻底稳定。 |

| 11-17 | [`32aa4e3`](https://github.com/zjt56/mudu-titi-plugin/commit/32aa4e3)、[`96f9cc1`](https://github.com/zjt56/mudu-titi-plugin/commit/96f9cc1)、[`ab559fa`](https://github.com/zjt56/mudu-titi-plugin/commit/ab559fa)、[`18d0be9`](https://github.com/zjt56/mudu-titi-plugin/commit/18d0be9)、[`44099a6`](https://github.com/zjt56/mudu-titi-plugin/commit/44099a6) | 视觉理解链路停用；结构闸门只生成结构候选；内容完整性和来源一致性后移最终审计；听力待复核不阻塞文字分支；无印刷题号的书面表达按来源证据保留。旧视觉桥接文件未同步清理，见下文。 |

### 2.1 前一轮审计之后的架构收敛

`32aa4e3` 删除了 `visual_columns.py`、`source_pages.py`、`visual_review.py`、`visual_crosscheck.py`、`visual_transport.py` 及其测试；`lesson_bands.py` 被压缩为纯印刷文本边界识别，`content_media.py` 和 `content_structure.py` 只保留附件字节/哈希绑定。这个变更是明确的产品政策，而不是临时关闭开关。它也留下了一个可定位的尾巴：`backend/wf-vision.mjs`、`backend/wf-vision.md`、`backend/wf-vision.test.cjs` 仍在仓库，但当前生产代码没有调用它们，说明文件还引用已经删除的 `workflow.visual_transport`。这三份文件是本审计中最强的安全清理候选；删除前只需确认没有外部脚本把 `node backend/wf-vision.mjs` 当公开入口。

`96f9cc1`、`ab559fa` 和 `18d0be9` 把流程的职责重新分开：结构阶段产出候选结构，前期允许独立内容分支继续，来源/内容完整性在最终审计集中判断，只有 review 等待听力。它们没有取消最终来源、身份、题号、标签或上传 schema 检查。任何把这些提交解释成“整体放宽校验”的重构都会重新引入早期 A1/A3/A4 类问题。

当前仍有两类文档残留。`AGENTS.md` 的旧视觉段落仍列出已删除的视觉模块，并与后面的“视觉理解已停用”规则矛盾；`app/workflow/pipelines/mudu-flow-review.workflow.js` 及 `independent_review.py` 的标题和注释仍使用“三审/three-level”措辞，而正式 `review.ROUND_IDS` 只有 `labels`、`sample` 两轮。前者应优先修正文案；后者不能直接删，因为 `MUDU_REVIEW_CONFIG` 显式启用时 `runner.flowchart_checks()` 仍会调用独立验签。

本次复核的日期计数为：11-04：42；11-05：3；11-06：8；11-07：1；11-08：32；11-09：50；11-10：59；11-11：53；11-12：42；11-13：46；11-14：4；11-16：2；11-17：14。`git log --all` 的 356 个对象包含 Codex 检查点和 stash，产品主线以 353 个 `master` 可达提交为准。

## 3. 有意设计、兼容、遗留与废弃矩阵

置信度说明：高＝有直接差异/代码调用证据；中＝机制明确但外部使用或运行后果未核实；低＝不足以形成清理决策。下表的“可移除”不等于本次获得删除授权。

| 对象 | 归类与置信度 | 证据 | 安全处置条件/禁忌 |
|---|---|---|---|
| 仓库固定 `default` | 意外复活的回归，高 | `10294b7…`、`d3051ec…` 明确相反意图；`f312f3d…` 重新固定；首次启动迁移读取模板。 | 本轮父任务已在工作区恢复 `{}`；保留设备本地显式选择。不能靠删用户本地配置修复。 |
| `self_heal.UPSTREAM` | 运行未用的旧清单，高 | 始于 `41e92f5…`；基准 HEAD 仅定义、两条测试读取及历史 RULES 描述，无运行消费者；注释说明运行依结构化缺陷归属。 | 本轮父任务已移除清单；保持结构化 producer/evidence 及边界测试，不恢复“按失败阶段固定倒推”的路由。 |
| `backend/wf-vision.mjs`、`wf-vision.md`、`wf-vision.test.cjs` | 视觉退役后的孤立桥接，高 | `32aa4e3…` 删除了 Python 视觉传输和所有生产调用；当前仅三份桥接文件互相引用，说明仍导入已删除的 `workflow.visual_transport`；`git grep` 未发现 runner、agent、插件入口调用。 | 可在确认没有外部脚本调用该 Node 入口后连同测试和说明删除；不需要删除历史 CHANGELOG，也不需要恢复视觉能力。 |
| `.mjs` 与 shell/Python 路由链 | 当前默认 + 有意兼容，高 | `0e863c2…` 新增原生入口但未移除旧入口；shell 调 `route_pick.py` 和 provider merger；local_agent/structure_ai 同时识别两种入口。 | 只有明确宣布 POSIX/旧配置退役、迁移公开入口和测试后才可删；不能因 Windows 使用 `.mjs` 就判 shell 死代码。 |
| types 独立入口 | 无模型交接 + 旧任务/局部修正兼容，高 | `2657460…` 及根规则 20；新任务判型在 preassemble。 | 可阻止新批次重复模型调用；删除旧标识须有批次/任务迁移策略。 |
| 文件/CLI 内容任务协议 | 原生宿主之外的通用协议，高 | 原生内容文档保留 auto/strict；宿主不在线时在选择模式时使用旧协议，原生失败不再偷偷跑 CLI。 | 不把两种执行方式当重复实现；通用接口还服务其他 Agent/离线环境。 |
| `release-coordinator.js` / `code-restore.js` / watchdog | 安全安装与生命周期职责分层，高 | `41e92f5…`、`d4e9b52…`、`5259d35…`；宿主仍导入、启动和停止它们。 | 先证明暂停、重启、健康探测、回滚和失败终止由新实现全覆盖；否则不能删。 |
| 原生 PDF text reader | OCR 旁证兼容，高 | `ocr_backend.read_pdf` 即使云 OCR 配置也用本机 native text 取证；强制 OCR 在另一路拒绝 native 假完成。 | 保留旁证用途；不能恢复“文字层足够即代替 OCR”。 |
| OCR 文件锁/异步回执 | 资源与提交安全边界，高 | OS `msvcrt/flock`；`c432de9…`、`5cb36fe…`。 | 不按 lock 文件年龄或 PID 猜测删锁；未知 POST 结果只能对账；回执不是随手删的缓存。 |
| 运维 autonomy/旧显式安装 | 有意授权兼容，高 | `f18cbe5…`；源码根绑定、默认关闭；旧 `confirm=True + MD_OPERATIONS_INSTALL=1` 保留。 | 不删除保护名单、CAS、备份、journal、复验与重启认领；不能借重构开放运行中自改。 |
| 教材目录/远端 schema | 暂停自动匹配，未废弃，高 | `c03fdff…`；上传仍校验 `textbookMappings`。 | 当前不生成本地 textbook；恢复自动匹配须用户另行要求，不能把远端契约一并删掉。 |
| `config/prompts/*`、policy API | 疑似未接通的遗留能力，高“未接通”/中“可删” | 宿主存取存在，前端和 app 工作流未找到消费者；历史查询未找到接线。 | 先核外部 API 使用和用户定制副本，再选择接通或退役；绝不能误删实际 `app/prompts/roles/`。 |
| 三轮审核文档 | 基准 HEAD 过时、当前已定向修正，高 | `fef6968…` 改两轮；当前 `ROUND_IDS=(labels,sample)`；父任务工作区已把子 AGENTS 两处三轮说明改为两轮。 | 保持两轮真实独立验收，不因此增加第三轮或放宽实际审核。 |
| `.tmp-*` 调试脚本 | 历史污染，当前已删除，高 | `3c04ed7…` 明确删除九个脚本；`ec32898…` 补卫生测试。 | 不把历史已删文件重复列作本次成果；不需重写 Git 历史。 |
| `.batch-backup` | 用户数据，高 | `4c08822…` 加入 111 个跟踪文件，含原卷、媒体、候选、manifest、运维和日志。 | 保留。迁出仓库、从当前树移除或清历史均须用户明确授权、分类和可验证备份。 |
| flow SVG/截图 | 文档/设计证据，高 | SVG 被 11-10 changelog 与 RULES 引用；多次前端改动保存截图。 | 可整理，但先同步文档链接；没有运行用途不代表没有审计价值。 |
| 路由生成 profiles / content-agent 会话 / 运维 journal / stash | 活动任务或恢复状态，中至高 | 路由按 provider/model 摘要生成 profile；会话删除依宿主安全清理；journal 有回滚职责。 | GC 前扫描当前引用、会话、队列和恢复状态；不在代码清理时自动删用户状态。 |

## 4. 全库子系统现状与重要边界

### 4.1 插件、后端与数据生命周期

[宿主入口](../../lib/index.js) 的数据根选择为：显式 `WF_DATA_DIR` → 已存在的插件内 `workflows` 兼容目录 → DSH 状态目录下 `wf-plugin-data`。该兼容分支承接早期独立项目的数据，不是无效默认值。

后端健康检查结合子进程存活和 instance ID，不仅凭任何进程返回 health 200 就认领；POSIX 孤儿清理核工作目录/命令，Windows 有独立进程树处理。watchdog 负责意外退出后重新确保后端，release coordinator 负责安全安装后的暂停、重启和回滚，两者不能互相替代。

[发布协调](../../lib/release-coordinator.js) 对 prepared 状态有有界暂停，对已回滚清状态，对 failed 不无限循环；新代码健康失败后最多回滚并重启旧代码一次。把这些路径简化为“不断 ensureBackend”会失去故障终止和回滚证据。

### 4.2 包装、配置与题库连接

[包清单](../../package.json) 包含 `lib/`、`app/`、`backend/`、`config/`，排除 Python cache、临时文件和机器 OCR/local-agent/bridge/upload 配置；历史 `157de78…` 的“200 文件/2.6MB”是当时打包声明，本审计没有重新执行 npm pack。`version=0.1.0` 本身不是删除或升级依据。

[题库设置](../../lib/question-bank.js) 把 key/password 与设置分开保存在仓库外的状态目录，限制 URL、禁止非 loopback 明文 HTTP、拒绝凭据路径经 symlink 回到仓库，并使用原子写入。这里的验证、权限和分离存储属于主动安全设计，不应为了减少代码而内联到仓库 JSON。

当前包描述仍说模型经 `wf-router.sh` 注入，未完整描述原生 `.mjs`；还宣称“自定义提示词/审核口径”，而实际工作流未找到消费接线，见风险 R2。

### 4.3 模型路由与原生内容 Agent

默认不固定模型与显式选择严格执行，是独立于“能否调用成功”的产品约束。设备路由路径迁移保留了旧配置；模板回归会被首次启动逻辑复制到设备本地，因此不是一份无害示例。

[原生内容子智能体说明](harness-content-agents.md) 覆盖结构确认、preassemble、答案、解析和知识点；直抄优先，审核与听力没有被此入口取代。auto 模式只在选择时判断活跃宿主，strict 必须原生，原生失败不可再无声换 CLI。任务绑定、复验和删除会话的安全清理是其必要部分。

### 4.4 提取、OCR、视觉与听力

现行策略先按实际内容判材料角色，再在同角色正文中应用 Word 优先。独立答案 Word 不能压过正文 PDF。每份 PDF 都必须服务 OCR，等待属于未完成，不是成功；原生文字/图片仅旁证。

[OCR 后端](../workflow/ocr_backend.py) 只在默认配置缺失时提供 native reader；显式配置损坏应报错。mandatory PDF 路径绕过 OCR assist 总开关并拒绝 native fallback 假通过。OS 锁在进程死亡后由系统释放，磁盘上存在 lock 文件不等于锁仍被占用。

[提取说明](extraction.md) 规定回执在 POST 前落盘、未知结果用 batchId 对账、查询/下载有界重试、预算耗尽按 5–300 秒等待续查；回执不存令牌或签名 URL，保留服务阅读顺序和脱敏原始响应，不自行硬编码重排。

Word 嵌入图逐图 OCR 在 `3cc9741…` 被视觉复核替代；这不废弃 PDF OCR 或手动图片识别工具。表格单元格媒体能力缺口自 `5259d35…` 起为 warning，但嵌套表格、绑定失败、投影不一致仍为实质错误。[source_objects](../workflow/source_objects.py) 的 `NON_BLOCKING_CAPABILITY_CODES` 只列 `LAYOUT_CELL_MEDIA_UNSUPPORTED`，不能扩散为普遍放宽媒体/结构。

[听力配置](../../config/audio.json) 默认关闭、小模型/CPU/int8/并发 1，是可选能力的保守默认值；只有审核依赖听力，组装及其他独立内容阶段并不等待。近期提交较少不足以证明 ASR 已废弃。本审计未安装或运行 WhisperX。

### 4.5 审核、来源身份与上传

[审核实现](../workflow/review.py) 使用两轮协议 `upload-workflow.review-rounds/v3`，一轮全量盲判、一轮抽样。差异是审核产出，不是自动纠错对象；执行完成不等于质量通过，不能清掉差异来给流程“放行”。`1e57d17…` 专门防止技术性绑定变化吞掉未裁决差异。

按来源坐标/scoped identity 绑定，服务于课时重号、材料分段、无号题和完整答案归属。`types` 兼容标识、旧批次协议和远端 `textbookMappings` 有当前职责；局部清理不能改写历史批次身份。

`85421d7…` 的手动上传容忍审核未完成/内容变化，并不授权自动上传。`6f6f3c2…` 将显式原卷题号作为上传投影契约，不能再用 `location` 或 `paperOrder` 猜范围。上传冻结、媒体和硬 schema 检查的安全边界继续保留。

### 4.6 运维与部署

[运维说明](harness-operations.md) 定义附加式首个确定性阻断接手，健康推进、审核差异和限流等待不建无效修复单；未知受控请求先核回执，不能盲重发。

[开发运维](../workflow/operations_development.py) 的保护名单锁住 operations/contracts/manifest/supervisor/auto/runner/local_agent/self_heal 等控制面；生产代码、测试、脚本有明确白名单，机密/用户数据排除。源码根绑定 `.operations-autonomy.json` 缺失默认关闭，损坏或属于别的源码根应拒绝。

隔离复制包含当前有效的未提交代码，固定测试剥离秘密，变更测试必须实际执行；安装计划绑定源码哈希，备份、CAS 替换、失败回滚，重启后重新认领并复验原失败检查。`installed_pending_restart` 不得当成修复成功。预算耗尽也不能清空预算继续原样循环。这些限制不是临时样板代码。

## 5. 本轮已处理与后移的历史问题

以下状态由本审计定稿前的工作区差异直接核对；基准 HEAD 尚含旧内容，修复提交/当前测试最终结果由父任务记录。

### F1. 仓库 default 意外复活：已恢复空模板

意图链为 `10294b7…` 清空 → `d3051ec…` 迁到设备本地 → `f312f3d…` 写回 `deepseek-official/deepseek-flash/profile wf-deepseek-fast`。后一个混合提交没有取代硬规则 8 的明确授权证据，实际也会被 `ensureRouterFile` 迁入新设备路由，应定性为回归，而非新设计。

父任务已把 [仓库模板](../../config/model-router.json) 的 `default` 恢复 `{}`，并负责模板回归验证。修复不删除或覆盖设备已有显式选择，也没有尝试将所有已迁入设备的值强制清空。

### F2. `self_heal.UPSTREAM` 旧映射：已移除运行未用清单

基准 HEAD 中该映射的唯一代码读取是 `test_self_heal_gaps` 两条测试断言；运行恢复通过结构化缺陷 producer/evidence 找归属，映射自身注释也承认不参与选择。历史 RULES 的名称引用是变更记录，不是运行消费者。

父任务已移除该清单。应验证的是实际结构化归属、局部影响范围和不能越过上传/审核边界，而非继续要求一份与真实依赖分叉的字典存在。这属于有调用证据的窄删除，与删除整个自愈机制不同。

### F3. 前期检查职责收窄：保留内容硬检查，将最终要求后移

当前工作区的 [检查表](../workflow/checks.py) 已将 `audit_type_layout` 前期调用限定为 `--content-only`，结构与选项完整性继续阻断；完整重复显示观察通过独立 `audit_type_layout_full` 后移最终格式阶段且非阻断。`check_difficulty` 同样后移，但保留 blocking，不能把这项变更描述为放宽难度硬约束。[最终闸门](../workflow/format_gate.py) 把这些硬检查失败汇入 `errors/passed`；聚合检查异常时仍须执行难度检查或明确失败。

[消费者交接](../workflow/handoff.py) 对 meta/underlines 改用来源投影、身份/题号覆盖、子题形状与受管理来源绑定校验，不再要求尚未交付的答案/解析/标签。完整 `_batch` 仍检查解释投影、wire schema、标签与原文保真，并用于最终组装等完整批次交付；最终上传闸门仍执行硬 schema/identity/numbering 校验。这是按真实生产者时机分工，不是删除来源、身份和最终正确性要求。

检查结果新增完整 `stdout/stderr/findings`；超时、执行异常、缺输入和非零退出保留结构化证据，不能只剩截断尾句。新增 `content_snapshot` 来源快照让 meta/underlines 与答案、解析、知识点分支并行，最终 assemble 消费画线视图，validate 显式依赖 assemble；audio 仍不加入 assemble 依赖。上述为直接代码差异确认，测试和真实批次效果由父任务最终记录。

## 6. 未解决风险与优先级

### R1 · P1：显式路由损坏仍存在静默回退路径

置信度：**高，代码路径已核；未作真实模型/计费事故复现**。

- [原生 router](../../backend/wf-router.mjs) 的 `choose` 对 default 严格，但 `roles/stages/review` 不完整显式对象经 `!valid(entry)` 会退到低优先级/default；[Python selector](../../backend/route_pick.py) 有同型行为。
- [宿主入口](../../lib/index.js) 的 `readRouter()` 调 `readJson()`，现存本地 JSON 解析失败会变成 empty router；随后 `operationsRouteFor()` 可使用 Harness 默认模型。
- 注释和硬规则要求“显式配置损坏应报错，不能静默换模型”。当前机制可能违反此约束。

建议：区分“缺省/明确空选择”和“存在但不完整/损坏”；对每层显式 override 和本地 JSON 读错分别报可定位错误；增加 default/roles/stages/review/operations 的负例。不要借修复迁移或覆盖设备选择。本审计未修改路由。

### R2 · P2：自定义提示词/审核口径接口与实际工作流脱节

置信度：**高“仓库内未接通”，中“可直接删除”**。

[宿主入口](../../lib/index.js) 保留 `PROMPT_FILES`、`readPrompt/writePrompt`、`GET/POST /prompt` 和 `POST /policy`，配置目录含四份 prompt 及 `review-policy.json`。但 JS/PY/SH 全库搜索未找到 app 工作流消费这些自定义内容；[当前前端](../../lib/client.js) 没有 `/prompt`、`/policy`、policy/promptFiles 的消费。

历史 `git log -S '/prompt' -- lib/client.js lib/index.js` 仅指向初始提交；`human_review_scope` 在 app/lib/backend 的历史查询未找到消费者。实际任务使用 `app/prompts/roles/`、content rules 和代码契约。

建议先修“支持自定义提示词/审核口径”的能力声明，或明确完成接线；若退役接口，先核外部调用者及用户在仓库配置留下的自定义内容，保留副本/迁移说明。不要误删角色规范，不把“仅存取成功”当成“任务已采用设置”。本审计未改 API 或包描述。

### R3 · P2：子 AGENTS 与提取说明存在历史残留

置信度：**高**。

- [根 AGENTS](../../AGENTS.md) 的旧视觉板块仍保留“用 Harness 模型看图”和 `visual_transport.py` 的落地说明，紧接着的规则 34 又声明视觉理解已停用；两条规则互相矛盾，不能把后者当成前者已被删除的证据。
- 基准 HEAD 的 [子 AGENTS](../AGENTS.md) 仍要求一审/二审/三审，而两轮规则已于 11-05 生效，当前 `ROUND_IDS` 只有 labels/sample；父任务工作区已定向修正两处说明，但根规则中的旧视觉段落仍未修。
- [提取说明](extraction.md) 仍有“仅本地/不联网”句子，与当前云 jobs 冲突；稀疏 Word 图片 OCR 的旧说明也需与纯 Word 视觉策略统一。
- [包描述](../../package.json) 已移除“自定义提示词/审核口径已接通”的能力声明；`/prompt`、`/policy` 兼容 API 仍保留，是否继续公开由后续外部调用核查决定。

建议定向修正文案及验证入口；不要据此新增第三轮审核、恢复 Word 逐图 OCR 或移除仍支持的 shell。

### R4 · P2：通用审核 JSON 缓存可能仍有同类 stat 风险

置信度：**中，未复现当前错误**。

`9701316…` 为 `_units_revision` 加结构文件内容 SHA，修复同尺寸写入/NTFS 惰性时间戳问题；但 [review](../workflow/review.py) 的通用 `_load` 仍以进程级 `(str(path), st_mtime_ns, st_size)` 缓存 JSON。相同机制可能影响其他被长期进程读取的 JSON。

建议先用具体消费链构造受控同尺寸同时间戳改写用例，再决定内容摘要或 evaluation-scope cache；不能把风险推测写成已复现的 stale review 事故，也不要不加区分删除全部性能缓存。

### R5 · P2：根测试命令漏掉 `.test.mjs`

置信度：**高**。

根说明的 `node --test backend/*.test.cjs` 不会运行 [OCR routes 测试](../../backend/ocr-routes.test.mjs)、[题库 routes 测试](../../backend/question-bank-routes.test.mjs)、[题库设置测试](../../backend/question-bank.test.mjs)。没有跟踪的 CI 配置能证明另一路自动补跑。

建议补统一入口/修改说明，同时覆盖 `.cjs` 和 `.mjs`；保留跨平台通配方式的可执行性。不能删除漏跑的测试以制造“完整通过”。

### R6 · P3：分发树与仓库数据/证据整理应分开

置信度：**高，处置需用户授权与完整引用检查**。

`.batch-backup` 的 111 个跟踪文件来自 `4c088228f741dcbb54b774ff5083c891c3a3928d`，含原卷和完整批次证据；它不是构建缓存。flow SVG 有 changelog/RULES 引用。生成 profiles、content-agent 会话和运维 journal 有恢复/活动引用。

可以单独审查包发布文件列表、证据归档布局和未来 GC 规则，但本次没有证明需要删除上述内容；尤其不能把清理 Git 树等同于永久删除用户数据或重写 Git 历史。

### R7 · P2：问题台账导出与结案不能作为验收权威

流程审计发现 `workflow.issue_log.sync` 重复同步没有导出检查点，可能再次累加 occurrences；`workflow.issue_log.fix` 可接受空验证文本，不能仅以 fixed 状态证明修复。此轮未修改该模块；后续应以原失败检查的真实复验回执作为结案证据，并验证重复同步的幂等性。

### R8 · P2：视觉桥接文件已脱离当前架构

置信度：**高，生产调用关系已核；外部脚本调用未知**。

`backend/wf-vision.mjs`、`backend/wf-vision.md` 和 `backend/wf-vision.test.cjs` 是 11-12 视觉方案留下的 Node 桥。`32aa4e3` 已删除 Python 视觉传输和生产侧视觉理解，当前 `git grep` 只找到这三份文件相互引用，说明还导入不存在的 `workflow.visual_transport`。它们不在 `package.json` 导出、`lib/index.js` 宿主入口、`runner.py` 或内容 Agent 的调用链中。

建议先在用户脚本、安装文档和宿主配置中搜索 `node backend/wf-vision.mjs`；若无外部调用，可连同测试和说明删除，并保留历史 CHANGELOG 作为演变记录。不要删除历史批次中的 `image_semantics.json`、`visual-*` 证据文件，也不要把删除桥接文件扩展为恢复或放宽图片/结构校验。

## 7. 后续验收建议与未验证事项

优先完成当前窄修复、检查时机调整的回归和父任务已有工作，再按 R1、R2、R3 的剩余文案/R5、R4、R6 分项处理。每项应保留对应契约和用户数据边界，避免一次“清旧代码”再次混入跨子系统行为变化。

本审计没有运行全量或受影响运行测试，也没有重新打包、安装、调用模型/识别服务或推进真实试卷。历史提交/CHANGELOG 里的通过数字和性能数字均仅为历史证据，不是当前验证结果。当前修复的测试数量、提交号、远端状态和最终交付由父任务在完成后记录；本文不预先声称通过。

最安全的删除证据是“无生产消费者 + 当前替代路径已验证 + 对外/旧批次迁移边界清楚”；文件老、存在新入口、测试只测存在、或某次任务没用到，都不足以支持广泛删除。
