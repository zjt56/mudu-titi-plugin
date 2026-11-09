# AI 运维快速纠错：故障证据包、重复失败升级、隔离代码修复

## 背景/触发

用户询问「如何实现快速纠正卡点的错误」，并明确授权（「可以」）按建议补齐 AI 运维的快速纠错闭环。原实现只在已有自愈实际失败后接手，能做的直接修复只有来源表格/来源选项/上传格式三类，未知程序缺陷停在 `needs_deep_repair`，且同一确定性故障可以在相同输入上反复尝试。

本次目标是让每次卡点处理都回答清楚：哪项检查失败、错误来自哪里、这次改变了什么、凭什么证明恢复。范围限定为后端受控流程；不自动上传发布，不改写归档批次。

## 改了什么

### `app/workflow/operations.py`

- 新增 `_packet(batch, incident)`：`diagnose` 额外返回自包含故障包——批次名/目录、阶段与标题、签名、错误文本、失败检查（报告/错误码/检查名/题号/消息）、受影响单元、阶段输入哈希、输入文件哈希、当前产物哈希、已完成且不受影响的独立阶段、被阻塞的下游阶段、最近尝试、升级状态、允许的修复与续跑策略、插件代码指纹。数据只来自既有来源/报告/台账，不调模型。
- 新增重复失败升级：`_evidence` 由阶段输入、输入文件内容哈希和插件代码指纹组成；`apply_repair` 与带策略的 `resume` 在真正执行前调用 `_block_repeat`。同一动作在完全相同证据与代码上再次失败时返回 `repeat_without_new_evidence`，写入 `escalation.kind=code_repair`，并提示改用 `request_code_repair`。`_attempt` 记录 applied/refused/failed 与瞬时错误标记；限流、超时、网络、暂时不可用仍允许重试。
- 新增 `request_code_repair` / `code_repair` / `install_code_repair` 三个动作，接入原有回执、事件、尝试、会话与快照栅栏：
  - `request_code_repair` 为当前批次建立唯一隔离工作区（临时目录，按批次目录哈希分键），复制当前运行有效源码（含未提交修改），保留只读基线；
  - `code_repair` 支持 read/write/test/diff/prepare/rollback/status，读写限制在 `app/workflow/*.py` 与测试文件，拒绝符号链接、`..`、盘符和清单外路径，修改已有文件必须带原哈希；
  - `install_code_repair` 要求 `confirm=true` 且启动插件宿主的进程环境带 `MD_OPERATIONS_INSTALL=1`（一次性显式开通；安装期间监听暂停由 `prepared` 标记自动完成），核对安装计划与当前工作区指纹一致后才写入，成功后只标记 `installed_pending_restart`，并清除既有复验结果。
- 只有可能已写入的动作才把异常记为 `unknown`；写入前的拒绝保持 `failed`，仍可重试。安装开始标志单独记录，避免"计划校验失败"被误判为结果未知。
- `_source_root()` 抽出运行源码根，测试可指向假仓库，真实插件不会被改写。

### 局部重生成

- `operations.py` 新增 `regenerate_units` 动作：把一次内容错误限制在受影响的题目单元，而不是整卷重跑。
- 只允许 `answers`、`explanations`、`types`、`knowledge`、`audio` 五个内容阶段；上传/发布边界、确定性阶段与未知阶段直接拒绝；未指定阶段时由结构化错误定位生产者，定位不到不猜。
- 显式 `unit_ids` 必须属于当前批次的确认单元，未知题号整单拒绝；未指定时只使用证据里真实出现的单元，定位不到返回 `regenerate_units_requires_scope`。
- 反馈文件（`workflow/repair-feedback/<stage>.json`）新增 `scoped_units`，只带本次题号；阶段仍按自身逻辑重跑并用原有校验验收。同一阶段+题号集合在相同证据与代码上已排队时返回 `already_scheduled`，不重复入队。
- `self_heal._feedback` 增加可选 `unit_ids` 参数（默认行为不变），供局部重生成显式限定范围；`self_heal.py` 属于开发流程的锁定文件，只能由开发者在评审中修改，运行时隔离修复无法改动它。

