# 本地 Agent 阶段执行

工作流通过可替换的本机 Agent CLI 适配器执行代理阶段，支持任意参数数组命令，以及 DSH headless、Codex exec 的便捷适配。新安装默认走[通用文件交接接口](agent-interface.md)，不要求安装任何指定厂商的 CLI；本文描述可选的自动调用方式。复用 CLI 自己的登录和模型配置，不读取或复制密钥；“本地 Agent”指本机 CLI，是否远程推理由该 CLI 的模型配置决定。

## 执行关系

1. 工作流按阶段契约选择就绪阶段。
2. `agent_tasks.build_task` 准备原卷文字、已确认单元、上游产物、机器目录和规则摘要，记录输入哈希。
3. `local_agent` 在独立执行目录启动本地 CLI，通过 stdin 交付任务，要求返回 JSON。
4. 导入前再次核对输入哈希，再由阶段校验器检查允许的文件路径、目录值、单元/子题覆盖和产物结构。
5. 仅校验通过的结果写入正式阶段文件；调度器确认文件存在后记录完成状态及哈希。
6. 缺证据返回 `needs_review`（退出码 3）；协议或校验错误可按配置附带反馈重试。失败和待复核不会伪装为完成，不影响其他可独立执行的阶段。

模型不直接负责更新 manifest，也不能批准结构闸门、审核放行、人工上传或发布。执行器保留原始答复与日志，不能把模型说“完成”当作阶段已完成。

自动推进在使用默认代理桥且已配置本机 Agent 时，会把同时就绪的 `answers`、`audio`、`types` 模型调用并行执行（它们都只依赖 `structure_gate`，互不依赖）。任务导出、结果校验与导入仍在批次锁内串行完成，导入前重新核对输入哈希与上游状态。没有配置本机 Agent 时仍导出任务包。

结构闸门、体检（validate）、审核闸门都会先在私有目录计算，再在锁内重读台账、核对原卷/审核证据未变化后才提交；期间有并发写入或证据变化时拒绝发布过期结论，而不是覆盖新状态。`mark`、`invalidate` 等人工命令与闸门共用同一独占锁。

同一批次的多个自动推进进程会互斥。自动推进期间，不要在其它进程里直接改 `manifest.json` 或手工写入批次产物：这些非协作写入不在锁内，无法保证并发安全。不同批次可各自推进，仍受本机 Agent 锁约束（POSIX 并行调用用共享锁，Windows 仍独占，故 Windows 会串行）。

## 本机配置

复制 `workflow/local-agent.example.json` 为 `workflow/local-agent.json`，按需填写：

```json
{
  "provider": "dsh",
  "executable": "/path/to/dsh",
  "timeout_seconds": 900,
  "max_input_chars": 180000,
  "retries": 1,
  "retry_feedback_chars": 1200
}
```

`retries` 是“协议/校验失败”的带反馈重试次数（默认 1）：错误会附在下次提示末尾，不会重跑语义判定本身。额度/限流（429、DAILY_LIMIT_EXCEEDED）、超时与启动失败不重试；其中限流/额度交给 `auto run` 进入冷却退避（见下文），其余直接停下并在批次里标明原因。

