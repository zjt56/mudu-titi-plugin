# 2026-11-11：单课时混合编号答案的完整来源绑定

## 背景与触发

批次 `workflows/02七上人教版课时A计划StarterUnit1Hello!第二课时Pronun` 在答案阶段拒收已有候选，报 `scoped answer source is not uniquely bound`。原附录确有答案：Ⅰ音素归类跨两行、Ⅱ字母歌整串、Ⅲ图片填词整串、Ⅳ区间选择答案，以及「能力提升」后的无 Roman 阅读答案。第一轮两份候选已覆盖17个 question ID；正式产物缺失不等于模型未产出。

原实现只认可 Roman 后紧跟数字，Ⅱ.C;G;…被误作Ⅰ的异常续文；正文末尾「阅读理解」无 Roman，又使整课时骨架提前拒绝。Ⅲ的内部别名1应代表整节，不能只抄第1项VR。原候选引用也未给可解析行号，须从真实来源独立恢复。

## 改了什么

- `app/workflow/upload_content.py`：答案专用解析保留每块的 Roman、板块、逐题值、完整原文字串及实际行号；识别分号分隔单个英文字母的无号整节答案。未知续文、错标题、图片答案、重复号、坏区间不获豁免。
- 完整单课时 Roman 顺序仍须逐项一致；仅对唯一末尾无 Roman 大题开放剩余映射：恰好一个真实课时标记、唯一且有序的「能力提升」、唯一无 Roman 尾答案块，前面所有印刷号完整覆盖，尾部每个号码与正文全部锚点逐项一致且无重复。原图横幅不冒充 OCR 文字证据。
- `unnumbered_section + group + numbers=[1]` 且整节无印刷号时聚合自己大题完整答案，保留内部编号和所有实际行；截断、只取首项、standalone 误用别名及不完整引用均拒收。
- 缺失检查保留未绑定 literal 原答案证据，不允许把真实存在但未绑定的内容记作缺失。
- `app/workflow/test_scoped_lesson_mixed_answers.py` 新增20项临时夹具回归；`app/docs/sync-practice.md` 记录适用条件。

## 边界

不改正文/附录/确认结构/原印刷题号，不跨大题或材料按同号借答案，不生成答案，不改变一般 `scoped_source_matches`、解析/source_ref、作文例外、Word优先及副本不竞争口径。交付仍经通用任务导出/导入、当前输入与消费者校验；不直接改 manifest/binding/receipt，不自动上传发布，不裁决审核差异。

## 验证

- 新增混合课时模块最初20/20通过，独立审查再补3项最小反例：损坏PDF兜底不能误伤Word、纯无号字母答案不能伪记缺失、全Roman的整节别名仍须真实课时；最终相关模块共225/225通过（含协议/局部合并/消费校验）。
- 既有 scoped答案、来源身份、完整直抄及Roman附录模块：58/58通过。
- 真实批次只读复验：17个第一轮原候选值逐字保持，独立恢复真实附录L3、L4、L5、L6、L7、L9引用，完整 `_collect -> _validate` 与9单元 scoped merge 通过。Ⅲ保留 `1.VR … 6.CHINA`，未生成子题号。
- 全量首轮2570项：18失败、7错误、19跳过；其中多数是环境开关禁止metadata识别及macOS `/var` 临时路径软链接。规范 `TMPDIR=/private/tmp/mudu-scoped-test-tmp`、不禁用metadata单测入口、`MUDU_AGENT_CONFIG=/dev/null` 离线配置重跑2572项：6失败、1错误、19跳过；6个非通过方法（audio_skip两subtest计2失败、review缓存、作文编号、历史审核状态、Windows junction、旧CHANGELOG断言）在内存加载未修改的HEAD版upload_content后全部原样复现，未放宽或顺带修无关口径。后续继发装配修复的最终总回归另记。
- 受控真实交付：持有 `supervisor.advance_claim`，重新导出答案任务 `0c1fe0af-5fde-4512-b781-a140545582e2`，确认受影响scope为原9单元，用原第一轮17个答案值加独立恢复引用交回。正常 `agent_protocol.import_result` 返回 `completed=true`，正式answers17个值与原候选逐字相同，原文/附录/确认结构/types/knowledge摘要不变，`dependency_satisfied(answers)=true`。没有写完成态、binding或receipt。
- 已经原执行队列续跑，答案同步到batch各正确question ID。继发assemble暴露A8/A9原生无号题组及D11格式误判，另登记问题并修复，见 `2026-11-11-native-unnumbered-assembly.md`；本记录不把答案交付等同整批审核通过。

## 回退

仅回退本次 `upload_content.py` 的答案专用解析与验证改动及新增测试/文档，不回写批次状态或覆盖候选；旧实现将继续如实拒收此混合写法。运行时回退后仍由正常任务绑定与消费者校验决定交付有效性。
