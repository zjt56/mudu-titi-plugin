# 组装显式等待当前前置对象

## 背景

调查【木牍教育】20年安徽中考英语的代理启动失败及 AI 运维报错时，发现前置对象重新校验已报 `Batch fill正文 conflict for group-6-7; existing content preserved`，标签阶段仍保留历史完成状态。assemble 的直接依赖未声明 preassemble，无法直接表达当前前置对象必须合格的约束。

## 改了什么

- `app/workflow/contracts.py`：assemble 显式依赖 preassemble；继续不依赖 audio。
- `app/docs/pipeline.md`：同步依赖表，修正文档仍写 assemble 等待 audio 的陈旧描述。
- `app/workflow/test_runner_auto.py`：覆盖前置对象失败、四路标签已完成时禁止启动组装。
- `app/workflow/runner.py`：复用 supervisor 已有的跨平台 OS 文件锁，修复 Windows 直接导入 fcntl 导致 runner run 失败的问题，保留同批次并发拒绝与超时语义。

## 边界

不覆盖用户批次、不伪造 manifest 状态、不放宽正文冲突检查，不上传或发布。该依赖修复本身不代表存量正文冲突或模型传输问题已恢复。

## 验证

`python -m unittest workflow.test_runner_auto.RunnerAutoTest.test_failed_preassemble_blocks_assembly_with_completed_labels workflow.test_runner_auto.RunnerAutoTest.test_audio_needs_review_does_not_block_assemble_but_blocks_review workflow.test_progressive_pipeline`：8/8 通过。覆盖前置失败阻断、听力待复核仍允许组装，以及真实前置建立、合并、元信息与画线链路。

扩大检查 `python -m unittest workflow.test_runner_auto workflow.test_progressive_pipeline`：初次 48 项有 2 errors、1 failure、2 skipped，三处失败均由 Windows 不支持 fcntl 引起；改用已有跨平台锁后重跑 48 项，46 通过、2 skipped、0 failures/errors（16.257 秒），包含执行锁互斥测试。此前 shell 后续 diff 命令掩盖了 unittest 退出码，数字以 unittest 输出为准；复跑显式传播退出码。

## 回退

回退上述契约、文档及对应回归测试改动；无需修改任何批次数据。