`provider` 可取 `command`、`dsh` 或 `codex`。通用 `command` 使用参数数组及 stdin/stdout 或文件传输，完整示例见[通用接口](agent-interface.md#可选任意-cli-自动接入)。`executable` 用于 dsh/codex，可写绝对路径或 PATH 中的命令。Codex 可额外指定 `model`；DSH 使用自身 headless 配置。也可以通过 `MUDU_AGENT_CONFIG` 指向另一份配置。

代理桥：复制 `workflow/agent-bridge.example.json` 为 `workflow/agent-bridge.json`。七个代理阶段（answers、audio、types、explanations、knowledge、assemble、review）都按同样方式调用：

```json
["{python}", "-m", "workflow.agent", "run", "--stage", "types", "--batch-dir", "{batch_dir}"]
```

`{python}` 自动替换为当前工作流 Python，避免把某台电脑的解释器路径写进可迁移配置。原卷 `extract` 已有原生/OCR 复合执行器，不需要经过 Agent。

两份实际配置均被 Git 忽略。迁移电脑需要安装并登录所选 CLI，再填写本地配置；规则文档、目录和适配代码随仓库迁移。

## 调用与排查

```bash
# 只检查配置和可执行程序，不调用模型
python -m workflow.local_agent --check

# 打包任务，不调用模型、不导入候选
python -m workflow.local_agent --stage types --batch-dir workflows/<批次> --dry-run

# 执行一个阶段并按台账登记（通常使用这个命令）
python -m workflow.runner run --batch-dir workflows/<批次> --stage types

# 自动推进可执行阶段，保留闸门和待复核问题
python -m workflow.auto run --inbox inbox --once
```

网页上传后的后台推进与单步 runner 共用同一份代理桥。`workflow.local_agent` 直接调用可用于诊断，但本身不把 manifest 阶段标记为完成；生产推进应经过 runner 或自动流程。

每次独立调用保存在批次 `workflow/agent-runs/<阶段>-<唯一编号>/`，包括输入、stdout/stderr、结构化答复、导入报告和调用元信息。主调度日志在 `workflow/logs/<阶段>.log`。这些文件可能含真实试卷内容，与批次一同排除于版本控制。

装配默认每片最多 8 个单元、审核每片最多 15 个单元；可用 `"assemble_part_units"` / `"review_part_units"` 分别调整；设置 `"unit_split": false` 可关闭。分片按单元裁剪上游产物，但不再让每片重背整份上下文：原卷提取只保留表格、图片、警告与溯源（重复正文与逐段版式块剔除），装配片不再附知识点目录与打标规则（标签合法性由导入器与 `label_audit` 程序校验），审核片仍保留目录以核对叶子。全部通过覆盖检查后合并并进行整批校验。若单个单元连同必要来源仍超过上限，明确报错，不截断原卷或规则。独立审核各轮也按目标分片，保留每片执行记录并支持从已接收分片继续；每轮仍须分别执行，不能复用其他轮的模型结果。

分片并发：`"part_concurrency"`（默认 1；上限 64）控制同一阶段内分片的并行调用数，`"review_part_parallel"` 单独控制审核。官方 API 支持高并发，真正的限制是本机同时运行的 dsh 进程与内存；插件生效配置默认 16。分片之间没有依赖，只有合并需要按原顺序，因此并发只压缩墙钟，不改变产物语义；单片校验、快照落盘与整批导入规则不变。

分片级断点续跑：每个分片通过本地校验后，其候选答复按“精确提示 + 单元集合 + 输入摘要 + 模型配置”指纹存入 `workflow/agent-runs/<阶段>-parts/`。该阶段重跑时，指纹未变的成功分片直接复用，只重新调用失败或尚未产出的分片；任一上游输入或模型路由变化都会使旧分片失效。复用前仍重跑 `validate_part`，不把未校验结果当成品。

组装确定性重跑：每次组装被接受后，`assemble_local` 在 `06_batch/assemble-guard.json` 登记结构确认与批次产物的指纹。之后重跑组装时，只有“结构未变、批次未被手工改动、确认单元集合一致”才走确定性路径：题干、选项、材料、表格与子题结构沿用已审核批次，仅用最新四路产物覆盖题型、子类、词数、答案、难度、解析、大意和知识标签，**零模型调用**。产物仍须通过 `accept_result` 的整批校验才安装；校验不过或守卫不满足都自动回落模型组装，并在 `06_batch/deterministic-fallback.json` 留下原因。首次组装（无基线）始终走模型。旧批次可用 `python -m workflow.assemble_local --batch-dir <批次> --seed` 补登记基线，登记前逐题核对单元、题号与每个选项都能回指当前结构，核对不过不登记；`--check` 只读报告确定性结果与已接受批次的字段差异。

限流/额度退避：`classify_failure` 识别的 429/`DAILY_LIMIT_EXCEEDED`/quota 属可恢复失败，不是判死。`auto run` 撞到后在该批次登记冷却（`workflow/agent-runs/.rate-limit-cooldown`，默认 300s，可用 `--rate-limit-backoff` 调整），冷却期内本轮不再调用模型，常驻循环睡到最早冷却到期再扫，避免空转反复撞同一限额；一旦有阶段成功推进即清除冷却标记。`scripts/bin/wf-agent.sh` 在限流时会自动切换到备用 profile 重试一次，两条通道都受限才按 rate_limit 交回；这是**独立 CLI** 的旧行为。插件面板使用 `wf-router.mjs`，不做这种切换：选中哪个模型就只调用哪个，路由缺失时直接报错。其它失败（解析、崩溃）不盲目重跑，仍按原规则处理。

可选 provider 的范围：`wf-router.mjs` 生成 headless 路由 profile 时，只复制 base profile 的 `llm-pi-ai` / `llm-deepseek` / `llm-deepseek-account` 条目。桌面端启用、但 profile 没有声明的 provider（例如 WorkBuddy 的 `magpie` 中转）在该 profile 里没有适配器，选中会 `NO_ADAPTER`。因此设置页的模型列表只列路由真正能服务的 provider；`wf-router` 调用前也会校验，不在列表里就带可选值直接报错，不会静默换成默认模型。要在工作流里用某个 provider，先在 profile 的 `llm-pi-ai.providers` 里声明它。

并行模型调用使用共享锁，普通调用使用独占锁，两者互斥；因此并行只在 POSIX 生效，Windows 串行。POSIX 超时取消进程组，Windows 当前只能可靠终止直接子进程。

可选设置 `"candidate_cache": true` 启用本地非审核候选缓存（当前仅 POSIX）；缓存绑定完整提示、输入摘要和本机模型配置，命中仍重新检查与导入。审核阶段不复用模型结果。模板缓存仅在当前进程内有效，缓存文件可能含试卷内容，应随本地批次保管。模型配置可能由 CLI 在配置文件之外改变，更换模型或规则时应清理缓存或关闭复用。

## 各阶段完成条件

| 阶段 | 输入与输出 | 关键校验 |
| --- | --- | --- |
| answers | 单独答案卷的提取全文或原卷附录 → `01_source_ans/answers.json` | 题号覆盖、非空答案、来源定位；本阶段只提取，不自行猜答案 |
| audio | 已切分音频及核对证据 → `02_audio/audio_map.json` | 切片必须真实存在，时间戳候选不算切分完成；缺证据则待处理 |
| types | 确认单元及原卷 → `03_types/types.json` | 单元 ID、27 类主类、固定子类与完整覆盖 |
| knowledge | 题型、答案、原文、知识点规则 → `04_knowledge/knowledge.json` | 目录叶子、组级/子题维度、答案来源与 AI 内容标记 |
| assemble | 已验证上游产物 → `06_batch/batch.json` | 单元/子题覆盖、严格标签校验；保留原文结构和媒体引用 |
| review | 最终批次及证据 → `07_review/agent-review.json`，再转换审核文件与人工队列 | 审核覆盖、发现定位、严重级别；保留既有人工处理记录，不生成虚假的独立审核签名 |

单独上传的 DOCX/PDF 答案卷会先复用复合提取，保存完整原始答案文字，避免把答案卷内部重复标题后的部分漏掉。原卷和附录存在歧义时仍须核验对应版本与题号；提取出的来源位置只是可追溯线索，不等于答案语义正确的自动证明。

## 边界与未贯通部分

- DSH 调用使用独立工作目录和“只返回 JSON、不调用工具”的任务约束；这不是操作系统沙箱。Codex 适配使用 `read-only` 沙箱。导入器只允许指定阶段的白名单文件，不能凭响应路径写 manifest 或项目代码。
- Agent 只看到随任务附带的文字与元数据，不能由此声称已看过未附带的图片、听过音频或做过独立的一审/二审复核。
- 音频切分和逐段回听尚未由本适配器执行；没有真实切片证据时会停住。
- 原卷结构未通过时，下游分类、打标、组装仍应等待结构闸门。Agent 不会自动抹掉 OCR 复核和结构歧义。
- 难度、后置画线以及其他外部体检脚本缺失、独立复核记录未配置、上传冻结机制未完成等问题，仍按原流程阻断。本次接入不将这些状态伪装为完成。

## 测试

```bash
python -m unittest workflow.test_local_agent workflow.test_agent_tasks workflow.test_agent_dispatch workflow.test_agent_prepare
python -m unittest discover -s workflow -p 'test_*.py'
```

测试使用模拟 CLI 或临时小批次验证调度、超时、JSON、路径、哈希与产物规则。真实 CLI 连通性和实际题型阶段另行验证，不把模拟测试说成真实模型执行。
