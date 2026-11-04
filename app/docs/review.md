# AI 审核、三审与人工队列

用户要求的顺序是：AI 审核 → 一审重新判断标签 → 二审判断知识点与题目的语义对应 → 三审抽查 20% 小题 → 处理真实发现 → 审核闸门。没有发现的题不需要人工逐题确认，不要求人工姓名、签名或密钥。

## 现有接口的能力边界

`workflow.agent_tasks` 的 `review` 任务是 **一次本地 AI 审核**。旧产物 `07_review/agent-review.json` 的 `checked_units` 仅说明该任务声称检查的单元；不能据此推导一审、二审、三审已完成。导入兼容旧协议，但会把缺少的分轮审核保留在 `07_review/unchecked.json`，正式闸门仍阻断。

非空、结构覆盖、目录值合法、知识点 ID 对应等程序检查是确定性校验。它们不能判断“这个空是否考这个知识点”，不能重复运行三次后宣称三次独立语义审核通过。机器也无法凭 JSON 证明审核者真正阅读过题目；下面的协议验证输入绑定、记录完整性和证据可追溯性，语义质量依赖真实执行的审核。

## 分轮记录协议

新增产物为 `07_review/review-rounds.json`，形状为 `{ "plan": {...}, "rounds": [...] }`。计划由可信编排器调用 `workflow.review.prepare_round_plan(batch_dir)` 生成；模型不得自造摘要或改写计划。计划不包含任何已审结论。

计划包含 `protocol=upload-workflow.review-rounds/v1`、`input_binding`、`input_hashes`、单元清单 `units`、完整小题清单 `questions`、样本清单 `sample` 和 `sample_method`。绑定覆盖原卷文字、确认结构、类型、知识点、最终批次、存在的答案/提取/音频证据，以及目录和匹配规则。相关输入变化、增加或消失时，旧计划失效，必须重新导出并真实重审。外部图片或音频未核验时仍须记录不确定，文本摘要不能证明看图或听音。

每轮必须来自实际的不同执行，并包含：

- `round_id`：`labels`（一审）、`knowledge`（二审）、`sample`（三审）。
- `input_binding`：原样返回计划的绑定值。
- `execution_id`：真实调用/任务 ID，三轮不能相同；它不是人名或签名，也不能证明独立判断质量。
- `checks`：实际逐单元或逐样本的结论，不能从程序校验结果自动填 `ok`。

每条 check 必须包含 `unit_id`、`verdict`（`ok` / `error` / `uncertain`）、解释判断依据的 `rationale`、`evidence` 和该轮专用 `assessment`。三审另带字符串 `number`，必须对应抽中的小题。

非 `ok` 的明确问题可选填 `issue_key`（具体问题）及 `resolution_key`（包含目标和预期结果的具体处理动作）。只有实质相同的处理动作才能使用相同的 `resolution_key`；不确定时省略。跨轮合并还要求问题、处理动作、目标、输入绑定、判断、理由和证据完全一致，且原发现和人工队列均未关闭；完整各轮 assessment 均保留在 `round_sources`。缺少这些键的旧记录不会据此合并，新来源不会继承已有的关闭决定。程序只能比较文字，不能证明不同 assessment 在语义上或处理诉求上确实一致；一个合并后的人工决定会覆盖其所有来源，审核时必须逐一核对。

证据是非空列表，每项 `{ "path": "01_source/正文.txt", "locator": "第1题/相关句", "quote": "原文真实片段" }`。路径必须在当前绑定输入中，quote 必须真实存在；每条 check 至少引用一条原卷文字证据。只引用目录、复述“非空/目录合法”，不构成语义审核。未经读取的图片、音频不允许编造原文片段或核验声明。

### 一审：重新判断标签

`labels` 精确覆盖所有确认单元，每单元一条。**盲判**：轮次上下文不含任何现成标签（题型、子类、知识点、难度都不给），只给原卷题干、材料、选项、答案与解析；审核者独立推导主类和子类，并由程序把盲判结果与当前标注比对。`assessment` 只记录 `{ "type": "重新判断的主类", "subtypes": ["重新判断的子类"] }`；答案、难度或内容的冲突用 `error` / `uncertain` 加依据指出。与当前值不同而声称 `ok` 会被拒绝；真实差异或不确定用 `error` / `uncertain`，说明依据并进人工队列。

