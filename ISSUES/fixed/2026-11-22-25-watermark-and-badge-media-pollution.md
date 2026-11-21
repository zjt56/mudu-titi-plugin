# 卡点：2025 安徽卷页眉水印、新考法徽章及答案解析跨文档图标污染试题图片

- id: 2026-11-22-25-watermark-and-badge-media-pollution
- status: fixed
- batch: 2025年安徽省初中学业水平考试英语正文
- batch_dir: /Users/zjt/Desktop/上传插件版/workflows/2025年安徽省初中学业水平考试英语正文
- stage: extract / draft / assemble / explanations
- check: decorative banner/badge/cross-archive image pollution
- created: 2026-11-22T00:00:00+08:00
- last_seen: 2026-11-22T00:00:00+08:00
- fixed_at: 2026-11-22T00:03:40+08:00
- fix_commit: working-tree
- title: 2025 安徽卷页眉水印、新考法徽章及答案解析跨文档图标污染试题图片

## 现象（卡点）

1. 卷头 `[IMG image3.jpeg]1.2025年安徽省初中学业水平考试`（`1091x191` 水印横幅）、`答案链接☞答案册P1` 与 `[IMG image4.jpeg]`（`扫码练听力` 二维码）在去噪与装配时未被完整清洗，旧元数据把 `image3.jpeg` 误绑到每道题的 `attachments`（`placement=question`）；
2. `新考法` 栏目小胶囊（`260x43` / `398x43`）与答案解析里的 `熟词生义`/`记笔记`（`194x58` / `151x58`）未超过原有 `size <= 3072` 阈值，未被标为 `decorative`，且 `wire_format.replace_images` 此前仅在 `explanation` 位置跳过装饰图，导致题干/材料/选项里的 `新考法` 仍转成题目附件；
3. `explanations_direct.py` 从 `01_source_ans/正文.txt` 直抄书面表达与解析时保留了 `answers.docx` 内部 `[IMG image6/9/10.jpeg]` 占位，落盘核对时与 `01_source/media/` 同名听力配图错绑。

## 具体检查与判定

- 修复方案：
  1. `app/workflow/denoise.py`：`NOISE_PATTERNS` 补齐 `答案链接☞答案册...`，`clean_body` / `_looks_like_question_line` 共用 `_plain_line` 剥离 `[IMG ...]` 后再识别编号卷头，彻底清除 `[IMG image3.jpeg]1.2025年...` 与 `答案链接` 行；
  2. `app/workflow/docx_extract.py` + `app/workbench/media_audit.py`：在 `docx_extract` 提供共享无依赖图片尺寸解析（`image_dimensions`）与通用装饰判定 `is_decorative_media`（覆盖 `h <= 62 & ratio >= 2.4 & size <= 32768` 的徽章胶囊、`ratio >= 4.8` 的水印横幅及高频小图标，经实测全保真听力选项最小图高 `>= 69px` 不误伤），`media_audit` 直接复用该尺寸与装饰口径；
  3. `app/workflow/wire_format.py`、`question_draft.py`、`agent_tasks.py`、`batch_fill.py`：新增共享 `wire_format.decorative_names(batch_dir)`（汇集 `extraction` 装饰标记、`01_source/metadata.json` 元信息引用、`01_source/media/` 尺寸启发式与 `扫码` 邻近图，同时保护已确认纯图题干），`replace_images` 在 `question/material/option/explanation` 全位置过滤装饰图，并在 `normalize_item` 与 `batch_fill._sync_media_attachments` 清理陈旧装饰附件；
  4. `app/workflow/explanations_direct.py`：`_writing_explanation` 与 `_parse` 对来自 `01_source_ans/` 的文本剥离跨包 `[IMG ...]`，消除与 `01_source/media/` 的文件名碰撞。

## 修复记录

- 复验证据：使用 `/tmp` 对 2025 卷 `source.docx` 与 `answers.docx` 无破坏实跑重提取与直抄：`metadata.source` 为 `2025年安徽省初中学业水平考试`；`image3.jpeg`、`image11/13/18/20/21` 全部标为 `decorative`，`answers.docx` 全部 8 张徽章/横幅标为 `decorative`，全部 12 张真实试题配图（`image5/6/7/8/9/10/12/14/15/16/17/19`）完整保留；书面表达 `explanations_direct._writing_explanation` 零 `[IMG` 残留。
