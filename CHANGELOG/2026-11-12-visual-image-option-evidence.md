# 2026-11-12 图片选项题改用视觉/原文佐证判定，替代正则硬啃选项排版

## 1. 背景 / 触发

04 批次结构闸门被 SOURCE_OPTIONS_INCOMPLETE 第4题阻断：该题是图片选项题，原文排版为
`[IMG image14.jpeg]A. [IMG image15.jpeg]B. …`（图紧贴选项标签之前）。正则按“标签后到下一
标签”切选项，把每张图算到前一个选项、D 选项切空 → 误报“缺选项”，连带 NUMBERING_RESTART
也无法按分区消解。用户指出：这种图片选项题应该用视觉理解直接判断，而不是用正则硬啃排版。

## 2. 改了什么

- 新增 `question_content.image_option_evidence(unit, local_lines, image_semantics)`：
  保守判定“这题是图片选项题、且窗口内图片数与选项数匹配”。判据：窗口内出现
  `[IMG]X.` 紧贴形态、`[IMG]` 引用数 ≥ 应有选项数、若提供视觉语义则这些图不能全是装饰
  图标（icon）。返回证据或 None（不适用/不豁免）。
- `structure_gate.run_gate`（scoped 分支）：对 audit 报的 SOURCE_OPTIONS_INCOMPLETE，
  用上述证据核实；成立则该阻断降级为留痕（`report.visual_option_resolutions`），不阻断。
  纯文字选项、完形/语法选择缺选项、以及图数不足或全装饰的情形仍照常报错。
- 新增 `_load_image_semantics`（读 01_source/image_semantics.json，缺失不阻断）。
- 回归 test_question_content.ImageOptionEvidenceTest（3 例）：图片选项识别、纯文字不误判、
  装饰图标不豁免。

## 3. 没改什么 / 边界

- 不改 question_parts 的正则切割（避免脆弱补丁）；不改原文、不生成内容。
- 视觉佐证仅用于“证明缺失选项其实是存在的图”，不据此放行其它阻断，不改判定阈值。
- 无 image_semantics 时退回纯原文 `[IMG]` 计数判断（仍保守），不影响无视觉环境的批次。

## 4. 如何验证

- 04 批次真机 run_gate：passed=True、blockers 空；visual_option_resolutions 记录第4题为
  “图片选项题：窗口内图片数与选项匹配（4=4），非缺项”。
- question_content 视觉已识别 image14=镊子/image15=汤匙/image16=餐叉/image17=餐刀（与题目
  “不能用什么吃中餐”对应，答案应是餐叉），证明视觉判图片选项可靠。
- test_structure_gate + test_question_content 33 例全过；legacy_format+visual 28 例全过。

## 5. 回退方式

删除 image_option_evidence 与 structure_gate 中该降级分支即可；无数据改动。
