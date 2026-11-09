# 2026-11-09 路由 provider 校验：避免 NO_ADAPTER 被当成阶段失败

## 背景 / 触发

批次【木牍教育】18年安徽中考英语 的前置批次组装（含题型）失败，界面提示
「Agent subprocess failed (exit 1, exit); inspect stderr.log」。实际 stderr 是：

```text
wf-router: stage=types agent=types round=? -> provider=magpie model=auto/deepseek-v4.1-flash (base=wf-plugin-route-e402e9e6201b4c5ba9af8d8c)
dsh: NO_ADAPTER: no adapter registered for provider "magpie"
wf-router: dsh call failed (profile=wf-plugin-route-e402e9e6201b4c5ba9af8d8c model=auto/deepseek-v4.1-flash exit=1)
```

根因两层：

1. 插件面板的模型列表把宿主里已启用、但 profile 没声明的 provider（WorkBuddy 的
   `magpie` 中转）也列了出来，用户选择后写进 `config/model-router.json` 的 default；
2. `backend/wf-router.mjs` 生成的 headless 路由 profile 只复制 base profile 里的
   `llm-pi-ai` / `llm-deepseek` / `llm-deepseek-account` 条目，`magpie` 不在其中，
   于是 dsh 直接 `NO_ADAPTER`。这个错误还发生在模型调用之前，却被阶段层当成
   普通 agent 失败，界面只能提示“重跑一次”，指向错误方向。

## 改了什么

- `lib/index.js`：新增 `servableHostModels(detected, host)`，只为路由 profile 已声明的
  provider 合并宿主模型（用于补显示名）；未声明的宿主 provider 不再出现在模型列表里。
  注释写明原因，避免以后又“好心”把桌面端 provider 加回来。
- `backend/wf-router.mjs`：新增 `overlayProviders(patch)`，从生成的路由 profile 里解析
  可用 provider（`llm-pi-ai` 的 provider 键 + `llm-deepseek` 行的 `deepseek-official`）。
  调用 dsh 之前先校验选中的 provider 是否在其中；不在就立即失败并给出中文提示和可选
  列表（“provider \"magpie\" 不在路由 profile 的可用 provider 中（可用: …）。请在插件
  设置里重新选择模型”），不再把配置错误伪装成阶段产物不合格。

## 没改什么 / 边界

- 仍不静默切换模型：选中的 provider 不可用就报错，不会自动换成默认模型（符合
  AGENTS 硬规则 8）。
- 只影响插件面板的模型列表和 wf-router 的前置校验；不改任何阶段契约、审核口径、
  上传边界，也不改用户批次数据。
- profile 里没有任何 llm 条目时（测试夹具等）跳过校验，避免误伤非常规环境。

## 如何验证

- 新增 `backend/wf-router-native.test.cjs`：
  - `native router refuses a provider the route profile cannot serve`：base profile 声明
    `opencode-go`、默认选 `magpie` → 退出码 1、stderr 含 provider 名与可选列表、不生成
    response 文件；
  - `native router accepts a provider declared by the route profile`：同一 profile 选
    `opencode-go` → 正常调用。
- 新增 `backend/model-list.test.cjs`：`host-only providers are not offered because the
  route profile cannot serve them`，断言 `magpie` 被过滤、已声明的 `opencode-go` 保留。
- 结果：`node --test backend/wf-router-native.test.cjs backend/model-list.test.cjs`
  → 13 个测试、12 通过、0 失败（1 个为仅在设置 `WF_ROUTER_REAL_DSH` 时运行的用例）。
  `node --check lib/index.js`、`node --check backend/wf-router.mjs` 均通过。
- 现场复验：把 `config/model-router.json` 的 default 临时改为
  `{"provider":"opencode-go","model":"deepseek-v4.1-flash"}` 后，手工跑
  `node backend/wf-router.mjs` 输出 `provider=opencode-go …` 并返回 `OK`；批次
  preassemble 由自愈监督器以全新上下文重跑。另在临时 plugin 目录把 default 指向
  `deepseek-official/deepseek-flash`，同样 3.6 秒返回 `OK`，确认两个已声明 provider
  都可服务。
- 与「仓库不再固定模型」并行：仓库 `config/model-router.json` 的 default 仍保持为空、
  由各设备跟随自己的 Harness 模型；本次提交不含任何在本机写死的默认 provider，
  现场临时设置已还原。

## 回退方式

1. `lib/index.js` 恢复为 `detected = mergeDetectedModels(detected, host)`；
2. 删除 `backend/wf-router.mjs` 中 `overlayProviders` 与调用前的校验块；
3. 删除两个测试文件里新增的用例。

回退后模型列表会重新列出不可用的宿主 provider，选它仍会 NO_ADAPTER。
