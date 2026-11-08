# 2026-11-08 修复2017卷表格空列丢失及格式诊断误报

## 触发与实际原因

用户报告【木牍教育】17年安徽中考英语“最终上传格式检查”卡住，原因显示“上传前必须读取当前启用通用知识点版本及完整节点快照”。

实际 `07_review/upload-format.json.errors` 仅有 `MATERIAL_TABLE_GRID_INVALID`，对象为 `group-75-76-77-78`。`CATALOG_REQUIRED` 属 `deferred`，本地离线格式检查将真实远端目录读取留给 prepare-upload，本来不阻断。诊断器扫描日志尾部的“必须”字样，误将延期检查解释成失败原因，自动修复记录也因此偏离真实问题。

独立读取 Word 原件发现诗歌表实际4列，首行 `gridBefore=1` 后接三首诗，第二行 Notes 的 `colspan=4` 正确。提取元数据已保留这一偏移，但 question_draft 转换丢了行首空列。原件 SHA256 与 extraction.source_sha256 一致，不应将 Notes 擅改为跨3列。

## 实现

- `question_draft._source_table_rows`：显式保留 Word grid_before/grid_after 的空白位置为线格式空 cell；保留原始文字及 rowspan/colspan，垂直合并续行仍由原始跨度表达。未定义/非法偏移不作猜测。
- `self_heal_actions._prepare_tables`：扩展既有受控表格恢复，只有唯一源表绑定、旧 rows 严格等于已知丢空列投影时才补回空位置。验证提取所绑定原件摘要，拒绝双材料别名；通过原有备份/收据/来源审计/安装复验路径更新批次。无证据的缺格或非空文本变化不自动修。
- 将 question_draft 纳入修复规则摘要。
- 按用户后续“将这个修复的规则写到插件里，避免下次出现此类问题。同时提交到GitHub”的要求，同步根 `AGENTS.md` 硬规则16/17、`CHANGELOG/RULES.md` 和已随插件打包的 `app/docs/workflow-regressions.md`。规则对应实际转换、事务恢复与诊断代码及回归，不仅是一条人工注意事项。
- `diagnose`：格式阶段优先读取报告 errors，展示错误码、unit_id、说明；缺损报告回退完整格式日志的结构化 errors，普通异常回退前排除 warnings/deferred。错误数组为空但阶段失败时保留证据不足说明，不伪造通过。表格错误的修复提示指向具体材料网格字段与判定口径。
- 回归：真实4列稀疏表→草稿→payload检查、原件绑定与事务恢复→最终格式通过→幂等、双别名/原件变化/内容变化拒绝，以及诊断报告优先/缺损回退/延期项不冒充阻断。

## 真实批次恢复

只操作用户指定的 `workflows/【木牍教育】17年安徽中考英语`，未处理归档或其它卷。

- 通过 `apply_source_table_repair` 安装已证明的空列恢复，收据 `workflow/format-migrations/bb183e6aacdb4c8db2f6dccc5fc1f15c/receipt.json`，原文件备份保留。未直接编辑台账制造完成态。
- 原始 Word、正文/来源证据、两轮审核结果字节不变；真实标签差异保留，不因格式修复清空人工队列。
- 使用 runner 正常重跑 validate：19项检查全部通过、阻断0、提示0、跳过0。
- 使用 runner 正常重跑 format_gate：completed；passed=true、errors=[]；48单元、96题、36附件。
- 工作台实际 HTTP /api/batches：flow_done=true，diagnostics.blocking_count=0，uploaded=false。审核11项差异仍保留，未提交题库。真实上传时仍读取启用目录和完整节点，不放宽上传安全校验。

## 测试

最终针对 diagnose/question_draft/self_heal_actions/format_gate/docx_extract 共90项通过（1.699秒）。最终防护及加强断言后的隔离全量1690项（88.899秒），9 failures、0 errors、17 skipped，失败名称集合与既有9项基线完全一致，无新增失败。不能将基线失败表述为全通过。

## 边界与回退

没有编造诗歌/Notes，没有更改答案或知识点，没有联网读取或改写远端目录，未自动上传/发布。只把源布局中明确的空白位置转换成服务器支持的空格子，实际网格错误仍拒绝。

代码可回退本次实现；批次可依据上述私有事务收据中的原始字节备份回滚。回滚应走同类受控流程并重跑检查，不能直接改台账为完成。
