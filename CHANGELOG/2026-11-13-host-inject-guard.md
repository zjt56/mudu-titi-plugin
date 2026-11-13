# 注入声明守卫：防止未声明的宿主服务被读取

日期：2026-11-13。类型：宿主插件（防复发测试 + 注入声明补全）。

## 背景与触发

删除批次时曾报 `Native session purge failed: cannot get property "sessions" without inject`：
`lib/content-agent.js` 的 `purgeSessions` 读 `ctx.sessions.get`，但 `lib/index.js` 的
`ctx.inject([...])` 没有声明 `sessions`，宿主拒绝读取未声明的注入服务。

单独补 `sessions` 只是修一个点；这类“读取未声明注入服务”的错误会再犯。

## 改了什么

- `lib/index.js`：内容智能体注入列表补上 `sessionQuery`（`purgeSessions` 清理后用
  `ctx.sessionQuery.searchSessions` 刷新索引；此前仅靠 `typeof` 保护才没报错，同属该缺陷）。
  现在是：`['agents','sessions','sessionQuery','tools','systemPrompt','sessionPersistence']`。
- `backend/host-inject-declarations.test.cjs`（新增）：
  1. 扫描 `lib/index.js`、`lib/client.js` 每个 `ctx.inject([...], (scope)=>{...})` 块，
     块内直接读取的宿主服务必须声明；
  2. **跨模块**：注入块把 `scope` 交给 `createContentAgentManager` / `createOperationsAgentManager`
     时，对应子模块（`content-agent.js` / `operations-agent.js`）里读取的宿主服务也必须在同一注入块声明；
  3. 专项断言 `content-agent` 作用域必须声明 `sessions/agents/sessionPersistence`；
  4. 回归锚点：`lib/content-agent.js` 仍需 `ctx.sessions.get`（行为变了要同步改守卫）。

## 没改什么 / 边界

- 不改变任何注入服务的语义，也不放行未声明读取（守卫是让 CI 提前拦住，而非运行时放宽）；
- 不改批次数据、上传边界、删除/审核/自愈逻辑；
- 宿主对 `sessionQuery` 若某版本不提供，代码仍走 `typeof` 保护的软失败路径，不阻断。

## 验证

- `backend/host-inject-declarations.test.cjs`：4 项通过（含跨模块断言）；
- 实测守卫有效性：临时移除 `sessions` 声明 → 专项断言失败；恢复 → 通过；
- 全量 `node --test backend/*.test.cjs`：372 项，370 通过、2 跳过、0 失败；
- `node --check lib/index.js` 通过。
- 未验证项：真实宿主是否提供 `sessionQuery` 需插件重载后在实际删除流程中观察；代码已做可选保护。

## 回退

撤回本提交即恢复原注入列表与无守卫状态；不影响批次数据。
