# 快速安装与启动

这份文档只回答一件事：**怎么在 5 分钟内把工作流跑起来**。分四种用法：

1. 在智能体里接手任务；
2. 在本机网页端使用 / 打包给别人；
3. 让工作流自动调用本机 Agent 的大模型；
4. 对接云端题库 API（上传暂存）。

更细的规则见 [docs/README.md](README.md) 的索引，不在本篇重复。

---

## 0. 前提

| 项 | 要求 | 说明 |
| --- | --- | --- |
| Python | 3.9+，建议 3.12 | 核心流程零第三方依赖 |
| 系统 | macOS / Linux / Windows | 命令里 `python3` 在 Windows 换成 `python` |
| 可选 | ffmpeg + faster-whisper | 只有听力切段/转写需要 |
| 可选 | 本地 OCR 服务 | 只有扫描版 PDF 需要，见 [extraction.md](extraction.md) |

所有命令都在**仓库根目录**执行（有 `AGENTS.md`、`workflow/` 的那一层）。

```bash
# 自检：环境、规则、契约、闸门、批次；无输出即正常
python3 -m workflow.doctor
```

---

## 1. 在智能体里快速启动

适用于 DeepSeek Harness（DSH）、WorkBuddy、Codex、Cursor、Claude Code 等**能读文件、能执行命令**的智能体。不需要安装任何厂商 SDK。

### 1.1 把仓库交给智能体

把整个目录（或下面第 3 节的压缩包）放到本机，对智能体说：

> 按 AGENTS.md 接手这个录题工作流，先看 `workflows/<批次>` 现在停在哪个阶段。

智能体会自己读 `AGENTS.md` → `docs/agent-interface.md`，然后运行：

```bash
python3 -m workflow.agent next --batch-dir workflows/<批次>
```

`next` 会返回四种动作之一：

| action | 含义 | 智能体该做什么 |
| --- | --- | --- |
| `exported` | 已导出任务包 | 读包内 4 个文件，生成 `result.json`，再交回 |
| `run_workflow` | 该跑确定性脚本/闸门 | `python3 -m workflow.runner run --batch-dir workflows/<批次> --stage <阶段>` |
| `human` | 需要人确认（上传/发布） | 停下来，交给人工 |
| `blocked` | 上游或证据不足 | 看原因，补齐或报告 |

### 1.2 领取任务 → 交回结果

```bash
# 1) 导出（next 已自动导出时跳过）
python3 -m workflow.agent export --batch-dir workflows/<批次> --stage types

# 2) 让智能体读任务包
#    workflows/<批次>/workflow/agent-tasks/<task_id>/
#      README.md  task.json  response.schema.json  response.example.json
#    task.json 的 prompt 已带原卷文字、上游产物、目录和规则

# 3) 把 result.json 交回（task_id 必须原样使用）
python3 -m workflow.agent import --batch-dir workflows/<批次> \
  --task-id <task_id> --result <result.json>
```

只有 `import` 返回 `"completed": true` 才算该阶段通过程序校验并登记；返回 `needs_review` 表示证据不足，阶段不会假装完成。

### 1.3 智能体不能做的事

结构闸门、审核放行、上传确认、发布确认都是闸门/人工阶段：智能体只能准备证据和候选，**不能代签通过**。

---

## 2. 在本机网页端启动

### 2.1 起审核台

```bash
python3 -m workbench.server --port 8765
```

打开 <http://127.0.0.1:8765>：

- **首页**：上传原卷（docx/pdf，可附答案卷、听力 mp3）→ 自动建批次并开始提取；
- **试卷审核**：左栏选批次，中栏按单元列题，右侧左边看原卷预览、右边处理待人工问题；
- 题目列表可用 `↑`/`↓` 或 `k`/`j` 切换。

上传后的后台推进规则：

- 提取、结构闸门、体检等确定性阶段自动跑；
- 代理阶段（答案/听力/题型/解析/知识点/装配/审核）有两种走法：
  - 配了本机 Agent（第 4 节）→ 自动调用；
  - 没配 → 导出任务包等待接单（第 1 节）。

### 2.2 命令行快速跑一遍

```bash
python3 -m workflow.auto scan --inbox inbox          # 看收件箱能识别哪些批次
python3 -m workflow.auto run  --inbox inbox --once   # 推进一轮，遇闸门即停
python3 -m workflow.runner status --batch-dir workflows/<批次>
```

进度汇总在 `workflows/_queue.json`：每条批次停在哪个阶段、为什么停。

### 2.3 打包给其他用户

