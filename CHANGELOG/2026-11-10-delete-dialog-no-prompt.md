# 2026-11-10 修复“彻底删除”点击无反应：改用页面内确认弹层

## 背景

用户反馈：点击批次里的「彻底删除」没有任何反应。核实发现前端 `doArchive` 使用 `window.prompt` 让用户输入批次名确认；DSH 桌面端（Electron/嵌入式 WebView）不支持 `window.prompt`，返回空值，`typed !== name` 直接 return，于是静默无操作。后端 `purge` 接口本身正常（此前已成功彻底删除 8 个批次）。

## 改了什么

- `lib/client.js::WorkbenchTab`：
  - 新增删除确认弹层状态 `del`，用页面内 `role="dialog"` 的输入框替代 `window.prompt`；输入完整批次名后才能点「确认彻底删除」，支持 Enter 确认、Esc/点遮罩取消，显示错误与“正在删除…”状态。
  - 卡片「⋯ → 彻底删除」和详情页「彻底删除批次」两处都改为 `openDelete(name)`，删除成功后 `loadBatches()` 刷新并清空选中。
  - 保留后端 `POST /batches/<name>/purge {confirm_name}`，仍要求完整名确认，语义不变。

## 没改什么 / 边界

删除仍是不可恢复、需完整批次名确认，且只由用户显式点击触发；不自动删除用户试卷；不改变 `purge` 后端清理范围（目录、收件箱、回收归档、音频状态、内容缓存）。上传/重跑的 `window.confirm` 未改（Electron 支持 confirm，仅 prompt 不支持）。

## 验证

- `node --check lib/client.js` 通过。
- 后端 `purge_batch` 现有测试继续覆盖（`workflow.test_workbench_catalog_archive` 的彻底删除用例），本次未改后端口径。
- 实机删除接口此前对 8 个批次全部返回 `purged=True`，证明后端正常；本次仅修复前端触发方式。

## 回退方式

回退本提交即可恢复原实现（但会重新出现 Electron 下点击无反应的问题）。
