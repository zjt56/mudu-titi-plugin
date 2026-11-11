# 2026-11-11 同课时跨大题重号的来源身份

## 背景

用户按一个课时一批次录入，要求保留原卷Ⅰ、Ⅱ、Ⅲ的排序。原课时基础/提升两段及各大题均可能从1编号；原全局数字键会让结构漏题、答案覆盖、解析借用、预览和保存定位到另一题。此次将来源身份贯通全链，不重编号。

## 改了什么

- `app/workflow/question_identity.py`：新增显式 `scoped-v1`、section/unit/question ID。大题原标题、罗马序号、上下文、出现序次和坐标保留；题号仍是印刷原号。ID与答案/题型无关，支持保留已确认身份；同组拆合不改变题目ID。局部印刷锚支持同行多题、C:1.等对话空位，无号图题必须显式绑定整大题且内部别名不当成印刷号。
- `structure_ai.py`、`structure_gate.py`、`question_content.py`：仅同步新批次启用局部来源窗口身份；逐实际出现位置验证漏题、重号和材料覆盖。A/B同号独立窗口，不删真重复检查。原文恢复/审计按unit ID匹配；草稿复制身份字段，保全共享对话材料及选项。
- `agent_tasks.py`、`assemble_local.py`、`batch_fill.py`、`empty_answers.py`、`label_execution.py`、`unit_context.py`、`unit_tasks.py`、`scoped_regeneration.py`：答案、missing、evidence、difficulty和分片过滤统一question ID；组装确定性回填身份和子题question_id。旧协议仍使用数字。
- `upload_content.py`、`content_rules.py`、`explanations_direct.py`：同号答案/解析/大意须有本scope的真实坐标及章节归属。source_ref展开前校验；Word优先、同号作文只豁免自身。纯数字附录有答案但无法对应scope时不能猜配，也不能伪记缺失。证据不足的旧直抄通道不抢先交付。
- `review.py`、`review_rounds.py`、`review_legacy.py`：审核上下文按身份隔离；新版上下文不误复用旧整卷免审记录。修复来源邻居证明及旧冻结文件在Windows CRLF/LF下的布局等价识别，保留原始取证文本；不改审核结论。
- `provisional.py`：数字候选入口明确拒绝scoped协议，不允许数字兜底串题。
- `preview.py`、`app/workbench/server.py`、`lib/client.js`：预览按unit ID切完整已确认来源窗口，审核/候选/保存按身份隔离；显示原标题及出现位置；无号题显示未标题号，编辑保留内部别名。
- `format_gate.py`、`upload_payload.py`：格式闸门与Builder共用严格身份校验，完整scoped身份允许跨大题同号；混用、缺失、重复qid、子题映射篡改和来源冲突仍拒绝。本地身份不输出远端wire，legacy行为保持。
- `app/scripts/06-upload/check_answers.py`：体检按qid比对prepared答案，支持显式question_id与unit映射；重号数字来源不明时报ANSWER_SOURCE_SCOPE_UNBOUND，作文只豁免自身。新增真实run_all→子进程回归。
- `app/docs/sync-practice.md`、根AGENTS规则27、规则台账及回归同步。

## 验证

本地临时目录测试，无真实模型调用、无用户批次改写。Python使用UTF-8模式，结构模型禁用。

- 核心来源/草稿/格式旧回归：78项，74通过、4跳过。
- 最终全部八个身份专项模块及变更规范共84项全部通过（1.246秒）；包含结构交接顶层identity_version的真实消费链。
- 将实际git暂存内容导出到独立目录（不带本机路由、运维、提取/画线等其他未提交代码），28个受影响与旧协议模块共556项：551通过、5跳过、0失败/错误（90.350秒）。隔离暂存版本lib/client.js及lib/index.js语法检查通过。
- 内容来源六模块：135项全部通过；包含C:1、同行小题、无号图题、A/B同号跨正文窗口及附录错误归属反例。
- 旧审核与答案链联合：103项全部通过；另组装实际落盘与身份专项15项全部通过，组装model_calls=0。测试只隔离全局锁，不更改生产锁。
- 原件首课时118行只读临时回归：结构确认通过，27个单元、40处印刷小题号及1个无号图题内部别名；draft 27项、issues=[]，最终原文审计passed=True/errors=[]。包含基础/提升罗马序号重置、图片/表格、同行题号、对话空位与共享选项。最小来源形状回归同时验证删去共享对话句会失败。
- 真实端到端新增2项通过，与内容来源六模块联合137项通过：实际提取→结构确认→confirmed读取→草稿→qid答案及source_ref解析校验→组装注入→原文审计→unit_id预览；错scope答案/解析、全局数字键和跨窗题干反例均拒绝，无交接或来源helper mock。
- 格式/Builder/结构及同步规则七模块最终154项全部通过；答案体检新链与旧链56项通过，追加边界后checks11项+答案身份14项共25项通过。以上为重叠集合，不累加总数。
- 全量workflow按153模块隔离执行：2270项，34跳过，19失败、60错误、0超时；135模块通过，18模块失败。16个失败模块在HEAD基线复现（Windows路径、shell/目录句柄及既有复用问题）；另assemble_local和operations受共享源码根全局锁干扰，组装fixture隔离后13项通过，当前代码复制隔离根后operations63项全部通过。changelog旧标题缺项已修复。此全量为共享树时点快照，不能称全量通过。
- backend全量246项：226通过、19失败、1跳过；其中生命周期8、路由6、样式1共15个失败已在HEAD三模块复现（22项7通过15失败），其余运维HTTP3和发布交接1未归因为本轮代码。lib/index.js与lib/client.js语法检查通过。

## 边界

不改用户原件、历史批次、模型路由、审核差异或上传/发布权限；未安装发布插件。旧全局协议保持原行为。仅有数字的答案附录、同节A/B之间缺少材料映射、完全无编号且无法建立owner的游离解析，不会猜配为成功，须补足实际来源映射。书面表达仍不要求确定答案，不生成范文。

## 回退

对本次独立提交执行git revert；不要回滚或改写用户数据。已经生成scoped产物的批次应继续由支持该协议的版本消费，不能把身份字段删去再当旧数字批次。