只打包**代码与规则**，不要带 `inbox/`、`workflows/`、`.cache/`、`.workbench-trash/`、`deliverables/` 和本机配置（含真实试卷与本机路径）。

```bash
# 在仓库根目录执行
zip -r exam-workflow.zip \
  workflow workbench docs references scripts skills-reference \
  AGENTS.md README.md .gitignore
```

对方拿到压缩包后：

```bash
unzip exam-workflow.zip -d exam-workflow
cd exam-workflow
python3 -m workbench.server --port 8765
# 浏览器打开 http://127.0.0.1:8765，上传自己的试卷即可
```

> 如果要连自己的大模型或题库 API，再按第 4、5 节建本机配置；示例配置随包提供，不会带任何人的密钥。

---

## 3. 让工作流自动调用本机 Agent 的大模型

目标：网页/自动推进时，代理阶段由本机已登录的 CLI（DSH、Codex 或任意命令）执行，而不是等人接单。

### 3.1 建配置

```bash
cp workflow/local-agent.example.json workflow/local-agent.json
```

按你的 CLI 选一种：

**A. DeepSeek Harness（DSH）**

```json
{
  "provider": "dsh",
  "executable": "/absolute/path/to/dsh",
  "timeout_seconds": 900,
  "max_input_chars": 600000
}
```

**B. Codex**

```json
{
  "provider": "codex",
  "executable": "/absolute/path/to/codex",
  "model": "gpt-5-codex",
  "timeout_seconds": 900,
  "max_input_chars": 600000
}
```

**C. 任意命令（参数数组，不走 shell）**

```json
{
  "provider": "command",
  "command": ["my-agent", "--prompt-file", "{prompt_file}", "--response-file", "{response_file}"],
  "input_mode": "none",
  "response_mode": "file",
  "timeout_seconds": 900,
  "max_input_chars": 180000
}
```

字段说明：

| 字段 | 作用 |
| --- | --- |
| `provider` | `dsh` / `codex` / `command` |
| `executable` | dsh/codex 的可执行文件或 PATH 命令 |
| `command` | command 模式的参数数组，支持 `{prompt_file}`、`{response_file}`、`{work_dir}`、`{python}` |
| `input_mode` | `stdin`（默认）或 `none`（配合 `{prompt_file}`） |
| `response_mode` | `stdout`（默认）或 `file`（配合 `{response_file}`） |
| `timeout_seconds` | 单次调用超时，最大 1500 |
| `max_input_chars` | 单次任务字符上限；支持的阶段按单元分片，仍超限时明确报错，不截断 |
| `model` | 仅 Codex 可用 |

也可以用环境变量指向别处的配置：

```bash
export MUDU_AGENT_CONFIG=/path/to/another-config.json
```

### 3.2 验证

```bash
python3 -m workflow.local_agent --check                 # 只查配置和可执行文件，不调用模型
python3 -m workflow.local_agent --stage types \
  --batch-dir workflows/<批次> --dry-run                # 只打包任务，不调用模型
```

`--dry-run` 会把完整提示词写到 `workflow/agent-runs/<阶段>-<编号>/input.txt`，可先确认内容。

### 3.3 正式执行

```bash
# 单阶段（经过 runner 才会登记台账）
python3 -m workflow.runner run --batch-dir workflows/<批次> --stage types

# 或整体自动推进
python3 -m workflow.auto run --inbox inbox --once
```

说明：

- 装配和初审默认按最多 15 个单元分片；原卷和规则完整保留，单片仍超过 `max_input_chars` 时会明确报错；
- 可在本机配置设置 `"candidate_cache": true`，复用输入与提示均匹配的非审核候选；仍需重新校验。审核结果不会缓存；
- 调用失败会保留 `workflow/agent-runs/<阶段>-<编号>/` 下的输入、输出、错误日志；
- **协议/校验失败会自动重试一次**，并把上次错误附在提示末尾（`retries` 默认 1，可用 `retry_feedback_chars` 调整反馈长度）；
- **额度/限流（429、DAILY_LIMIT_EXCEEDED）会立即熔断**，不再逐轮重试；批次会标明“代理额度用尽”，恢复额度后重跑同一命令即可继续；
- 代理仍不能批准结构/审核/上传/发布闸门。

可选：需要给不同阶段配不同命令时，复制 `workflow/agent-bridge.example.json` 为 `workflow/agent-bridge.json` 覆盖默认入口。

---

## 4. 对接云端题库 API（上传暂存）

上传只创建**暂存**（`pending_confirmation`），远端人工确认后才生成草稿；本流程不会自动发布。完整规则见 [upload-api.md](upload-api.md)。

