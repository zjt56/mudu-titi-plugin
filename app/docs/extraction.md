# 原卷复合结构提取

`extract` 阶段现在自带执行器，不配置 agent bridge 也会在上传后运行：

```bash
python -m workflow.runner run --batch-dir workflows/<批次> --stage extract
# 已完成批次需要明确重跑时加 --force
```

## 处理路径

- DOCX：标准库直接解析文档 XML，按正文顺序读取段落、真实表格网格和图片关系。保留可解析的画线、加粗、上下标及样式继承。页眉、页脚不混入题面；显式分页线索保留在证据中，不猜物理页码。
- 文字极少而含图片的 DOCX：保留原图，同时尝试用 OCR 补充图中文字。常规 Word 不整页转图片，避免丢失原生格式；图片、表格的归属仍需核对。
- PDF：通过 OCR 独立环境中的 PyMuPDF 逐页读取文字、原生文字块坐标和嵌入图片。少于 80 个字符、出现替换字符或严重英文粘连的页转视觉 OCR，其余保留文字层。此检测只是路由启发式，不能证明文字质量或多栏顺序。
- PNG/JPEG/WebP/BMP/TIFF：调用当前配置的 OCR（飞桨 jobs API 或兼容的本地 CLI）。
- 旧 DOC 不直接支持，应先转为 DOCX。当前网页上传入口仍接收 DOCX/PDF，图片路径可通过批次 source 或 Python API 使用。

## 来源优先级（Word > 带文字层 PDF > 扫描件）

用户 2026-11-11 确认的口径，代码入口 `workflow/source_policy.py`（`select_primary` /
`build_report` / `upload_notice`），三处共用同一实现，不得各写一套判断：

1. **同一批上传同时含 `.docx` 与 `.pdf`**：`.docx` 一定是提取主源（`source`），PDF 只作附件
   供版面/层级核对；`01_source/material-roles.json` 中它的 `role=structure_reference`、
   `content_authority=false`，不参与唯一绑定竞争。
2. **只有 PDF（正文提取）**：文字层可用的页直接原生解析，只有质量不足的页用 OCR 文字替换；不因“是 PDF”就把 OCR 当作正文来源。版面证据另按下一节整卷采集，两者互不替代。
3. **扫描件/纯图片 Word**：只能走 OCR，属于最后手段，结果需核对题号、音标、画线、表格顺序。
4. 旧版 `.doc` 需先转 `.docx`；转换可能丢图，提示用户另存后重传。

为什么 Word 优先：`.docx` 由标准库直接解析 OOXML，段落、表格网格、合并跨度、画线、图片关系
都是结构化数据，逐字位置可信且不需要 OCR 环境；文字层 PDF 依赖运行环境提供 PyMuPDF，页质量
只能靠启发式路由；OCR 文字没有逐字坐标与可信度。

落地与证据：

- `app/workbench/server.py::handle_upload` 用 `select_primary` 选主源，并在上传响应里返回
  `source_kind` 与 `source_notice`，前端在「原卷文件」区显示该提示；
- `app/workflow/extract.py::run` 把本次实际来源、PDF 逐页用法（`pdf_usage`）写进
  `01_source/source-policy.json`，同内容并进 `07_review/extraction-report.json` 的
  `source_policy` 字段；记录失败不阻断提取。

## Word 图片板块的视觉辅助

纯 Word 仍直接提取文字与原生画线/表格/媒体，不调用 OCR。同步课时正文中的 A基础巩固/B能力提升整图横幅由 `lesson_bands` 批量看图，边界和图片原文分开存证于 `01_source/lesson-bands.json`；文字内容不由视觉重写。原件/正文/所有候选图摘要、实际选定模型、原始行范围都须有效，才参与结构、答案scope和预览独立重建。相同图字节+路由复用，切模型或换原件不沿用旧识别。细节见 `docs/sync-practice.md` 与 `backend/wf-vision.md`。

## PDF 版面证据（上传即整卷 OCR）

