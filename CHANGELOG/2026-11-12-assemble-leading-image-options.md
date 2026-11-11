# 填充汇总与固化：图片选项前置于标签（[IMG a]A.）导致选项解析错位

## 背景 / 触发
批次 `04七上人教版课时A计划StarterUnit1Hello!第四课时Projec`（Starter Unit 1 课时4 Project）
填充汇总与固化（assemble）阶段 `needs_review`：
```
Assemble source audit failed:
[{"code":"SOURCE_OPTIONS_INCOMPLETE","number":4,
  "message":"第 4 题源头选项 ['A','B','C','D']，本组应完整包含 ['A','B','C','D'] 且内容非空"},
 {"code":"SOURCE_IMAGE_MISSING",... "第 4 题原卷图片引用缺失或未映射：image14.jpeg"},
 …（image15/16/17）]
```
任务型阅读第 4 题是**纯图片选项**，原卷排版为
`[IMG image14.jpeg]A.	[IMG image15.jpeg]B.` / `[IMG image16.jpeg]C.	[IMG image17.jpeg]D.`
（图片在选项标签之前）。

## 根因
`question_content.question_parts` 用 `OPTION` 正则按“标签 i 到标签 i+1 之间的文本”切选项。
`[IMG]X.` 布局下图片位于标签**之前**，导致：
- 每张图左移一位（A 的 image14 进题干，A 拿到 image15……）；
- 最后一项 D 文本为空。

于是 `audit_source_unit` 因 D 空文本判 `SOURCE_OPTIONS_INCOMPLETE`；assemble 审计
`check_option_images` 因 A 的 image14 未映射到 A 选项判 `SOURCE_IMAGE_MISSING`。
**两处报错同源于一个选项解析错位。**

## 改了什么
`app/workflow/question_content.py`
- `question_parts` 新增 `_split_leading_option_images`：当**每个选项标签前都紧邻
  图片标记**（纯图片选项布局）时，把前置图片归回各自标签，并从题干移除这些前置图；
  纯文本选项布局不受影响（返回 None 走原路径）。
- `app/workflow/test_question_content.py` 新增 `LeadingImageOptionTest`（3 用例）：
  前置图片归位、题干不残留 [IMG]、纯文本选项不受影响、图片选项任务型阅读 audit 通过。

## 没改什么 / 边界
- 只修正“选项图片归属”的解析；未放宽任何选项完整性/图片映射校验。
- 真实缺图、缺选项、选项错位仍照报（图不足、缺标签时不豁免）。
- 未自动上传/发布；assemble 走确定性组装（`assemble_local.try_assemble`，不调用模型）。

## 如何验证
- `PYTHONPATH=. python3 -m unittest workflow.test_question_content ... question_image_ownership
  empty_image_option_repair ocr_assembly_images source_url_image_integrity question_identity_content
  question_identity_answers upload_content assemble_local legacy_format question_draft`
  → **204 tests OK**（skipped=4）。
- 批次复跑：`assemble` 阶段 `needs_review → completed`，产出 `06_batch/batch.json`，
  `model_calls=0`；下游 `meta` 可执行。

## 回退方式
回退提交 `bd9f82e` 即恢复原选项解析；不影响已交付批次与历史数据。
