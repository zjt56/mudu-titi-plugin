# 原卷题号上传适配设计

日期：2026-11-12。状态：已实施并通过本机 18 个批次迁移；远端无号题组子题契约仍未核实（见第 4 节）。

## 1. 依据与目标

本次读取开发者文档中心 `http://101.43.1.57/docs/` 及纯文本 `http://101.43.1.57/developer.md`，两者均包含新增的题号规则。页面仍标文档版本 2026-10-23 / API 2026-09-29，因此以本次实际读取正文及日期为核对依据，不把页脚日期当成新增规则发布时间。

规范要求：

- `items` 数组保持原卷题目单元顺序，不按 ID、题号或更新时间重排。
- 独立题顶层 `numberStart/numberEnd` 均为该题原卷数字题号。
- 题组保留 `subquestions[].number` 原印刷号，顶层填写完整连续范围。
- 没有印刷数字题号的单元显式填写 `numberStart=null, numberEnd=null`。
- 本地 `number/numbers` 的题号语义必须映射到上传载荷，不能被白名单删除。无需因此把所有本地记账字段原样发送。
- `location` 仅作来源追溯；`paperOrder` 是单元排序位置，均不能代替结构化题号。
- 正式试卷档案使用独立完整 manifest 的 `order/numberStart/numberEnd`；题号不写进 `questions` 表。

目标是修正本插件的上传生成和同源检查。暂存确认、正式试卷档案保存以及展示属于远端责任；本设计不声称远端接口已经联调通过。

## 2. 现状及已复现问题

| 位置 | 实际行为 | 设计结论 |
|---|---|---|
| `app/workflow/upload_payload.py:32-42` | WIRE_FIELDS 没有范围字段，numberStart/numberEnd 被归为 LOCAL_FIELDS | 显式范围也会静默丢失 |
| `upload_payload.py:635-645` | 非 scoped、非作文独立题在 location 为空时补“第N题” | 不满足新规范；以题型排除作文不可靠 |
| `upload_payload.py:656-660` | 顶层 number 被剥离，子题 number 保留且要求正整数 | 独立题原号丢失；无号组的内部别名存在误发风险 |
| `upload_payload.py:828-838` | 验证组级与子题号集合相等，不验证范围连续性 | 不能直接对任意集合 min/max |
| `upload_payload.py:963-978` | 逐项遍历原 items 并 append | 保留此行为，加顺序回归，无需增加排序 |
| `question_draft.py:662-668` | 只对部分形态保留 source_span/printed_numbers | 普通无号作文的 single_writing_section 未通用传递到成品 |
| `upload.py:69-70`、`format_gate.py:285` | 已传入 batch_dir 并调用同一个 build_payload | 可在同一入口做只读来源回接 |
| `app/docs/upload-api.md:60,161` | 仍写 location 推断及旧的整卷档案说明 | 实现时同步修订，区分上传保号与正式档案管理 |

使用合成批次复现：独立题输入 `81/81`，题组输入 `56/58`，二者已有显式 numberStart/numberEnd；build_payload 返回 passed=true，但两个输出均没有范围字段，仅题组子题保留 56、57、58。复现不访问真实批次和远端。

三个数值必须分清：

1. 原卷数字题号：本题真实印刷号或已识别的编号空位。
2. 本地内部兼容号：无号作文可能续编 81，无号图片/整节可能使用 1；仅用于既有内部协议。
3. 单元顺序：题组占一个位置；第 21 个单元不必是原卷第 21 题。

## 3. 推荐实现：统一只读编号投影

建议新增 `app/workflow/upload_numbering.py`，把“读取并证明来源”与“计算输出范围”分开。名称是拟定，不是已存在模块。

- `load_numbering_context(batch_dir, items)`：只读当前确认结构、原文及已有有效绑定，产生按 unit_id 索引的编号证据；没有 batch_dir 时只能使用调用者提供且验证过的明确证据。
- `project_numbering(item, confirmed_unit)`：纯函数，明确区分 `printed / proven-unnumbered / unknown` 三态，返回范围、已证明的子题号映射、诊断及所用来源绑定；不改输入，不读网络。
- `build_payload` 在白名单过滤之前执行投影，校验既有范围，再显式把结果写入 API 副本。
- `format_gate/prepare/verify` 共用 build_payload 和编号投影，禁止三处各写一套推断。

