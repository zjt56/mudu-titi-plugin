# 2026-11-09 URL 原文保真与图片段落标记体检修复

## 背景与证据

用户授权实际修复 `【木牍教育】20年安徽中考英语` 批次并将问题永久修入插件。批次目录为 `C:\Users\Administrator\.dsh\wf-plugin-data\workflows\【木牍教育】20年安徽中考英语`。

- `group-56-57-58` 的材料只有原卷段落标记 `A\n`，正文为题目级图片附件 `image10.jpeg`。相邻阅读文章包含字母 A，旧 E20 子串检查把长度 1/1152 当作重复文章。
- `app/workflow/agent_tasks.py::_sanitized` 中 Windows 盘符表达式 `[A-Za-z]:[\\/]` 匹配了 `https://` 内的 `s:/`，将原文网址改成 `http[host-path-redacted]`。
- 第 58 题权威解析已完整保留于 `01_source/附录.txt:64` 和 `04_knowledge/explanations.json`，但三份既有批次投影仍保留被脱敏的网址，严格来源检查正确拒绝该投影。

## 改动

- `app/workflow/agent_tasks.py`：为路径表达式增加前缀边界，网址与网址内 `/home/`、`/Users/` 等正常路径保持原文；独立 Windows/POSIX 主机路径及凭证继续脱敏。保留同文件已有并行修复。
- `app/scripts/06-upload/audit_batch_integrity.py`：仅将带 `kind=image, placement=question` 附件且文本恰为单个大写字母的段落标记排除于相邻文章重复比较。不修改材料或附件。
- `app/workflow/test_source_url_image_integrity.py`：覆盖脱敏→严格来源绑定链路、错误网址仍拒绝、真实路径/凭证仍脱敏、图片段落标记不误报，以及无图片短材料/带图真实重复文章仍阻断。
- 对当前批次执行一次有证据的历史数据修复：同时持有 auto、runner、agent 锁，逐项确认旧值只出现一次，确认原解析逐字存在于权威附录，严格 `inspect_content` 校验三份候选成功后，仅替换 `06_batch/batch.json`、`batch_meta.json`、`batch_meta.base.json` 的第 58 题解析。原始字节及前后 SHA256 保留在 `workflow/repairs/explanation58-url-20261109-113147/`，没有直接修改 manifest。

## 验证

- 设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8` 后，相关工作流 unittest 共 **106 项通过**；新回归文件 **3 项通过**。此前未开启 UTF-8 的测试调用因既有 Windows cp936 默认文件编码失败，不能视为代码回归。
- 显式 `workflow.runner run --stage validate` 先如实等待因数据变化而失效的 underlines（退出 1）。现有正常监督器随后重跑确定性下游，没有修改阶段状态来伪造成功。
- 实批 `07_review/validation-report.json` 为 `passed:true`，正常 status 显示 extract 至 validate 全部 completed/delivered，review 已 running。没有重复派发审核。

## 边界与回退

不 OCR 猜造图片文字，不删除合法解析，不放宽 URL 原文来源检查，不豁免带图的真实重复文章。仅隔离提交本记录与表格等价修复，不自动上传/发布，保留其它并行改动。

同步 master 后，合并执行 `workflow.test_source_url_image_integrity workflow.test_batch_fill workflow.test_agent_tasks`：86 项通过。

全量 `python -m unittest discover -s workflow -t .` 已运行约 6 分钟，输出中已有其他 F/E 且数分钟没有新增输出，故中断；未获得最终失败清单，不能判定全量通过，也未将这些未定位失败归因于本次修改。定向测试及实批两项原失败检查均已通过。

代码回退仅移除此记录对应的路径正则边界、图片字母标记分支及新测试，勿覆盖 `agent_tasks.py` 的其它并行修复。数据备份可用于逐项比对；回退会重新引入已证明错误的网址，不应覆盖已经继续推进的批次。
