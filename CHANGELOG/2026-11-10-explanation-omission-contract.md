# 2026-11-10 解析防遗漏提示词与同源代码规则

## 背景

用户要求“加强提示词约束并写进代码规则，确保后续不会出现”，随后要求继续完成。承接 7c7357c 的 2024 卷修复，将本次根因转为跨入口的实际提示约束与回归契约。

## 改了什么

- `content_rules.RULES` 加入“防遗漏交付硬约束”，明确括号/方括号/图标文字标题均需识别；填「无」前核对同题/同篇 Word 与 PDF 来源；保留多行原文；原始冲突不能挑首条或伪装缺失；大意按来源坐标和完整题号集合绑定，不按顺序分配。
- 同一规则要求直抄、source_ref、模型字符串共用来源/归属/遗漏校验，证据说明、status=completed、空 findings 不能豁免；遗漏错误必须局部修复并用原检查复验，禁止改无关字段或跳检查。下游须同步已接受内容，书面表达例外不变。
- `unit_tasks` 的实际解析分片提示明确图标标题、完整题号集合及 ORIGINAL_EXPLANATION_OMITTED / ORIGINAL_SUMMARY_OMITTED 的局部修复边界。
- `prompts/roles/explanations.md` 将笼统“修不了回上游重跑”改为按原文坐标修具体解析/大意；仅有真实原件缺损证据时才允许受控提取修复。
- 新建 `content_labels.py`，统一供来源体检与 source_ref 展开使用标题语法，确定性直抄沿用同一来源体检规则。补齐 source_ref 原先只剥括号标题、未处理方括号和图标标题的遗漏，支持标题内起始列吸附正文；裸图片不提供内容语义，错题/冲突/遗漏校验仍有效。
- `test_direct_explanations_delivery` 验证真正构建出的完整任务、文件协议、分片提示均携带防遗漏条款；`test_content_fragments` 验证图标/方括号标题引用、标题内列号与错题引用拒绝。
- 根 AGENTS 硬规则22、content-labeling/source-fragments 文档和 RULES 台账同步。

## 没改什么 / 边界

不改任何用户批次，不重跑已完成审核，不清空差异，不自动上传。没有更改 JSON 必填键、来源优先级、作文例外、标签或结构硬校验。未来任务构建使用新提示词，不修改冻结的历史任务。

## 验证

- 定向回归 180 项通过：content_fragments / direct_explanations_delivery / explanations_direct / unit_tasks / agent_roles / agent_tasks / upload_content。
- 全量 workflow：2016 项，1997 项通过、19 项跳过，失败/错误 0；耗时83.885秒。采用独立临时目录隔离测试锁；19项跳过包含原有17项，以及沙箱禁止 ps 的2条进程探活用例。
- git diff --check 通过。
- 拉取远端尝试失败：HTTPS HTTP2 framing error，未强推或覆盖远端。

## 回退方式

回退本提交恢复此前提示词与独立标题语法；7c7357c 已有的直抄来源校验与下游同步保护继续保留。
