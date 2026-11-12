# 2026-11-12 路由补齐设备侧 provider（opencode-go 等 host provider 可选）

## 1. 背景 / 触发

用户把插件模型切到 **opencode-go / deepseek-v4.1-flash** 后，05 批次 preassemble 报
`provider "opencode-go" 不在路由 profile 的可用 provider 中（可用: deepseek-official,
magpie, magpie-responses）`。

排查：
- `wf-router.mjs` 的路由 profile 只从**基础 profile**（`wf-deepseek-fast`）复制
  `llm-pi-ai` provider 行；`opencode-go` 只声明在用户自己的 `desktop` profile 里，因此
  被判定为「host-only、无 adapter」并被提前拒绝。
- 隔离验证（复制 `wf-deepseek-fast` 为临时 profile，补入 opencode-go 的 provider 块 +
  注入 `OPENCODE_GO_API_KEY`）证明 **opencode-go 完全可用**（返回 `OK`），拒绝是路由
  取数范围太窄，不是 provider 本身不可用。
- 另：`app/workflow/local-agent.json` 走 `wf-agent.sh`，硬编码 `wf-deepseek-fast`
  → magpie → WorkBuddy；该账号额度已用尽。CLI 直接跑批次会撞这条，需用插件的
  `backend/local-agent.plugin.json`（`wf-router.mjs`，按 model-router.json 选模型）。

## 2. 改了什么

`backend/wf-router.mjs`：
- 新增 `providerBlocks(file)`：解析某个 patch 文件 `llm-pi-ai` 行下的 provider 子块。
- 新增 `mergeProviders(overlay, extra)`：把 overlay 缺失的 provider 块并入 `llm-pi-ai`
  行末尾（幂等，已有则不重复）。
- `makeProfile()` 生成路由 patch 时，把 `profiles/desktop/cordis.patch.yml` 中声明、
  基础 profile 未提供的 provider 合并进来。于是设备上已配置好的 host provider
  （如 opencode-go）能真正被路由使用，而不是被误判为「无 adapter」。
- 路由 patch 是内容寻址的（含完整 overlay 文本），合并后会得到新的 `wf-plugin-route-*`
  目录，不会复用旧缓存。

## 3. 没改什么 / 边界

- 不改变「明确配置了却不可用就报错、绝不静默换模型」的规则：若该 provider 在设备
  profile 里也没有定义，仍然提前失败并列出可用 provider。
- 不动用户 `desktop` profile、不动 `model-router.json` 的选中项、不改基础 profile。
- 只影响路由 patch 的 provider 取数范围，不影响审核轮次/角色路由选择逻辑。

## 4. 如何验证

- 真机：插件路由指到 `opencode-go/deepseek-v4.1-flash` 后
  `node backend/wf-router.mjs` 返回 `OK`（此前直接报 provider 不可用）。
- 真机：05 批次用 `MUDU_AGENT_CONFIG=<生效 bridge 配置>` 跑
  `run --until validate`，preassemble→types→assemble→meta→underlines→validate 全通过，
  **validate 19 项体检 0 阻断失败**（含 check_structure / publish_check /
  audit_source_marks）。
- `node --test backend/*.test.cjs`：260 例，pass 258、fail 0。

## 5. 回退方式

git revert 本提交；路由会自动回到「只认基础 profile provider」的旧行为。
