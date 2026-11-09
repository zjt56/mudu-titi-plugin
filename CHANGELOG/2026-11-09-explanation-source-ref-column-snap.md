# 2026-11-09 解析 source_ref 列号吸附正文（17 年安徽卷卡点）

## 背景 / 触发

批次 `【木牍教育】17年安徽中考英语` 解析阶段 `needs_review`，最终只剩 `q-31` 与 `group-71-72-73-74` 反复报
`EXPLANATION_SOURCE_UNVERIFIED 解析未能唯一逐字绑定原题`。

只读排查确认是模型 `source_ref` 列号误差（非原卷缺解析、非校验过严）：

- `q-31` 附录第 12 行 `31.D　【解析】考查交际用语。…`，正文从第 9 个字符开始（`0` 起算）。模型三轮把
  `start_column` 填成 8 / 7 / 7，切出 `】考查…` / `析】考查…`，把 `【解析】` 的尾字符带进正文。
- `group-71-72-73-74` 四个子题同样起点定在 8 列（切出 `】细节理解题…`）；其中 72、73 的 `end_column`
  还比整行正文短，正文被截在句中（如 `…away!”可知` 后缺失 `,这对老夫妇把茶叶吃掉了。`）。

现有 `content_fragments` 只对“末尾列越界”做行末截断，未处理“起点落在字段标签内”和“末尾列短于正文、
截在句中”两种列号误差。

## 改了什么

- `app/workflow/content_fragments.py`：
  - 新增 `_FIELD_LABEL` 与 `_snap_label_start`：当 `start_column` 落在 `【解析】/【答案】/【文章大意】`
    等字段标签范围内时，吸附到标签结束后的正文起点；已位于正文或末尾的起点不受影响。
  - 新增 `_clean_boundary` 与句读边界字符集：仅当 `end_column` 停在句中（前一字符不是句末/分句标点）
    时把末尾延到行末；停在边界上的短引用（有意只引一句）保持不变。末尾越界仍按行末截断。
  - 二者都只把“模型列号误差”吸附到原文对应位置，不删改原文，不改变归属校验；吸附后仍走原有
    `【解析】` 标签剥离与逐字归属校验。
- 回归测试 `app/workflow/test_content_fragments.py`：新增起点落标签内吸附（含 `【文章大意】`）、
  末尾截在句中延到行末、停在边界不延长的用例。

## 没改什么 / 边界

- 不放宽逐字归属校验，不生成/推断解析；仍要求正文逐字来自权威来源。
- 有意只引用部分正文（停在句读边界）的合法片段不会被打回或改写。
- 未直读真实批次台账或产物，17 年批次只用内存复验；校验规则、上传边界不变。

## 如何验证

- `workflow.test_content_fragments`：23 项通过（新增 3 组列号吸附回归）。
- 关联下游 `workflow.test_upload_content` / `workflow.test_no_ai_generation_rule` /
  `workflow.test_agent_tasks` / `workflow.test_label_execution` 与片段测试合计 148 项通过。
- 17 年批次内存复验：用模型 round-02 实际 `source_ref` 展开后，`q-31`、`group-71-72-73-74` 及其余
  未填单元之外不再产生这两处 `EXPLANATION_SOURCE_UNVERIFIED`。
- 全量 `workflow` 结果见提交说明（沙箱内 `test_local_agent_ownership` 依赖 `ps` 会报环境错误，非本次改动）。

## 回退方式

`git revert` 本提交即可；仅恢复旧的“不做列号吸附”行为，无数据迁移。
