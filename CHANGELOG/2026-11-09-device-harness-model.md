# 2026-11-09 模型不再固定在仓库：每台设备跟随自己的 Harness 模型

## 背景

用户明确指出：**模型不是固定的，要随不同设备自由选择他们 Harness 的模型。**

此前 `config/model-router.json` 被提交了一个固定 `default`（先是 `deepseek-official/deepseek-flash`，中途误改为 `magpie/auto/deepseek-v4.1-flash`）。仓库一旦固定模型，其它设备同步源码后就会被迫使用同一模型；该设备没有对应 provider 凭据时，`wf-router` 直接 exit 1，表现为「前置批次组装：阶段失败」。

## 改了什么

1. `config/model-router.json`：`default` 清空为 `{}`，仓库不再固定任何模型。
2. `backend/route_pick.py` / `backend/wf-router.mjs`：`default` 为空时读取本机 Harness 选中模型：
   - 顺序：`DSH_PROFILE` → `desktop` → `wf-deepseek-fast` → `wf-deepseek`；
   - 解析 `profiles/<name>/cordis.patch.yml` 的 `- id: agent-default-model` 的 `provider`/`model`；
   - 生成的临时路由 profile 也以该 profile 为基底，确保该设备自己的 provider 声明可用。
3. `lib/index.js`：`GET /api/config` 返回 `harnessDefault`（本机 Harness 当前模型），设置页可显示“跟随 Harness”。
4. `lib/client.js`：默认模型下拉在当前无显式 default 时显示“跟随 Harness 当前模型”，子 Agent 的“跟随默认”也指向该模型。
5. `app/workflow/local_agent.py::_part_model_binding`：模型身份指纹同样支持 Harness 默认，并把该 profile 文件纳入缓存键，换模型后不会复用旧结果。

## 边界

- 显式配置的 `default` / `stages` / `roles` 仍然严格优先，且必须完整；存在但损坏时直接报错，不静默改用其它模型（保留原「不静默兜底」规则）。
- 不写入、不上报任何凭据；只读取 profile 的 provider/model 名称与文件哈希。
- 不改变各设备自己的 Harness 模型菜单，也不替用户选择模型。

## 验证

- `backend/model-router.test.cjs`、`backend/wf-router-native.test.cjs`：新增「default 为空时跟随设备 agent-default-model」用例通过；其余路由用例通过。
- `backend/*.test.cjs` 全量：204 项，197 通过、2 跳过、5 失败；5 项均为此前干净基线已复现的既有失败（两项图片 CSS 断言、三项本机 macOS 无法执行 Windows cmd 路由）。
- `workflow.test_local_agent + test_preassembly + test_label_execution + test_agent_protocol + test_runner_auto`：132 项通过。

## 回退

恢复 `config/model-router.json` 的显式 `default`，并回退 `route_pick.py` / `wf-router.mjs` 的 Harness 回退段与相关测试即可；插件设置页的显式模型选择不受影响。

## 与另一台设备 provider guard 修复的互相印证（2026-11-09 18:20）

另一台设备的实际 stderr 揭示根因：`dsh: NO_ADAPTER: no adapter registered for provider "magpie"`。`f9bfb49` 已加两道防线：

1. `lib/index.js::servableHostModels`：设置页只列路由 profile 能服务的宿主模型，不再出现 WorkBuddy 的 magpie；
2. `backend/wf-router.mjs::overlayProviders`：调用 dsh 前校验 provider 是否在生成的路由 profile 中，不在则带可用列表报错。

本地复核发现需要补的一环：**“跟随 Harness 默认模型”也可能指向不可服务的 provider**（本机 `desktop/agent-default-model` 就是 magpie）。若不处理，空 `default` 仍会自动选中 magpie 并触发 NO_ADAPTER。已统一加上可服务性过滤：

- `lib/index.js::harnessDefaultModel`：候选 profile 的 `agent-default-model` 只有在其自身 `llm-pi-ai`/`llm-deepseek` 声明了该 provider 时才采用；
- `backend/route_pick.py::harness_default`、`backend/wf-router.mjs::harnessDefault`、`app/workflow/local_agent.py::_harness_default_route`：同一规则，跳过不可服务的 provider，继续下一个候选 profile；全部不可用时明确报错让用户在设置里选可调用模型，绝不静默换模型。

同时把 `backend/wf-router-native.test.cjs` 的假 `dsh` 改成平台原生（Windows `.cmd` / POSIX 可执行脚本），使 provider guard 的两条用例在 macOS 本地也能真实执行：9 项中 7 通过、2 项需真实 DSH 而跳过，0 失败。新增/调整用例覆盖：设置默认为空时跟随本机可服务的 Harness 模型、不可服务 provider 被跳过、显式损坏配置不回落。

验证：后端全量 207 项（203 通过 / 2 跳过 / 2 项既有图片 CSS 断言失败）；工作流全量 1869 项（1849 通过 / 17 跳过 / 3 项既有失败）。

## 设置页提示修正（2026-11-09 18:32）

用户指出初始化引导里的「大模型」项显示“未配置默认模型。填 API Key 并选模型后即可运行全流程”是错的：**插件不需要填写模型 API Key**，凭据由 Harness 提供，默认应直接路由到 Harness 当前模型。

- `lib/client.js::OnboardingSection`：模型项改为“跟随 Harness 当前模型：<provider/model>（无需填写 API Key，凭据由 Harness 提供）”；确实没有任何可服务模型时才提示去「模型与设置」选择本机 Harness 已配置的模型，不再提“填 API Key”。
- 模型设置页说明同步改为“默认跟随本机 Harness 当前模型，无需填写 API Key”。
- `lib/index.js::harnessDefaultModel` 增加最后兜底：没有显式 `agent-default-model` 时，取本机 Harness profile 里第一个可服务模型（优先 `desktop`，其次 `wf-deepseek`），保证新设备无需任何手工填写即可跑通。
- `backend/route_pick.py` / `backend/wf-router.mjs` 同步该兜底；不可服务的 provider 仍被跳过。
- `backend/model-list.test.cjs` 新增“无需 API Key 即可解析出可服务 Harness 默认模型”用例。

验证：`node --test backend/model-list.test.cjs` 6/6；路由相关 35 项 33 通过 2 跳过 0 失败。
