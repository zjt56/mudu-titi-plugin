# 调度器按模块执行仓库包内脚本，修复新批次 extract 相对导入失败

## 背景/触发

批次 `47七上人教版课时A计划Unit5FunClubs综合阅读提升` 在原卷复合提取阶段失败：

```
原卷提取失败：attempted relative import with no known parent package
```

日志显示 runner 执行的是 `<python> workflow/extract.py --batch-dir …`（`cwd=app`）。
以脚本路径方式运行时 `sys.path[0]` 变成 `app/workflow`，`extract.py` 顶部
`from workflow import manifest as mf` 无法解析父包 `workflow`，因此**任何新批次**的
`extract` 都会确定性失败，`types/format_gate` 等以 `-m` 声明的阶段则正常。

## 改了什么

- `app/workflow/runner.py`：新增 `_stage_argv()`，`_execute_command_stage()` 改用它。
  - `app/` 下包内 `.py`（父目录链都有 `__init__.py`，如 `workflow/`）改为
    `python -m workflow.<mod> ...` 执行；
  - `app/scripts/...` 这类无父包的独立脚本保持 `python <path> ...` 路径执行；
  - 已是 `python -m …` / 自带解释器的外部命令原样透传。
- `app/workflow/test_runner_auto.py`：新增
  `test_repo_package_script_runs_as_module_not_by_path`。
- `ISSUES/open/2026-11-13-runner-按路径执行仓库包内脚本致相对导入失败.md`：登记卡点。

## 没改什么 / 边界

只调整子进程 argv 构造方式，不改任何阶段命令、产物路径、闸门口径或上传边界；
不重跑历史批次；`scripts/` 下 standalone 脚本行为不变。

## 如何验证

- `PYTHONPATH=app python3 -m unittest workflow.test_runner_auto` → **57 passed**。
- `workflow.test_agent_entry` + `workflow.test_local_agent` → **65 passed (1 skipped)**。
- 真实批次 `47…Unit5FunClubs综合阅读提升`：修复后用 runner 重跑 `extract` **成功**
  （2180 字符，2 分区，7 候选题号，3 项待核验；板块视觉 verified、1 张原卷图语义），
  `status` 显示 `extract completed / delivered`，下一步 `structure_gate` 可执行。

## 回退方式

`_stage_argv` 仅控制是否以 `-m` 执行；如需回退，删除该函数并恢复
`argv = [sys.executable] + rendered if … endswith(".py") else rendered`。
