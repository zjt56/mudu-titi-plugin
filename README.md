# 录题工作流 · 插件版（独立项目）

把录题工作台与上传工作流嵌入 DSH 的原生插件。**独立运行**：自带 `app/` 工作流与后端，试卷数据保存在本项目目录下（`workflows/`、`inbox/`），不依赖任何外部仓库。模型路由通过 `MUDU_AGENT_CONFIG` + 插件自有 `wf-router.sh` 注入实现。

## 数据目录

- 运行时试卷数据固定存放在本项目目录：`workflows/`（批次）、`inbox/`（上传暂存）、`.workbench-trash/`（可恢复归档）。
- 三者已在 `.gitignore` 忽略，不随代码提交。
- 确需改位置时，显式设置 `WF_DATA_DIR` 才生效（否则一律使用项目目录本身，不受继承的 `WF_WORKFLOWS_DIR`/`WF_INBOX_DIR` 影响）。

## 能力

- **选择 harness 内模型（含第三方）**：面板下拉列出 base profile 里的全部模型（DeepSeek 官方 `deepseek-flash`/`deepseek-v4-pro`、Magpie 中转 GPT 系列、Codex 系列）。
- **分别设置三审模型**：一审(labels)/二审(knowledge)/三审(sample) 可各自指定；默认统一走 `deepseek-v4-pro`。
- **自定义内容**：可编辑三审提示词、各阶段补充、审核口径（`config/prompts/*.md`、`config/review-policy.json`）。

## 原理（非侵入）

工作流（`runner`/`auto`/`review_rounds`）通过 `load_config()` 读 `MUDU_AGENT_CONFIG` 指定的配置。
本插件生成 `backend/.local-agent.effective.json`（`provider=command` → `wf-router.sh`），
由 `wf-router.sh` 读 `config/model-router.json`：

1. 从提示词头部识别 `Stage: <name>` 与（审核时）`ROUND: <labels|knowledge|sample>`；
2. 按路由器选出 provider/model；
3. 复制 base profile 为一次性临时 profile，仅改 `agent-default-model`；
4. 注入对应 provider 凭证（`DEEPSEEK_API_KEY` / `MAGPIE_GATEWAY_KEY`，从 `~/.dsh/.credentials.yaml` 读）；
5. 调 `dsh --profile <临时> -`，返回结果后删除临时 profile。

## 目录

```
插件版/
├── lib/index.js        宿主：配置读写、模型清单、REST /wf-plugin/*
├── lib/client.js       客户端：原生面板（main + 侧边栏 + 设置）
├── backend/wf-router.sh    模型路由 wrapper（按 Stage/ROUND 选模型）
├── backend/local-agent.plugin.json   插件生效配置模板
├── config/model-router.json   模型路由（可改）
├── config/review-policy.json  审核口径（可改）
├── config/prompts/*.md    提示词（可改）
├── locale/ icon.svg
```

## 安装

在 DSH 里用 `plugin_manager`（install_bundle，目标为 `插件版/` 绝对路径），或：

```bash
dsh plugin --profile wf-deepseek add /path/to/插件版
```

安装后重启 Harness，侧边栏出现「录题工作流」入口，打开即面板。

## 注意

- 临时 profile 从 `wf-deepseek`/`wf-deepseek-fast` 复制，所以**可选模型以这两份 profile 为准**；新增模型要同步加到 profile。
- 凭证只从 `~/.dsh/.credentials.yaml` 读取注入，不落盘、不打印。