用户 2026-11-12 要求：只要批次的输入里有 PDF，就调用 OCR 识别功能，产出 JSON 作为 Word 的版面参考辅助。落地为 `workflow/pdf_ocr.py::run` 对每个去重后的 PDF 一律 `force_ocr=True`、`mandatory_pdf=True`：

- 触发条件与 OCR 总开关、文字层质量、Word 是否为主源都无关；Word + PDF 同批时配套 PDF 也整卷识别。
- 产物：`01_source/pdf-ocr.json`（每源状态、页码、文字、版面块、警告），每源快照 `01_source/pdf-ocr/<摘要前缀>/source.pdf` 与完整原始结果 `<摘要前缀>/acquisition.json`；同一 SHA 只提交一次。
- 消费方：`structure_ai` 结构归类提示词与 assemble/review 任务把该 JSON 当作层级/阅读顺序参考，并注明有 Word 时以 Word 原文为准。
- 边界：Word 仍是内容权威。`extract._pdf` 只在文字层质量不足的页用 OCR 文字替换，正文不被 OCR 覆盖；版面块存在不证明阅读顺序正确，顺序问题由识别服务侧排查，不在此处手工重排。
- 失败：识别未配置、超时或空结果时该 PDF 记为 failed，不写伪 JSON。Word 为主源时只记 warning 继续用 Word；PDF 为主源时提取失败。
- 代价：整卷识别更慢并消耗识别额度，这是上述要求的已知成本。

## 本机 OCR 配置

复制 `workflow/ocr-config.example.json` 为 `workflow/ocr-config.json`，填写：

```json
{
  "python": "/path/to/ocr/.venv/bin/python",
  "script": "/path/to/ocr/ocr_local.py",
  "timeout_seconds": 1500,
  "lock_timeout_seconds": 180,
  "dpi": 200,
  "preview_dpi": 120,
  "max_new_tokens": 4096
}
```

也可以设置 `MUDU_OCR_CONFIG` 指向其他配置文件。相对路径以配置文件所在目录为基准。配置仅包含本地路径，不上传文件、不连接第三方 OCR。

`ocr-config.json` 已被 Git 忽略；示例和适配器不写死某台电脑的路径。迁移时单独安装 OCR 环境和权重，再更新这两条路径。纯文字 Word 路径不需要 OCR 配置；PDF（即使有文字层）需要配置的环境提供 PyMuPDF，并且需要可用的 OCR 服务做整卷版面识别。

沿用 OCR 现有独立虚拟环境，不向审核台环境安装 torch/transformers，也不升级模型依赖。OCR 命令以参数数组启动，不使用 shell。多个批次调用同一模型脚本时通过操作系统文件锁串行执行，避免同时加载多个模型；直接在工作流外调用原脚本不受此锁控制。

## OCR 辅助总开关

插件「模型与设置 → OCR 识别」里的「启用 OCR 辅助识别」默认关闭。上传的 PDF 不受此开关影响：只要批次里有 PDF，就整卷调用识别服务生成版面证据（见上一节）。开关只控制其余辅助识别，关闭时：

- DOCX 与文字 PDF 的正文完全走原生解析；文字层质量不足的页保留原文字，只记录警告并交给结构闸门（版面 JSON 仍照常采集）。
- 图片型 Word 不补充图中文字，只保留图片引用。
- 没有 PDF 的扫描件/图片无法提取文字时明确失败并提示先开启开关，不会静默产出空正文。

开启后文字提取仍是主路径：文字 PDF 的正文只对质量不足的页用 OCR 文字，整卷版面证据照常采集；扫描 PDF/图片由 OCR 识别，图片型 Word 补充图中文字。开关通过私有 `assist.json` 实时生效（`MUDU_OCR_ASSIST_STATE`），不需要重启后端；独立运行工作流而没有该状态文件时保持旧行为，也可用 `MUDU_OCR_ASSIST=0/1` 显式覆盖。

## 产物与证据

