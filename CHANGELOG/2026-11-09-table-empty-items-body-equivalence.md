# 2026-11-09 表格空 items 字段不再触发正文冲突

## 背景

用户要求将 2020 安徽卷已出现的卡点在插件中修复，防止同类问题复发。实际冲突之一仅是表格单元格缺少 `items: []`，并无正文或结构变化。

## 改了什么

`app/workflow/batch_fill.py::_canonical_body` 只在表格单元格及其嵌套文本项中，将缺失 items 与空列表视为同一正文表达。不改落盘题文、不刷新精确原件/媒体哈希、不替换绑定来源，不放宽文字、非空 items、rowspan/colspan 或其他真实正文差异。

`app/workflow/test_batch_fill.py` 新增完整填充状态链路测试：空 items 字段差异通过受保护正文检查，文字、合并跨度、非空嵌套内容变化仍抛正文冲突。

听力材料被 confirmed stem 重复注入的永久修复由当前工作区 `agent_tasks.py` 的有绑定 draft.material（含明确空串）选择逻辑处理；`test_agent_tasks.py` 的组装测试覆盖该链路。本记录不覆盖并行自愈动作修改。

## 验证

从 app 目录以 UTF-8 模式执行 `python -m unittest workflow.test_batch_fill workflow.test_agent_tasks`：83 项通过，0 失败。实际批次此前已备份修复，preassemble 复验完成，详见 `2026-11-09-2020-preassemble-body-conflict.md`。

## 边界与回退

未改变审核或上传边界，未自动上传发布。回退本次 canonical 表格处理和新增测试即可；真实正文冲突检查始终保留。