第一阶段优先在上传边界回接现有结构证据，不为新增两个输出字段整体升级本地题目身份协议。当前 `number/numbers/unit_id/question_ids` 保持原语义；既有 scoped-v1 不改版本。

### 3.1 证据绑定

必须从当前有效确认单元按稳定 unit_id 严格唯一匹配；旧格式 ID 的兼容关系须由既有来源对象证明，不跨大题按全局同号、列表位置或首个同号兜底。

核对题组/独立题身份、numbers、question_ids（适用时）、原文窗口、内容摘要及当前确认结构的有效性。读取并复用现有来源锚点、编号空位及合法无号形态口径，避免再写一套正则。若现有 helper 会写候选/触发模型，应先抽出只读读取和验证接口，上传投影不得调用它的写入路径。

有效标记包括：

- `source_span.number_binding=single_writing_section`：经当前来源验证的无印刷号作文节，内部续号不输出成原号。
- `source_span.number_binding=unnumbered_section` 且 `printed_numbers=[]`：经当前来源验证的无号整节/图片等。
- 有印刷号：当前单元的局部原文锚点与目标题号完整对应，包括库侧统一识别的编号空位。

单独的 `type=书面表达`、正整数 number、空 printed_numbers、printed_number=false、original_number=null、location 文本均不能代替有效来源证明。作文有真实印刷号时照常输出该号；数字无法确认不等于无号，不得直接填 null。

### 3.2 输出规则

| 来源形态 | 顶层范围 | 子题 | 关键检查 |
|---|---|---|---|
| 独立题，印刷号 21 | 21 / 21 | 无 | 不受 location、ID、单元顺序影响 |
| 题组，印刷号 26、27、28、29、30 | 26 / 30 | number 保留原号、原顺序 | 唯一、全覆盖、连续，不能补出不存在的号 |
| 有印刷号的作文 81 | 81 / 81 | 无 | 不因题型强制 null |
| 无印刷号作文，内部号 81 | null / null | 无 | 证明 single_writing_section，不能发成 81 |
| 无号图片独立题，内部号 1 | null / null | 无 | 不从内部别名生成第1题 |
| scoped 大题甲1、大题乙1 | 各自 1 / 1 | 各自原号 | 仍以各自来源身份绑定，不改全课时连续号 |
| 无号整节题组，内部子题别名 1 | null / null | 见下文契约缺口 | 内部 1 不冒充印刷小题号 |

range 严格类型为正整数或 null，排除 bool、浮点数、数字字符串、0、负数。键必须成对；数值范围不得反向，独立题两端相同；题组实际号集合必须等于闭区间且无重复。不得靠排序/自动补号修复不合格数据。

调用者已提供 numberStart/numberEnd 时，不可直接信任：形状非法或与来源投影冲突必须报错，不静默覆盖；若成对 null 但原卷有号，同样冲突。没有提供时才由证明结果补齐。

将 numberStart/numberEnd 从 LOCAL_FIELDS 移到 WIRE_FIELDS，但只在顶层发送；子题仍使用自身 number，不因共用白名单而无意新增范围字段。

### 3.3 顺序与重号

items 和 subquestions 保留输入顺序，不新增按号、ID 或范围排序。对于整卷，输入顺序也须与当前确认来源顺序一致；发现错序应定位差异，不能在上传边界静默重排。

范围连续性只在一个题组内检查。scoped 跨大题、基础/提升、A/B 材料同号合法，不加全批次数字去重；同一来源窗口真实重复仍拒绝。普通整卷继续沿用已有重复号、漏题和结构校验。

不为 scoped 课时新建普通整卷 manifest，不把 section ordinal/Roman 转成阿拉伯小题号。远端普通 paper 对重复号的支持未在本文档中明确，本地保持身份和原号，不伪造全卷连续号来迎合其校验。

## 4. 无号题组的远端契约缺口

