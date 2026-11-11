# 2026-11-11 — 跳过解析阶段仍须交付当前结构的缺失占位

## 背景与根因

课时批次 `01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio` 的 `explanations` 已为 `skipped`，但组题 `sec-2fd9dfe1dc666eda-u111` 当前只有子题 1–3，旧 `explanations.json` 仍保留 1–8。runner 对已有 skipped 且仍无解析来源的阶段直接跳过；skip helper 调用又被终态条件挡住，导致 assemble 的消费者校验正确拒收：`Subquestions must have exact confirmed child number coverage`。

## 改了什么

- `app/workflow/runner.py`：历史解析 skip 先用正常 `handoff.inspect_stage` 验收；不合格时通过实际 skip helper 重建当前 confirmed units 的缺失占位。已合格的 skipped 产物不重写 manifest。不能合格交付时保留既有内容并返回等待，不能将旧 skipped 当作当前交付。
- `app/workflow/optional_inputs.py`：拒绝在已识别来源存在时安装空占位，拒绝覆盖旧产物中的任何非占位解析/大意；按当前 units 构造所有组题子题，调用原有 explanations 校验器后用现有 `_install` 原子安装并备份旧产物。
- `app/workflow/test_optional_inputs.py`：新增 scoped-v1 历史组题占位 1–8→1–3、正常 handoff/dependency 验收与确定性组装、合格 skip 无写、缺 types 交接、既有真实内容与原文解析不能被空占位覆盖等回归。

## 边界

未修改教材暂停相关文件、消费者覆盖/来源/遗漏校验或上传边界。实现与回归在隔离副本完成；主线程随后通过正常 runner 续跑当前课时批次，生成当前子题覆盖的缺失占位，未直接编辑 manifest、未调用解析模型、未上传。审核差异保持不裁决。

## 验证

外部唯一 TEMP `C:\Users\Administrator\AppData\Local\Temp\mdu-skip-9d79360c5bdd41bb8f14551e8fa809a6`，`PYTHONDONTWRITEBYTECODE=1`：

`python -m unittest workflow.test_optional_inputs workflow.test_explanations_direct workflow.test_content_rules_preserve workflow.test_question_identity_content -v`

**54 tests passed**（0.517 秒；optional_inputs 11、explanations_direct 21、content_rules_preserve 3、question_identity_content 19）。最终以全局 `workflow.local_agent.invoke` 拒绝真实调用的 guard 在另一独立外部 TEMP `C:\Users\Administrator\AppData\Local\Temp\mdu-skip-final-642338df10364e1596d9b6cc2a694ee5` 重跑，同为 **54 passed**（0.537 秒）。新增路径使用 fake batch；全部测试不使用真实 batch。

额外离线调度验证 `workflow.test_runner_auto`：46 tests，40 passed / 4 failed / 2 skipped（11.332 秒），四项均为并行标签测试；以内存加载 HEAD 版本 runner/helper 重跑同四项，仍 3 failures / 1 error（10.106 秒，error 为并行读取 manifest 的 Windows PermissionError），确认旧实现同样不能通过，未扩大本次修改范围。该套件始终全局拒绝真实模型调用，使用独立外部 TEMP。

`git diff --check`（仅本次三个实现/测试文件）通过。

真实批次于 2026-11-11 16:14 正常 runner 续跑：explanations 保持 skipped，当前 `handoff.inspect_stage` 为 passed、errors=[]，组装已越过原先子题覆盖阻断。组装随后发现独立 A8/A9（语音组题漏入原卷作答导语，且结构校验未识别已保留原生附件的合法纯图形态），另建 `A8_A9_SCOPED_GROUP_CONTENT_MISSING` 台账继续修复，不将整批误报为完成。

## 回退

仅回退本次 runner、optional_inputs 与 test_optional_inputs 的局部补丁；不回退并行的教材暂停改动或任何用户批次数据。
