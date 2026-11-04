# 流水线：16 个阶段与自动驱动

这篇管"一批卷子从原件到待上传草稿"的推进方式：有哪些阶段、每个阶段产出什么、自动执行器在哪里停下，以及哪些动作永远不允许自动化。

## 16 个阶段

阶段定义是封闭契约，写在 `workflow/contracts.py` 的 `STAGES` 里，顺序、依赖、产物都不可临时增删。类型只有四种：`agent`（由执行者或代理完成）、`command`（跑脚本）、`gate`（机器闸门，只能由检查结果放行）、`human`（人工确认，只能由人标记）。

| # | id | 做什么 | 产出 | 类型 | 依赖 | 停点 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `extract` | 来源、年份、地区、来源类型与原卷复合提取 | `01_source/metadata.json`、正文、结构与提取证据 | command | 原件（必有） | 确定性执行器 |
| 2 | `structure_gate` | 对照原卷逐题核题号、选项、材料边界和真实对话分组 | `01_source/structure.confirmed.json`、`07_review/structure-gate.json` | gate | `extract` | 证据不足则阻断并准备版面复核 |
| 3 | `answers` | 子智能体4：对照确认后的题号提取答案、整理答案与解析对应关系并判定难度 | `01_source_ans/answers.json` | agent | `structure_gate` | 无单独答案卷时可用原卷附录；无任何答案证据则待核验 |
| 4 | `audio` | 按确认后的听力单元切段并核对起止 | `02_audio/audio_map.json` | agent | `structure_gate` | 无音频才跳过 |
| 5 | `types` | 子智能体3：判题型与子类、统计题目词数、判断内容是否完整 | `03_types/types.json` | agent | `structure_gate` | 等待代理执行 |
| 6 | `explanations` | 子智能体1：解析匹配、缺解析生成、文章大意匹配、缺大意生成 | `04_knowledge/explanations.json` | agent | `types`、`answers` | 等待代理执行；归属不确定转待核验 |
| 7 | `knowledge` | 子智能体2：知识点打标与教材知识点匹配，逐字沿用解析与大意 | `04_knowledge/knowledge.json` | agent | `types`、`answers`、`explanations` | 等待代理执行；改写内容即拒绝 |
| 8 | `assemble` | 组装批次 | `06_batch/batch.json` | agent | `audio`、`types`、`knowledge` | 等待代理执行 |
| 9 | `meta` | 难度与分类 | `06_batch/batch_meta.json` | command | `assemble` | — |
| 10 | `underlines` | 从 docx 字符格式补画线 | `06_batch/batch_meta.json` | command | `meta` | 来源非 docx 则跳过 |
| 11 | `validate` | 本地脚本体检（18 个配置项）+ 源头逐题对账 | `07_review/validation-report.json` | gate | `meta` | 失败即阻断 |
| 12 | `review` | 三类审核与人工队列 | `07_review/manual-review-queue.json` | agent | `validate` | 等待代理执行 |
| 13 | `review_gate` | 审核放行判断 | `07_review/review-gate.json` | gate | `review` | 失败即阻断 |
| 14 | `upload_gate` | 上传前人工确认 | 无（写入台账） | human | `review_gate` | 必须人工确认 |
| 15 | `upload` | 写入上传暂存 | `07_upload/receipt.json` | command | `upload_gate` | 冻结请求、批准绑定、远端目录/媒体和复核通过后，显式 `upload --yes` 提交 |
| 16 | `publish_gate` | 发布前人工确认 | 无（写入台账） | human | `upload` | 必须人工确认 |

要点：

- 第 9 阶段的产物仍是 `batch_meta.json`，它只改文件内容，不另存副本。
- 第 10 阶段运行目录/排版/表格等本地脚本，并额外直接执行源头逐题对账。部分脚本为本项目补充实现，不能视为已验证等价于发布侧原脚本；任何缺脚本、源题选项丢失、跨材料或表格/画线缺失都必须如实报告，不能调低检查来换取通过。
- 第 14 阶段现通过[上传 API 对接](upload-api.md)实现：`prepare-upload` 读取当前目录、校验并冻结 API 请求，人工批准绑定最终文件与目标，再由 `upload --yes` 原样提交到暂存；普通 run/auto 不提交。缺凭据、媒体、来源或复核证据时仍阻断，远端人工确认后才生成草稿。
- 第 15 阶段永远是最后一步，`publish_gate` 通过也不会让批次变成已发布；发布动作在发布侧执行。

## 结构自动闸门（第 2 阶段）

`structure_gate` 是**自动机器闸门**，不再需要人工确认。执行 `run`/`auto run` 时由
`workflow/structure_gate.py` 自动完成：

1. **去噪**：`workflow/denoise.py` 把广告、扫码、水印、页眉 logo、分隔线、页码等与题目无关的
   行整行剔除，并记入 `structure-gate.json` 的 `removed_count` 供审计，不静默删除。
2. **元信息抽取**：卷名、年份、地区、考试时长、总分、整卷作答说明抽到 `meta` 字段
   （`structure.confirmed.json` 的 `meta`），题目正文里一律不再保留。题库入库时这些值走专门字段。
