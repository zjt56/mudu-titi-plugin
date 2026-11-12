# 派生批次视图按上游真实内容失效，消除最终格式检查幻影 KNOWLEDGE_REQUIRED

## 背景/触发

批次 `05七上人教版课时A计划StarterUnit2KeepTidy!第一课时Sec`（工作台显示「最终上传格式检查：阶段失败」）报：

```
KNOWLEDGE_REQUIRED [sec-70effc938c7cd4fd-u7/1]：knowledge 必须是非空完整叶子路径数组
... /2、/3
```

实际核对：

- `04_knowledge/knowledge.json` 与 `06_batch/batch.json` 中该题组（四线三格写颜色词，3 小题）
  的单元级与子题级知识点都已存在（`词汇/词汇辨析/形容词辨析`）；
- `06_batch/batch_meta.json` 仍是 11:21 的旧内容：组级为旧标签 `词汇/词汇辨析/名词辨析`、
  子题 `knowledge=[]`。

即知识点局部重生成 → `assemble` 已更新 `batch.json`，但依赖它的
`meta → underlines → validate` 未被失效，最终格式检查读到过期派生视图，
把「当前数据没有问题」误报成题目知识点缺失。

## 改了什么

### 1. 派生视图/闸门的输入新鲜度（`app/workflow/manifest.py`）

新增 `DERIVED_ARTIFACT_STAGES = (structure_gate, meta, underlines, validate, review_gate, format_gate)`。
`is_stale` 对它们比较完成时记录的 `semantic_inputs` 中 **上游 artifact 输入**（规范化 JSON 摘要）
与当前值：真实内容变化 → 返回过期原因（如「上游交付内容已变化：06_batch/batch.json」），
调度器随即重跑该阶段；只有空白/键序/序列化差异（摘要相同）不会触发。

只比较 `artifact:` 键，**不**改变既有的「原卷/媒体变化不自动重开填充阶段」口径；
`meta/underlines/validate/review_gate/format_gate` 都是确定性派生或闸门，重跑代价低且必须
反映当前生产者内容。上游完成后 `record_delivery → invalidate_changed_consumers` 也会立即失效
这些过期视图，避免「batch.json 已更新、batch_meta 仍旧」的混合批次。

### 2. 二审组题按小题抽样的 number 契约（`app/workflow/review_rounds.py`）

二审样例中，组题（如单词拼写组）按小题抽样时 `TARGETS` 带具体 `number`；原提示词只写了
「整组题 number=null」与「独立单题必须带 number」，模型把 `u7/2` 当成整组返回、省略 number，
导致 `Review checks must cover each target exactly once` 连续失败。现在明确：

- 只有 `TARGETS` 中 `number=null` 的才是整组核对；
- 带 number 的目标（含组题按小题抽中）必须原样写回同一个 number，只核对这一小题；
- 每条 check 的 `unit_id+number` 与 TARGETS 一一对应、恰好一次。

### 3. 本批次处理

用新逻辑重跑 `meta → underlines → validate`（`validate` 19 项检查通过），
当前 `06_batch/batch_meta.json` 的 u7 三小题已带非空叶子知识点，
`python -m workflow.format_gate` 通过（`status=passed`、`errors=[]`）。
知识点变化后 `review` 按当前输入重跑：一审全量 + 二审抽样均执行完成，
`review_gate` 保留 17 项真实标签差异待人工判断（`execution_completed=true`、
`continue_unqualified=true`，规则 6 的设计态，不阻断手动上传）；
`format_gate` 已重跑为 `completed`，`07_review/upload-format.json`
`status=passed`、`errors=[]`、`final_sha256` 与当前 `batch_meta`（`11c098c2…`）一致。

## 没改什么 / 边界

- 不放宽 `KNOWLEDGE_REQUIRED` 本身：知识点仍必须是非空完整叶子路径；本修复只是让检查读取当前数据。
- 不改变「原卷/正文/媒体变化不自动重开填充阶段」的既有口径（显式失效仍由用户/上游决定）。
- 不伪造审核：本批次 `review` 因知识点变化重跑，未把 17 项差异自动 resolve 或改标签；
  `review_gate` 保持 `needs_review` 设计态，`format_gate` 只按当前真实数据重跑通过。
- 不改上传边界：`format_gate` 通过后仍停在人工上传闸门，未自动上传/发布。

## 如何验证

- `PYTHONPATH=. python3 -m unittest workflow.test_stage_semantics`：29 项通过（含新增
  `test_changed_upstream_artifact_reopens_derived_views`、
  `test_assemble_reopened_delivery_invalidates_stale_derived_views`）。
- `workflow.test_review_rounds`：90 项通过（含新增 sample prompt number 契约断言）。
- `workflow.test_format_gate`：23 项通过。
- `workflow.test_runner_auto`：46 项通过。
- 真实批次：`format_gate` 退出码 0、`07_review/upload-format.json` `status=passed`、`errors=[]`
  （此前 3 条 `KNOWLEDGE_REQUIRED` 消失），`upload-format.json.final_sha256` 与当前
  `06_batch/batch_meta.json`（`11c098c2…`）一致。

## 回退方式

`git revert` 本提交即可；已重跑的 `batch_meta/validation-report` 属当前数据正常重算，无需回退。
