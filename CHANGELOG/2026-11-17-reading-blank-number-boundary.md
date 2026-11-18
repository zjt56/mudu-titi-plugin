# 阅读材料数字误判为空位题号与旧组跨大题串节修复

日期：2026-11-17

## 背景

批次【木牍教育】2024中考英语试卷 结构确认被阻断：

- `QUESTION_COUNT_MISMATCH`：Ⅷ.阅读理解声明 20 小题，实际定位 23 个；
- `NUMBERING_RESTART` / `DUPLICATE_NUMBERS`：重复归属 2、5、6、56、57、58；
- `QUESTION_NUMBER_INFERRED`：Ⅹ.书面表达无印刷题号，候选 81 需核对。

## 根因

1. 阅读材料（正文第163行）时间 `　　5：30~6：00`、`　　6：15~6：45` 与词汇注释（第165行）`※Notes：1.register　2.contact` 的「全角缩进 + 数字 + 冒号/点号」被 `blank_numbers` / `_weak_numbers` 当成空位编号 5、6、2 收进阅读，导致声明 20、定位 23，并把 2/5/6 混入题号序列。
2. 旧 `structure.json` 里因此存在污染组 `[2, 5, 6, 56, 57, 58]`。`_build_units` 用全局 `group_of[number]` 建索引，只要本节含任一相同数字就采用整组，于是这个阅读组被错误挂到听力短对话（含 2、5、6），产生跨大题组与重复归属。

## 改了什么

- `app/workflow/extract.py::blank_numbers`：空位号后接数字、`.`/`．`、`:`/`：`、`、`、`)`/`）` 时不再计入（时间、序号注释、小数、带句点的行内编号）。
- `app/workflow/preview.py::_BLANK_BEFORE_NUMBER`：同步同一负向边界，保证结构锚点与预览同源。
- `app/workflow/structure_gate.py::_build_units`：组必须在所属大题的完整题号集合内才采用；部分命中视为候选缺陷而非法证据，不再把旧污染组整组挂到另一节。
- 回归：`workflow.test_extract::test_reading_times_and_notes_are_not_numbered_answer_blanks`、`workflow.test_structure_gate::test_partial_group_match_does_not_leak_into_another_section`。

## 未改什么（边界）

- 未删除 inferred：作文确实无印刷题号，仍按 81 内部顺延并保留 `QUESTION_NUMBER_INFERRED` 说明；上传范围仍按来源证据投影 `null/null`。
- 未重跑 OCR/模型：使用 `runner refresh-extract` 按留存正文与 OCR 证据重算确定性提取尾巴。
- 未放宽任何结构校验；阅读/听力分组仍逐个单元对照原卷题号。

## 验证

- `python -m workflow.runner refresh-extract --batch-dir workflows/【木牍教育】2024中考英语试卷`：阅读恢复为 56–75 共 20 题，听力 1–5、6–7、8–10，完形 31–50，无 23 计错误。
- `python -m workflow.runner run --stage structure_gate`：`passed=True`、`blockers=[]`；确认结构 34 单元（阅读 6 组、听力 1–5 独立 + 2 组、书面表达单节 81），无 `GROUP_CROSSES_SECTION`。
- `workflow.test_structure_gate`：20 项通过；`workflow.test_extract`：37 项通过；预览四文件 27 项通过。
- 界面自动推进到 preassemble/types 完成。

## 遗留与并行口径

排查期间发现另一并行会话正在把 `structure_gate` 改为「结构确认只产出字段、不阻断，疑点降级为 structure_warning」（新增 `structure_warnings`）。该口径会使 `test_structure_number_inference::assert_inferred_blocked` 等原本期望“无证明的作文顺延必须阻断”的用例失败（约 34 项）。这属于并行上游规则变更，本记录不改写也不回退其代码；该批测试口径需由该变更统一更新测试与 CHANGELOG。

## 回退

回退本次三个实现文件与两条回归测试；批次侧可用备份的 `01_source` 与 `refresh-extract` 复原。不得直接回写历史 manifest。

## 追加：听力切段锚点吸附与晚到音频投递（同一批次续修）

结构通过后该批次进入听力阶段，暴露两处通用缺陷，均已修复：

1. **切段锚点吸附跨过前一段**：中文听力卷整段试卷播报在前，题号锚点落在播报之后；`_snap` 取「锚点前最后一个长静音」会跨过前一个题组，导致 q-1/q-5 与多个 group 的 `unsupported_start/end`。改为在 `near_window` 内吸附到锚点**最近**的长静音（短静音仅在更近时参与），并在 `section_anchors` 取该大题**首次**播报（试卷朗读两遍，避免以第二遍起点把后段材料错误归入上一单元）。修复后 9 个听力单元边界全部落在真实长静音切点。文件：`app/audio/segment.py`；回归：`workflow/test_audio_boundary_truth.py::test_anchor_snaps_to_nearest_long_silence_not_last_prior_pause`、`::test_fine_pause_snaps_only_when_closer_than_any_long_silence`。
2. **晚到音频改写已部署媒体地址与视图投影**：`batch_fill._apply(audio)` 原把已有 `/source-media/...` 音频附件覆盖成相对路径；`project_audio_views` 与 `handoff._assert_meta_projection` 又把本地相对媒体路径与已部署 URL 判为「非音频字段变化」。修复：音频投递保留已部署 URL；两处比较对 image/audio 附件统一套用「部署 URL 规范化」并按空列表归一，只豁免媒体 URL 归一，不豁免题干/标签/答案。文件：`app/workflow/batch_fill.py`、`app/workflow/handoff.py`；回归：`workflow.test_batch_fill`（102 项含音频段与边界测试通过）。

验证：`runner run --stage audio --force` 通过；`assemble/meta/underlines/validate` 重新执行并通过；`review` 两轮完成，`review-gate` 显示「20 项差异待人工审核」（知识点标签差异，属设计产出，非卡点）。确认结构 34 单元（阅读 6 组 56–75 共 20 题、听力 1–5 + 6–7/8–10/11–15/16–20、书面表达单节 81），结构闸门 `passed=True`、`blockers=[]`。

未改：不改题干/答案/标签内容，不自动 resolve 审核差异，不自动上传；听力切段仍以服务静音骨架为准，不人工调行。