3. **按题型结构化**：独立题（单项填空、单词拼写、完成句子等）一题一单元逐题呈现；组合题
   （完形、阅读、任务型阅读、听力信息转换、补全对话等）以题组为一个单元整体呈现，共享材料只出现一次。
4. **机器自检**：`source_present`、`sections_present`、`coverage`（题号全覆盖无重复）、`denoise`
   （切片不残留卷头）、`grouping`（题组题号属于合法大题）。任一失败即 `blocked`，列出阻断码
   （`SOURCE_EMPTY`、`NO_SECTIONS`、`MISSING_NUMBERS`、`DUPLICATE_NUMBERS`、`RESIDUAL_HEADER`、
   `GROUP_OUT_OF_SECTION`）。

通过即写 `01_source/structure.confirmed.json` 并进入题型判定；不通过停下等人工或重跑，
**不会**因题号归属、图片理解的语义歧义被机器静默放行——机器拿不准的会以阻断码暴露出来。

## 阶段状态

台账里每个阶段只有九种状态：

| 状态 | 含义 |
| --- | --- |
| `pending` | 未开始 |
| `running` | 正在跑 |
| `completed` | 完成且产物已记录哈希 |
| `approved` | 人工确认通过 |
| `needs_review` | 需要人工再看 |
| `blocked` | 被阻断，等外部条件 |
| `failed` | 执行失败 |
| `invalidated` | 上游变了，必须重跑 |
| `skipped` | 按规则跳过（如没有音频） |

只有 `completed`、`approved`、`skipped` 算完成。`failed`、`blocked`、`invalidated` 都必须处理后才能继续。

## 批次目录

一个批次一个目录，原件留在原位，只登记路径和 SHA-256，不复制、不覆盖。

```text
workflows/<批次>/
├─ workflow/
│  ├─ manifest.json          # 台账：阶段、哈希、人工决策
│  ├─ run-log.jsonl          # 执行日志
│  ├─ decisions.jsonl        # 人工决策审计
│  └─ logs/                  # meta.log、upload.log
├─ 01_source/
│  ├─ 正文.txt
│  ├─ structure.json          # 结构候选
│  ├─ structure.confirmed.json# 自动结构化的确认视图（去噪/元信息/单元）
│  └─ inventory.json          # 题号清点
├─ 01_source_ans/answers.json
├─ 02_audio/audio_map.json
├─ 03_types/types.json
├─ 04_knowledge/knowledge.json
├─ 06_batch/
│  ├─ batch.json              # 组装的中间批次
│  └─ batch_meta.json         # 加完难度分类的最终校验对象
├─ 07_review/
│  ├─ validation-report.json
│  ├─ content-review.json
│  ├─ metadata-review.json
│  ├─ manual-review-queue.json
│  ├─ unchecked.json
│  ├─ review-gate.json
│  ├─ decisions.jsonl
│  ├─ report.json / report.md / notification.txt
│  └─ independent-plan.json / independent-records.json
├─ 07_upload/receipt.json
├─ candidates/units/<unit_id>.json   # 审核台写回的单元级标签
└─ media_upload/                     # 待上传媒体
```

没有 `05_` 段位：难度与分类写进 `06_batch/batch_meta.json`，不单独占一级目录。

## 自动执行器的行为

`python -m workflow.auto run` 是一个"扫一遍、推一步、记下来"的循环，不做业务判断。

### 扫描（scan）

1. 遍历 `inbox/` 下每个子目录。
2. 若有 `batch.json`，按其中的 `workflow_id`、`year`、`region`、`subject`、`source`、`answers`、`audio`、`docx` 初始化。
3. 没有 `batch.json` 时按后缀猜：`.docx`/`.doc`/`.pdf` 为原卷；文件名含"答案/answer/key/解析"的判为答案卷；`.mp3`/`.wav`/`.m4a`/`.aac` 为音频。
4. 目标批次没有 `workflow/manifest.json` 时，调用 `runner` 的初始化流程登记输入。

### 推进（run）

按 `STAGES` 顺序逐个看，遇到第一个"过不去"的阶段就停下，把原因写进队列：

| 情况 | 停下的原因文案 | `action` |
| --- | --- | --- |
| 上游还没完成 | `等待上游：<stage>` | `agent` |
| 人工闸门 | `等待人工确认（<标题>）` | `human` |
| 自定义桥未配置当前代理阶段 | `等待代理执行（<标题>）` | `agent` |
| 通用任务等待接单或代理需要补充证据 | `代理任务等待接单、补充证据或处理` | `review` |
| 代理桥命令失败 | `代理桥执行失败` | `failed` |
| 普通命令失败 | `脚本执行失败` | `failed` |
| 机器闸门没通过 | `闸门未通过` | `gate` |
| 上传阶段 | `上传须人工通过专用命令显式确认` | `human` |

已经在终态的阶段直接跳过；`invalidated` 的阶段会被重新执行。

### 队列文件 `_queue.json`