### 4.1 建安全连接

API 默认地址是 `http://127.0.0.1:8089/api`，由你自己建立 SSH 隧道（占位示例）：

```bash
ssh -N -L 127.0.0.1:8089:127.0.0.1:89 \
  -i <本机私钥文件> -o IdentitiesOnly=yes \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=60 \
  <SSH用户>@<SSH主机>

# 另一个终端只读检查
curl -fsS http://127.0.0.1:8089/api/health
```

### 4.2 建上传配置

```bash
cp workflow/upload-config.example.json workflow/upload-config.json
```

```json
{
  "base_url": "http://127.0.0.1:8089/api",
  "api_key_env": "MUDU_API_KEY",
  "timeout_seconds": 30,
  "subject_id": "english",
  "max_response_bytes": 8388608
}
```

- 只写**环境变量名**，不把密钥写进文件；
- 密钥在当前终端注入：`export MUDU_API_KEY=...`（不要写进命令历史或仓库）；
- 也可用 `MUDU_UPLOAD_CONFIG` 指向别的配置文件；
- 需要指定知识点版本时，在配置里加 `"knowledge_version_id": "..."`。

### 4.3 四步提交

```bash
# 1) 读取远端目录、核验媒体并冻结请求（不提交题目）
python3 -m workflow.runner prepare-upload --batch-dir workflows/<批次> \
  --upload-mode source --upload-target 中考真题 --name <批次名称>

# 2) 人工确认这一份冻结文件与目标（真实授权后才执行）
python3 -m workflow.runner mark --batch-dir workflows/<批次> \
  --stage upload_gate --status approved --actor <操作标识>

# 3) 只核对本地冻结与批准绑定，不联网提交
python3 -m workflow.runner upload --batch-dir workflows/<批次> --dry-run

# 4) 明确写入上传暂存
python3 -m workflow.runner upload --batch-dir workflows/<批次> --yes
```

产物：

| 文件 | 用途 |
| --- | --- |
| `07_upload/preflight.json` | 本地到 API 的只读校验结果、缺项与警告 |
| `07_upload/plan.json` | 源文件摘要、冻结请求摘要、目录摘要、目标与媒体检查 |
| `07_upload/frozen/<sha256>/payload.json` | 实际发送的冻结字节 |
| `07_upload/receipt.json` | 远端暂存 ID、`pending_confirmation` 与提交时间 |

### 4.4 必须知道的边界

- **媒体是独立部署步骤**：文档没有媒体上传 HTTP API，图片/音频需按受控运维流程部署到站的 `static/source-media/<批次>/`，题目再以 `attachments` 引用；未部署的本地路径不会被自动替换成 URL。
- 超时、连接中断、服务端 5xx 属于“结果未知”，**不要自动重发**；到远端工作台核实后再处理本地记录。
- 401 查凭据、403 查权限、409 查冲突、413 拆批次重新审核；不要靠重试绕过。
- 上传前必须已有真实审核记录与审核闸门结果，不能用候选或空队列冒充。

---

## 5. 最小检查清单

```bash
python3 -m workflow.doctor                                   # 环境/规则/契约
python3 -m workflow.auto scan --inbox inbox                  # 批次识别
python3 -m workflow.runner status --batch-dir workflows/<批次>  # 当前阶段
python3 -m workbench.server --port 8765                      # 网页端
```

常见问题：

| 现象 | 先查什么 |
| --- | --- |
| 网页上传后不动 | 是否停在代理阶段：配了 `local-agent.json` 吗？没配就去 `workflow/agent-tasks/` 领任务 |
| 代理调用失败 | `workflow/agent-runs/<阶段>-<编号>/stderr.log`；配额/登录/超时 |
| 超过输入上限 | 调大 `max_input_chars` 或拆批次；不要截断原卷 |
| 上传被拒 | `07_upload/preflight.json` 的缺项；媒体是否已部署 |
| 审核闸门不过 | `07_review/manual-review-queue.json` 里未处理的问题 |

---

## 6. 相关文档

| 文档 | 内容 |
| --- | --- |
| [agent-interface.md](agent-interface.md) | 通用任务包、结果协议、任何 CLI 适配 |
| [local-agent.md](local-agent.md) | 本机 Agent 配置、日志与平台限制 |
| [pipeline.md](pipeline.md) | 16 个阶段、闸门与自动推进 |
| [upload-api.md](upload-api.md) | 上传目录、冻结/批准/暂存与媒体边界 |
| [workflow-regressions.md](workflow-regressions.md) | 切题、来源、审核的强制回归约束 |
