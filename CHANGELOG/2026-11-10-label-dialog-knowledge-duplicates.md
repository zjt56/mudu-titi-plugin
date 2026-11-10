# 修改标签弹窗里同一条知识点显示两遍

## 背景 / 触发

用户（2026-11-10）在 2020 年安徽卷、第 69–72 题（题组）的「修改标签」弹窗里看到知识点标签出现两遍：

- 第一行是目录全路径：`语篇体裁/说明文/介绍类短文`、`主题/人与社会/历史、社会与文化/文化习俗`、`设题角度/阅读/细节理解`、`设题角度/阅读/推断文章后续发展`；
- 第二行是同一条叶子的「二级/末级」短名：`说明文/介绍类短文`、`人与社会/文化习俗`、`阅读/细节理解`、`阅读/推断文章后续发展`。

弹窗下方还误报「知识点路径不在目录里」「知识点超出允许维度」，并提示 `[说明文/介绍类短文, 人与社会/文化习俗, 阅读/细节理解, 阅读/推断文章后续发展]` 这些值有问题。用户指出：红框里的其实就是上面的同一条，只是少了第一级。

## 根因

`app/workflow/preview.py::_rows_from_batch`（:513）给工作台行卡片的 `knowledge` 写的是短名投影 `_short_knowledge_list()`（只留第二级与末级，为卷面速览服务），但这一行没有标注「这是投影」。

`app/workbench/server.py::label_payload` 组装标签值时，把 `batch_item["knowledge"]`（目录全路径）和 `row["knowledge"]`（短名投影）**合并**去重——两份字符串不同，于是同一叶子出现两遍。随后 `taxonomy.resolve_knowledge()` 因为短名解析不出，`unknown` 非空，跳过「统一成目录全路径」的规范化，短名被当成真标签，进而产生上面的假告警。

如果用户在弹窗里直接点「保存标签」，这份短名还会被写进单元标签草稿，属于会污染批次数据的隐患。

## 改了什么

1. `app/workflow/preview.py`：`_rows_from_batch` 生成的行增加 `"knowledge_projection": True`，明确「这份知识点是展示用短名，不是标签来源」。
2. `app/workbench/server.py`：
   - 新增 `_knowledge_projection(path)`（第二级/末级短名，与 preview 口径一致）与 `drop_short_knowledge_copies(paths)`：若某路径是另一条路径的短名投影（或 `/<短名>` 后缀），丢弃短名、保留全路径。
   - `label_payload()` 不再合并带 `source_projection` / `knowledge_projection` 标记的行知识点；合并后统一过一次 `drop_short_knowledge_copies()`，兼容存量批次里已经写进短名的行汇总。
3. `app/workflow/test_workbench_catalog_archive.py` 新增 3 条回归：投影行不重复、存量短名副本被丢弃（保留无关叶子）、无目录外告警。

## 没改什么 / 边界

- 不改动任何批次数据、不改 `06_batch/batch.json`、不重跑批次。
- 卷面速览卡片上的短名照旧显示（那是刻意的「二级/末级」写法），只是不再被当标签来源。
- 保存路径不变：弹窗保存的仍是目录全路径；目录校验仍要求叶子合法。

## 如何验证

- `workflow.test_workbench_catalog_archive + workflow.test_preview_labels + workflow.test_workbench_group_save + workflow.test_workbench_units + workbench.test_list_projection` → **46 个用例全部通过（5 个按平台跳过）**。
- 用真实批次（2020 年安徽卷，`group-69-70-71-72`）直接调用 `server.label_payload()`：`knowledge` 恰为 4 条全路径，`warnings` 为空（修复前是 8 条且带假告警）。

## 回退方式

`git revert` 本提交即可；改动只涉及上述三个文件，无数据迁移。
