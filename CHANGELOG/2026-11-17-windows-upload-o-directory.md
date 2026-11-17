# Windows 上传兼容：移除本地 POSIX 目录标志硬依赖

## 背景

用户反馈上传未完成，错误为 `module 'os' has no attribute 'O_DIRECTORY'`。触发点是 Windows 本地运行时执行上传前的审核历史与媒体文件安全读取。

## 改了什么

- `app/workflow/media_transfer.py`：本地批次根目录在 Windows 使用解析后的 `Path`，执行路径包含校验、符号链接拒绝、文件类型/大小检查及前后 stat 身份一致性校验；POSIX 继续使用 `O_DIRECTORY/O_NOFOLLOW/dir_fd` 防 TOCTOU。新增 `_close_root` 统一关闭句柄/路径引用。
- `app/workflow/media_inventory.py`、`app/workbench/server.py`：所有本地根目录读取改用统一关闭接口；审核历史检测在 Windows 使用限定目录、文件名、解析路径和常规文件校验，不再因缺少 POSIX 常量直接返回假阴性。
- 未改变远端 SSH 媒体部署脚本的 POSIX 要求，也未改变上传确认、审核差异或发布边界。

## 验证

- `python -m py_compile workflow/media_transfer.py workbench/server.py workflow/media_inventory.py`：通过。
- `python -m unittest workflow.test_workbench_comparison.ComparisonTest.test_windows_review_history_check_does_not_require_posix_flags`：1 项通过。
- 新增 Windows 本地读取回归；当前机器运行该测试时 `tempfile` 报无可用临时目录（`FileNotFoundError: No usable temporary directory`），属于测试环境 TEMP 目录问题，未将其误判为代码通过。

## 回退方式

回退本记录对应提交即可恢复原实现；回退后 Windows 上传会重新暴露 `O_DIRECTORY` 兼容性错误。