### `app/workflow/operations_development.py`（新增）

隔离开发的纯文件与测试支撑，不依赖模型：

- 允许清单：`app/workflow/[a-z][a-z0-9_]*.py` 与 `app/workflow/test_*.py`；锁定 `operations.py`、`operations_development.py`、`contracts.py`、`manifest.py`、`supervisor.py`、`auto.py`、`runner.py`、`local_agent.py`、`self_heal.py`。
- `capture` 以**当前字节**（含 dirty/untracked）计算基线；不复用 `git archive HEAD`，因为运行中的行为来自工作树而非提交。
- `create` 复制仓库时跳过 `.git`、`workflows`（用户数据）、虚拟环境、缓存、点文件与密钥样式文件，再写入允许清单基线字节与 `.base/` 只读副本；不使用硬链接指向生产文件。
- `run_tests` 固定 `[sys.executable, "-m", "unittest", *names]`，剥除令牌/密钥/密码/API Key/认证变量与 `DSH_*`；记录测试指纹，测试后再改文件会使测试证据失效。
- `prepare_install` 对每个改动文件做 CAS（生产文件必须仍等于基线），拒绝删除与锁定文件改动，要求存在与当前工作区指纹绑定的通过测试，且改动的测试文件本身被执行过。
- `install` 只在显式允许时执行：写安装日志与备份、原子替换、逐个校验；任一步失败自动回滚并删除新增文件；回滚前若已安装文件再次被改动则拒绝覆盖。

### `lib/operations-agent.js`（宿主工具）

- 运维会话新增四个受控工具：`operations_regenerate_units`、`operations_request_code_repair`、`operations_code_repair`、`operations_install_code_repair`；参数按后端契约声明（阶段枚举、题号数组、工作区操作枚举、确认布尔），仍受 `restrict({allow:[]})` 与事件级 guard 限制，只能调用已注册的运维动作。
- 系统提示补充优先级：个别题内容错误先局部重生成并给出题号/阶段；同证据重复失败时才进入隔离代码修复；安装成功不算修复，必须重启后重新认领复验。
- 局部重生成与安装都会清除既有复验证据；`install_code_repair` 返回 `restart_required` 后立即结束本次尝试、发出 `awaiting_restart`，并以“等待宿主重启并复验”为由释放接手，绝不在同一尝试内报告 resolved。

### `lib/release-coordinator.js`（新增）与发布标记

- 后端 `operations_development.install` 在 `<源码根>/.operations-release.json` 写发布标记：替换前 `prepared`、全部成功 `installed`（计划号、文件清单、journal）、回滚 `rolled_back`。
- 新模块 `createReleaseCoordinator({markerPath, restart, log, intervalMs})` 轮询标记：`prepared` 暂停监听并抑制去抖重启；`installed` 执行**一次**重启并等待健康，成功后删除标记、状态 `active`；失败把标记改写为 `failed` 并停止重试，避免重启循环；超时 `prepared`（默认 5 分钟）不再暂停监听，防止崩溃后热更新永久失效。
- `lib/index.js` 接入：`startBackendWatch()` 同时启动协调器；`schedule()` 与 `restartBackendSoon()` 在 `paused` 时直接返回，保证安装期间不会有并发重启；协调器的 `restart` 复用既有 `ensureBackend({restart:true})`，因此仍走原有停止/端口选择/健康探测逻辑。

### 重启失败自动还原（`lib/code-restore.js` 新增）

