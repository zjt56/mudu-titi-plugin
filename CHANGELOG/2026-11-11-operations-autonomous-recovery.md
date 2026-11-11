# 2026-11-11 AI 运维自治恢复与持久本地安装授权

## 背景 / 触发

用户要求重设计 AI 运维，保留已有自愈，卡点由 AI 自动解决，零人工接管；本轮明确授权本机自治修复。原流程把逐次人工确认、宿主环境变量和人工终态作为后续推进条件，不能满足该要求。前台不再要求展示修改建议，诊断证据与修改方向继续在后台留档。

本记录覆盖此前“每次安装都须 confirm 且 MD_OPERATIONS_INSTALL=1”“预算耗尽交人工”及“必须前台展示建议”的当前生效要求，历史变更记录保持原样。

## 改了什么

- `app/workflow/operations_development.py`：新增绑定源码根的 `.operations-autonomy.json`，schema 为 `md-operations-autonomy/v1`；默认关闭，配置损坏/错根/链接拒绝。可信本地 API `set_autonomy(root, enabled=..., authorization=...)` 原子保存或撤销，`autonomy_config(root)` 查询，`install_authorization(root, confirm=False)` 返回授权来源。模型工具不能写此配置，隔离复制跳过该文件。自治安装无需逐次确认或环境变量；未启用时兼容旧确认加 `MD_OPERATIONS_INSTALL=1` 通道。
- 自治安装重新核验当前授权、严格测试指纹及变更测试执行证据；保留冻结计划身份、生产基线哈希、保护文件、备份 CAS、逐文件哈希校验、失败回滚及新增文件清理。安装 journal 记录授权方式，返回 `restart_required`，不以安装成功宣称卡点已修复。测试子进程剥除旧安装开关。
- `app/workflow/operations.py`：接入持久安装授权；未解决事件的旧 `needs_human/needs_deep_repair` 处置进入自治恢复计划。优先隔离代码修复；无可执行新策略或同证据无进展时等待真实新证据，保留预算保护；输入或代码真实变化后重新准入，恢复仍须原失败检查复验。
- `lib/operations-agent.js`：加载本版后巡检由5秒缩短为2秒；模型仅输出分析即空闲时，同一有效尝试最多追加两次执行纠正，不因未知回执、断连、失租约或预算耗尽盲续发。`finish` 只提供 `resolved/recovery_queued`，旧人工终态迁移自治队列；未执行新动作不能反复 finish 退出。前台移除“分析与建议”常驻块，后台保留证据。
- `operations.py`：后端进程世代更换立即回收旧租约；持久预算不随新会话或历史截断清零。阶段已经完成时先运行当前输入和消费者复验，只有真通过才结案，避免模型调查已消失的旧错误。安装后保留待原检查复验状态，恢复原阶段仍交原 FIFO，不直接修改 manifest。
- `docx_extract.py`、`mark_underline.py`、`check_underline.py`：仅“私用字形原码位已保留”告警不阻断机械画线，告警仍留存，Symbol 等其他提取不确定性保持阻断。原卷 U+100C77 不猜替换为其他音标，不以此声称字体视觉正确。
- `activity.py`：Windows 用进程句柄零超时等待探活并关闭句柄，代替会发控制台信号的 `os.kill(pid, 0)`，避免显示层探活打断工作与测试；其他平台保留原行为。
- `AGENTS.md` 规则18、`app/docs/harness-operations.md`、`CHANGELOG/RULES.md` 与本索引同步本轮要求。

## 没改什么 / 边界

- 原录题、自愈、审核及用户手动上传入口保留；运维关闭或不可用时原流程继续。
- 默认不开启其他源码根的未授权自动安装；用户本轮授权由可信本地配置入口启用。授权文件不是仓库分发配置，不向修复模型开放。
- 不放宽测试、哈希、保护文件、计划、路径、批次身份、租约或写锁约束，不自动上传/发布，不直接改用户数据或 manifest，不自动删除原卷。
- 审核差异是输出，保留给人工判断，不是运维卡点；不以自动改标签、resolve、重生成至一致或假造质量通过消除差异。零人工接管针对确定性故障修复，不替代审核判断与远端授权。
- 同证据重复失败拒绝原样重试，预算耗尽不能清零空转。无安全可执行策略时保留失败和等待证据状态，不能伪称已恢复。安装后必须真实更换后端进程、重新认领并通过原失败检查、输入及消费者校验。
- 自动安装范围仍限既有允许清单；锁定文件、宿主 JavaScript 和配置不因此获得自动修改权限。宿主协调器仍执行单次重启、健康探测与可验证回滚，不能循环重启冒充修复。

## 如何验证

