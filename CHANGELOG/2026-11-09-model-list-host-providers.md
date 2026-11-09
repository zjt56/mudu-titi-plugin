# 2026-11-09 模型列表：把宿主实际可用的 provider 也列出来

> **后续修订（2026-11-09 晚）**：本记录基于「`wf-router.sh` 会把 desktop 里缺失的 provider
> 合入路由 profile」这一旧前提。现在插件走 `backend/wf-router.mjs`，headless 路由 profile
> **只复制 base profile 的 `llm-pi-ai` / `llm-deepseek` 条目**，宿主独有的中转 provider
> （WorkBuddy `magpie`、账号目录等）不会被复制，选中它们会直接 `NO_ADAPTER`。因此模型列表
> 已改为只列「路由 profile 能真正服务」的 provider，详见
> [2026-11-09-router-provider-guard.md](2026-11-09-router-provider-guard.md)。

## 背景

用户反馈（原话）：「本地里有这么多模型，他都没选择映射出来」——DSH 设置→模型页里能看到 `DeepSeek 账号`、`第三方`、`opencode-go` 等 provider 及其模型目录，但插件「设置 → 大模型配置」的下拉只有 5 条（opencode-go/deepseek-v4.1-flash、openai/gpt-6.1-sol、openai/gpt-6.1-terra、openai/gpt-6-astra、deepseek-official/deepseek-flash）。

根因在 `lib/index.js::collectModels`：模型清单只来自 `parseProfileModels('wf-deepseek')`（本机不存在，退回 `desktop`）对 `cordis.patch.yml` 的手写正则解析，而该解析只认两个块：

- `- id: llm-pi-ai` 下的 `providers.<id>.models[].id`；
- `- id: llm-deepseek` 下的 `models[].id`。

`- id: dsh-connect-workbuddy`（账号目录，`lastCatalog` 里 20 个模型、`enabledModelIds`/`offModelIds`）以及 `deepseek-account` 这类 provider 完全没被读；`ctx.llm.listProviders()/listModels()` 当时只用来补显示名，不新增条目。

## 改了什么

`lib/index.js::collectModels`：

- 枚举 `ctx.llm.listProviders()` → 逐个 `ctx.llm.listModels(provider)`，把宿主真正注册的 `provider|model` 收进 `host`，同时继续用它们补 `nameMap`（显示名）。
- `detected = mergeDetectedModels(detected, host)`：profile 里没声明的 provider（workbuddy / deepseek-account 等）整组补进来；profile 已声明的 provider 仍只保留 profile 里的模型（路由 profile 不会新增未声明的 model，列出来只会点不动）。
- 宿主 API 不可用（`ctx.llm` 缺失或抛错）时行为与改动前一致：只用 profile 解析结果，不报错、不静默换模型。

`mergeDetectedModels` 的去重/同 provider 规则未改，只把数据源从「profile overlay」扩成「profile overlay + 宿主注册表」。

## 没改什么 / 边界

- 不改 `config/model-router.json`、不改 `backend/route_pick.py` 的优先级（review 轮次 > roles > stages > default），不改任何阶段实际用哪个模型。
- 不自动给阶段分配模型：仍然是「默认模型 + 7 阶段 + 2 轮审核」全部可留「跟随默认」。
- 不改 `backend/wf-router.sh` 的 profile 合入逻辑（`merge-route-providers.py` 只补缺失 provider，不改已有 provider 的模型集合），所以列出来的条目都能落到真实路由上。
- 不保证账号目录里 `offModelIds` 里的模型可用：宿主是否接受由宿主决定，失败会如实报错，不做静默兜底（硬规则 8）。

## 如何验证

- `node --check lib/index.js` → 通过。
- 用真实 `~/.dsh/profiles/desktop/cordis.patch.yml` 抽出 `parseProfileModels`/`mergeDetectedModels`/`collectModels` 跑一次性脚本：
  - `parseProfileModels('wf-deepseek')` = 0 条（本机无该 profile）；
  - `parseProfileModels('desktop')` = 5 条，与线上 `GET /wf-plugin/models` 完全一致；
  - 注入假 `ctx.llm`（opencode-go / openai / deepseek-official / workbuddy / deepseek-account）后 `collectModels` = 8 条：保留原 5 条，新增 `workbuddy/deepseek-v4.1-flash`、`workbuddy/hy4-preview`、`deepseek-account/deepseek-v4-pro`；`openai`/`opencode-go` 的多余模型按规则丢弃；
  - 不传 `ctx` 时仍返回 5 条（无宿主 API 不崩）。
- 线上 `GET http://127.0.0.1:6875/wf-plugin/models` 改动后仍返回 5 条——插件宿主进程（PID 28168，09:33 启动）加载的是旧代码，**需重启 DSH 才生效**；这一点是本次未能端到端验证的部分，已如实记录。

## 回退方式

单文件改动，`git revert` 对应提交即可；回退后模型列表重新变回「只读 profile 里 llm-pi-ai / llm-deepseek 两处」。