### 二审：知识点语义

`knowledge` 精确覆盖所有确认单元，每单元一条，同样**盲判**：不参考任何现成知识点标签。判断题目实际考查内容、答案与考点的联系、组级体裁/主题及分题类题型每个子题的角度（整篇类题型只看组级）。不能用目录合法性代替语义判断。

`assessment` 是 `{ "knowledge": ["重新判断的组级或独立题完整路径"], "subquestions": [{ "number": "1", "knowledge": ["该小题实际考点"] }] }`。独立题的 subquestions 为 `[]`；题组子题逐一列全。`ok` 必须与当前值一致；有差异、冲突或不确定则记录真实发现，不静默修改正式题目。

### 三审：20% 小题抽样

分母是小题：独立题算一个，题组的每个成员各算一个。样本量为 `ceil(小题数/5)`。计划按绑定值和 `{unit_id, number}` 的 SHA-256 排序，取前 N 个；同一输入得到同一清单，模型不能自行挑方便的题。此方法是可复算的固定样本，不宣称秘密随机或统计代表性。

`sample` 精确覆盖该样本，不得用单元数量代替小题数量，不得遗漏、重复或换题。`assessment` 是 `{ "source_comparison": "对抽中小题题面、选项、答案、标签与原文的具体核对结论" }`，另附原文证据和理由。未抽中的题没有三审结论，不能称全卷三审逐题通过。

## 导入与闸门

`workflow.review.evaluate_rounds(batch_dir, payload=None)` 校验当前输入与记录，默认读新增产物；返回 `passed`、`errors`、`unchecked`、`findings`、`checked_rounds` 和当前计划。缺文件、缺轮次、错误抽样、重复执行 ID、过期绑定、缺证据都保留 required unchecked。函数不会生成审核证据或已审数据。

可信编排器收集三份真实结果后，可通过 `workflow.ingest.build_review_files(batch_dir, {"review_rounds": payload, "findings": [...]})` 保存记录、合并发现并更新 unchecked。无效或不完整提交会被拒绝，不能因此清除原有待核验项；完整记录中的 error/uncertain 进入人工队列。旧的 `annotations` 导入计数只记为 `observations`，不再递增“独立审核次数”。

`workflow.review.evaluate` 将分轮校验纳入闸门，同时要求没有未处理的阻断发现、没有未处理人工队列项、没有其他 required unchecked，并保留已有审核文件检查。空 findings/空人工队列是正常的无问题结果，不能为了消除空文件提示制造每题确认项。

真实执行入口是 `python -m workflow.review_rounds run --batch-dir <batch>`。有本机 Agent 配置时，每轮通过 `local_agent.invoke` 启动一个全新实际进程；程序生成 execution_id，模型只回该轮 checks。无配置时只导出当前轮，目录含 README、task、schema、示例四份公开文件；用 `python -m workflow.review_rounds import --batch-dir <batch> --task-id <ID> --result <result.json>` 交回，再 export/run 下一轮。`export` 可显式逐轮文件交接。任务/输入/此前轮次变化时拒绝旧结果；同一任务重复交回相同结果返回原回执，不同结果拒绝覆盖，needs_review 也需重新导出后重试。原始候选、失败和历史记录均保留。文件交接默认完整上下文预算 600000 字符，可用 export 的 `--max-input-chars` 调整；自动调用沿用本机配置的 `max_input_chars`，超过预算直接报错，不截断原文。单题的 error/uncertain 和已有人工队列不会中断后续独立轮次；所有轮次均尝试执行，实际发现保留人工未决。自动执行默认每轮最多尝试 3 次，失败候选与错误记录保留；本轮仍无法完成时继续尝试其他轮次，该轮保持 unchecked，不计作执行完成。缺失安全输入、非法任务绑定或输入变化仍阻断相应执行；修复输入后重新绑定重审。不会跳过冲突单元再把缺项当全覆盖通过。所有三轮具有合法完整记录时 execution_completed=true；quality_passed 单独表示审核质量结果，有真实发现时仍可为 false。整卷结果写入 `07_review/overall-review-report.json` 与 `07_review/overall-review-report.md`，分别报告审核执行完成、质量通过和人工未决，不能将“流程执行完毕”描述为“质量通过”。

