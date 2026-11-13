# 删除批次：原生会话清理需要注入 sessions 服务

日期：2026-11-13。类型：宿主插件注入（删除流程）。

## 背景与触发

删除批次 `11七上人教版课时A计划StarterUnit3Welcome!第三课时Sect` 时报：

```
Native session purge failed: cannot get property "sessions" without inject
```

目录未删掉，`inbox` 副本已不在，`workflows/<批次>` 仍在。

排查：`lib/content-agent.js` 的 `purgeSessions` 要求 `ctx.agents.get` 与 `ctx.sessions.get`，
用来在删除前确认该会话不再存活（`PURGE_LIVE_SESSION`）。但 `lib/index.js` 的注入列表只有
`['agents','tools','systemPrompt','sessionPersistence']`，没有 `sessions`；宿主拒绝读取未声明
注入的服务，读取 `ctx.sessions` 直接抛错，删除流程因此失败。

`@deepseek-ai/dsh-session` 提供 `ctx.sessions.create/get/list` 等，是宿主正式服务。

## 改了什么

- `lib/index.js`：内容智能体注入列表加上 `sessions`（`['agents','sessions','tools','systemPrompt','sessionPersistence']`），
  使清理前能查询会话存活状态；注入声明只是提供读取能力，不改变删除授权与执行锁。

## 没改什么 / 边界

- 不放宽任何删除保护：仍在阶段边界停下、仍检查执行锁与运维租约、仍拒绝删除运行中的批次；
- 不改变会话清理的安全校验（身份头、绝对路径、JSONL 位置、单文件校验、删除后复验）；
- 不改批次数据、manifest、上传边界。

## 验证

- `backend/content-agent.test.cjs`：47 项通过（含 trusted purge 拒绝非法绑定/活会话/错头/不安全位置）；
- `node --check lib/index.js` 通过；
- 真实删除需插件重载后在新会话再执行一次（旧的失败 purge 回执是插件宿主旧版本产生的）。
  重载后重新删除该批次即可；若仍有残留 purge 回执，宿主会按新代码重新清理。

## 回退

撤回本提交即恢复原注入集合；不影响批次数据。
