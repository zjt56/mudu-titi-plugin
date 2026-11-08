# 2026-11-08 解析/大意改为按题号从 Word 原文直接抄录（不调模型、不严格溯源）

## 背景 / 触发

用户反馈：2023 安徽卷卡顿且跑不出结果。根因是 explanations 阶段让模型重抄解析，
再用“逐字绑定原题”的严格来源校验比对模型输出，一有 OCR 星号/引号/全半角/空格差异
就判失败并自动重排重跑（34 分片 × 130K 字符，连跑多轮，占满 CPU）。

用户明确要求：上传文件本身就把试卷、答案、解析放在一起（Word 原件），
**确定正文与题号后，按题号把对应原文直接抄进去即可；答案形式多样，不做繁复格式校验。**

## 改了什么

- 新增 `app/workflow/explanations_direct.py`：从 Word 提取文本
  （`01_source/materials/source.txt`，缺失退回 `01_source/正文.txt`）按题号直接抽取
  解析与文章大意，清洗 OCR 星号后逐字抄入 `04_knowledge/explanations.json`：
  - 有解析就抄、没有填「无」；孤立 `【解析】` 段归属其上编号答案行；
  - 文章大意按阅读/完形等题型单元顺序归属；
  - 完全不调模型、不与 PDF OCR 比对。
- `app/workflow/agent.py`：explanations 阶段新增确定性快路——上述直抄能产出就
  直接安装并登记完成，不调用模型。
- `app/workflow/content_rules.py`：带 `DIRECT_COPY_EVIDENCE` 标记的直抄产物跳过
  “逐字绑定原题”的严格溯源（仍保留结构、覆盖、占位与生成内容检查）；模型响应不含该
  标记，仍走原严格校验，编造/改写仍被拒。

## 没改什么 / 边界

- 模型参与的非直抄解析仍严格校验，不得编造、改写或跨材料。
- 不改审核口径、不上传、不动用户批次数据。

## 如何验证

- 2023 安徽卷实数据：直抄产出 81 题位、50 条原文解析、31 条无解析占位（听力/拼写/
  无解析题），`agent_tasks._validate(... 'explanations')` 通过。
- `workflow.test_explanations_direct`(4) + 相关解析/标签回归共 122 项通过。

## 回退

移除 agent 的 explanations 快路与 `explanations_direct.py`、`content_rules` 的直抄跳过分支即可。

## 追加修复（2026-11-08 续跑打通）

续跑 23 卷时发现三处“上游已产出、下游却判定不合规”的交接误判，均已修正（不放松原文保真）：

1. **audio 交接误用代理 payload schema**（`workflow/handoff.py`）：audio 的交付物是
   `audio_map.json + segment-evidence.json`，之前用 `_validate('audio')`（要求每行含
   evidence）去查，导致 audio 永远“未交付”、assemble 一直“等待上游 audio”。改用
   `audio.consistency.pair_is_consistent` 验收（覆盖、文件摘要、绑定一致）。
2. **图片缺失被当阻断**（`workflow/handoff.py`、`workflow/runner.py`）：assemble 交接与
   validate 的原卷审计把 `SOURCE_IMAGE_MISSING` 等图片/选项图问题当硬阻断，使 meta/validate
   无法推进。按“图片/音频能力受限只作 informational 不阻断”的既有规则，改为只记 warning；
   文本类（结构/题号/题干/材料归属）仍阻断。
3. **补全对话/选项块带 OCR 星号识别失败**（`workflow/question_content.py`、
   `workflow/question_draft.py`）：`**A. …**` 使 `option_block_start` 认不出选项块，
   group-51-55 补全对话 options 为空、assemble E20 拦截。识别前剥离 `**` 定位，写入时清星号。

另：**“继续处理”清冷却**（`workflow/supervisor.py` `clear_exhausted` + `workbench/server.py`）：
点继续先清 supervisor 的 exhausted/长冷却，从当前阶段立即续跑。

验证：23 卷实数据 assemble（确定性 0 模型调用）→ meta → underlines → validate 全部通过，
推进到 review（需模型的两轮审核，正常调度）。相关回归随后复跑。
