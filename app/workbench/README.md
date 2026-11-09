# Python REST API 后端

`server.py` 是**纯 REST API 后端**：只提供数据接口，不提供任何页面、静态文件或浏览器工作台。
唯一的前端是 DSH 插件面板 `../../lib/client.js`，它通过宿主的 `/wf-plugin/api/*` 代理调用这里的接口。
界面、交互、卷面渲染、弹窗全在插件面板实现；本目录不参与显示。

## 启动

由 DSH 宿主插件自动拉起（`lib/index.js` 的 `ensureBackend`），默认端口 8791、只绑定回环地址。
手动排查时才需要直接起进程：

```bash
python3.12 -m workbench.server --host 127.0.0.1 --port 8791
```

数据目录由 `WF_WORKFLOWS_DIR` / `WF_INBOX_DIR` 指定（宿主注入，指向插件项目根下的 `workflows/`、`inbox/`）。

## 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/health` | 探活 |
| GET | `/api/batches` | 批次列表（含统计与单元行） |
| GET | `/api/batches/{batch}/units/{unit}` | 单元详情：原卷预览块、小题、标签、待人工意见 |
| POST | `/api/batches/{batch}/units/{unit}` | 保存标签（`{labels}`）或题面草稿（`{items}`、`{unit}`） |
| GET | `/api/batches/{batch}/pipeline` | 当前推进状态与停留原因 |
| POST | `/api/batches/{batch}/advance` | 触发流程推进 |
| POST | `/api/batches/{batch}/decisions` | 人工裁决一条意见（`{item:{finding_id,status,note}}`） |
| POST | `/api/batches/{batch}/archive` | 归档批次（`{confirm_name}`，可恢复） |
| POST | `/api/batches/{batch}/purge` | **彻底删除批次**（`{confirm_name}`，永久删除批次目录、收件箱副本与同批次回收归档，无法恢复） |
| GET | `/api/batches/{batch}/media` | 图片体检结果 |
| POST | `/api/batches/{batch}/media/exclusions` | 标记非题目图 |
| GET | `/api/batches/{batch}/media/{file}` / `audio/{file}` | 二进制媒体（支持 `?format=data-url`） |
| GET | `/api/taxonomy` | 题型/子类/难度/分类目录 |
| GET | `/api/knowledge/tree` | 完整知识点树 |
| GET | `/api/knowledge?q=` | 知识点搜索 |
| POST | `/api/uploads` | multipart 上传原卷并建批次 |

除 `/api/*` 外一律 404；没有首页、没有 hash 路由、没有静态资源服务。

## 数据模型与口径

- **一个入库单元一行**：独立题一行一题；题组（完形、阅读、补全对话、信息转换、长对话/短文听力等）合并一行，带小题数与题号区间。合并依据批次里已有的 `subquestions`，不由前端推断。
- **标签是单元级**：题组整体一套，独立题一题一套；存 `candidates/units/<unit_id>.json`（`applies_to=unit`、`inherit_subquestions`）。保存前只接受目录内叶子值，服务端重新校验。
- **原卷预览块**：`preview_blocks` 是文字/图片/表格/音频的结构化数据，由插件面板负责渲染成卷面样式。
- **待人工处理**：只审标签类问题（`knowledge`、`type`、`difficulty`、`source` 等）；`fixed`/`rejected` 才算处理完。写队列只合并 `status`/`note`/`resolution_note`，不覆盖原问题的 `field`/`question`/`severity`，覆盖前留 `.bak` 并追加 `workflow/decisions.jsonl`。

## 流程推进

原卷提取是内置确定性阶段；答案、题型、知识点、组装和审核通过通用 Agent 文件任务交接。
结构闸门和审核放行是机器检查：证据齐全才通过；三审只把具体差异/冲突/不确定项送人工；缺少执行记录保留未完成。
上传确认、发布确认仍由人决定。台账、输入或产物摘要不一致时应按正式 runner/import 重新验证或重跑，不能手工回填完成状态。

## 写入边界

- 只写批次目录内的 `candidates/`、`07_review/`、`workflow/` 与最外层 `inbox/`；覆盖前留 `.bak`。
- 不向远端上传或发布；工作台删除须确认完整批次名，调用 `purge` 永久清除批次、收件副本、同批次归档和本地缓存/调度记录。旧 `archive` 接口仍只作可恢复归档。
- 删除与归档均受写锁保护；推进、上传或运维任务仍运行时拒绝删除，避免后台写入重新创建已删批次。
