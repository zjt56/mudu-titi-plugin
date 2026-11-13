# OCR 双栏网格题：按版面块坐标把图文绑定回各自小题

- 日期：2026-11-13
- 触发：用户批次「七上人教版课时A计划StarterUnit2KeepTidy!课时二Pronun」（PDF 单页 Pronunciation）
  在装配阶段被 A8/A9/E20 阻断——`[A8] sec-f84d2b7afb671c5b-u24 (语音题) 题组 material 为空`、
  `[A9] 同单元子题 1 stem 为空`、`[E20] sec-a10ee32127a5e553-u120 子题 3 选项应为 4 条，现有 0 条`。

## 背景

硬规则 28 要求 PDF 一律走识别服务、正文直接消费服务结果，正文投影（`ocr_markup`）只把
`<img>`/HTML/LaTeX 换成项目标记，**不重排**。识别服务返回的 Markdown 是行优先顺序，而原卷
「Ⅱ. 根据图片及所给音标写出下列单词所缺的字母」是**双栏网格**：左栏 1./3./5.、右栏 2./4.，
每题的图片在上、词形行在下。行优先展平后：

- 子题 1 的窗口为空（图片落在题号前的材料边界外被丢弃）；
- 子题 2 的窗口同时收进自己与前两题的词形行；
- `A./B./C./D.` 各跟一张图的阅读题选项，图片不在选项文本里 → 选项条数为 0。

## 改了什么

1. 新增 `app/workflow/ocr_grid.py`（`ocr-grid-items/v1`）：
   - 只读 `01_source/pdf-ocr.json` 的 `sources[].pages[].blocks`（`bbox`/`type`/`text`）；
   - 按大题标题块取同水平带（`title ± max(30, 宽度*5%)`，下界为下一标题块的上沿），
     避免把邻栏正文当成本节；
   - 带内按块中心 x 的最大间隙切两栏（`gap < max(40, 宽度*15%)` 视为单栏 → 返回 None）；
   - 每栏内「图片在题号前、词形行在题号后」聚合；题号集合必须与确认单元的 `numbers` 完全一致；
   - 文字块含 `<`、`>`、`$`、`[IMG` 一律拒绝（投影不完整不猜），剩余图片/剩余文字也拒绝；
   - `item_stem()` 生成「自己的图片 + 自己的词形行」，`needs_repair()` 只在当前单元格为空或
     与他格文本重复时才认为行优先解析确实错位。
2. `app/workflow/question_draft.py::build`：组题子题循环后调用 `ocr_grid.section_items()`；
   命中且确实错位时把各子题 stem 换成自己的图文（清空被错绑的 options）、删除对应
   `CHILD_CONTENT_UNRESOLVED`/`CHILD_STEM_EMPTY` issue、写 `proof["layout_items"]`
   （页号/栏目/basis/每项 images/texts/bbox）并追加 `GRID_ITEMS_REBOUND` 诊断；
   `wire_format.normalize_item` 之后只对重绑单元 strip 子题 stem 的换行。
3. 新增 `app/workflow/test_ocr_grid.py`（7 项）：双栏绑定、单栏不动、题号集合不符、
   标题不匹配、含未投影标记拒绝、失败采集不采信、`needs_repair` 只在错位时为真。

## 没改什么 / 边界

- 不改写 `正文.txt`、不重排识别服务输出、不新增或补造题号；识别服务坐标判不出来就返回
  `None`，保留原样并留诊断（`GRID_ITEMS_REBOUND` 只在真正重绑时出现）。
- 不是对所有题都生效：单栏、跨栏、含未投影标记、题号集合与确认单元不一致的组一律不处理。
- 图片仍只按已落盘媒体名引用（`01_source/media/pdf-page-*.jpg`），不编造文件名。

## 如何验证

- `python -m unittest workflow.test_ocr_grid` = **7 项 OK**；
  `workflow.test_ocr_grid workflow.test_question_draft workflow.test_phonetic_structure
  workflow.test_question_identity` = **61 项 OK**。
- 真实批次：`question_draft.build()` → items 5、issues 0、u24 五个子题各带自己的图片与词形行；
  `assemble_local.build()` + `assemble_precheck.precheck(payload, batch_dir=批次)` →
  `passed=True, problems=[]`（A8/A9/E20 全部消失）。

## 回退方式

`git revert` 本提交即可：`ocr_grid.py` 与 `test_ocr_grid.py` 为新增文件，
`question_draft.py` 的接线是独立代码块（删除该块后行为回到行优先解析 + A8/A9 阻断）。
