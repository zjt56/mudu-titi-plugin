# 2026-11-08 标注阶段改为整卷一次分析（不再一题一片）

## 背景 / 触发

用户反馈每个阶段都很慢，并明确要求：“不要分篇，就整张试卷拿去分析、去匹配；
试卷里包含答案解析就用，没有就留空占位；然后统一写到上游批次格式里组装，再打标签。”

排查实测（23 年安徽卷，34 个题目单元）：

- `label_part_units` 默认 1 → 题型/解析/知识点阶段**一题一片**，共 34 片；
- 每片都完整重复带整卷上下文：`question-draft` 69.6K + `structure.confirmed` 30K
  + `confirmed_units` 30.6K + `pdf-ocr` 34K + PDF 提取文本 32.5K + 正文 26K + 规则目录 ~95K
  ≈ 21.5 万字符/片，34 片合计 7.3MB；
- 结果：explanations 296s、knowledge 209s。

## 改了什么

1. **标注阶段默认整卷一次**（`workflow/local_agent.py`）：新增 `label_split`（默认 False），
   题型/解析/知识点阶段不再一题一片；装配/审核仍按 `unit_split` 分片。
2. **单次分析精简提示词**（`workflow/agent_tasks.py`）：
   - `build_task(..., slim=True)`：单次调用时只保留最小单元索引（unit_id/numbers/kind/type），
     不再整份下发 `structure.confirmed` 与单元题干副本；
   - PDF OCR 只在装配/审核（需要版面证据）下发，标注阶段不再带；
   - 已有 Word 原文时，标注阶段不再重复下发 PDF 提取文本（哈希仍绑定）；
   - 知识点目录只下发**路径清单**（73K → 约 10K），ID 由导入器按路径自动反查回填；
   - 不再重复下发 `knowledge-catalog.md`（与路径清单重复）。

结果：整卷单次 prompt 约 18.4 万字符（原 36.6 万），一次调用完成。

## 没改什么 / 边界

- 分片机制与快照复用能力保留（`label_split: true` 可恢复一题一片）。
- 解析/答案缺失仍按既有规则填「无」占位，不编造。
- 内容正确性关卡不变：导入校验、`validate`/`format_gate` 全量体检照旧。
- 不自动上传/发布。

## 如何验证（23 年安徽卷实测）

| 阶段 | 改前 | 改后 | 说明 |
|---|---|---|---|
| knowledge | 209 s / 34 片 | **69 s / 1 次** | 34 单元全覆盖，knowledgeIds 自动回填 34/34 |
| explanations | 296 s / 34 片 | **80 s / 1 次** | 34 单元；组级 10 条解析、子题 40/60 有解析，其余按规则「无」 |

- `workflow.test_label_part_reuse` + `test_agent_tasks` + `test_unit_tasks` +
  `test_content_labeling` + `test_knowledge_content_fallback` + `test_explanations_direct`
  + `test_agent_protocol` 共 **131 项通过**。

## 回退

`label_split: true`（配置）即可恢复分片；其余精简可按上述条目还原。
