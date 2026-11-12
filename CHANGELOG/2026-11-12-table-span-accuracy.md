# 2026-11-12 表格识别保真：合并跨度、空档归一与预览网格

## 背景/触发

用户上传 2024 安徽中考「Ⅳ.信息转换」表格截图，指出表格结构与空档渲染不准。核查后确认：
原件是 Word 原生表格（`01_source/extraction.json` 已保留 `colspan:3` 表头与 `Reasons` 的
`rowspan:3`），抽取端没有错；错在后续两处本地处理：

1. `question_draft._table_blocks` 用「`[TABLE n]` 文本逐字等于 `table.text`」判断是否采用
   Word 合并证据。该批次绑定走空白归一路径（确认文本行尾空格被清），只差 3 个尾随空格，
   精确匹配失败 → 退回按 `|` 切行，合并信息整体丢弃，并记 `TABLE_LAYOUT_UNVERIFIED`；
   上传 `materialBlocks` 因此是「表头三格、后两格空」的扁平网格。
2. 一处空档在 Word 里被拆成连续多段下划线（`<u>　</u><u>16</u><u>　</u><u> </u>`）：
   `normalize_blank_text` 逐段归一，产出一串并列下划线（`________16______________`）；
   预览 `markLine` 只认连续的 `　16　`，数字不走填空线样式，尾部空白下划线在窄格里换行
   成孤立「_」。预览又只按 `|` 切分渲染，`rowspan/colspan` 即使存在也画不出来。

## 改了什么

- `app/workflow/wire_format.py`
  - 新增 `BLANK_RUN`：先识别「连续多段下划线属于同一处空档」再归一；含题号 → 一个 `__N__`，
    纯空白 → 一个 `______`。单段写法与普通下划线文字（`<u>farmers</u>`）行为不变。
- `app/workflow/question_draft.py`
  - 表格匹配改为 `_table_grid_equal`（只忽略版面空白：NBSP、全角空格、行尾空格、连续空格；
    可见文字仍须逐字一致）。
  - 新增 `_table_grid_matches`：Word 逐格网格与正文网格行数/列宽必须同形才采用合并跨度；
    旧协议/人工元数据没有 `grid` 时不猜形状，只看文本。
  - 匹配成功保留 `colspan/rowspan`；失败仍退回平面网格并如实记 `TABLE_LAYOUT_UNVERIFIED`
    （形状不一致与无唯一证据给不同 reason）。
- `app/workbench/server.py`
  - 新增 `table_structure(folder)`：按表号索引 `01_source/extraction.json` 的 Word 表格。
  - 新增 `structured_rows(table, flat_rows)`：行数、每行列宽与正文网格一致时产出带
    `rowspan/colspan/images/header` 的结构化行，否则返回 `None`。
  - `source_blocks(lines, tables=None)` 在表格块上附加 `structured`；`attach_media` 把结构化
    单元格内的图片解析成真实 URL（图片仍留在所属单元格）。
- `lib/client.js`
  - 表格渲染优先用 `structured`，按真实 `rowSpan/colSpan` 输出 `<td>`；无结构化数据时保持原
    `|` 网格渲染。纯平面行沿用原来的短文本表头判定。
  - `markLine` 先合并被标签切开的空档，再走 `wf-blank`；`__N__` 也渲染为填空线。
- 测试：
  - `app/workflow/test_question_draft.py`：`test_split_underline_runs_are_one_numbered_blank`、
    `test_reflowed_table_text_keeps_word_merge_evidence`。
  - `app/workbench/test_table_preview.py`（新）：合并跨度进入预览、缺结构/形状不一致退回平面
    网格、单元格图片 URL 与文字保持同格。
  - `backend/table-preview.test.cjs`（新）：多段下划线合并、`__N__` 填空线、普通下划线不变。

## 没改什么 / 边界

- 不改远端上传契约、不放宽 `upload_payload` 的网格校验；只是让本地草稿重新带上本来就该传的
  `rowspan/colspan`。
- 不重跑、不改写任何已归档批次：`01_source/*`、`06_batch/*`、冻结载荷均未改动；
  `question_draft.build` 只读重建（验证时直接调用，未写批次文件）。
- 正文 `material` 文本仍是逐字原卷（空档统一为库侧 `__N__`/`______`），不生成、不补写内容。
- 匹配放宽仅限空白；可见文字、网格行列数、单元格跨度仍逐项校验，改文/改格仍走退回与留痕。

## 如何验证

- `cd app && PYTHONPATH=. python3 -m unittest workflow.test_question_draft workbench.test_table_preview`
  → 37 tests OK（含新增 2+4 条）。
- `cd app && PYTHONPATH=. python3 -m unittest workflow.test_upload_payload workflow.test_question_content
  workflow.test_docx_extract workflow.test_agent_tasks workflow.test_structure_gate workflow.test_upload_content`
  → 233 tests OK（skipped=4）。
- `node --check lib/client.js`；`node --test backend/table-preview.test.cjs` → 4 pass。
- 全量 `unittest discover -s workflow` 尝试过，但本机跑到 26 分钟仍 0% CPU 停住（疑似某个既有
  模块阻塞在外部等待），未作为本次证据；改为跑受影响模块 233 + 37 + 25 条，均 OK。
- 已知既有失败（与本次无关，改动前同样失败）：`workbench.test_media_scoped_preview` 2 条断言
  `source_slice` 首行是否带大题标题，属于既有回归，未在本次改动范围内。
- 真实批次只读复验（2024 中考，`group-16-17-18-19-20`）：`materialBlocks` 表头 `colspan=3`、
  `Reasons` `rowspan=3`、空档 `__16__`/`__17__`…，无 `TABLE_LAYOUT_UNVERIFIED`；
  `upload_payload.build_payload` 无 `MATERIAL_*` 错误；预览 `source_blocks` 产出 `structured`。

## 回退方式

按提交整体回退四个文件（`wire_format.py`、`question_draft.py`、`server.py`、`client.js`）
及对应测试；数据产物无需迁移（本次未写入批次文件）。
