# 2026-11-09 2016 卷反复卡点根因修复（不重跑、不伪造、不保留删除痕迹）

## 背景 / 触发

对【木牍教育】16年安徽中考英语及其 `-2` 复跑批次做只读排查，发现同一卷在 explanations 与 review 阶段反复卡住：

- explanations 连续 3 轮修复仍失败（失败单元 22→18→13），最后靠确定性直抄完成；
- review 在 20:36:59–20:46:20 共执行 6 次，self-heal 记录 5 次要求重跑，50 个 review attempt 目录，9 条 review-failures；
- `01_source/正文.txt`、`source.docx`、`audio.mp3` 与原批次完全相同，说明 `-2` 是同卷重复上传；
- 用户要求彻底修复并保证后续不再出现；删除批次事件时必须彻底删除，不留本地痕迹。

## 改了什么

1. **`app/workflow/review.py`：审核证据按实际绑定来源校验**
   - 模型常把附录答案/听力转写 quote 误标为 `01_source/正文.txt`，而旧校验对每条 evidence 都要求 path/quote 同时匹配，导致主证据有效也整题失败（扫描 508 个 target checks，68 个是“有效主证据 + 一条错误附加证据”）。
   - 现在逐条 evidence 先在声明的绑定文件校验；引用已绑定但误标的路径时，在 `plan.input_hashes` 绑定来源中按逐字/规范化 quote 查找并纠正 path。未绑定路径、格式错误或找不到原文的证据仍拒绝并局部补审，不能丢弃无效证据来放行。正文主证据仍必须至少一条有效。
2. **`app/workflow/review_rounds.py`：sample 提示词补齐 `source_comparison`**
   - `check_shape` 与 JSON schema/校验器保持一致，明确要求二审 assessment 内包含 `source_comparison:string`，避免整组 10 个 sample 目标因模型漏字段一起失败；提示词强调题型知识点维度，并提升 `PROMPT_VERSION`，使旧分片任务不会继续复用缺字段提示词。
3. **`app/workflow/content_fragments.py`：source_ref 容错展开**
   - `end_column` 超出实际末行时按末行末尾截断（模型常按中文显示宽度计算列）；
   - 展开文本开头若为 `【解析】`/`【答案】`/`【文章大意】` 字段标题则按字段语义去掉；题号/答案前缀仅在紧跟明确解析标题时移除，小数及普通编号正文保持原样；
   - 行号越界、起点越界、反向/空片段仍拒绝。
4. **`app/workflow/self_heal.py` + `auto.py`：record_only 不再无限重跑**
   - `record_only` 达到 `MAX_ATTEMPTS_PER_STAGE` 后返回 `retry_budget_exhausted`，拒绝再次原样入队；保留原失败检查，不把自愈说明改写成新的卡点。
   - 重试预算绑定实际输入、原件及代码；模型理由、时间戳不能重置预算。上游升级也按真实错误去重，避免模型换一种说法就重复回退。
   - `auto` 保留有限冷却并自动重新诊断，要求实际修复或隔离代码修复策略；真实输入或代码改变会开启新预算。
5. **`app/workflow/diagnose.py`：审核卡点读取结构化失败**
   - review 的 `diagnose` 优先读取 `workflow/review-failures/*.json` 的真实 error，不再只从 `review.log` 尾部取到 `[stderr]`。
6. **`app/workflow/manifest.py`：允许已执行审核的本地格式检查继续推进**
   - `dependency_satisfied(..., allow_unqualified=True)` 的上游检查接受 `review_gate` 的 `needs_review + execution_completed + continue_unqualified + release_ready=false`，避免 format_gate/upload_gate 已满足却仍被永久等待。
7. **`app/workbench/server.py`：同内容重复上传复用已有批次**
   - 上传前按 source/answer/audio/附件实际字节 SHA256 与文件类型匹配已有批次；重读原件防止陈旧 manifest 摘要误判，附件顺序不影响去重，损坏台账跳过。相同内容返回 `duplicate:true`，不再生成 `-2`/`-3` 副本与第二套审核结果。
8. **彻底删除批次**
   - 新增 `purge_batch()` 和 `POST /api/batches/{batch}/purge`：永久删除 `workflows/<batch>`、`inbox/<batch>`、数据根及 workflows/inbox 下同批次 `.workbench-trash`/`.trash`/`archives`/`archive` 的具名或回执归档；清除该批次音频状态、内存调度队列、上传记录及内容缓存。
   - 删除先获取推进/运维锁并预检全部目标；活跃推进、音频、上传或运维期间拒绝删除。队列取出与运行登记共用写锁，防止删后重新创建。归档路径严格按批次身份匹配，拒绝软链接和越界路径。
   - 工作台“删除批次”改为输入完整批次名后调用 purge，不再默认移到可恢复归档；
   - `archive_batch()` 保留给旧 API，可恢复归档不冒充删除。

## 没改什么 / 边界

- 不改变审核口径、人工审核范围、上传/发布边界；review_gate 的真实差异仍进人工队列，`release_ready` 仍不能自动为真。
- 不自动删除任何用户批次；purge 只在用户在界面明确输入批次名确认后执行。
- 不改变 source_ref 的权限路径、SHA256、行号要求；只对显示宽度造成的末列越界做确定性的行末截断。
- `record_only` 达到重试预算后保留原失败状态与证据，继续自动诊断但拒绝同策略重跑；不伪造完成。
- 不固定模型；同内容重复上传复用按内容哈希判定。

## 如何验证

- 使用本机 Python 3.12 执行全量 `PYTHONPATH=. python3.12 -m unittest discover -s workflow -t .`：1901 项，1882 通过、17 跳过、2 项受环境限制报错。两项均为 `test_local_agent_ownership` 调用 `ps` 被当前沙箱拒绝（PermissionError），无断言失败；不得宣称全量全部通过。默认 `python3` 是 3.9，曾导致现有运维测试 `write_text(newline=...)` 不兼容，因此改用 3.12 复验。
- 自愈、交付和审核证据专项 182 项通过；提示词版本提升后 `test_review_rounds`、`test_review_rounds_recovery`、`test_review_evidence_scope` 共 107 项再次通过。
- 原文引用、教材任务及 supervisor 专项共 58 项通过，包含普通编号/小数不可被误删的回归。
- 删除实现最后补齐缓存/音频状态与损坏台账处理后，工作台删除、上传、队列、supervisor、列表和媒体相关 94 项回归：90 通过、4 跳过；另独立重跑删除/去重/列表 50 项全部通过。测试锁隔离在临时目录，避免与其他回归或运行插件共享全局模型锁。
- 修正过期测试夹具：共享导语夹具须包含后续题组的已验证公共导语；OCR 只由布局消费者携带；worker 捕获异常后仍释放执行锁。未放宽生产校验。
- `node --test backend/*.test.cjs`：209 项，207 通过、2 跳过、0 失败；更新了图片缩略尺寸和“彻底删除”菜单的过期预期。
- `node --check lib/client.js`、`lib/index.js`、`backend/wf-router.mjs` 及 `git diff --check` 通过。
- 验证只操作临时测试批次，未对真实 2016 批次进行删除或重跑；未安装或重启当前运行插件，运行中页面尚未验证新版本。

## 回退

恢复对应 `review.py` / `review_rounds.py` / `content_fragments.py` / `self_heal.py` / `auto.py` / `diagnose.py` / `manifest.py` / `server.py` / `lib/client.js` 改动即可；已删除批次的 purge 不可回退（用户明确要求彻底删除）。
