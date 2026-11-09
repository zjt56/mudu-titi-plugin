# 2026-11-09 修复执行期间 AI 运维记录读取

## 背景与触发

用户反馈 AI 运维总是报错。截图明确显示批次“【木牍教育】20年安徽中考英语”下“运维记录读取失败”，HTTP 错误为 `worker_busy: 批次执行锁已占用`。批次执行期间，运维面板的只读 GET 与正常工作流争用自动执行锁，错误地把普通并发状态显示为读取失败。

## 改了什么

- `app/workflow/operations.py::view`：读取/发现仍优先在批次锁内进行；仅在锁忙时退化为 `_load` 已持久化状态，不运行发现、不写事件台账。
- `app/workflow/test_operations.py`：添加执行器持有 `.run.lock` 时仍可读取已有运维快照的回归测试。

## 没改什么 / 边界

claim、运维写入、修复和复验仍受现有执行/导入锁保护。锁忙时新故障发现可能等下一次正常读取再登记；不会伪造恢复、放宽批次写入或自动上传。

## 如何验证

- `python -m unittest workflow.test_operations`：33 项中 30 项通过，3 项失败。两项跨进程锁测试因当前 Windows Python 环境缺少 `fcntl` 无法启动；另有既存的真实修复事务测试因 Windows 不支持 `os.fchmod` 失败，与本次只读路径无关。
- Node 运维测试：37 项中 35 项通过、2 项失败。两项需启动 Python 的集成测试因本机 `python` 命令未能启动（退出码 9009），非应用断言失败。
- `python -m py_compile app/workflow/operations.py app/workflow/test_operations.py` 和 `git diff --check` 通过。
- 新增锁忙回归测试包含在已运行的 Python 测试集中；该测试通过。
- 后续 Windows 复验：跨进程测试改用 supervisor 已有跨平台文件锁；事务文件权限能力适配后，以 `PYTHONUTF8=1` 重跑 `python -m unittest workflow.test_operations`，33/33 通过（1.409 秒）。此前失败数字保留为诊断历史。
- 11:39 真实 Workbench GET `/api/batches/<目标批次>/operations` 成功返回事件与 29 条回执，未再报 `worker_busy`。目标批次 `07_review/validation-report.json` 为 `passed:true`，manifest 中 validate 于 11:32:07 完成、review 正在运行；这些只证明读取和原工作流续跑，不证明运维模型本身已恢复。历史运维事件仍为 queued，模型 `TRANSPORT` 的底层原因尚无足够证据。

## 回退

回退 `operations.view` 的锁忙只读快照分支及其对应测试；不需要改动或清理任何批次数据。