公开文档明确顶层无号单元使用 null/null，但没有说明无号题组的 subquestions[].number 能否为 null、能否缺省，及答案细则怎样关联。当前本地会要求 child.number 为正整数，并可能发出内部别名 1；不能以“已有代码如此”视为符合新规。

实施时用隔离的远端验证器/接口契约样例确认这一形态，优先只读检查或本地服务端测试，不创建真实暂存。若允许 null/缺省，增加有来源证明的专用传输映射并验证子题、答案和展示关联；本地 question ID 继续保留内部关系。若远端强制正整数，需远端先提供无号子题的关联契约后同步适配。

还须检查组级答案聚合：现有逻辑会给内部别名添加“1.”前缀，即使顶层已是 null/null 也可能伪装印刷号。无号组保留原答案整串和内部身份，不能用别名新生成编号答案或拆造子题；这一点纳入远端契约验收。

此缺口仅影响已定位的无号题组，不影响有号独立题、有号题组或无号独立题的开发与验收。证据不足时产生具体的传输契约诊断，不伪造印刷号，也不把合法无号形态本身判为内容缺失。

## 5. 校验、冻结与兼容

建议新增错误码（拟定）：

- `PAPER_NUMBER_RANGE_INVALID`：缺一端、非法类型、反向范围、独立题范围不相同。
- `PAPER_NUMBER_SOURCE_CONFLICT`：显式范围/子题号与当前来源不一致。
- `PAPER_NUMBER_EVIDENCE_REQUIRED`：无法证明有号或无号；指出 unit_id、来源窗口及缺的证据。
- `PAPER_NUMBER_GROUP_NONCONTIGUOUS`：组内缺号/断号，列出实际集合及缺号。
- `PAPER_NUMBER_ORDER_MISMATCH`：items 与当前已确认来源序列不一致。
- `UNNUMBERED_SUBQUESTION_CONTRACT_UNRESOLVED`：无号子题远端表达未确认，不伪装成内容缺失。

报告必须标明批次、unit_id、字段路径、检查项与具体冲突，不用“题号不对”泛化处理。不增加人工审核差异类别，不改知识点/答案，不用模型反复重试确定性映射错误。

编号所用原文/结构/绑定摘要与投影规则版本须进入预检及 plan，verify 同源重算比对；format_gate.RULE_FILES 与输入摘要覆盖新增投影模块及实际读取的证据文件，防止编号证据变更后复用旧通过报告。可复用现有已绑定摘要，不重复新增冗余大对象。

旧批次只读适配，不改历史 batch_meta、不重跑整卷；已上传批次不自动补传或修改云端档案。旧冻结字节不得就地改写；用户后续主动准备上传时生成新内容寻址副本，并按原冻结绑定流程处理。不能改变现有“曾 validate 通过即可手动上传，审核差异不阻碍”的用户边界。

location 原有来源描述保持；移除依赖“补 location 让服务端推题号”的策略。若未来需要补来源位置文本，应另按有效证据生成，不能成为结构化题号的替代路径。

## 6. 拟改文件与实施次序

1. 新增 `app/workflow/upload_numbering.py` 及纯函数测试：定义证据、映射、范围/顺序诊断。
2. 修改 `app/workflow/upload_payload.py`：白名单、顶层投影、范围冲突检查、删除 location 推号依赖；完整保留其他字段转换。
3. 修改 `app/workflow/format_gate.py`、`app/workflow/upload.py`：共用入口、摘要绑定与 verify，禁止冻结后补字段。
4. 如只读回接需要抽取 helper，仅局部修改来源/确认结构读取模块；不要扩大为所有阶段协议迁移。后续若选择在草稿通用保留编号元信息，需另验证其对对象摘要、审核陈旧判定的影响，不能混入本次最小修复。
5. 同步 `app/docs/upload-api.md`、本设计状态、根 AGENTS.md 相关编号规则与 `CHANGELOG/RULES.md`，记录真正实现后的口径及验证数字；当前设计不冒充运行规则已生效。
6. 在合成链路完成后，按用户指定范围做真实原卷只读载荷核对；不调用 POST /api/uploads 或 papers 写接口。

