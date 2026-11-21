# 卡点：装配溯源校验未识别听力 AI 转录标记导致补全后 assemble 连续失败

- id: 2026-11-21-装配溯源校验未识别听力AI转录标记导致补全后assemble失败
- status: fixed
- batch: 【木牍教育】2024中考英语试卷
- batch_dir: /Users/zjt/Desktop/上传插件版/workflows/【木牍教育】2024中考英语试卷
- stage: assemble
- check: Explanation not found in supplied source material (verbatim after normalizing marks)
- created: 2026-11-21T23:33:00+08:00
- last_seen: 2026-11-21T23:33:00+08:00
- occurrences: 4
- fixed_at: 2026-11-21T23:49:48+08:00
- fix_commit: working-tree
- verification: 14个工作流回归模块328/328通过（含 test_ai_supplement::test_provenance_and_check_explanations_accept_ai_transcript_mark）
- title: 装配溯源校验未识别听力 AI 转录标记导致补全后 assemble 连续失败

## 现象（卡点）

批次 `【木牍教育】2024中考英语试卷` 在工作台执行「听力材料补全」后，解析末尾写入了 `（AI转录，待校对）`（`AI_TRANSCRIPT_MARK`）；随后触发 `assemble` 时连续 4 次失败进入 `needs_review`，报错 `Explanation not found in supplied source material (verbatim after normalizing marks)`。

## 具体检查与判定

- 失败检查：`agent_tasks._provenance` 及 `check_explanations.audit`
- 判定口径：`upload_content.py` 与 `ai_supplement.py` 已支持 `AI_TRANSCRIPT_MARK`，但 `agent_tasks._provenance` / `_provenance_text` 仅判断了 `AI_MARK`（`（AI生成，待校对）`），漏掉 `AI_TRANSCRIPT_MARK`；同时 `check_explanations.audit` 初始化了 `stat['transcript']` 却未分支统计 `AI_TRANSCRIPT_MARK`。
- 修复方向：在 `agent_tasks.py` 引入 `AI_TRANSCRIPT_MARK`，解析末尾放行、答案禁止出现该标记；在 `check_explanations.py` 将 `AI_TRANSCRIPT_MARK` 计入 `stat['transcript']`。

## 修复记录

- 修复方式：更新 `app/workflow/agent_tasks.py` 与 `app/scripts/06-upload/check_explanations.py`，并在 `app/workflow/test_ai_supplement.py` 补齐单测。
- 修复提交：working-tree
- 复验证据：14 个工作流回归测试模块 328/328 通过。
- 标记时间：2026-11-21T23:49:48+08:00