- 发布协调器新增 `rollback` 选项：重启后健康检查失败时不再直接放弃，而是按安装 journal 还原旧文件，然后**只再启动一次**旧代码。还原成功且旧代码健康才清除标记（`rolled_back`）；还原失败或还原后仍不健康写 `failed` 并停止，不进入重启循环。
- `restoreRelease(journalPath)` 先整体校验再写入：文件必须仍在源码根内、仍等于安装后的哈希（被他人再次修改则报 `source_changed_after_install` 并拒绝覆盖）、备份必须存在且哈希等于基线；新增文件在确认内容后删除。任一项不满足则该文件不写，避免新旧代码混合的“半个版本”。
- `lib/index.js` 把 `restoreRelease` 接到协调器的 `rollback`，复用 `ensureBackend({restart:true})` 完成还原后的唯一一次启动。

### 跨轮次预算

- `app/workflow/operations.py` 新增 `BUDGET_ATTEMPTS=24`、`BUDGET_INSTALLS=2`、`BUDGET_REGENERATIONS=6` 与 `_budgets`/`_check_budget`：按事件累计的尝试计数（拒绝类不计入），分别拒绝 `attempt_budget_exhausted` / `install_budget_exhausted` / `regenerate_budget_exhausted`，总预算超限时把升级方向标为 `human`。
- `_regenerate_units`、`_request_code_repair`、`apply_repair`、带策略的 `resume`、`install_code_repair` 在写入前检查预算；安装成功记录一次 `kind=install, outcome=installed`，局部重生成记录 `scheduled`。
- `diagnose` 的 `packet.budgets` 返回已用/上限；运维提示词明确“被预算拒绝时不得改名重试”。

### 实机演练（发布还原链路）

在一份 `app/` 临时副本上做了真实进程演练（不触碰任何用户批次）：写入一个故意语法错误的 `workflow/supervisor.py` 并按其指纹生成安装 journal 与 `installed` 标记，用真实 `python -m workbench.server` 启动并轮询 `/api/health`：

- 第一次启动真实失败（进程退出码 1）；
- 协调器调用 `restoreRelease` 还原旧文件，第二次启动健康；
- 结果：`restart attempts = [false, true]`、`state = rolled_back`、标记已清除、`supervisor.py` 哈希恢复为原值。

这验证了“坏发布不会被第二次重启掩盖，旧代码能恢复服务”，但仍是副本演练；真实批次的完整链路尚未执行。

### 交人工出口

- `finish` 新增 `needs_human`：预算用尽或需要人决策时使用，事件保留证据并置为 `needs_human`、写入 `escalation.kind=human`、不再自动接手；后端复验为 `verified=false`，界面显示“需人工处理”，不会显示为已恢复。
- 运维工具与提示词同步：`finish` 的 outcome 枚举、终态集合与提示词都包含 `needs_human`。
- 事件结束时修复记录随处置落账（`verified` / `needs_human` / `failed`），不再长期停留在 `installed_pending_restart`；落账只表示处置结论，仍以复验结果为准。

### 文档

- `app/docs/harness-operations.md` 增加故障证据包、重复失败升级、隔离代码修复、局部重生成四节，并补充对应验证要求；明确该工作区不是操作系统沙箱，JavaScript 与配置只产出补丁，安装必须由受控宿主暂停监听后单次重启完成。

## 没改什么 / 边界

- 不自动上传、不自动发布、不确认暂存；`upload_gate / upload / publish_gate` 行为不变。
- 不改写归档或历史批次；隔离工作区基于临时目录与副本，安装只作用于被允许的运行源码。
- 未新增 shell 权限；运维会话仍只能调用注册的运维动作。
- `lib/*.js`、配置、数据结构不在自动发布范围；宿主已能暂停监听并单次重启，但重启后健康失败不会自动回滚（只留下 `failed` 标记），需人工处理。
- 内容阶段的「局部重生成」只缩小重跑范围（`regenerate_units`，限答案/解析/题型/知识点/听力，按确认题号或证据单元），不降低任何校验，也不改动上传边界；`self_heal.py` 属于锁定文件，运行时隔离修复无法改动它。
- 生产安装默认不可用（未设置 `MD_OPERATIONS_INSTALL=1` 时直接拒绝）；测试中所有安装均指向假源码根。

