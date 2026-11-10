# 2026-11-10 大模型配置新增「AI运维」模型选择，修复 MODEL_ROUTE_INVALID

## 背景

用户反馈 AI 运维报错：

```
AI运维需要有效的 operations 或默认模型配置；不会自动替换模型。
code=MODEL_ROUTE_INVALID
```

并且“大模型配置里看不到 AI运维、从没见它启动过”。原因是仓库 `config/model-router.json` 的 `default` 为空（跟随各设备 Harness 模型），而 `operationsRoute` 只读 `roles.operations` 或 `default`，没有 Harness 兜底；设置页也没有 AI运维 这一行，用户无法单独指定。

## 改了什么

1. `lib/client.js`：大模型配置的阶段/角色列表新增 **「AI运维」**（写入 `roles.operations`），并更新说明文字。
2. `lib/index.js`：新增 `operationsRouteFor(router, harness)`，AI运维模型解析顺序为
   **显式 `roles.operations` → 本机 Harness 当前模型 → 插件 `default`**。AI运维运行在宿主原生会话里，必须用宿主实际注册的 provider，所以 Harness 模型优先于面向 headless 的插件 default；仍不静默换到别的模型。
   `resolveRoute` 改为使用它。
3. `lib/operations-settings.js`：`MODEL_ROUTE_INVALID` 提示补充“请在「模型与设置 → 大模型配置」为 AI运维 选择模型，或设置全局默认模型。”
4. `README.md`：说明 AI运维 模型可在设置页单独选择，未选时跟随 default/Harness。
5. 测试：`backend/model-list.test.cjs` 新增 AI运维路由优先级（roles > default > Harness）与设置页存在 AI运维 行的断言。

## 边界

- 显式配置了 `roles.operations` 但无效时仍然报错，不静默回退（保持“不静默换模型”）。
- 只影响 AI运维 的模型解析与设置页呈现，不改变录题、自愈、审核、上传流程。
- AI运维 仍为附加能力，宿主缺少会话服务时不可用、原流程继续。

## 验证

- `node --test backend/model-list.test.cjs backend/operations-settings.test.cjs`：10 项通过。
- `node --check lib/index.js lib/client.js lib/operations-settings.js`：通过。

## 回退

移除 `operationsRouteFor`、设置页 AI运维 行与提示文字即可；行为回到“必须显式配置 operations 或 default”。
