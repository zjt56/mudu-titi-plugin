# 2026-11-11：合法无号字母歌/图片填词的装配与消费校验

## 背景

批次 `workflows/02七上人教版课时A计划StarterUnit1Hello!第二课时Pronun` 的17个答案经正常协议导入后，续跑在 assemble 暴露下一层误判：Ⅱ字母歌共享正文完整但内部兼容子题 stem 为空；Ⅲ填词游戏只有原生图片附件，目录为单词拼写，装配报A8/A9；Ⅳ第4题原卷是问句没有下划线，被D11空档格式阻断。这不是答案缺失，不能改造题干或补造空档来凑格式。

## 改了什么

- `app/workflow/shared_rubric.py`：历史共用入口 `phonetic_section_rubric` 除原有语音题外，对统一 `legitimate_source_shape` 判定的整节无号 group 绑定当前大题原导语。要求 scoped 身份、完整来源窗口、真实标题、当前规则重建的大题、类型/组单一致，且不能遗漏实质源行。图片填词的原始作答指令进入正常草稿和原文审计共用链路。
- `app/workflow/phonetic_structure.py`：当前来源证明扩展到合法无号图片/正文形态。重建草稿后仍检查完整身份、全窗、题型、材料文本、子题、真实非空且唯一归属的媒体附件。字母歌有真实 material 时允许内部别名子题没有伪造题干；图片填词以已证原生图片作为材料。没有当前来源证明、来源漂移、媒体丢失、材料改写等仍拒绝。
- `app/scripts/06-upload/audit_underlines.py`：原卷题干未印下划线记 `[W11]`，由现有 `_common.report` 返回提示。真正空答案和只有分号的答案仍为 `[D11]` 硬失败；组题检查各子题，不要求另造聚合答案。没有忽略预检脚本返回码，也没有全局豁免A8/A9。
- 新增聚焦回归验证真实装配预检、来源草稿、预览、工作台原文块与上传payload共同保留正文/图片/指令；最终数字完成后补记。

## 边界

不改真实原件、正文、确认结构、题型标签、原号或答案；不从图片推造文字，不把内部别名伪装原卷编号，不扩大普通空题组豁免。已有填充由正常 `batch_fill.initialize` 按相同原文/媒体/身份保留；不直接编辑正式产物或manifest。审核差异保持，上传/发布仍须用户授权。

## 验证

- 既有原生语音形态/题源草稿/完整性：55个测试，51通过、4跳过。
- D11/W11真实预检专项：8/8通过。
- 新增全消费链专项：9/9通过。最终答案来源/协议/局部合并/草稿/原生形态/装配格式相关合跑297个测试：293通过、4跳过（11.132秒）。独立只读审查通过本次来源与格式变更，未发现未关闭中高问题。
- 最终全量 workflow：2590个测试（88.775秒），6失败、1错误、19跳过；与答案修复后规范环境整套相同的既有失败集合（audio_skip两subtest、review_read_scope、作文编号、workbench历史审核、旧CHANGELOG校验、macOS误跑Windows junction），未新增失败。先前用内存加载原HEAD模块复现同一组6失败/1错误，不改无关逻辑来凑全绿。
- 受控真实批次：持 `supervisor.advance_claim`，正常 `assemble_local.build -> agent_tasks.accept_result(validate_only=True)` 校验通过；正常 `workflow.agent run --stage assemble` 确定性导入成功（0模型调用）。来源刷新导致旧前置交付字节指纹失效时，停止无必要模型重跑，导出新的 preassemble 任务 `3b6b6c68-6771-464a-a9c2-8e88c5058005`，逐项复验原已接受 types 并经正常协议交付，题型payload完全不变。
- 正常本地推进执行meta、underlines、validate，`validation-report.json`：19检查、passed=true、blocking_failures=[]、warnings=[]、skipped=[]；当前validate消费者通过。17个原答案在 `batch.json` / `batch_meta.base.json` / `batch_meta.json` 中逐question ID均与已接受原候选完全相等。
- 数据及展示链：Ⅱ保留完整147字字母歌正文；Ⅲ原第58行导语逐字保留、image9.jpeg原生question附件保留；source_slice、source_blocks、upload_payload三层均保留正文/导语/图片，GROUP_INCOMPLETE/MISSING_STEM为空。没有实际远端上传。
- 后续内容审核真实两轮均已执行。labels原响应的notes/findings误嵌入artifacts，且图片题引文漏「中」字；仅移动现有同名元数据至正确层并逐字恢复原第58行引文，9条verdict/assessment/rationale完全不变，原任务绑定仍当前，通用review importer复验接受。`execution_completed=true`、`checked_rounds=[labels,sample]`、unchecked为空；正常review_gate保留3项open差异（另5项历史fixed结构条目仍保留），quality_passed=false。正常format_gate报告passed=true/errors=[]，报告当前绑定与消费者均通过；批次pending_confirmation，未上传发布、无上传receipt。

## 回退

回退本次共享原导语/当前来源形态与W11代码、专项测试后重启本地后端；由正常阶段消费者重新判断，不手工恢复完成态，不覆盖现有答案候选与已接受标签。