- 已实测：`PYTHONPATH=app python -m unittest workflow.test_operations_autonomy_development workflow.test_operations_development`，**39 项通过**（新增自治专用 19 项、既有开发链路 20 项）。覆盖默认关闭、持久授权/撤销/错根/损坏/符号链接拒绝、旧通道兼容、配置不复制及不可模型写入、无 confirm/env 自治安装、测试失效/变更测试执行、保护文件、并发源码变更拒绝、安装失败回滚含新增文件清理、用户数据字节不变及重启标记。
- 文档政策检查 `PYTHONPATH=app python -m unittest workflow.test_changelog_policy`：首轮5项中4通过、1失败（独立历史记录标题不符合）；该记录由并行会话修正后，最终复验 **5 项全部通过**（0.024秒）。本轮未改写该独立记录。文档 `git diff --check` 通过。
- 主 agent 已回报本次运维宿主 **4 个模块、63 项全部通过**；全量 Node **246 项：229 通过、16 失败、1 跳过**。全量失败属于已知 Windows 基线（python3 空壳、lifecycle、model-router、release-handoff）及 style-inject 旧测试 fixture 的 `ReferenceError: injectStyles is not defined`，不能记为全量通过。
- Python 运维五模块 `workflow.test_operations workflow.test_operations_autonomous workflow.test_operations_development workflow.test_operations_autonomy_development workflow.test_operations_first_blocker`：**126 项全部通过**（14.523 秒，外置独立 TEMP/TMP，UTF8）；画线 `workflow.test_underline_glyph_gate workflow.test_underline_layout`：**19 项通过**。
- 宿主命令：`PYTHON=python node --test backend/operations-agent.test.cjs backend/operations-agent-autonomy.test.cjs backend/operations-ui.test.cjs backend/operations-http.test.cjs`，**63 项通过**。`node --check` 分别检查 `lib/index.js`、`lib/client.js`、`lib/operations-agent.js` 通过，`git diff --check` 通过。上述统计不相加为互不重叠总数。
- 现场：`C:\Users\Administrator\.dsh\wf-plugin-data\workflows\【木牍教育】13年安徽中考英语`，2026-11-11 10:37:53 当前消费者复验 underlines 通过，incident `cd59cf026aa34b4184c0fb821fd50cdd` 自动变为 `resolved`，证据 `00215cd49b1248be8a667b94bc166ce0`，reason=`current validated stage evidence`。underlines、validate、review、format_gate 均完成，体检 passed=true；13条审核差异仍保留，review_gate=needs_review，未自动上传。
- 生效边界：Python 后端已重启并真实复验。当前 Harness 无可用的独立宿主模块重载接口，且 HMR 根未覆盖本插件；新2秒间隔及会话空闲续推属于宿主 JavaScript，须下一次宿主/插件重载才加载，不能把后端重启当成这些逻辑已经生效。前台源码已去掉建议块。
- 最终冻结专项：上述运维五模块加 `workflow.test_activity workflow.test_underline_glyph_gate workflow.test_underline_layout`，**156 项全部通过**（11.428 秒）。activity 与自治模块单独交叉验证 **26 项通过**；Windows 真子进程活→退出、查询异常释放句柄、POSIX 兼容均覆盖。
- 全量 Python 隔离快照最终 **154/154 模块，2286 个测试方法：2200 通过、34 跳过、52 未通过（17 failure methods、35 error methods）**；unittest 原始事件为17 failures、47 errors，多出12个 subtest 错误事件；0超时、0最终中断。模板缓存5项先被源码写保护截断，已在仓库外 app 副本补跑5/5并合并统计。全量中运维126/126、画线19/19、旧时点activity7/7通过；后续冻结专项156项另行验证。未通过项涉及Windows路径/目录句柄、缺少sh、媒体POSIX依赖、并发原子替换、缓存、审核读取、旧自愈回滚和运行顺序断言，以及并行身份格式代码；未把所有失败武断认定为基线，也不宣称全量通过。
- 完整测试报告位于仓库外 `C:\Users\Administrator\AppData\Local\Temp\tiku-workflow-autonomy-isolated\final\report.md`，权威总计 `summary.json`。并行会话仍修改题目身份链，且没有每模块启动哈希，仅中途与结束快照；这是变化中工作区回归，不是单一冻结全量。仅提交本轮文件/区块，保留他人修改与设备模型配置。

## 2026-11-11 10:58 加载后复核

- 上述“宿主待加载”是10:52记录时的状态，现已更新：真实宿主API连续7个扫描时间为10:56:45.004、47.016、49.015、51.027、53.032、55.032、57.036（+08:00），6个间隔1.999～2.012秒，pollCount234→240，证明当前运维管理器已使用本版2秒缺省，不再等待加载。
- 加载后相同源码再次运行宿主运维四模块：63/63通过（2.680秒），覆盖同租约空闲续推、未知回执禁重发、真实Python HTTP协议及前台不展示分析建议；首错接管与自治Python两模块24/24通过（2.301秒）。未为验证而制造真实试卷故障，空闲续推的行为证据来自受控回归，现场证明加载与无故障状态。
- 现场13年原incident仍resolved、verified=true、证据00215cd49b1248be8a667b94bc166ce0；服务enabled/running均true，active/incident/healing为0，lastPollError为空。画线卡点已留档；首错接管延迟记录在加载后复核完成后归档。保持13条审核差异及上传边界，不改用户批次数据。
- 本次文档政策再次运行5项中4通过、1失败：并行前台提交新增的2026-11-11-misc.md尚未列入CHANGELOG/README.md（test_index_lists_every_dated_record）。保留该真实结果，不将先前5/5当成当前工作区结果；本次仅提交自治记录及卡点归档，不覆盖并行索引修改。

## 回退方式

调用 `set_autonomy(root, enabled=False, authorization="撤销本地自治安装授权")` 关闭后续自治安装，仍保留显式旧模式兼容和既有自愈。已安装改动按原 journal、路径及哈希检查执行备份回滚；撤销授权本身不删除批次、回执或证据。需要回退本次实现时仅回退本次相关补丁，保留其他未提交修改，不覆盖用户数据。
