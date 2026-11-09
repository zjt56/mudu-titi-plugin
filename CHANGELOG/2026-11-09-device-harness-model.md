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
