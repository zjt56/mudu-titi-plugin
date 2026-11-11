# 2026-11-11 — 课时语音组题原生来源与 A8/A9

## 背景/触发与证据

批次 `01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio` 的三组语音题在 assemble 预检被 A8/A9 拦停。只读核实：`sec-dc503c37f26d6384-u7` / `sec-e6fd8b30d038e76d-u10` 的 image2.jpeg / image3.jpeg 本来已保存在 `placement=question` 图片附件中；空 material 并非丢图。真正遗漏是三组公共作答导语。`sec-a62dcefde4641bd9-u73` 的八个字母判断子题无共享材料属原卷形态。不能改 group 或编造文字绕过。

## 改了什么

- `shared_rubric.py` / `question_draft.py`：从当前 source_section 坐标逐字恢复语音组作答导语；独立 heading_candidates 核标题、实际节末与完整窗口/题号覆盖；输出本地 source_span。
- `phonetic_structure.py`：当前来源复建证明，只认可 scoped-v1 语音整节组；比对组/child/身份/窗口/原生图片，检查媒体真实非空、归属唯一和本批 URL。纯图无印刷号的单个内部别名可无 child stem，答案仍要求；其他组照旧严格。
- `question_content.py` / `preview.py`：审计与预览共用导语归属；最终 restore_source_stems 使用 scoped 并排小题解析，避免右侧小题被左侧整行覆盖。
- `batch_fill.py`：刷新来源时保留 source_span，未变来源也同步语音坐标；不更改既有 BODY/source_token 哈希协议，保留已验答案与知识点。
- `assemble_precheck.py` / `agent_tasks.py`：临时候选预检显式传入调用方批次来源路径，不读取候选自报路径。
- `check_structure.py` / `publish_check.py` / `upload_payload.py` / `format_gate.py` / `upload.py`：共享窄形态证明；无上下文裸 payload 严格拒绝；本地 source_span/printed_numbers 明确排除出远端字段。证明 helper 纳入 draft/format 规则摘要。
- 文档 `app/docs/sync-lesson-types.md` 与回归 `test_phonetic_structure.py` 同步。

## 验证

- UTF8 定向：`workflow.test_phonetic_structure test_question_draft test_question_content test_upload_payload test_format_gate test_upload test_assemble_precheck test_batch_fill test_agent_tasks` 共 **271 tests，OK，skipped 4**（15.451s）。新增语音测试 13 项，含跨节、漏题、伪窗口、旧字符串原号兼容、缺图/空图/异批URL、临时预检和 source_slice→draft→assembly→payload/source_blocks。
- 最后本地字段映射补齐后重跑 `test_phonetic_structure test_upload_payload`：**80 tests，OK**（2.000s），明确验证证明字段不进远端 payload；`git diff --check` 通过。
- 外部副本 `%TEMP%/phonetic-a8a9-verified-20261111/<同批次名>`：通过正常 `assemble_local.try_assemble`→`agent_tasks.accept_result` 全批来源/标签/预检/规范化链路，退出 **0**，`completed=true`，`model_calls=0`；产物 **27 单元、41 非空答案、27 知识点单元、textbook 0，source_audit issues=[]**。
- 副本三组导语、image2/image3 原生附件、Boys/Girls 表格和八字母子题在 source_slice/source_blocks 与 payload 内容均保留，payload 无 GROUP_INCOMPLETE/MISSING_STEM。未把远端目录、批次ID、难度/来源元数据尚未准备等其他错误宣称通过。
- 正常 runner 首次因 preassemble 交付摘要旧而等待；正常交付协调登记过期后，主线程通过 protocol 导出/导入任务 `f0cc842f-a6a7-4950-a5d2-3e1ba80a7a44` 复验既有 types，语义逐项不变、五项上游 handoff 全通过、model_calls=0；没有伪改 manifest。随后真实批次正常 runner 于 2026-11-11 16:44 完成确定性 assemble、meta、underlines，**validate 19/19 通过，阻断/提示/跳过均为 0**，进入两轮审核。
- 真实答案再次逐题绑定原卷 **41/41、missing 0**，与确定性原文直抄投影一致；前两组 HTTP detail 保持 group/语音题与子类数组，两图媒体端点分别 **200 image/jpeg，29747 / 28846 bytes**。

## 隔离全量与最终补验

- 16:34 冻结快照全 workflow：**2462 executions，2359 passed / 20 failed methods / 48 errored methods / 35 skipped**；native unittest 为 21 failure events / 60 error events（含额外 subtest 事件），未宣称全绿。原快照捕获预检接线 `root` 未定义，影响 10 个方法；生产已修为 `batch`，保留原报告。
- 最终四文件同步后的独立外部副本，`agent_tasks / batch_fill / assemble_local / preassembly / progressive_pipeline / phonetic_structure` **148/148 passed，0 failures/errors/skips**（27.435s）。此补验不覆盖或替换原全量记录；最后 LOCAL_FIELDS 两字段另有上述 80 tests 通过。
- 其余全量失败包括隔离禁模型导致 metadata 预期不满足、空 profile、Windows 缺少 POSIX 目录句柄或 sh，以及尚未对照归因的并行/审核/发布测试。完整精确 ID/trace：外部 `C:\Users\Administrator\AppData\Local\Temp\tiku-phonetic-regression-af021dd81d654161955ec8c55f4bfc65\FINAL-NOTES.md`、`REGRESSION-REPORT.md` 和 `logs/failing-methods.txt`。不将这些结果描述为已证明的旧基线。

## 边界与回退

本子任务只读真实批次，写入仅代码/测试/文档及外部副本；不调用模型，不上传、不提交，不改 runner/optional_inputs/教材或配置。普通 group、缺失答案、来源内容缺损、未知字段仍严格。回退本记录涉及文件中的对应局部修改；保留原始候选与真实批次原数据。主线程负责全量验证、规则台账与卡点台账汇总。
