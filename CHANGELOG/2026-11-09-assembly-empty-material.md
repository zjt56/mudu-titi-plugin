# 2026-11-09 装配保留来源绑定的空共享材料

## 背景

听力题组可以只有印刷子题，没有文字共享材料。装配导入已通过原文保真检查后，最终 wire-format 转换仍把 confirmed unit 的完整 stem（包含所有子题）作为缺失材料兜底，导致空 material 被子题正文污染。

## 改了什么

- `app/workflow/agent_tasks.py`：复用装配时已生成的来源草稿及 source_bindings；有可靠来源绑定且 material 为字符串时，最终规范化只使用该共享材料字段，包括权威空字符串，不再把完整题组正文误当材料。映射使用已归一的稳定 unit_id 和题号元组，兼容确认结构未显式写 ID 的情形。
- 无来源绑定或没有有效草稿材料时保留 confirmed stem 的历史兜底路径；真实非空共享材料仍保留。
- `app/workflow/test_agent_tasks.py`：新增实际来源草稿 → assemble accept_result 导入 → 已安装批次 → normalize_item 再消费的回归，覆盖空材料听力题组与具有真实共享对话的题组，逐项核对子题题干与选项。

## 边界

本项材料修复只改上述实现与测试，不改 wire_format 通用契约或用户批次；未安装/重载运行时、未上传、未提交。父任务统一维护 changelog 索引，阶段依赖和 Windows 执行锁修复另见同日组装依赖记录。

## 验证

从 `app/` 运行，设置 `PYTHONPATH=app`（绝对路径）、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`、`WF_STRUCTURE_AI=0`。

- 修复前新增回归准确复现：空 material 被两道子题全文填充（1 个测试、1 个失败子用例）；修复后同一测试的空/非空材料子用例通过。
- `python -m unittest workflow.test_agent_tasks workflow.test_wire_format workflow.test_question_content workflow.test_agent_protocol`：90 tests，85 通过、5 跳过、0 失败。包含既有按题号恢复真实共享材料的 normalizer 回归。
- 扩展运行 `workflow.test_question_draft workflow.test_agent_entry workflow.test_local_agent` 等共 169 tests：158 通过、8 跳过、3 失败。失败分别为 Windows CRLF 导致 unchanged ensure 文件 mtime 改变、agent_entry 期望 POSIX `/tmp/a batch` 而实际为 Windows 路径、local_agent 候选缓存预期一次调用而实际两次；不在本修复范围。
- 初次未启用 Python UTF-8 模式时，既有测试的无 encoding read_text 出现 5 个 GBK 解码错误；启用 UTF-8 后上述错误消失。
- `git diff --check`：通过。

## 回退

仅撤销本记录及对应 agent_tasks 实现、回归测试的差异；不要覆盖共享工作区中其他任务的改动。不需要迁移或改写任何用户批次。