`app/workflow/contracts.py` 的阶段、依赖和产物无须改变；不增加模型调用，不改变听力并行、教材匹配暂停或上传发布边界。

## 7. 验收矩阵

| 用例 | 期望 |
|---|---|
| 独立21、location空/原章节/误导性第1题 | 基于来源输出21/21，location不作为号证据 |
| 有号作文81与无号作文内部81 | 分别81/81、null/null |
| 有号组26–30 | 范围26/30、全部子题号保留 |
| 断号组[26,28]、重复号、numbers/child不一致 | 明确失败，不用min/max掩盖 |
| 显式范围合法/冲突、半null、两null与有号来源冲突、bool/字符串/0/负数 | 严格校验，不静默清洗 |
| 无号图片/无号非作文/无图字母歌 | 证明无号后顶层null/null；无号子题按已确认远端契约处理 |
| 仅缺字段、伪造无号标记、旧摘要、重复unit_id/来源不唯一 | 不猜null、不跨大题借号 |
| scoped多大题各1、A/B同号 | 原号原序、身份不变，不全局去重或重编号 |
| items为非数字排序的原卷序列、含group | 上传完全保持来源确认序列；乱序输入单独报错 |
| format_gate→prepare→freeze→verify | 最终JSON确有范围；序列和来源摘要一致 |
| 编号来源/代码变化、冻结被改 | 旧计划不能通过新规则重算，不就地改冻结字节 |
| 输入深拷贝前后对比 | batch/candidate、知识点、答案、材料、媒体均未被投影改写 |

实施后至少执行受影响的 upload_payload、format_gate、upload、question_identity、question_draft 与无号来源相关测试；重大运行改动再跑全量 workflow。实际 HTTP 请求字节应由本地模拟服务器断言；模拟成功不等于远端已确认入库。

## 8. 本次已做验证（实施阶段）

- 线上文档中心和纯文本各读取一次并交叉核对题号段落；尚未检查远端实现代码或发任何认证 API 请求。
- 合成复现证明显式范围被静默丢弃，build_payload 仍 passed=true。
- `workflow.test_upload_payload workflow.test_format_gate workflow.test_upload` 共 123 项：首次共享临时锁运行出现8项lock_timeout；换隔离 TMPDIR 后123项全部通过，用时2.668秒。失败原因来自 local_agent 使用系统临时目录全局工作流锁，未删除锁或中断运行批次。
- 实施后新增 `upload_numbering.py`（14 项测试）与 `numbering_migration.py`（6 项测试）；合并编号/上传/语义/装配/审核相关 297 项在隔离 TMPDIR 全部通过（含题组子题空知识点放行回归）。审核语义兼容子代理另跑 15 个审核与装配 suite 共 266 项通过。
- 本机 18 个批次已直接迁移：`batch.json`/`batch_meta.base.json` 全部写入范围；13 份整卷的无号作文为 `null/null`，02 课时两个无号单元为 `null/null`；其余按原卷原号。每批在 `workflow/numbering-migration/backup-<stamp>/` 留有改前备份与 SHA-256，逐批 `workflow/numbering-migration.json` 记录范围、证据和复验。仅范围字段变化，`batch.base.json`、题面/答案/知识点/媒体、manifest、冻结件和回执均未改。
- 已批准上传的 4 个批次（04、19、2024、20）最终 `batch_meta.json` 保持批准绑定字节不变，下一次准备上传时由同一投影补范围；未伪造批准，也未自动重传。
- 迁移后 `build_payload` 复验：所有最终批次 0 编号错误、范围字段齐全；批准摘要仍与冻结最终件一致。两个批次（04 `format_gate`、23 `meta`）的上游输入在迁移前即已与记录不一致，非本次迁移造成，未自动改状态或重跑。
- 无号题组子题 `number` 的远端契约仍单列为待核实项；本地保留内部别名和原答案整串，只在编号证据中写 `UNNUMBERED_SUBQUESTION_CONTRACT_UNRESOLVED` 诊断，不阻断、不伪装成印刷号。

本次只新增设计及变更索引/说明，未改运行行为。回退只需撤回这组文档增量，不涉及批次或云端回退。
