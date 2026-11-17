# assemble 元数据附件传输地址兼容

## 背景

批次 `七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun` 在 `assemble/meta` 消费 `06_batch/batch_meta.base.json` 时失败，报错为 `Metadata payload changes protected body field: attachments`。附件内容未变化，仅传输 URL 可能由本地源路径变为部署路径。

## 改了什么

- `app/workflow/batch_fill.py`：metadata 保护字段检查对 `attachments` 复用既有 canonical body 归一化，忽略传输 URL 差异，但继续保护附件的名称、类型、placement 和数量等身份字段。
- `app/workflow/test_batch_fill.py`：新增等价 URL 可接受、附件身份变化仍拒绝的回归测试。

## 没改什么 / 边界

- 不修改批次原件、题干、选项、附件文件或上传边界。
- 不把真实附件身份变化降级为提示；真实变化仍阻断 metadata 消费。
- 本次修复只处理 metadata 保护字段误报，不自动上传或发布。

## 验证

- `PYTHONPATH=app python -X utf8 -m unittest workflow.test_batch_fill.BatchFillTest.test_metadata_snapshot_accepts_equivalent_attachment_url workflow.test_batch_fill.BatchFillTest.test_metadata_snapshot_rejects_attachment_identity_change`：2 项通过。
- 对目标批次 `C:\Users\Administrator\.dsh\wf-plugin-data\workflows\七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun` 只读比较 `06_batch/batch.json` 与 `06_batch/batch_meta.base.json`：5 个顶层单元（含子题）保护字段检查通过，写入 0 次。

- `PYTHONPATH=app python -X utf8 -m unittest workflow.test_batch_fill`：43 项全部通过。Windows 默认 GBK 模式的测试夹具读取错误通过显式 UTF-8 运行模式排除。
- 工作台当前实例健康接口返回 200；`/api/batches` 返回 200，实测 2.73 秒。此前冷投影超时为已观测现象，未仅凭此认定唯一根因，也未修改列表实现。
- 最新工作台状态显示目标批次 `meta/assemble/validate/format_gate` 均为 completed，诊断 `blocking_count=0`；1 项真实审核差异仍保留，未自动放行、上传或发布。

## 回退方式

回退本记录对应代码提交即可恢复原保护比较逻辑；回退后等价附件传输 URL 会再次触发误报。