每次执行在 `01_source/extract-<唯一目录>/` 下保留独立证据，原件只读：

| 产物 | 内容 |
| --- | --- |
| `01_source/原始正文.txt` | 原始提取全文（含后部附录候选） |
| `01_source/正文.txt` | 供后续阶段读取的正文候选 |
| `01_source/附录.txt` | 大题标题重新出现后的内容候选，可能是答案附录或另一份卷子，必须核验 |
| `01_source/extraction.json` | 方法、源文件 SHA256、原生块、表格、媒体、PDF 页及 OCR 原始结果和日志路径 |
| `01_source/structure.json` | 分区、题号、题组候选、定位和 `unresolved` |
| `01_source/media/` | 供现有预览器读取的 Word 媒体；原始证据另存于独立执行目录 |
| `07_review/extraction-report.json` | 本次提取状态、候选统计、待核验项和 `source_policy`（来源优先级记录） |
| `01_source/source-policy.json` | 本次实际使用的提取主源、PDF 文字层/OCR 页数与推荐说明 |
| `01_source/pdf-ocr.json` | 每个上传 PDF 的整卷 OCR 版面证据（状态、页码、文字、版面块、警告）；Word 为主源时只作版面参考 |
| `workflow/logs/extract.log` | 调度执行日志 |

重复执行会为稳定名称的产物留 `.bak`，独立证据目录不覆盖。提取前后校验原件哈希。配置错误、识别超时、空结果或子进程失败会让 extract 阶段失败，不自动重复提交、不制造成功产物。

PDF 原生文字块有坐标；现有本机 `ocr_local.py` 返回的 OCR 结果只有页码和文字，**没有逐字坐标或可信度**。适配器明确保留这一限制，将原文字层存为 `native_text/native_blocks`，不会把它的坐标套到 OCR 文字上。PDF 预览、原始图片和原生块记录仍可用于复核。

## 结构候选与放行边界

标题和结构线索用于生成题型候选，不代表完成最终题型/子类判定或知识点匹配。

- 明确题号与带下划线/全角空格的空位用于定位。完形材料空位和选项行各出现同一题号是正常结构。
- 独立 A/B 材料标题可用于拆分组题；无法确认材料或对话边界时保留候选并提示核验。
- 书面表达内部的“1、2、3”内容要点不当作独立题；没有显式全卷题号时生成的编号标记为推断，不能自动放行。
- 大题标题重新出现时，保留其后内容到附录候选，不静默把答案或另一份试卷并入题目。
- OCR、源格式警告、题数不符、题号重置或材料边界不确定，都会写入 `unresolved`。结构自动闸门读取这些问题并阻断，不能只凭数量一致覆盖掉。
- `coverage_verified` 默认 false，提取成功不表示已逐题视觉验证，也不会自动完成答案、听力切段、题型知识点代理或发布确认。

下游仍以现有阶段契约执行。若没有配置答案/听力/标签等执行器，提取结束后会如实停在对应阶段等待，不能将“复合提取已接入”解释为全流程入库已自动完成。

## PaddleOCR 托管 jobs 的版面证据与参数边界

`paddle_jobs` 使用官方 `/api/v2/ocr/jobs`，保留 `layoutParsingResults[].prunedResult.parsing_res_list` 的全部块字段与服务顺序，并提供 `type/text/bbox/order` 别名。独立 `raw-result.jsonl` 保存脱敏原始响应；令牌、凭据及远端签名 URL 不写入证据。PDF 提取页保留这些 OCR 块，原生 PDF 块另放 `native_blocks`；未声明尺度的 OCR 坐标不能冒充 PDF points。块存在不等于版面或阅读顺序已核验。

OCR 返回的 Markdown 按服务给定的阅读顺序消费。不得针对某页手工调行、硬编码重排或按 bbox 自排来冒充识别修复。识别自身错序先核查官方参数及原始结果；结构验收保留原响应、有效隔离单元、失败单元与来源行范围，缺题结构不能交付下游；同输入确定性拒收先本地复验，不重复烧模型。Markdown `#` 标题在识别课时/大题时去除标记，但保存文字不改变；中考等非同步卷仍不允许全局重号。

