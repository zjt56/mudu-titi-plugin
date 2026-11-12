# 上传题号新规落地与本机批次迁移

日期：2026-11-12。类型：运行行为 + 数据结构（派生元数据）+ 用户批次迁移。

## 背景

用户提供开发者文档中心 `http://101.43.1.57/docs/`，指出新增题号规范，要求阅读并设计修改内容；随后明确「将本机目前所有批次都进行对应的修改」，并纠正「不在放在排队区修改，你直接自己后台改」。旧实现把 `numberStart/numberEnd` 当本地字段过滤，只用 `location=第N题` 让远端推断，且按题型排除书面表达，无法满足显式范围、无号 `null/null`、原卷顺序和课时重号要求。

## 改了什么

- 新增 `app/workflow/upload_numbering.py`：`project_batch(batch, batch_dir=None)` 统一投影。独立题 `n/n`，题组 `min/max` 且要求子题号唯一、完整、连续；无号只认当前来源可重建的 `single_writing_section`/`unnumbered_section` 证据，顶层 `null/null`；scoped 跨大题同号保留原号原序，不全局去重；显式范围非法或冲突报 `PAPER_NUMBER_*`。`build_payload` 在白名单前调用它，顶层保留范围、子题过滤范围，并透出 `numbering_evidence`；删除 `location` 自动补号旧逻辑。
- 新增 `app/workflow/numbering_migration.py`：只改 `batch.json`/`batch_meta.base.json`/`batch_meta.json` 的派生范围。逐批预演、来源证据校验、仅允许范围变化、备份改前字节与 SHA-256、原子写入、写后重新投影复验；每批 `workflow/numbering-migration.json` 留档。已批准上传且已完成的批次不改最终 `batch_meta.json`，不伪造批准。
- 审核语义兼容：`stage_semantics.py` 仅对 06_batch 四个文件的 items 顶层范围做内容投影；`review.py` 只对题目初始行浅剥范围，嵌套 `evidence` 范围仍敏感；`assemble_local.py` 的组装结构指纹复用同一投影；`manifest.py::_content_digest` 对同一组文件投影范围，raw `output_hashes`、在途任务绑定、上传冻结仍精确。
- 顺手修复验证中发现的旧拦截：题组（含语音题）子题知识点留空是既定规则，上传校验原先只放行部分整篇题型，现按 `parent_group` 放行题组子题；独立题仍强制非空。回归 `test_upload_payload::test_group_children_may_leave_knowledge_empty`。
- `format_gate.py` 把编号模块纳入规则文件摘要；`upload.py` 现有 prepare/verify 走同一 `build_payload`，冻结件与新投影同源复算。
- 文档：`app/docs/upload-api.md` 更新题号段落；`app/docs/question-number-upload-design.md` 标记已实施；根 `AGENTS.md` 新增硬规则 32；`CHANGELOG/RULES.md` 追加规则条。

## 本机批次结果

- 18 个批次全部直接后台迁移，无排队、无人工确认步骤。
- 13 份整卷的无号作文写为 `null/null`；02 课时两个无号单元写为 `null/null`；其余按原卷原号；scoped 跨节重号各自保留。
- 每批 `workflow/numbering-migration/backup-<stamp>/` 保留改前文件与 SHA-256；迁移报告记录证据文件摘要、单元状态、范围统计和复验结果。
- 04、19、2024、20 四个已批准上传批次：`batch.json`/`batch_meta.base.json` 已迁移，最终 `batch_meta.json` 保持批准绑定字节；下次准备上传时仍由同一投影补范围，任何范围变化须重新预检批准。
- `batch.base.json`、题面/答案/知识点/媒体、`workflow/manifest.json`、`07_upload` 冻结件与回执均未改。

## 没改什么 / 边界

- 不自动上传、不发布、不撤回、不修改云端试卷档案；不为已上传批次补传或改远端数据。
- 不把无号题组的内部子题别名 `1` 改写成印刷号，也不新增聚合「1.」；远端子题 `number=null`/缺省契约未核实，仅记录 `UNNUMBERED_SUBQUESTION_CONTRACT_UNRESOLVED` 诊断，不阻断、不伪装合规。
- 初始实施仅改工作区；用户随后明确「提交全部并推送」，现统一提交到 master 并推送 origin，不新开分支。
- 两个迁移前已存在的上游输入不一致（04 `format_gate` 的 review-gate、23 `meta` 的 batch.json）未自动改状态或重跑，留待真实推进处理。

## 如何验证

- 隔离 TMPDIR 合并 suite：`test_upload_numbering`、`test_numbering_migration`、`test_upload_payload`、`test_upload_content`、`test_upload`、`test_format_gate`、`test_stage_semantics`、`test_assemble_local`、`test_review_local_reuse`、`test_question_identity`、`test_question_draft` 共 **297 项通过**。
- 审核/装配扩展 suite（子代理）：15 个 suite 共 **266 项通过**；另有 `test_review_read_scope` 1 项旧失败，已用 `git show HEAD:app/workflow/review.py` 基线复现，与本次改动无关。
- 迁移后逐批复验：`build_payload` 编号错误 0、顶层范围字段齐全；仅范围字段变化；备份 SHA 一致；已批准批次批准摘要仍与冻结最终件一致。
- 迁移前基线：同批 upload 相关 123 项在隔离 TMPDIR 全通过。

## 回退

- 批次：每批 `workflow/numbering-migration/backup-<stamp>/` 有改前 `batch.json`/`batch_meta.base.json`/`batch_meta.json` 与 `backup-manifest.json`；按记录逐文件原子恢复即可。已批准最终件未动，无需回退。
- 代码：撤回 `upload_numbering.py`、`numbering_migration.py` 及 `upload_payload.py`/`format_gate.py`/`manifest.py`/`stage_semantics.py`/`review.py`/`assemble_local.py` 的相关 diff；旧 `location` 补号逻辑已删除，如需完全回退按本记录反向恢复。
