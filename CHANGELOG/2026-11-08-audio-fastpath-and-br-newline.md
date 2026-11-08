# 2026-11-08 修复听力切段快路误判与装配 `<br>` 换行

## 背景 / 触发

用户报：14 年安徽卷「听力切段与音频填充」报 `代理产出待复核`，提示需要重新切段；
随后装配又报 `[B10] <br>换行: q-101`。两处都让流程卡住。

## 根因（两个独立缺陷）

1. **听力切段快路永远误判“证据不足”**（`app/workflow/agent.py` `_audio_evidence_ready`）：
   切段证据 `segment-evidence.json` 里 `audio` 是绝对路径，`_collect` 交给模型前会脱敏成
   `[host-path-redacted]`；而快路却拿“脱敏后的上下文”和“磁盘原文件”直接比较，
   两者永不相等 → 即使证据完全合规也判为不足 → audio 永远 needs_review。
2. **装配回填题干把 `<br>` 带进题库**（`app/workflow/agent_tasks.py`）：
   原卷 docx 提取用 `<br>` 表示表格单元格内换行；装配时 `restore_source_stems`
   等按原卷逐字回填，于是 `stem` 里出现 `<br>`，被入库约束 B10 拦下。

## 改了什么

1. `_audio_evidence_ready`：比较前对磁盘原文件做同样的脱敏
   （`agent_tasks._sanitized(document)`），确保同一口径。
2. `_normalize_blank_text`：统一把 `<br>`/`<br/>` 转成真实换行 `\n`（约束 B10 要求），
   装配回填的 stem/material/materialBlocks 都走这一步。

## 没改什么 / 边界

- 听力切段仍需先有真实切片产物；没有切段时仍会提示先切段或明确跳过听力。
- 不放松原文保真：`<br>` 只是换行表示方式，转成 `\n` 后文字内容不变。
- 不自动上传/发布。

## 如何验证（14 年安徽卷实测）

- audio：`{"stage":"audio","status":"completed","detail":"切段产物已通过校验，未调用模型"}`
- assemble：`{"deterministic":true,"model_calls":0,...}` → completed
- 继续推进：meta / underlines / validate 全部 completed，本地体检通过，停到 review。
- `workflow.test_agent_tasks` + `test_agent_entry` + `test_audio_skip` +
  `test_audio_stage_fastpath` + `test_assemble_local` + `test_content_labeling` +
  `test_question_content` 共 **120 项通过**（4 跳过）。

## 回退

还原上述两处函数即可。