通用 `workflow.agent run` / `import` 已在单次 AI 审核完成后接入上述分轮入口；后续 `next` / `run` 可在同一输入上继续未完轮次。分轮未完时阶段保持 needs_review，不能靠单次 local report 的 completed 宣称全流程完成。单次 AI 初审返回 needs_review 时，若已有通过候选形状与发现证据校验的可读候选或真实 finding，且安全输入与上游条件齐备，入口仍继续独立三轮；初审候选保留在 workflow 内部，初审未决保留 required unchecked，不将其伪造为已完成初审。初审与三轮都完成时 review 阶段登记执行完成，质量与人工未决交由整体报告和 review_gate 判断；仅三轮完成而初审尚未完成时，入口仍返回 needs_review，并显式输出 ai_review_completed=false 与 execution_completed=true。runner/contracts 已将新增分轮文件列入审核产物并保留输入失效传播，正式闸门调用当前 `review.evaluate`；全部完成时记录完整审核产物哈希。旧单次 review 阶段完成不代表整个三审完成。部署者如另行显式启用 legacy `independent_review.py` 的 HMAC 配置，仍可额外验签；那是防篡改附加机制，不是用户要求的人工姓名/签名，也不能替代语义复核。

### 分片预绑定与并发（2026-11 更新）

盲审上下文版本为 `blind-context-v7`；`v1`–`v6` 只作为历史保留，不能导入为新结果。每轮导出时一次性为该轮全部分片生成固定任务与绑定（`frozen_state`），因此分片可以独立执行、不必等待前一分片结果；导入仍按分片顺序进行，每个分片都必须对得上自己的任务、绑定、原始响应与安装事务。

`run` 默认按本机配置的 `part_concurrency`（或显式 `review_part_parallel`，上限 4）并发调用同一轮的多个分片。成功的分片响应先写入 `workflow/review-pending/<task_id>.json`，再按顺序导入；某分片失败时，后续已成功的响应不会丢弃，下一次运行直接复用而不再调用模型。失效或被篡改的轮次（例如证据被改动）会换发新的任务标识，不会被旧回执永久卡住。

三轮统一为同一套结构化判断：`assessment` 含 `type`、`subtypes`、组级 `knowledge`、`subquestions`（整篇题组的子题允许 `knowledge=[]`）和逐小题 `difficulty`；三审另含 `source_comparison`。程序按 `(unit_id, 字段)` 与 `(unit_id, number, 字段)` 逐项比较原标注与各轮盲判，只把真实分歧、错误或 `uncertain` 送入人工队列；`uncertain` 允许 `assessment=null`，其他目标与后续轮次继续。三审的每个抽样目标还需可核验的逐小题原文范围；只有题号行/空位锚点能在绑定原文中唯一确定时才视为已核验，表格等无法界定的目标保留未核验而不阻断其他题。知识点目录按原卷大题标题推导的题型范围裁剪（`workflow.review_paper_scope`），题号无法归类时回退完整目录。

## 人工队列只接真实发现

`07_review/manual-review-queue.json` 只接真实差异、冲突、不确定和缺证据发现。没有问题时 items 可以是 `[]`。缺少分轮执行本身保留在 unchecked，不为每道正常题制造人工确认项。

人工决定沿用现有决定日志和处理接口，记录问题、动作、理由、修改及时间即可，不要求人工姓名或签名。代理不能冒充人工作决定、接受风险或确认上传。现有 `review.py` 闸门侧将 `reviewed/resolved/accepted/fixed/closed/approved/passed/waived` 视为已处理；工作台待审徽标若采用更窄的状态集合，应与闸门口径分别说明，不据此声称内容已重新核验。

修复题目或标注后输入绑定变化，三轮记录必须失效并真实重审。处理人工队列不能把缺失的语义审核自动变为通过。
