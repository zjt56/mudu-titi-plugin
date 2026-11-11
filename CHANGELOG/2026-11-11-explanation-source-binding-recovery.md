# 解析来源等价验收与确定性拒收恢复

## 背景/触发

用户要求修复解析填充此类 bug，并加强代码与提示词约束。现场批次为 `C:\Users\Administrator\.dsh\wf-plugin-data\workflows\【木牍教育】12年安徽中考英语`，不是已完成解析的2019年卷。

2012年附录的31–70、76–95题解析重复出现。旧消费者按同号锚点数量必须等于一判断，正确的原文引用仍被拒绝；模型已产生候选，正式产物因验收失败尚未安装，运维却把正式文件缺失当成“未产出”，反复执行生成。原DOCX摘要 `76a65b9ce1f8e75068d6c9d19a7cbd61efd92d2e317b99349948dc06bb957393`，XML中第95/419段均有第31题同文解析，证明重复存在于原件，并非凭空假设提取重复。

进一步复验发现，第96行重复解析继承了不匹配的大题标题；该坐标仍须拒绝，不能因与第15行同文而借用身份。另第70题重复段后紧接 `71~75 DBFGE`，旧段落边界把范围答案吞进第70题解析，产生伪异文。

## 改了什么

- `upload_content.py`：逐字同文重复按真实引用坐标与题目身份验收，逐候选判断章节；真实异文、空锚点、错误题号/坐标/SHA及跨scope仍拒绝。消费者与来源索引采用相同绑定证明。范围答案行使用共享边界识别，不能当单题owner，也不被吞进前题解析。
- `explanations_direct.py`：使用同源范围答案边界，直抄仍走正常导入和消费者校验。
- `label_execution.py`：只根据校验器明确的结构化 `retryable=false`、`failure_class=source_binding/validator_fault` 停止对应单元重复模型；保存原候选、验证错误和严格输入绑定。续跑先本地复验，独立成功单元检查点保留；错文/错引用仍允许纠正。
- `self_heal.py`、`operations.py` 与知识点修正入口：内容续跑别名都执行局部范围准入；确定性来源问题不能靠换上下文或反馈绕过。运维证据包提供当前阶段最近运行的有限候选、验收进度与失败证据索引，明确候选未验收，仍禁止任意读取输入、提示词、跨阶段和穿越路径。
- `content_rules.py`、解析角色、运维提示词及文档：明确正式产物缺失不代表未生成，先查候选/验收/源坐标；同文不等于异文冲突，内容续跑保留基线且只处理受影响单元。根AGENTS规则4/19与RULES台账同步。
- 第二轮补强（同一目标，解决"首次交付被自己的保护挡住"与两处安全缺陷）：
  - `private_label_baseline.py` 新建并重写绑定：私有基线/失败候选的键改为批次自有来源真相（`01_source/**`、`01_source_ans/**`、`06_batch/question-draft.json`），不再用阶段任务 `input_hashes`——任务输入集随全量/收窄提示变化，旧键会让刚验收的单元在下次收窄时找不到基线（实测两个不同哈希：写入 `d4243c63…`、查找 `6baedb41…`）。加载仍逐单元复验 `_validate` 且要求 `candidate == payload`，键只做识别不做放行。
  - `agent_tasks.py::_widen_repair_scope`：scoped 修复但没有任何可复用已验收单元时，改为整阶段一次性交付并把原因写进反馈，而不是抛 `Scoped repair cannot validate private baseline units` 永久卡住首批交付。
  - `self_heal.py::_deterministic_delivery`：内容阶段若能由确定性直抄从原文整卷交付（`explanations_direct.build_payload` 有真实内容、非占位「无」），允许整阶段重跑一次——不调用模型、不能编造，因此不受"无定位拒绝整卷重跑"限制；全为占位时仍拒绝，避免用空解析覆盖既有产物。
  - `supervisor.py::_regular_file` 与 `operations.py::_candidate_evidence`：拒绝 NTFS junction/reparse point 并校验解析后仍在批次根内，堵住"把另一批次 response.json 当本批次候选读出全文"的跨批次泄漏；`operations.py::_foreign_owner` 把同一 pid 的不同 generation 视为本进程重启，不再误判为其他后端占用租约。
  - `label_execution.py` 交付响应语义明确：`run_dir/response.json` 只写本次局部响应，完整产物由 `agent_tasks.accept_result` 的 scope 合并产生。

## 没改什么 / 边界

不编辑manifest造完成态，不改原始来源、不生成解析或范文，不改审核差异裁决及上传边界；设备本地模型配置不提交。结构化确定性判断来自消费者，不根据普通错误码、模型自述或重复报错直接停止可纠正内容。真实异文冲突、跨大题借用、遗漏原文与消费者校验继续阻断。

## 如何验证

- 第一轮来源、内容片段、直抄、标签执行与检查点、候选证据和提示词集成：178项通过。后续边界与初次局部基线修改另行补充最终数字。
- 宿主全量：254项，238通过、15失败、1跳过。失败为backend-lifecycle的8项Windows进程/执行器测试、model-router的6项Windows python3执行器9009、release-handoff的1项9009；不能称全量通过。日志：`C:\Users\Administrator\AppData\Local\Temp\tiku-explanation-node-full.log`。
- `node --check lib/index.js`、`lib/client.js`、`lib/operations-agent.js`通过。
- 真实批次只读复验：第31题原候选指附录第15行可通过；第96行归属不符仍拒绝。范围边界修复后直抄候选覆盖52个单元，整批内容消费者通过；正式交付和下游复验结果在完成后追加。

## 现场复验（2026-11-11 13:30）

- 生产批次【木牍教育】12年安徽中考英语：`python -m workflow.agent run --stage explanations` 走确定性直抄
  → `{"status":"completed","completed":true,"deterministic":true,"model_calls":0}`，
  `04_knowledge/explanations.json` 52 单元、无空解析；`manifest.stages.explanations.notes`
  记录「按来源坐标直接抄录原解析与文章大意，未调用模型」。
- 下游：`workflow.runner run` 依次完成 assemble、meta、underlines、validate（19 项体检阻断 0、提示 0）、
  review（两轮完成）、format_gate；`review_gate=needs_review`（17 项标签/内容差异按设计留待人工），
  `upload_gate/upload/publish_gate` 仍 pending，未上传。
- 回归：受影响 15 个模块 **262 项全部通过**；宿主运维三模块 **67 项全部通过**；
  `workflow.test_changelog_policy` 5 项通过。提交 `3480b1b` 已推送 `origin/master`。

## 回退方式

按本次Git提交回退代码与配套测试/提示词，不恢复旧批次数据或伪改状态。已写私有候选和诊断不代表正式交付，旧版本不得把它们当成通过；远端提交仍需用户明确授权。
