# 仓库卫生与临时路径断言修复

日期：2026-11-13。类型：测试稳定性 + 文件卫生。

## 背景

上一轮提交前验证暴露两类问题：

1. `test_content_agent` 在 macOS 上把 `tempfile` 的 `/tmp/...` 与代码 `Path.resolve()` 后的 `/private/tmp/...` 直接比较，2 项误报；
2. `git diff --check` 报多条 `new blank line at EOF`，来源是 `issue_log.fix()` 重写已有「修复记录」段时在文末多留了一个换行。

只改这一次的断言/文件不能防止后续再出现。

## 改了什么

- `app/workflow/test_content_agent.py`：`self.base` 统一用 `Path(...).resolve()`，所有批次路径从根上就是规范路径；去掉两处临时补的 `.resolve()`。
- `app/workflow/issue_log.py::_set_section`：重写已有段后统一 `rstrip("\n") + "\n"`，文末只保留一个换行。
- 全仓清理 38 个已跟踪文本文件的末尾多余空行（含 `ISSUES/fixed`、文档和两个代码文件）。
- 新增 `app/workflow/test_repo_hygiene.py`：扫描 `git ls-files` 的文本文件，禁止文末空行；非 git 检出环境自动跳过。
- `app/workflow/test_issue_log.py`：标记修复后断言文件只以一个换行结尾。

## 没改什么 / 边界

- 不改任何批次数据、审核结论、上传边界或业务行为。
- 只清理文末空行，不改正文内容；二进制文件跳过。
- 仓库卫生测试只守卫「文末多余空行」，不扩大到全仓格式重排。

## 验证

- `workflow.test_repo_hygiene`、`workflow.test_issue_log`：15 项通过。
- `workflow.test_content_agent`、`test_upload_payload`、`test_format_gate`、`test_stage_semantics`、`test_repo_hygiene`：169 项通过。
- `node --check lib/client.js` 通过。

## 回退

撤回 `issue_log._set_section` 的换行归一、测试根路径 `.resolve()` 与新增卫生测试即可；已清理的文件可保留，不影响运行。
