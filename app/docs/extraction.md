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

`ocr-config.json` 已被 Git 忽略；示例和适配器不写死某台电脑的路径。迁移时单独安装 OCR 环境和权重，再更新这两条路径。纯文字 Word 路径不需要 OCR 配置；PDF（即使有文字层）需要配置的环境提供 PyMuPDF。

沿用 OCR 现有独立虚拟环境，不向审核台环境安装 torch/transformers，也不升级模型依赖。OCR 命令以参数数组启动，不使用 shell。多个批次调用同一模型脚本时通过操作系统文件锁串行执行，避免同时加载多个模型；直接在工作流外调用原脚本不受此锁控制。

## OCR 辅助总开关

插件「模型与设置 → OCR 识别」里的「启用 OCR 辅助识别」默认关闭。关闭时：

- DOCX 与文字 PDF 完全走原生解析；文字层质量不足的页保留原文字，只记录警告并交给结构闸门。
- 图片型 Word 不补充图中文字，只保留图片引用。
- 扫描 PDF/图片无法提取文字时明确失败并提示先开启开关，不会静默产出空正文。

开启后文字提取仍是主路径：文字 PDF 只对质量不足的页 OCR，扫描 PDF/图片由 OCR 识别，图片型 Word 补充图中文字。开关通过私有 `assist.json` 实时生效（`MUDU_OCR_ASSIST_STATE`），不需要重启后端；独立运行工作流而没有该状态文件时保持旧行为，也可用 `MUDU_OCR_ASSIST=0/1` 显式覆盖。

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
| `07_review/extraction-report.json` | 本次提取状态、候选统计和待核验项 |
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

## 验证

```bash
python -m unittest workflow.test_extract workflow.test_docx_extract workflow.test_ocr_backend
python -m unittest discover -s workflow -p 'test_*.py'
```

OCR 适配器测试通过模拟子进程验证失败、超时、结果格式和串行锁，不加载模型。PDF 原生提取测试在安装 PyMuPDF 的环境执行；其他环境显式跳过该项。真实模型效果需另用试卷样页验证，尤其是音标、画线、多栏和合并表格。
