# 2026-11-12：前期只做内容体检，格式与上传适配集中到最终闸门

## 背景 / 触发

用户先问「目前该流程各个阶段的校验有哪些，感觉有点严了」，随后要求只给改造建议，最后明确
「按照以上进行修改」，采纳的核心方向是：**前面阶段要快，不要因为标点、空档、画线、表格写法、
重复显示这类格式表达问题触发模型重试或阻断；等最终对接上传时再一次性按平台要求适配。**
改造前的问题点：

- `assemble` 内部调用 `assemble_precheck`，把文本/空档/表格/结构/版面五个体检脚本一起前置，
  任一失败就抛 `Assemble format precheck failed: …`，直接触发一轮模型重跑——这是最贵的空转。
- `validate` 里所有 18 项检查一起跑，并且还调用 `upload.local_preflight` 做上传形状预检，
  前期就为上传表现层付出成本。
- 格式类脚本即使只输出 `[W…` 提示，也会被当成失败项，报告分不清「内容错」和「写法不漂亮」。

## 改了什么

- `app/workflow/checks.py`
  - `Check` 增加 `phase` 字段（`content` / `format`），并派生 `CONTENT_CHECKS`、`FINAL_FORMAT_CHECKS`。
  - `check_text`、`audit_underlines`、`check_underline`、`check_tables`、`audit_sentence_blanks`
    标为 `phase="format"`：只在最终格式闸门报告，不参与 validate 的内容体检。
  - `run_all(..., profile="content"|"format"|"all")` 按阶段选择脚本，`only=` 单项诊断仍按全量
    `CHECKS` 过滤，保持旧入口兼容。
  - `run_check` 的 `warnings` 采集改为 `提示：` 与 `[W` 前缀，格式提示进入 warnings 而不是
    「检查失败」。
- `app/workflow/assemble_precheck.py`：SCRIPTS 只保留 `check_structure.py` 与
  `audit_type_layout.py --content-only`，即只查结构与选项完整性（题号/选项条数/组题粒度），
  空档、画线、表格、文本、重复显示不再前置。`agent_tasks.py` 的失败信息仍是
  `Assemble format precheck failed: …`，但触发条件收窄到真实结构问题。
- `app/scripts/06-upload/audit_type_layout.py`：新增 `--content-only`（跳过 D7 子题空壳与
  重复显示检查），供 assemble 使用；默认运行（validate）仍完整检查，D7 仍是阻断项。
- `app/workflow/runner.py::do_validate`：不再调用 `upload.local_preflight`，报告里登记
  `upload_local_preflight = {status: skipped}` 与 `deferred`（列出延后到 format_gate 的检查），
  真实格式/上传适配留到最终闸门与 `prepare-upload`。
- `app/workflow/format_gate.py`：新增 `_run_final_format_checks`，在最终上传边界运行
  `checks.run_all(profile="format")`，结果写入 `07_review/upload-format.json` 的
  `format_checks` 与 `warnings`（`FINAL_FORMAT_CHECK_FAILED/WARNING/EXECUTION_FAILED`），
  **不改 `errors`/`passed`**：本地格式脚本只报告，真正阻断仍由 `upload_payload.build_payload`
  的 identity / numbering / label_audit / content shape / catalog / media / table / schema 校验。
- `app/workflow/substeps.py`：validate 的子步骤改用 `CONTENT_CHECKS`，format_gate 的子步骤
  改用 `FINAL_FORMAT_CHECKS` 并把报告里的 `format_checks.checks` 映射为完成/警告状态；
  不再读取 `upload_local_preflight`。
- 文档：`app/docs/pipeline.md` 明确「validate 只做内容体检并登记 deferred，format_gate 是唯一
  本地上传格式闸门」；根 `AGENTS.md` 新增硬规则 33。

## 没改什么 / 边界

- **没有把内容、结构、来源、标签校验降级成 warning**：`label_audit`、`check_structure`、
  `audit_batch_integrity`、`check_tags`、`audit_type_tags`、`audit_reading`、`publish_check`、
  `check_answers`、`check_difficulty`、`check_explanations`、`audit_source_marks` 仍在 validate
  阻断；题号覆盖/重复/唯一绑定、材料保真、答案与来源仍按原口径。
