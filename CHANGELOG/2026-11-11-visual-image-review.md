# 2026-11-11 引入原卷图片视觉语义复核（候选证据）

## 1. 背景 / 触发

用户反馈「课时识别效果非常不好，能不能直接用大模型的视觉能力做分析判断」——并明确选 B：
把本地 Harness 的视觉能力接进正式链路。根因：Word/PDF 提取把图片压成 `[IMG name]`
占位，纯文本 + 正则判不出「这是选项图/材料表格/天气图标/曲线图/纯装饰」，导致
图片题、图片选项题、表格材料题的组题与归属靠猜（如 04 第四课时图片选项
`[IMG]A/[IMG]B` 切错位、06 天气表格里的图标无法识别）。

## 2. 改了什么
- 新增 `app/workflow/visual_review.py`：扫描批次内所有原件图片（排除 `extract-*` 临时目录），
  经本地 Harness 视觉模型（magpie 网关 OpenAI 兼容接口 `http://127.0.0.1:3425/v1`，
  `auto/deepseek-v4.1-flash`，`input=[text,image]`，原生多模态）逐图识别，
  产出只读候选 `01_source/image_semantics.json`（kind 类别 / title / contains_text /
  readable_text / summary）。大图降采样、JSON 宽容解析（含截断/未转义换行修复）。
  CLI：`python -m workflow.visual_review --batch-dir <批次> [--force] [--limit N]`。
- `app/workflow/runner.py`：extract 成功后（与 metadata_ai 同级）触发
  `visual_review.build`，**任何异常/网关不可用只打日志、绝不阻断主流程**。
- 新增 `app/workflow/test_visual_review.py`（7 例）：JSON 宽容解析、截断不伪造、
  候选产物边界（note 声明原文为准）、无 key/全失败时 unavailable 且不写产物、缓存幂等、
  临时目录跳过。
- 新增 `app/workflow/visual_crosscheck.py`：Word × 视觉交叉印证。把正文里的
  `[IMG name]` 占位按 `source_span` 关联到所属单元、并与 `image_semantics.json`
  的图片语义对齐，逐条给出 {占位, 行号, 所属单元/题号, 视觉类别/标题, 是否纯图行,
  判定 ok/note, 说明}，产出 `01_source/source_crosscheck.json`；
  `report()` 提供人读摘要。**只留痕报告，冲突不自动改原文、不升级闸门。**
- 新增 `app/workflow/test_visual_crosscheck.py`（4 例）：[IMG]→单元映射与判定、
  缺语义时 skipped 不写产物、other 类别标 note、纯图行识别、边界声明。
- `app/workflow/runner.py`：extract 成功后，先视觉复核再 Word×视觉交叉印证
  （同一钩子链，各自失败只打日志、绝不阻断主流程）。

## 3. 没改什么 / 边界（关键）

- **只产出候选证据，不生成内容**：不生成/改写/补全题干、选项、答案、解析、文章大意；
  不据此放行任何闸门。正式链路仍以原卷原文为准（硬规则 4/22/28）。
- 网关不可用时优雅跳过（`status=unavailable` 且不写产物），主流程照常——符合硬规则 2/18。
- 未改动 `wf-agent.sh`/`dsh` 桥（纯文本 stdin）：视觉走 magpie 的 OpenAI 兼容
  HTTP 接口，是独立通道，不影响现有文本模型链路。
- 尚未把视觉语义**消费进**结构/题型的判定逻辑（本轮只落到候选产物 + 接线）；
  后续可按需让 `structure`/`preassemble` 读取 `image_semantics.json` 作佐证，
  但仍须过确定性校验与来源绑定，不得据此直接通过。

## 4. 如何验证

- 模块在真实批次 `06八上…第六课时Project` 上跑通：10 张图全部识别——
  6 张天气图标(icon)、1 张曲线图(chart)、1 张 "Sanya" 标识、1 张整页(page，
  "英语试卷整页…含语法填空、句型转换和三亚天气阅读题")，人工抽查一致。
- 交叉印证在真实批次 `02…第二课时Pronun`：3 处 `[IMG]` 关联到单元并判定——
  image1(板块标题栏) ok、image9(材料表格/填字格) ok、image4(视觉 other) note 待核；
  未引用图片 2 张(image158/159 页眉装饰)正确标注。
- `workflow.test_visual_review`：7 例全过；`workflow.test_visual_crosscheck`：4 例全过。
- `workflow.test_runner_auto`：46 例全过（含 runner 接线）。
- `test_visual_review + test_visual_crosscheck + test_runner_auto`：57 例全过。

## 5. 回退方式

删除 `app/workflow/visual_review.py`、`test_visual_review.py`，并移除 `runner.py`
extract 后的视觉复核钩子即可；视觉产物 `01_source/image_semantics.json` 可独立删除，
不影响任何既有阶段。

## 6. 未决点

- 图片语义如何反哺结构/题型判定（读取 `image_semantics.json` 的门槛、与
  `[IMG]` 占位的映射、是否用于补图片选项/表格材料归属）需下一轮设计，本轮先立通道与候选产物。
- 视觉识别对同一图偶有类别抖动（如两朵云被分判 icon/other），消费时应按稳定字段
  (contains_text/readable_text) 而非单次 kind 做硬判。

## 7. 补充（同日）：提取策略固化——有PDF才OCR，纯Word文字提取+视觉补图

用户 2026-11-11 明确提取策略：有 PDF 才调用 OCR 识别；纯 Word 直接文字提取；两者都配合
视觉理解模型判断/补充页面结构。现状核对：source_policy 已按
`word > text_layer_pdf > scan_ocr` 落实（Word 原生解析不 OCR；PDF 文字层优先、质量不足才 OCR），
OCR 默认 auto 仅在 image_only/floating/symbol_font 触发，纯文字 Word 不触发 OCR。

- source_policy.build_report 新增 `visual_assist` 参数，报告新增
  `visual_structure_assist` 字段（可用时标记"配合视觉理解判结构"，仅记录事实不改提取结果）。
- extract.run 调用处传入 `visual_structure_assist`（依据视觉网关是否可用，判定失败不阻断）；
  同步提交 `page_evidence`（读实际 pages 而非 stage summary，避免已 OCR 页被报 0 页）。
- 回归 test_source_policy 新增 3 例：纯 Word 不 OCR 且记视觉标记、PDF 文字层不 OCR、
  未知时不写该字段。`test_extract + test_source_policy` 52 例全过。
- 验证：02(Word源) extraction=docx_native、visual_structure_assist=True、recommendation
  "Word 原生解析，无需 OCR"；extraction.json 实测 image_ocr/ocr_auto/pdf_ocr 均无。
