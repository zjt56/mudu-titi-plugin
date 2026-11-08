# 2026-11-08 低风险性能与仓库卫生优化

## 背景

项目开发期间出现重复文件哈希、审核摘要重复读取，以及本地验证/备份目录混入工作区的问题。目标是提速和清理，不改变阶段顺序、审核口径、上传边界或模型规则。

## 改动

- `app/workflow/manifest.py`：`sha256_file` 增加进程内有限缓存，键为规范化路径、文件大小、`mtime_ns`；文件内容发生常规修改时自动失效，缓存上限 4096 项。
- `app/workflow/question_content.py`：补齐既有排版兼容，允许 `39.A.first` 形式识别行内 A 选项；不改变选项目录或题型规则。
- `.gitignore`：忽略 `.mnemon/`、`.verification/`、`app/workflows/` 和 `batch_fill.py.bak` 等本地运行/备份产物，不删除现有文件。
- 新增最小回归覆盖：哈希缓存按文件变化失效、题号紧贴行内选项可解析。

## 边界

- 未改变阶段契约、审核规则、知识点映射、听力待核对口径、上传/发布边界。
- 未启用 `speculative_candidates`，未删除旧兼容模块；它们仍需独立评估后再清理。
- `inspect_stage` 的重复计算未直接加结果缓存，避免阶段状态缓存造成卡点；该项保留为后续专项优化。

## 验证

- `workflow.test_question_content`、`test_manifest_atomic`、`test_agent_protocol`、`test_agent_entry`、`test_local_agent`：100 项通过，4 项跳过。
- `node --test backend/settings-navigation.test.cjs backend/model-router.test.cjs`：14 项通过。
- 哈希缓存实测：重复 `unreviewed_summary` 10 次总耗时约 55ms；文件变更后摘要哈希自动重算。
- `test_source_structure` 仍有既有结构边界失败（18 项约 605 秒，4 项失败），未将其伪装成此次性能改动已修复；该问题需要单独处理来源边界。
