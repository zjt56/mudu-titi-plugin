# 2026-11-17：原生内容代理工具参数 JSON 错误恢复

## 背景/触发

批次 `【木牍教育】21年安徽中考英语` 的前置批次组装（含题型）失败：

```
Tool call "content_submit_candidate": Expected ',' or '}' after property value
in JSON at position 9117 (line 1 column 9118)
```

候选内容本身没有进入验收；这是模型生成的工具调用参数 JSON 不合法（截断/逗号或括号不完整）。
宿主已有 `session/event → turn/end` 的 JSON 格式恢复分支，但实际错误通过
`agent/error` 事件上报时直接 `cancel`，所以没有走恢复，阶段被误判为失败且 `retryable=false`。

## 改了什么

- `lib/content-agent.js`：新增 `recoverToolArgument()`；`agent/error` 与
  `session/event(turn/end)` 两条路径都使用同一恢复逻辑。工具参数 JSON 非法且未超过
  `FORMAT_RECOVERIES=2` 时保留同一会话，提交“合法 JSON、完整候选、检查逗号/括号”的纠错提示后重试；
  真正模型错误和超过恢复预算仍失败，不重放已提交候选。
- `backend/content-agent.test.cjs`：新增 `agent/error` JSON 错误恢复回归。

## 没改什么 / 边界

- 不把非法候选当作已提交，不跳过程序验收；只有 `accepted=true` 才算完成。
- 恢复预算仍是 2 次；重复格式错误最终失败并保留证据。
- 不改模型路由、候选内容或质量校验。

## 如何验证

- `node --test backend/content-agent.test.cjs`：50 项通过。
- 真实批次 `【木牍教育】21年安徽中考英语`：重跑 `preassemble` 完成，
  `03_types/types.json` 已生成，`types` 交接通过；结构 34 单元（独立21/组合13）。

## 回退方式

删除 `lib/content-agent.js` 的 `agent/error` 恢复分支即可恢复原行为（JSON 错误会直接失败）。