## 如何验证

- `python -m unittest workflow.test_operations workflow.test_operations_development workflow.test_self_heal` → **Ran 71 tests**，70 通过；唯一失败 `test_read_source_and_redact_credentials` 是临时副本缺少仓库级 `lib/` 源文件导致的副本问题（直接在工作区运行为本机 `worker_busy` 锁，不终止用户进程）。
  - 新增 `FastRepairOperationsTests` 10 项：证据包内容与下游阶段；同证据重复修复被拒并升级；代码指纹变化后续跑允许；代码修复全生命周期（工作区隔离、清单外读取拒绝、测试、计划、未开启开关拒绝安装、开启后安装仅待重启、发布标记 `installed` 与文件清单）；未确认安装被拒；局部重生成只带指定题号、manifest 回到待执行、重复请求返回 `already_scheduled`；未知题号与上传/非内容阶段被拒。
  - `test_operations_development` 14 项覆盖路径逃逸/符号链接、dirty 捕获、CAS 冲突、回滚与测试指纹。
- `node --test backend/operations-agent.test.cjs` → **36 项通过**，其中新增 3 项：四个新工具受事件级 guard 约束且原样转发输入；安装返回 `restart_required` 后结束尝试、发出 `awaiting_restart`、以等待重启释放，不能再验证或报告 resolved。
- `node --test backend/release-coordinator.test.cjs backend/code-restore.test.cjs` → **9 项通过**：`prepared` 暂停监听且不重启；`installed` 只重启一次、健康后删除标记；重启失败还原旧文件后只再启动一次；还原失败写 `failed` 且不循环；超时 `prepared` 与 `rolled_back` 不会永久暂停；还原拒绝覆盖已再次修改的文件、拒绝越界路径、删除新增文件。
- `node --test backend/operations-agent.test.cjs backend/release-coordinator.test.cjs backend/code-restore.test.cjs` → **45 项通过**（36 + 9）。
- `python -m py_compile workflow/operations.py workflow/operations_development.py workflow/self_heal.py workflow/test_operations.py workflow/test_operations_development.py` → OK；`node --check lib/index.js lib/release-coordinator.js` → OK。
- 受影响目录测试（operations/self_heal/contracts/manifest/supervisor）中，与本改动相关的用例通过；其余失败为本机既有环境问题（Windows 默认 GBK 解码 `read_text()`、OCR/模型配置缺失），与本次改动无关。
- `node --test backend/*.test.cjs` 全量 164 项中 146 通过；失败集中在 `backend-lifecycle`（8）与 `operations-http`（2，日志 `Python exited (9009/null)`：本机 shell 没有可直接执行的 `python`）、`model-router`（5，工作区未提交的模型配置改动）与 `review-layout`（2，`lib/client.js` 未提交的样式改动）；`operations-agent` 36/36 通过。均与本次改动无关。
- 未执行真实用户批次、未调用真实模型、未上传。

## 回退方式

删除 `app/workflow/operations_development.py`，移除 `operations.py` 中 `request_code_repair` / `code_repair` / `install_code_repair` / `regenerate_units` 分支及相关辅助函数，回退 `self_heal._feedback` 的可选参数、两份测试与文档即可；没有数据迁移，也没有对外接口格式变化。

## 未决点

1. 宿主侧独立 developer 会话（cwd 指向冻结副本、独立模型与工具限制）尚未实现；当前由运维会话直接持有受限工作区工具。
2. 发布协调器已能在重启不健康时还原旧代码并启动一次；但真实批次上的「安装—重启—重新认领—原检查复验—续跑」端到端演练尚未执行。
3. 跨轮次预算已加（24 次尝试 / 2 次安装 / 6 次局部重生成），但预算用尽后只有“交人工”状态，没有人工工单或通知出口。
