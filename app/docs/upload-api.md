# 题库 API 上传暂存对接

核对依据：[开发者文档中心](http://gudi.metal-anchor.site/docs/)、[纯文本文档](http://gudi.metal-anchor.site/developer.md)、[实际题型目录 JSON](http://gudi.metal-anchor.site/filter-framework.json)。核对日期：2026-11-03。页面标注 API Version 2026-09-29、文档版本 2026-10-23。部分文字仍写 26 个题型，实际目录 JSON 有 27 类；知识点基础节点数量的文字也与本地快照可能不同，不能据此覆盖目录，应读取当前启用 API 目录。

## 远端状态与权限

`POST /api/uploads` 只创建**上传暂存**，成功返回 `id` 和 `stage=pending_confirmation`。这不表示已生成草稿或已发布。所属科目的人工编辑在远端“上传暂存”确认后才创建草稿，之后另行人工核验与发布。

客户端只实现目录读取、媒体只读 HEAD 核验与上传暂存提交。不调用上传暂存列表、确认、删除、发布、撤回、知识点确认或清除警告接口。不创建管理员账号、不操作服务器数据库、不新增知识点；缺知识点时报告具体路径和缺项，另行走建议/人工批准流程。

## 连接与凭据

默认 API Base 是 `http://127.0.0.1:8089/api`。插件上传设置中的「SSH 隧道」卡片保存服务器、端口和密钥路径，后端在读取目录/上传时自动建立本机隧道；使用者只需填 API Key 与上传账户。只把文档公网 HTTP 地址用于阅读，认证 API 请求走本机隧道或 HTTPS。客户端拒绝向公网明文 HTTP 发送认证请求，并禁止自动跟随重定向。

```bash
# 以下均为占位符，按受控运维说明替换，不把实际私钥或密码放进仓库
ssh -N -L 127.0.0.1:8089:127.0.0.1:89 \
  -i <本机私钥文件> -o IdentitiesOnly=yes \
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=60 \
  -o ServerAliveCountMax=3 <SSH用户>@<SSH主机>
```

另一个终端可只读检查：

```bash
curl -fsS http://127.0.0.1:8089/api/health
```

API Key 由所属科目的上传账号绑定，上传只需要适当的 `draft` 范围；SSH 私钥与 API Key 不可互换。Key 通过 `MUDU_API_KEY` 环境变量注入当前进程，可由密码管理器或终端静默输入。不要把明文写入命令历史、配置 JSON、任务包、题目、日志或版本控制。

复制 `workflow/upload-config.example.json` 为 `workflow/upload-config.json`，仅设置目标与环境变量名称；也可设置 `MUDU_UPLOAD_CONFIG` 指向本机配置。配置可指定 `knowledge_version_id`，用于存在多个启用通用版本时明确选择。实际配置被 Git 忽略，示例不含任何个人凭据或私钥位置。

## 上传前的数据映射

本地 `06_batch/batch_meta.json` 保持只读。API 副本的转换是确定性的：

- 本地 `question_type` 映射为 `type`，容易/适中/困难映射为易/中/难。顶层字段与 `filterMetadata` 中的别名冲突会阻断，不静默覆盖。
- 组题保留共享材料、完整子题、共用选项、题图、表格 `materialBlocks`、合并单元格和 `articleSummary`；不重新切题。文章大意原卷没有时留空或填「无」，不生成；历史 AI 生成内容直接丢弃。
- 来源、位置、版本与授权须有明确字段，不猜授权、不填占位。真题/模拟题需要年份；模拟题还需要机构、学校与地区试卷版本。
- `answer`、`explanation` 不重复答案/解析前缀。来源没有解析时按用户规则在 `explanation` 填「无」，不再使用 AI 生成；不可溯源且非占位的自撰解析仍会拦停。`answer` 字段只写答案本身。
- 本地题组的 `answer` 可以为空，答案保存在全部结构化子题。仅在 API 副本中，确认子题号为唯一正整数、完整覆盖组级 `numbers` 且各子题答案非空后，按题号升序生成 `6. C\n7. A` 形式的组级答案；子题原顺序和答案、本地正式文件均保留。已提供的非空组级答案不覆盖；缺题号、覆盖不符或空子题答案仍阻断。此转换兼容开发文档的组级答案字段，不代表已实测远端暂存接口拒绝空组级答案。
- 题目 `id` 是题库全局主键。本地装配的 `q-N` / `group-N-N` 只在单卷内唯一；冻结上传时自动加 `<年份>-<批次ID前10位哈希>-` 前缀（例如 `2015-fd3966e824-q-1`），显式 ID 保持原样。多卷或重传原样发送会撞云端 `409 question_id_exists`；同一批次目录再次上传得到相同前缀，属有意防重，替换旧卷应先删旧草稿或另建批次身份。
- 先请求 `GET /api/catalog/knowledge-versions`，选择启用且不绑定教材的英语通用版本，再请求 `GET /api/catalog/knowledge-points?version_id=...`。只匹配已有、启用的目录叶子；完整父路径用于同名消歧。
- 本地目录 ID 不假定等于服务器 ID。API 副本依据完整路径重新匹配当前远端 ID，写入 `knowledge_version_id` / `knowledge_ids`；本地快照不被远端目录覆盖。
- API 要求组级目录关联汇总组级知识点（整组题型子题 knowledge=[]），因此仅在提交副本中按组级名称与 ID 生成一致关联；本地组级/子题打标规则照常先校验。
- 题型不匹配、缺知识点、明显题数/子题型矛盾、来源不全、未知字段和未解析本地媒体标记会阻断；空答案会填「无答案」继续（服务端不强制答案非空），答案核验遇到该占位不作为冲突。
- 精确载荷上限为 **2,000,000 个 UTF-8 字节**。超限需按完整单元另建批次、重新审核，不自动截断或一口气分批发送。

2026-11-03 已通过本机安全隧道实测认证目录读取：启用版本为 `KCV-ENGLISH-KNOWLEDGE-1-1`，返回 522 个节点。真实节点的 `active` 为 SQLite 整数 0/1，映射器仅兼容精确的 0/1 与布尔值，0 节点和停用父节点下的路径不可用于上传；字符串、任意整数或浮点数不被宽松当作已启用。客户端继续支持数组及 `versions/items`、`points/items` 包装，遇到未知形状或未取全的分页会报错，不把部分目录当全集。

## 最终内容的额外阻断

- 对 API 请求副本的每一层再次检查精确题型、子类和分类值；不允许带首尾空格的非目录原值通过本地规范化检查后原样发出。
- 听力单元优先带自己的 `audio` 附件；缺音频只记 `MISSING_AUDIO_ATTACHMENT` 警告并继续上传，服务端暂存不强制音频，后续可补传。共享听力题组仍应在组级关联片段，子题音频不能冒充整组覆盖。
- 附件统一走受控 `attachments`。正文中的 HTML `img/audio/video/source` 和 Markdown 图片也会阻断，避免无引号或外链媒体绕过路径及 HEAD 检查；普通文本链接、`<u>` 等文字格式不因此删除。
- 带 Markdown、HTML 或括号包装的“答案／解析”标题同样会报错，不静默删掉源内容。
- `upload_content` 只读检查已提供的答案/解析原文。解析与文章大意是可选字段：能逐字溯源就保留；为空、占位或历史 AI 内容都按缺失处理，不阻断。文章大意按其后真实题号确定所属材料，重复 A/B 标记不当作唯一 ID；明确混入其他材料大意的解析会报 `EXPLANATION_CROSSES_MATERIAL`。
- 明确占位「无」（及兼容的历史占位写法）不参与溯源校验：它表示原卷确实没有，不是来源主张。不可溯源的解析只记录 `EXPLANATION_SOURCE_UNVERIFIED` 提示并清为「无」；`check_explanations` 只统计可溯源/缺失/未溯源数量，不再报「解析为空」或阻断流程。
- 地理地区应写入 `filterMetadata.region`；只有省份名称的 `location` 不代表原卷中的题号、页码或章节位置。

大意/解析来源文件摘要也加入冻结计划。来源变化后，原批准不能继续提交。批次名称可以从**绑定当前正文摘要**的已识别 `metadata.paper_title` 继承，不从文件夹年份猜测；版本和授权不自动补造。

以上机器检查不代替真实独立审核、原图对照或音频回听。只将已核实的具体冲突送现有工作台队列，不为每道题批量制造人工确认。

## 媒体是独立部署步骤

文档没有提供媒体上传 HTTP API，本次不会虚构接口。使用者通过既有受控运维流程部署媒体到站点的 `static/source-media/<批次>/`，随后在题目 `attachments` 中引用 `/source-media/...`。

```json
{
  "attachments": [
    {"kind":"image","url":"/source-media/example/option-a.png","placement":"option","optionKey":"A"},
    {"kind":"audio","url":"/source-media/example/dialogue-6-7.mp3"}
  ]
}
```

附件只允许 image/audio；选项字母为大写 A–Z。拒绝外链、Base64、路径穿越、查询串与片段。准备和提交前会通过同一站点只读 HEAD 检查路径与类型，媒体请求不附带 Bearer。文件不存在或类型不符则停止。

每个题目或子题最多携带 **20 个附件**，超限在插件侧报 `ATTACHMENTS_LIMIT_EXCEEDED`，不截断附件继续上传。混合 ID 批次逐题处理：`q-N/group-N` 加批次前缀，已有自定义 ID 保持原样，不能因混入自定义 ID 而跳过本地题号的前缀保护。

题库只渲染 `placement` 的 `question` / `option` / `answer` / `explanation` 四个位置。正文（题干、材料）里的 `[IMG]`，以及历史批次中显式的 `material` / `stem` 位置，生成线上载荷时统一按 `question` 发送；原样发送 `material` 会在远端的「逐题确认」阶段被整批拒绝为 `400 invalid_request`（内部原因 `invalid_attachment`）。

`[IMG image.png]`、本地音频路径和未部署图像不能自动替换成看似存在的 URL。听力必须已完成真实边界切分和回听；部署可达并不能证明没有串题。HEAD 本身也不能证明内容与本地源件逐字节一致，媒体哈希、题号对应和版面仍应在批次证据中归档。

## 四步提交

1. 按当前流程完成结构、标签、装配及真实分轮审核，解决具体冲突和缺项。
2. 准备上传副本并冻结请求，不提交题目：

```bash
python -m workflow.runner prepare-upload --batch-dir workflows/<批次> \
  --upload-mode source --upload-target 中考真题 --name <明确的批次名称>
```

`upload_mode/target/name` 可直接来自正式批次；命令参数用于显式补充，不能用来绕过题目级来源矛盾。`batch_id` 沿用正式字段，缺少时使用当前 workflow_id 作为本地稳定标识。

可用 `--catalog <已读取的远端目录快照.json>` 做离线准备。快照结构为 `{"version":{...},"points":[...]}`。这不冒充当前在线目录；提交前仍会重新读取 API 并核对。含媒体时仍须目标可达并通过检查。

3. 查看预检、冻结请求与本地审核报告，由人批准这一确切文件与目标：

```bash
python -m workflow.runner mark --batch-dir workflows/<批次> \
  --stage upload_gate --status approved --actor <实际操作标识>
```

标识只用于本次上传授权，不为日常一审/二审/抽审强加人工姓名或签名。机器闸门仍需实际执行，不能手工标记通过。

4. 提交已批准的字节：

```bash
# 只核对本地冻结与批准绑定，不提交、不承诺远端此刻可达
python -m workflow.runner upload --batch-dir workflows/<批次> --dry-run

# 明确写入上传暂存
python -m workflow.runner upload --batch-dir workflows/<批次> --yes
```

上传时重新检查最终文件、冻结计划、目标、远端目录、媒体、结构证据和审核闸门，再把已批准的冻结字节原样 POST。自动推进不会自行执行提交，Agent 不能代签人工上传批准。

## 产物与防重复

| 产物 | 用途 |
| --- | --- |
| `07_upload/preflight.json` | 本地到 API 的只读校验结果、缺项、摘要和警告 |
| `07_upload/plan.json` | 源文件摘要、冻结请求摘要、目录摘要、目标、映射选项与媒体检查 |
| `07_upload/frozen/<sha256>/payload.json` | 实际发送的冻结字节 |
| `07_upload/frozen/<sha256>/catalog.json` | 准备时的目录快照 |
| `07_upload/attempts/<sha256>.json` | submitting / unknown / rejected / accepted 提交记录 |
| `07_upload/receipt.json` | 远端暂存 ID、pending_confirmation、文件摘要与提交时间 |

人工批准绑定本地最终批次、payload、plan 和目标 API。任何一个变化都需要重新检查和批准。上传过程不运行会改写批次的后处理脚本。

成功后本地状态为 staged（已暂存），不是已发布；远端编辑需人工确认。重复调用已成功且未变化的请求只返回已保存回执，不再次 POST。

网络超时、连接中断、服务端 5xx 或无法判定的成功响应可能意味着“已提交但未拿到回执”。提交前即落盘 submitting；发生不确定结果后禁止自动重发。由人工在远端工作台核实后处理本地提交记录，不调用 Harness 无权读取的上传暂存列表。当前没有自动对账或自动清理不确定记录的功能。

401 检查凭据，403 停止并确认权限，409 检查冲突，413 拆分并重新审核；不通过自动重试扩大写入范围。

## 验证与当前限制

```bash
python -m unittest workflow.test_upload_client workflow.test_upload_payload workflow.test_upload_content workflow.test_upload
```

测试使用隔离本地模拟服务器及合成批次，核对实际 HTTP 路由、Bearer、请求字节、精确大小边界、禁止重定向、错误分类、目录匹配、冻结及批准摘要、防重复与暂存回执。模拟测试不能证明远端密钥有效、SSH 已建立或真实试卷已获授权；这些需在使用者的受控本机另行验证。

整卷 `paperId`、题序、无印刷题号作文的 null 范围和固定选项列数仍需独立原卷清单。API 上传暂存并不自动建立整卷档案；真实题目尚未经远端人工确认时，不凭暂存 ID 猜造题目数据库 ID，也不擅自创建试卷档案。

## 本地装配与线上格式的对应（2026-11-06）

装配阶段在全部校验通过后，会对每道题做一次确定性规范化，保证 `06_batch/batch.json` / `batch_meta.json` 本身就是题库线上格式，而不是等到上传时才临时转换：

- 来源字段 `source`、`source_type`、`year` 提升到题目顶层；缺失时用已识别的卷面元信息补齐，不猜测。
- `year` 统一为整数，`filterMetadata.year` 同步，避免上传映射报 `ALIAS_CONFLICT`。
- `options` 统一为字符串数组（如 `"A. go"`）；输入可以是字符串、`{label,text}` 或 `{key,text}`。
- `filterMetadata.categories` 只保留固定目录值（如 `优选题`、`易错题`），丢弃 `未提供` 等本地占位。
- `materialBlocks` 仅保留 `type`、`variant`、`rows`、`cells` 的 `text/items/rowspan/colspan/header`，剔除 `column` 等本地字段。
- 正文、来源行、选项、解析里的 `[IMG name]` 按媒体映射转成 `attachments`（选项图带 `optionKey`），并从文本移除标记；未映射的标记继续阻断。
- 听力单元按 `02_audio/audio_map.json` 关联对应切分音频。
- 题组缺少 `material` 时，用已确认结构中的原文补回（去掉与 `stem` 重复的指令行）。

媒体映射文件（可选）放在 `06_batch/media-map.json` 或 `07_upload/media-map.json`：

```json
{"image3.jpeg": "/source-media/<批次目录>/image3.jpeg", "seg-6-7.mp3": "/source-media/<批次目录>/seg-6-7.mp3"}
```

也接受 `{"images": {...}, "audio": {...}}` 包装。没有映射文件时，按 `/source-media/<批次目录>/<文件名>` 生成候选映射；**生成 URL 不等于已部署**，提交前仍会逐个 HEAD 校验，未部署就不放行。

`location`、`version`、`authorization` 缺失时只报警告：服务端对这三项有默认值（空串/待核实），不再因为本地未填写而阻断上传；`source`、`source_type`、年份、题型、知识点、媒体和音频仍是硬性检查。
