# 2026-11-11 试卷名/来源不再显示「未提供」：按已识别元数据与原卷 Word 文件名补齐

## 背景 / 触发

用户反馈（原话）：「还有这个试卷名，它现在来源显示是"未提供"。你正常以 Word 文档的文件名去修改一下，它其实就是这个名字的来源」。

现场批次 `01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio`：`01_source/metadata.json` 已按规则 26 正确识别出 `七上人教版课时A计划 Starter Unit 1 课时1`，`attachments/batch.json` 也登记了 `01 七上 人教版 课时A计划 Starter Unit 1 Hello! 第一课时 Section A (1a-2d).docx`，但 `structure.confirmed.json`、`workflow/manifest.json` 的 `meta` 以及 `06_batch/batch.json` 每道题的 `source/source_type` 全是「未提供」，工作台卡片同样显示「未提供」。

根因（三处叠加）：

1. `structure_gate.run_gate` 的 `meta` 只来自正文卷头（`denoise.clean_body(raw_text)`），从不读 `01_source/metadata.json`。课时拆分文档正文首行是「七上 · 人教版 · 课时A计划」，没有卷头 → 直接 `with_placeholders` 落成「未提供」。
2. `runner.do_structure_gate` 把 confirmed 的 meta 合进 `manifest.meta` 时，只跳过 `None/""/[]/{}`；「未提供」是非空字符串，于是反过来覆盖了此前识别出的值。
3. `assemble_local` / `batch_fill` 把这份 meta 原样写进题目 `filterMetadata`，`server.paper_info` 又只认 `batch.json`、meta、卷头，缺卷头且 meta 是占位时就显示「未提供」。

## 改了什么

- `app/workflow/metadata_policy.py`
  - 新增 `known()`、`filename_name()`（取最后一段路径并去扩展名）、`_paper_suffix()`（只有 `docx/doc/pdf` 才算试卷名证据，内部转换件如 `source.txt` 不算）、`original_filename()`（先 `attachments/batch.json` 的 `original_filenames.source/answers`，其次 `manifest.inputs.source.path`）、`identified_paper_meta()`（读 `01_source/metadata.json` 的非占位文本字段）。
  - 新增 `paper_meta(metadata, batch_dir)`：`已有值 → 已识别元数据 → 原卷文件名 → 未提供`，只补空缺、绝不覆盖识别值；`source_type` 不用文件名兜底（文件名里没有题型信息）。
  - 新增 `resolved_paper_meta()`：要写入题目字段的版本，过滤掉「未提供」占位，避免每道题凭空多出 `source_type=未提供`。
- `app/workflow/structure_gate.py`：`meta = paper_meta(meta, batch_dir)` 后再判 `missing_source_fields`（原来直接 `with_placeholders`）。
- `app/workflow/runner.py`：合并 confirmed meta 时跳过 `MISSING`，占位不再覆盖已识别值（旧 `submitted_meta` 存档照旧）。
- `app/workflow/assemble_local.py`（`_initial_payload` 与 `build` 两处）、`app/workflow/batch_fill.py::_source_meta`：改用 `resolved_paper_meta`，题目字段只写真实值。
- `app/workbench/server.py::paper_info`：title/source/source_type 走 `paper_meta` 解析，仍保留原顺序（批次登记 → meta → 卷头 → 描述符），最后才落「未提供」。
- `AGENTS.md` 规则 26 补一句「正文卷头不是唯一来源」；`CHANGELOG/RULES.md` 追加同口径条目。
- 测试：新增 `app/workflow/test_metadata_source_name.py`（11 项：文件名解析、识别值优先、`source_type` 不兜底、manifest 路径、内部 `.txt` 不算证据、gate/`batch_fill`/`assemble_local`/`paper_info` 接线、真实来源优先）；`app/workflow/test_metadata_placeholders.py` 的 runner 用例改为断言「无卷头 → 取原卷文件名 `未命名试卷`」，task prompt 仍带「未提供」提示。

## 没改什么 / 边界

- 「未提供」仍是最后的展示占位，不编造年份、省份、考试类型；`source_type` 缺证据仍是「未提供」。
- 只影响试卷名/来源文字字段的补齐；题型、组题、答案、解析、知识点、审核与上传边界均未改动。
- 不重跑、不改写任何已归档或正在运行的批次数据；本修复在下一轮结构确认/组装时自动生效。
- 未安装或重启宿主插件；运行中的工作台服务需重载代码（重启后端）后才会用新逻辑。

## 如何验证

- 新增专项 `python -m unittest workflow.test_metadata_source_name`：11 项通过。
- 关联回归 `workflow.test_metadata_source_name + test_metadata_placeholders + test_metadata_ai + test_batch_fill + test_assemble_local + test_structure_gate`：116 项，除本改动初期两条自身断言（已改为「不写入占位」）外全部通过；修正后 `test_metadata_source_name + test_batch_fill + test_assemble_local` 64 项 OK。
- 真实批次只读复验（`copytree` 到 `.tmp/verify-batch`，跳过运行中的 `.run.lock`）：`paper_info` 的 title/source 为「七上人教版课时A计划 Starter Unit 1 课时1」、source_type「同步练习」；旧 confirmed meta 经 `paper_meta` 解析为识别值；重跑结构闸门 `passed: True blockers: 0`，重跑后 report/confirmed/paper_info 均为识别值。

## 回退方式

`git revert` 本提交即可；旧行为是正文没有卷头时一律落「未提供」。
