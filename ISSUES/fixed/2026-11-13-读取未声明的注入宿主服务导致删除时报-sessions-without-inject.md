# 卡点：读取未声明的注入宿主服务导致删除时报 sessions without inject

- id: 2026-11-13-读取未声明的注入宿主服务导致删除时报-sessions-without-inject
- status: fixed
- batch: 宿主插件
- stage: plugin
- check: PURGE_HOST_UNSUPPORTED
- created: 2026-11-13T13:48:13+08:00
- last_seen: 2026-11-13T13:48:13+08:00
- occurrences: 1
- fixed_at: 2026-11-13T13:48:13+08:00
- fix_commit: 46ba946
- verification: 守卫 4 项通过；移除声明可令断言失败（已实测）；全量 backend 372 项 370 通过 2 跳过 0 失败
- title: 读取未声明的注入宿主服务导致删除时报 sessions without inject

## 现象（卡点）

Native session purge failed: cannot get property "sessions" without inject；sessionQuery 同为未声明读取

## 具体检查与判定

- 失败检查：PURGE_HOST_UNSUPPORTED
- 判定口径：ctx.inject 声明与子模块实际读取的宿主服务不一致
- 修复方向：补全 sessions/sessionQuery 声明；新增跨模块注入声明守卫测试

## 修复记录

- 修复方式：注入列表补 sessions 与 sessionQuery；新增宿主注入声明守卫（含跨模块）
- 修复提交：46ba946
- 复验证据：守卫 4 项通过；移除声明可令断言失败（已实测）；全量 backend 372 项 370 通过 2 跳过 0 失败
- 标记时间：2026-11-13T13:48:13+08:00
