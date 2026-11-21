# 卡点：2024 安徽卷书面表达尾端误吞文末答案汇总表

- id: 2026-11-22-24-writing-answer-table-leak
- status: fixed
- batch: 【木牍教育】2024中考英语试卷
- batch_dir: /Users/zjt/Desktop/上传插件版/workflows/【木牍教育】2024中考英语试卷
- stage: extract
- check: ANSWER_APPENDIX_DETECTED (_title_key 未剥离粘连在年份前的列表数字)
- created: 2026-11-22T00:00:00+08:00
- last_seen: 2026-11-22T00:00:00+08:00
- fixed_at: 2026-11-22T00:03:40+08:00
- fix_commit: working-tree
- title: 2024 安徽卷书面表达尾端误吞文末答案汇总表

## 现象（卡点）

`【木牍教育】2024中考英语试卷/01_source/原始正文.txt` 第 300 行为重复卷名 `12024年安徽省初中学业水平考试`（`1` 与 `2024年` 之间缺少点号），紧接第 302–307 行 `[TABLE 3]`（`题号 | 1~5 | ...` / `答案 | CCAAB | ...`）。`extract.py` 未在第 300 行切分附录，导致答案汇总表被吞进第 X 大题「书面表达」（Q81）题干与结尾。

## 具体检查与判定

- 失败检查：`workflow/extract.py::_title_key` 仅剥离 `^\d{1,3}[.．、]`，无法匹配无点号紧贴四位年份的前缀 `12024年…`，令 `_title_key(raw) == paper_title` 判定失效。
- 判定边界：不能绕过重复卷名直接切分任意 `[TABLE]` 答案表（`test_title_or_answer_like_table_alone_does_not_cut_real_body` 明确禁止误切正文表格）。
- 修复方向：在 `_title_key` 中剥离行内 `[IMG ...]` 并将前导列表数字模式扩展为 `^\d{1,3}(?:[.．、]|(?=(?:19|20)\d{2}\D))`，从重复卷名处稳定切出答案附录。

## 修复记录

- 修复方式：修改 `app/workflow/extract.py`，并在 `app/workflow/test_extract.py` 新增回归测试 `test_trailing_repeated_paper_title_with_glued_digit_cuts_answer_table`。
- 复验证据：使用 `/tmp` 对 `source.docx` 进行无破坏重提取，第 10 大题 `source_end_line=299`，`正文.txt` 不再含 `题号 | 1~5` 与 `12024年...`，`附录.txt` 正确以 `12024年安徽省初中学业水平考试` + `[TABLE 3]` 起头。