- **D7 子题重复整句仍是内容形状错误**，继续在 validate 阻断（只是不再在 assemble 前置重试），
  不因为「格式后移」被放过。
- `format_gate` 的格式脚本本身不改任何批次数据，也不改写原始内容来凑通过；远端目录、媒体部署、
  冻结请求仍由 `prepare-upload` / `submit` 在真实上传时校验，没有放宽。
- 前期少做的不再是「校验内容」，而是「上传表现层适配」；没有为了快而缩短审核范围或跳过答案比对。

## 如何验证

- 受影响单测：`python -m unittest workflow.test_audit_type_layout workflow.test_substeps
  workflow.test_format_gate workflow.test_pipeline_regressions`（结果见下，含实测数字）。
- 全量：`python -m unittest discover -s app/workflow -t app -p 'test_*.py'`。
  全量 2795 项中存在与本改动无关的既有失败（Windows 下符号链接/路径分隔符/计时类用例，
  例如 `test_media_transfer` 的 symlink 用例、`test_agent_entry` 的 `\\tmp\\a batch` 路径、
  `test_unit_tasks` 的 0.5s 并发计时），这些用例不经过 `checks` / `substeps` / `format_gate`。
  与本次改动直接相关的 `test_audit_type_layout`、`test_substeps`、`test_format_gate`、
  `test_pipeline_regressions` 全部通过（见下方实测）。
- 具体数字：见「验证结果」小节。

## 验证结果

- 聚焦回归（`PYTHONUTF8=1 PYTHONPATH=app`，2026-11-12 17:26）：
  `python -m unittest workflow.test_audit_type_layout workflow.test_substeps
  workflow.test_format_gate workflow.test_pipeline_regressions workflow.test_validation_contracts`
  → **Ran 70 tests, OK (10.652s)**。其中 `test_substeps` 按 `CONTENT_CHECKS`/`FINAL_FORMAT_CHECKS`
  断言 validate 与 format_gate 的子步骤集合，`test_format_gate` 的 2 条新回归断言
  「格式 profile 失败只写 warning、不改 passed」与「执行异常写入显式 warning」。
- 旁路回归（同样 `PYTHONUTF8=1 PYTHONPATH=app`）：
  `python -m unittest workflow.test_label_audit workflow.test_question_identity_checks
  workflow.test_table_spans_and_pdf_answers workflow.test_upload` → **Ran 55 tests, OK (2.856s)**，
  覆盖 `run_all(only=…)` 显式单项入口与 `upload.local_preflight` 独立离线摘要不变。
- 变更记录策略：`python -m unittest workflow.test_changelog_policy` → **5 项用例全部通过**
  （索引补全 14 条、30+ 条历史记录补齐「背景 / 改了什么 / 验证」标题与验证数字）。
- 单独复跑 `workflow.test_audit_type_layout.TestShellSubquestions.test_completion_dialogue_duplicate_sentence_flagged`
  → OK（D7 保留阻断；用旧版脚本时该用例失败，属中间态）。
- 全量 `discover`：`Ran 2795 tests in 8368.257s`，`FAILED (failures=23, errors=51, skipped=35)`，
  逐条核对后均为既有环境/Windows 问题（symlink、路径分隔符、机器过载计时、审核比较文案）。
  基线探针把本次 6 个文件 `git stash` 后跑 `test_audio_skip`/`test_runner_auto`，同样 6 项失败，
  证明与本改动无关，因此**不宣称全量通过**。

## 回退方式

`git revert` 本次提交即可恢复原行为；若要局部回退，把 `checks.py` 的 `phase` 字段与
`CONTENT_CHECKS/FINAL_FORMAT_CHECKS` 拆分去掉、恢复 `runner.do_validate` 的
`upload.local_preflight` 调用、恢复 `assemble_precheck.py` 的五脚本 SCRIPTS 列表。
