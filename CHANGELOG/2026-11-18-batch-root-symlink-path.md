# 修复批次根路径经符号链接时被误判为路径越界

## 背景
`workflow.test_agent_tasks` 里 7 个装配用例（`test_assemble_*`）在干净 HEAD（b3e3355）上
同样失败：

- `ValueError: Question draft path escapes batch`（`batch_fill._snapshot` → `_path` → `question_draft._safe`）
- `ValueError: Batch fill root cannot contain symlinks`（`batch_fill._root`）

判定口径：路径必须留在批次根内，批内不允许符号链接。

根因：`question_draft._safe` 用**未解析**的根做包含判断
（`path.resolve().is_relative_to(root)`）；`batch_fill._root` 拒绝**任何**祖先符号链接。
macOS 上 `/var`、`/tmp` 本身就是符号链接，`tempfile.TemporaryDirectory()` 给出的路径必然
含链接祖先，于是合法批次被误判成越界。`agent_tasks._safe_path` 一直是先 `resolve()` 再判断，
两处实现不一致。

## 改动
- `app/workflow/question_draft.py::_safe`：根先 `Path(root).resolve()`，再做包含判断，
  与 `agent_tasks._safe_path` 同口径；批内符号链接仍逐个拒绝。
- `app/workflow/batch_fill.py::_root`：允许祖先目录是符号链接（解析后使用），
  **根自身是符号链接时仍拒绝**（否则同一份数据会被两个批次名同时驱动）；
  解析后不是目录也拒绝。
- `app/workflow/test_batch_fill.py`：新增
  `test_ancestor_symlink_is_allowed_but_root_symlink_is_rejected`。

## 边界（没有放宽的地方）
- 批内任何读写路径的文件/目录符号链接仍拒绝（原用例
  `test_unsafe_paths_symlink_batch_and_root_are_rejected`、
  `test_knowledge_ids_and_sidecar_symlinks` 保持通过）；
- 批次根是链接仍拒绝；`../`、绝对路径、反斜杠等不变；
- 未改动任何批次数据、manifest 或上传边界。

## 验证
- `workflow.test_agent_tasks` 由 6 error + 1 failure 变为 **66 项全部通过**；
- `workflow.test_agent_tasks workflow.test_batch_fill workflow.test_question_draft
  workflow.test_delivery workflow.test_format_gate workflow.test_upload_payload
  workflow.test_media_inventory workflow.test_operations_runner_handoff`
  共 **274 项通过**；
- 真实批次健全性：`【木牍教育】20年安徽中考英语`、`【木牍教育】2024中考英语试卷` 的
  `_root`/`_snapshot` 均正常，批次状态未被改动。

## 回退
撤回这两个函数与新增用例即可；纯路径解析改动，不涉及数据迁移。