官方文档：[异步 jobs](https://ai.baidu.com/ai-doc/AISTUDIO/Mml7n69e7) 的 `optionalPayload` 链接到 [VL 参数表](https://ai.baidu.com/ai-doc/AISTUDIO/2mh4okm66)。版面相关字段包括 `useLayoutDetection`（检测与排序模块）、`layoutThreshold`、`layoutNms`、`layoutUnclipRatio`、`layoutMergeBboxesMode`。示例的三个预处理/图表开关不是完整参数表。当前适配器尚只发送这三个开关，未接入上述版面调参；服务已经返回版面检测图，不可据“未显式发送”判定服务未启用布局分析。托管文档旧版本表与当前 1.6 的实际默认值应分别核实，不能直接套用本地默认。

[本地 Python/自部署文档](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md) 说明完整产线先分析版面阅读顺序，再识别区域并合并。`use_layout_detection` 本地有效默认启用；`layout_shape_mode` 是区域框形状，不是阅读顺序开关；没有已核实的独立 `useReadingOrder` 参数。检测框扩张、合并和去重影响分区，但不能保证修正所有跨栏错误；未经对应 jobs 文档/服务验证的参数不应直接发送，也不得擅换模型。

## 原生来源对象与最终表格投影（2026-11-12）

DOCX 提取在既有 `blocks/tables/media` 上附加 `source_ref`，并在 `source_objects` 保存 `native-source-objects/v1` 索引。对象 ID 绑定原件 SHA-256、包内 part、提取遍历路径及对象种类；内容摘要独立校验。路径表示原生遍历位置，不是物理页码或 XML XPath。同一图片文件的多次出现保留独立 occurrence 引用，既有文字、顺序和网格不改写。

当前兼容入口仍以 `[TABLE N]` 与完整文字定位候选，再校验原生对象引用及内容摘要；它尚不是所有题目/来源归属的全量替换。存在索引但引用损坏时不可退回旧文本绑定。没有新索引的旧批次沿用既有校验，不迁移归档数据。

组装从原生单元格投影 `rowspan/colspan` 与 `grid_before/grid_after` 空白位置；预览使用同一原生表格，保留空行、合并跨度及单元格内图片。纯文本预览保留兼容回退，但不将回退结果当作已证明的原生布局。来源绑定证据包含采用的对象引用。

前期只收集 `layout_diagnostics`，不把上传表达能力检查加入内容体检。最终 `build_payload` 重新读取当前来源并按实际上传单元核对：原生引用失效、缺少当前绑定证据、逐格文字/顺序/跨度不一致时给出明确错误；`variant/header` 等样式不参与来源内容差异。平台当前表格契约不能表达单元格内图片位置及嵌套表格，因此分别报告 `LAYOUT_CELL_MEDIA_UNSUPPORTED` 与 `LAYOUT_NESTED_TABLE_UNSUPPORTED`，不得将图片移到题干附件后宣称单元格位置已保真。

媒体归一化按题干/选项/答案/解析及 `optionKey` 区分归属，同文件跨不同位置不能相互去重；同一位置重复引用保持幂等。最终远端字段仍使用既有协议，来源索引不发送给平台。

## 验证

```bash
python -m unittest workflow.test_extract workflow.test_docx_extract workflow.test_ocr_backend
python -X utf8 -m unittest workflow.test_source_objects workflow.test_source_table_preview workflow.test_media_occurrence_projection workflow.test_phonetic_proof_errors
python -m unittest discover -s workflow -p 'test_*.py'
```

OCR 适配器测试通过模拟子进程验证失败、超时、结果格式和串行锁，不加载模型。PDF 原生提取测试在安装 PyMuPDF 的环境执行；其他环境显式跳过该项。真实模型效果需另用试卷样页验证，尤其是音标、画线、多栏和合并表格。
