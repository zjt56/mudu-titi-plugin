# 2026-11-08 移除逐交接点严格校验与语义指纹，改为轻量哈希 + 末次整体验收

## 背景 / 触发

用户反馈插件反复卡顿、流程卡在各阶段推不动。复盘确认两类“过度校验”是主因：

1. **每个交接点跑 `inspect_stage` 全套消费者校验**（schema/字数/图片/来源绑定…），
   ~0.14s/次；调度器每轮巡检对每批反复调用，且一处细节不满足就卡住整条链。
2. **记录时计算 `stage_semantics.input_signatures` 逐单元语义指纹**（~0.13s/阶段）。

用户明确使用方式：原卷一次提取好后**基本不再改动**，后续只是逐个补缺字段
（缺答案填答案、缺解析填解析、缺知识点标签、缺难度…），不反复校验题干原文；
只在**最后**整体校验一次格式（如第 1 题是否绑定对的题干和答案）。

## 改了什么（app/workflow/manifest.py）

1. **`dependency_satisfied` 去掉逐交接点 `inspect_stage` 全量校验**，改为：
   上游状态为完成态、未被标记重建、其上游都已执行完成、且产物文件齐全即可消费。
   （needs_review/failed/未完成/重建中 仍不可消费，守住“质量≠执行”。）
2. **`is_stale` 改为轻量内容哈希**：只比对产物与本阶段声明的输入（源文件、结构、
   正文、答案、原始上传件、媒体、上游产物）的规范化内容指纹；不再跑语义指纹层。
   换原卷/改正文/改媒体/改结构/换源 → 下游失效；仅时间戳/键序/空白/词数/难度变化
   → 不失效。
3. **`record_semantics` 不再计算逐单元语义签名**，只记普通输入/产物哈希（廉价）。
4. **`_content_digest`**：JSON 文件按规范化结构哈希（忽略 `generated_at` 等时间戳、
   `words`/`difficulty` 等基本标签），非 JSON 裸哈希。
5. 补全各阶段真实输入绑定：answers/types/explanations/knowledge/audio/assemble 绑定
   正文/结构/原始上传件/媒体；explanations/assemble 另绑答案来源；assemble 绑
   extraction；validate 绑正文/结构/最终批次；audio 绑切片证据与切片文件。

## 没改什么 / 边界

- 严格**格式/题号覆盖/来源保真/标签**校验仍在，只是**集中到 validate / format_gate /
  上传前**一次性验收，不再每个交接点跑。
- `needs_review`/`failed`/重建中的交付仍不可被下游消费（质量≠执行）。
- 不自动上传/发布；不动用户批次数据。

## 如何验证

- `workflow.test_stage_semantics` 27 项通过（含换源/换媒体/换结构失效、时间戳/词数/
  难度不失效、needs_review 不放行、rebuild_required 不可消费等）。
- 23 卷实数据：assemble（确定性 0 模型）→ meta → underlines → validate 全部通过，
  推进到 review。
- 全量 `workflow.test_*` 逐模块回归（结果见下）。

## 回退

还原 `manifest.py` 的 `dependency_satisfied`/`is_stale`/`record_semantics`/`_content_digest`
与输入绑定段即可。
