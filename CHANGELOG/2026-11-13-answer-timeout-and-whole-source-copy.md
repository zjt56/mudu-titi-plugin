# 原生答案任务超时与无编号整串答案直抄

## 触发与实际根因

12课时 Starter Unit3 第四课时 Project 的 answers 任务 eb446d6d423d41caab2bf7382b235084 报 Content task time budget exceeded。原任务保留 failed/accepted=false、attempts=[]、tool_calls=2，没有可导入候选。会话创建比任务创建晚约840ms，排除长队列等待作为本次直接原因。

只读核对原生会话事件：第二次模型流最后chunk后无后续产出，最终记录TIMEOUT（pi-ai stream idle timeout after 300000ms），重试刚登记即被宿主总预算取消；两次source工具均成功。本次事实是模型流中断＋任务预算退出，不是答案验收拒绝。计时回调延迟同时发生，可能涉及主机暂停/事件循环停顿，但没有系统证据，不能定论。保留原预算，不以无限续期/重试掩盖。

另有可复现的来源直抄缺陷：古诗合法无编号standalone来源已证明，附录RomanⅠ给出 one,two,...,ten 整串，原compact解析只认分号单字母串；该块invalid触发完整课时skeleton校验，连带其余7单元均无法直抄，于是进入不必要的模型任务。

## 修复

- upload_content仅对明确Roman前缀的英文逗号词列表识别literal；至少一个多字母词，A,B,C仍拒绝。无编号standalone须完整单课时skeleton、当前可信source span、number_binding=unnumbered_section、printed_numbers=[]、内部单题别名且没有真实印刷锚点等共同证明，整串原样一条答案，不拆题/不造号码。仍拒错课时、partial、跨section、同号歧义和任意无号自由文本。
- agent_tasks抽出精确可信的离线传输前缀，native _child_prompt只替换该header为受控证据工具＋submit_candidate指令；保留所有阶段、来源与schema规则。解决native任务继承NO tools却需要read_source的冲突，不把该冲突冒称已证明的断流原因。
- content-agent宿主仅在已有provider TIMEOUT且失败发生于模型流时提供model_timeout分类、固定脱敏provider原因及最后模型/工具时间。普通预算或候选验收拒绝不误归因。Python state白名单持久这些证据、返回timeout code，不丢为泛化failed；原超时任务不被改写成成功。

## 现场恢复

通过正式config暂时停止运维并排空，避免源码修复时重复调查。备份到批次workflow/recovery-evidence-20261113-answers-timeout，保留原失败任务和关键来源hash。正式runner run --stage answers --force执行确定性preflight→通用export/import，06:50:36完成，22答案、missing=0、model_calls=0。原failed任务依然failed且未accepted，已完成知识点时间仍23:21:44，无重生成。同一已安装bundle经禁用/启用两个application=applied回执重载（无关第三方agent-teams契约警告保留，未改该插件）；原运维enabled=true已正式恢复。GET旧答案事件已resolved且verification.verified=true，正式advance请求已入队，原知识点未重新生成。

续跑汇总出现独立A9：任务型阅读表格6–10原作答空位stem为空，已另登记ISSUES/open并复现。该新问题不能回写成答案失败，也不能据答案交付声称整份课时成功。正在按当前完整table/source/identity证明修复，不编造题干。

## 验证

相关Python隔离337项：336通过、1跳过，无失败（16.169秒）；答案来源相关83项通过，新专项6项。真实正文/附录/结构复制临时夹具，8单元22答案经完整_validate通过，原已正确候选15条值及坐标全部保留。Node宿主timeout专项与原content组42项通过，新增4项验证无候选断流、普通预算、重试恢复、后续候选拒绝不误分类。全Node351项：349通过、2跳过、0失败（51.493秒）；宿主脚本语法与git diff --check通过。

## 边界与回退

原卷与正式知识点保留，未自动上传发布；不扩大安装自治权限，不改路由、不增加预算或重跑整卷。回退仅本轮源码差异，保留旧失败回执、备份和新导入记录，不能改manifest造状态。当前完成的是答案卡点交付；后续assemble/review尚需真实执行。