每轮结束写 `workflows/_queue.json`，供人和审核台看"现在卡在哪"：

```json
{
  "generated_at": "2026-01-01T12:00:00",
  "batches": [
    {
      "batch": "某批次",
      "batch_dir": "workflows/某批次",
      "status": "reviewing",
      "stopped_at": "review_gate",
      "action": "gate",
      "reason": "闸门未通过",
      "report": "07_review/report.json",
      "digest": "无误 40 / 有疑问 3 / 阻断 1 / 未核对 2"
    }
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `batch` | 批次名（目录名） |
| `batch_dir` | 批次绝对或相对路径 |
| `status` | 批次当前总状态 |
| `stopped_at` | 停在哪一个阶段 |
| `action` | 停下的类别：`human` / `agent` / `gate` / `failed` |
| `reason` | 给人看的原话 |
| `report` | 通报文件路径（可选） |
| `digest` | 四态统计一行话（可选） |
| `report_error` / `error` | 生成通报或推进时的异常（可选） |

## 代理桥 `workflow/agent-bridge.json`

`agent` 阶段默认使用[通用 Agent 接口](agent-interface.md)，无需指定厂商的 CLI。没有本机配置时导出任务包并等待接单；Agent 交回结果后，由导入器校验、保存产物并登记台账。有本机 CLI 配置时，同一入口使用任意命令或 DSH/Codex 的可选适配器。

桥文件可覆盖默认阶段调用，是阶段 id 到命令参数数组的映射，例如：

```json
{
  "types": ["{python}", "-m", "workflow.agent", "run", "--stage", "types", "--batch-dir", "{batch_dir}"]
}
```

规则：

- 桥命令只接受非空数组，不作为 shell 字符串执行。`{python}` 使用工作流当前解释器。
- 自动调用不替 Agent 批准机器或人工闸门；文件交接仅允许导入器登记通过校验的代理阶段产物。
- 退出码 0 仍须有全部声明产物才完成；代理退出码 3 表示等待接单或复核，其他失败如实记录。
- 没有桥文件时使用通用默认入口，不默认查找 DSH。自定义桥明确遗漏某个阶段时，保留等待代理行为。
- 原卷 `extract` 已有复合提取执行器，不需要配置代理桥。

可选覆盖示例在 `workflow/agent-bridge.example.json`。换电脑或交给其他 Agent 时先读根目录 `AGENTS.md`。

## 禁止事项

以下动作任何自动化、脚本、代理都不得执行：

1. 把 `upload_gate`、`publish_gate` 标成 `approved`（`structure_gate` 已是自动 gate，不得手工标记）。
2. 手工把 `gate` 类阶段标成 `completed` / `approved` / `skipped`（`runner.py` 直接拒绝）。
3. 跑上传、调发布接口、删除草稿。
4. 修改批次产物（`batch.json`、`batch_meta.json`、各类 review 文件）来"让它看起来通过了"。
5. 在审核文件里编造核对人、证据或签名。
6. 用 `--allow-missing` 在没有产物时强记人工确认。
7. 跳过 `validate` 直接进审核，或跳过 `review_gate` 直接确认上传。

改了上游就必须让下游失效重跑，不能只补一个"通过"。上游改动范围由 `contracts.py` 的 `EXTRA_INVALIDATION` 决定，例如：

| 改了什么 | 连带失效 |
| --- | --- |
| `extract` | `types`、`knowledge`、`assemble`、`meta`、`validate`、`review` |
| `types` | `knowledge`、`assemble`、`meta`、`validate`、`review` |
| `knowledge` | `assemble`、`meta`、`validate`、`review` |
| `assemble` | `meta`、`underlines`、`validate`、`review` |
| `meta` | `underlines`、`validate`、`review` |

## 常用命令

```powershell
python -m workflow.auto scan --inbox inbox
python -m workflow.auto run  --inbox inbox --once
python -m workflow.auto run  --inbox inbox --interval 60 --limit 3 --stop-at review_gate
python -m workflow.runner status   --batch-dir workflows/<批次>
python -m workflow.runner run      --batch-dir workflows/<批次>   # structure_gate 为自动闸门，随 run 一起执行
python -m workflow.runner mark     --batch-dir workflows/<批次> --stage upload_gate --status approved --actor <人名>
python -m workflow.doctor
```

退出码：`0` 成功或已停在人工节点，`1` 闸门未通过，`2` 用法或前置条件错误。

## 相关代码

| 文件 | 作用 |
| --- | --- |
| `workflow/contracts.py` | 16 个阶段、依赖、产物、失效关系 |
| `workflow/auto.py` | 扫描 inbox、初始化、推进、写 `_queue.json` |
| `workflow/runner.py` | 各阶段实际执行、人工标记、闸门判断、上传阻断 |
| `workflow/manifest.py` | 台账读写、哈希、状态推导、失效传播 |
| `workflow/checks.py` | `validate` 阶段的 16 项体检调度 |
| `workflow/review.py` | `review_gate` 的放行规则 |
| `workflow/independent_review.py` | 独立复核的快照与签名校验 |
