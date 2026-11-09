# 2026-11-09 最终格式与来源固化失败诊断

## 背景

2020 安徽卷现有 `07_review/upload-format.json` 为 `passed:true/errors:[]`，但阶段台账记录“固化批次失败：Source changed before finalization”。旧诊断遇到空 errors 就显示“失败原因未记录”，遮蔽了已记录的实际钩子异常。上游审核已执行但未放行时也笼统显示“上游未完成”。

只读比较来源对象确认：5 个题组的旧来源基线 stem 为空，现解析器补回同大题共用导语；来源切片摘要与媒体摘要相同，题组对象正文确实变化。来源 token 拦截正确，不能作为 JSON 序列化变化绕过。

## 改了什么

- `app/workflow/diagnose.py`：真实格式 errors 仍优先；空 errors 时保留本次台账固化异常，并说明报告不证明整个阶段完成。固化失败指引恢复来源对象后复验，不误导用户去修不存在的格式错误。
- 同一诊断视图区分上游未完成执行、已执行但质量/交付不满足、已执行但当前产物/消费者校验不满足；审核差异队列显示质量未放行及真实未解决项数。
- `app/workflow/batch_fill.py::mark_finalized`：来源 token 不一致时列出首个冲突 unit_id 和变化字段（正文字段、source_text_sha256、media_hashes），保留原拦截与原子写入保护。
- `app/docs/pipeline.md`：说明格式报告与固化钩子边界、受控前置对象刷新与质量放行区别。

## 验证

`python -m unittest workflow.test_diagnose workflow.test_batch_fill`：55 项通过。新增覆盖：通过报告与实际固化失败并存、上游交付未满足、真实 errors 优先、审核已执行仍保留质量差异、正文/来源/媒体变化逐字段报错及失败不写入状态。`git diff --check` 通过（仅工作区换行提示）。

## 合并与实批恢复

已快进合并远端 `ede2777`（共享导语来源与消费者一致性）和 `b48d104`（上传服务端契约），保留其它未提交修改。合并验证发现 Windows 草稿临时文件默认写 CRLF，而复用比较使用 LF，导致内容未变仍重写；`question_draft.ensure` 现显式写 LF，既有不重写回归覆盖此问题。合并后的五模块定向回归共 **177 项通过**。

当前批次恢复前已在 `workflow/repairs/section-rubric-refresh-20261109-131118` 保存批次、审核报告和台账及 SHA256。通过现有 runner 实际重验 structure_gate（清除真实重建等待），再执行 preassemble，成功刷新 5 个共享导语变化单元；没有手改 manifest 或清理审核差异。后续由原自动调度续跑并重新校验。13:12 实际台账确认 `format_gate=completed`，`review=completed`，审核闸门保留 12 项差异、`release_ready=false`；没有上传或发布。

## 边界与回退

未修改任何用户批次、审核队列或完成状态；未提交/推送，未上传/发布。未放宽来源正文、媒体或质量校验。已有 `batch_fill.initialize` 支持同来源切片/媒体/题号身份的解析器刷新并保留填充；真实意外正文改动仍先由 `_managed` 拒绝，实际批次恢复由主任务受控执行。

回退上述诊断、错误说明与新增测试即可；来源 token 算法未变。保留其它并行改动。
