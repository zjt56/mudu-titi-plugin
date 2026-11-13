# 课时结构/答案/选项绑定四处确定性缺陷修复（批次22 Unit2 第三课时 Grammar）

## 背景/触发

批次 `22七上人教版课时A计划Unit2We’reFamily!第三课时Grammar` 卡在结构自动确认：
`NUMBERING_RESTART：题号跨大题重置或重复`（同一报错重复 5 次）+ `SOURCE_NUMBERS_UNDECLARED`。
用户强调：这类归属必须由视觉大模型判断，不能退回全局数字合并。

排查后确认不是单一问题，而是四个相互叠加的确定性缺陷（全部与模型无关，可用真实批次复现）：

1. **印刷题号与答案内容黏连时丢题**：正文 `4.30 <u>…</u>(minute) walk…`、`(　)2.1st June is…`
   的题号后面直接跟数字。`source_anchors.printed_tokens` 为避免把小数 `3.5` 当题号而有
   `(?!\d)` 限制，于是这两行没有锚点；`_build_scoped_units` 的独立题分支只认
   `printed_tokens`，结果第 4 题整题缺失、第 2 题归属丢失，
   `_proven_scoped_restart` 因本地单元不完整拒绝放行 → `NUMBERING_RESTART` 与
   `SOURCE_NUMBERS_UNDECLARED`。
2. **答案附录的视觉栏目标签不被识别**：附录里 `语法精练`/`课时精练` 是
   `[IMG …]语法精练[IMG …]` 形式（装饰图片包裹）。`_compact_answer_blocks` 只看原始
   markdown，标签行被当成“无归属散文”，把前一个跨两行的 `Ⅳ.` 块标成 invalid，
   整个 compact 绑定抛 `duplicate or malformed compact answer block` → 33 个答案全部无法绑定。
3. **整卷栏目归属调用预算不足**：`visual_columns` 默认 `max_tokens=16000`、超时 180s。
   实测整卷 4 页栏目枚举需要约 6k tokens / 170s，默认值恰好在生成途中截断
   （`TRUNCATED_RESPONSE`），闸门报 `COLUMN_OWNERSHIP_UNRESOLVED`。
4. **独立题绑定只取题干行，丢掉选项行**：`question_draft._bound_source` 对
   `basis=ai_classified` 的独立题只绑定 stem 行（26 行），而选项在 27–28 行，且单元已
   声明 `source_parts`（stem 26 / option 27-28）。于是 batch.base 的 `options=[]`，
   组装审计报 `OPTIONS_MISMATCH`（第 1/2/3… 题）。

另外发现一处误导：CLI `runner` 把 `429 insufficient_quota` 写成 `failed`，诊断据此
提示“按校验器问题修复”，把外部额度问题伪装成内容缺陷。

## 改了什么

- `app/workflow/structure_gate.py`：`_build_scoped_units` 独立题锚点改为
  `printed_tokens` 与 `_strong_number`（行首题号规则）取并集，仍只取强锚点、不引入弱空位号，
  修掉“题号黏连答案内容被丢题”。
- `app/workflow/upload_content.py`：
  - 新增 `_answer_feature_topics()`，把**已验证的视觉栏目标签**作为答案分区主题；
  - `_answer_section_topics()` / `_compact_answer_blocks()` 接受并消费该主题，
    附录里 `[IMG]语法精练[IMG]` 这类带装饰图片的栏目标签按可见文字识别，开启新分区并
    正常关闭上一块（不再被当成散文污染前一块）；
  - `_scoped_answer_candidates()` 从已验证 `lesson-bands.json` 读取 features 传入两侧同一口径；
  - 新增校验：确认窗口短于已验证板块边界时直接拒绝（不得把本板块答案让给相邻板块）。
- `app/workflow/visual_columns.py`：整卷栏目归属默认 `max_tokens` 16000→32000、
  超时 180→600s（可用 `WF_VISUAL_TIMEOUT` 覆盖），截断重试上限 32000→64000；
  仍严格绑定所选路由，不做跨模型降级。
- `app/workflow/question_draft.py`：`ai_classified` 独立题按已声明 `source_parts`
  把同一题的选项行一并绑定，只允许在确认 span 内延伸，不越过 span 吞下一题。
- `app/workflow/runner.py`：agent 阶段遇 429/额度（`auto._is_rate_limited`）时回到
  `pending` + 冷却（返回 4），与网页后台同一口径，不再写成 `failed`。
- `app/workflow/extract.py`：函数内 `from . import …` 改为绝对导入，`python workflow/extract.py`
  这种直接脚本入口（`__package__` 为空）也能完整运行。
- 测试：`test_content_structure.py` 的视觉夹具支持指定起始行，并把“无号声明掩盖印刷题号”
  改为断言在视觉证据落盘前即被 `COLUMN_NUMBERLESS_HAS_PRINTED_ANCHORS` 拒绝。

## 没改什么 / 边界

- 没有放宽任何来源绑定、无号题 `null/null` 投影、视觉 fail-closed 或上传边界；
- 没有按全卷数字兜底合并题号，跨板块同号仍靠来源坐标与视觉栏目身份；
- 答案仍逐条来自附录真实坐标，模型自述不作为证据；
- 视觉不可用仍是“不可用”，不换模型、不回退数字猜配。

## 如何验证

- 真实批次 `22…Unit2We’reFamily!第三课时Grammar`：
  - 结构闸门由 failed → **passed**（26 单元；`column_ownership/extraction_evidence/
    coverage/source_number_coverage/grouping…` 全部 passed，5 条 NUMBERING_RESTART 均
    “已核对：来源位置、出现序次和身份均已独立重建验证”）；
  - 视觉栏目归属 `verified`，9 个栏目（语法导图 guidance / 语法精练 container + 4 大题 /
    课时精练 container + 2 大题），印刷题号完整（含此前丢失的 4）；
  - 答案 33 条全部绑定，并与确定性直抄 `answers_direct.build_payload` 逐条一致（0 mismatch）；
  - `knowledge` 完成；`assemble` 的 `OPTIONS_MISMATCH` 根因已修（batch.base 选项恢复）。
- 回归：`test_runner_auto`、`test_visual_columns`、`test_structure_gate`、`test_question_draft`、
  `test_answer_lesson_bands`、`test_content_structure`、`test_diagnose`、
  `test_structure_source_shape_regressions`、`test_upload_numbering`、`test_extract`
  合计 **249+102 项通过**。

## 未完成 / 外部依赖

批次继续推进时 `preassemble` 需要一次模型调用，但当前 Provider 额度已用尽
（`429 insufficient_quota`）。这是外部额度问题，不是题文/来源/校验器缺陷：阶段已按新口径
回到 `pending` 冷却，额度恢复后自动续跑；本地确定性部分（结构、视觉归属、答案、选项绑定）
均已验证通过。

## 回退方式

四处改动互相独立，可分别回退：`structure_gate._build_scoped_units` 锚点并集、
`upload_content` 的 feature 主题与板块窗口校验、`visual_columns` 预算默认值、
`question_draft._bound_source` 的 source_parts 延伸。
