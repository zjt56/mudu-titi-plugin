# 无题号文字题与栏目边界的通用修复

## 背景

用户反馈批次 `27七上人教版课时A计划Unit3MySchool第一课时SectionA(1a` 的“写出下面画线词的汉语意思”报 `QUESTION_BOUNDARY_MISSING` / `SOURCE_SECTION_UNCOVERED`，随后要求后续无题号、其他栏目板块均按通用规则处理。原卷有完整 Lang Lang / field 词义题及作答空位；“熟词生义”为图片标题，不是缺题。

## 实现

- `app/workflow/question_content.py`：`unnumbered_text_responses` 从当前来源段落重建完整作答窗口。单段真实正文加明确作答空位可证明一题；连续各有末尾作答区域的行分别建题；自然换行仅在唯一末尾作答区域及连续正文可证明时合窗。图片、表格、选项混入、多个歧义空位及缺正文不走该路径。
- `app/workflow/structure_gate.py`：确定性生成各窗口独立来源身份；要求所有预期窗口恰有一个完整来源单元，拒绝只交第一题、跨题合并或改写边界。编号仍为内部兼容别名，`printed_numbers=[]`，不生成原卷没有的印刷题号。
- `app/workflow/structure_ai.py`：候选须与当前来源重建的完整窗口相等，提示词同步通用无号规则；已有整卷覆盖检查保留。
- `app/workflow/lesson_bands.py`：证据版本升至 `lesson-bands-v3`。视觉识别完整可见栏目标题，不限“熟词生义”；原生文字只认明确包装或既有栏目标记。栏目 `features` 与 A/B `bands` 分开：裁去前题误含的横幅，不改变答案 A/B 归属。原件、正文、图片字节及实际模型路由绑定、批量缓存、失效重建均保留。
- 更新根 `AGENTS.md` 规则9、`CHANGELOG/RULES.md` 和 `app/docs/sync-practice.md`；新增连续无号、自然换行、陌生栏目、漏覆盖与消费者链路回归。

## 验证

- 10 个受影响测试模块：172 项，168 通过、4 跳过、0 失败（2.605秒）。覆盖 `lesson_bands`、`structure_ai`、`structure_source_shape_regressions`、`structure_gate`、`native_unnumbered_structure`、`question_identity_structure`、`question_content`、`question_draft`、`structure_closure`、`upload_numbering`。
- 全量 workflow 测试已启动，但夹具 `agent protocol .../original batch` 意外进入真实 `wf-router.mjs` 模型调用并停滞，已停止且确认无残留子进程；日志在停止前出现失败标记，未完成汇总，**不宣称全量通过**。没有为本次修改放宽这些测试或模型校验。
- 真实原件隔离副本以当前实际模型 `opencode-go / deepseek-v4.1-flash` 批量视觉一次识别，验证 `image86.jpeg` 完整标题“熟词生义”，SHA-256 `7392c5e747faf00fd291dd3ede3ba6d2c35914487daa4906cfe717080bba5390`。15 单元结构、草稿、原文审计、预览与上传题号投影均通过。
- 在原批次安全检查点（执行锁及运维租约检查通过）核对原件/正文/全部图片摘要和当前路由，备份旧派生产物后原子安装已验证视觉证据，经正式 `runner.do_structure_gate` 发布：15 单元通过，无阻断；熟词生义栏目为44–46行，前对话来源窗29–43行，不再误含横幅。随后只读重建草稿无问题、原文审计通过、题号投影无错误，无号题范围为 `null/null`。
- 卡点台账已通过 `workflow.issue_log fix` 留档；没有手改 manifest 伪造状态。

## 边界与回退

不生成题干、答案、解析或印刷题号；栏目名不决定题型、题数或答案。边界歧义、内容截断、漏题及真实来源冲突继续阻断。上传、审核差异及教材暂停规则不变；没有自动上传，也未重跑其他批次。

原批次旧派生产物备份位于该批次 `workflow/repairs/2026-11-13-generic-unnumbered`。回退须在安全检查点恢复对应视觉证据并经正式结构闸门重新验收，不直接恢复/伪改 manifest 完成态。代码回退仅限本记录列出的规则变更，不能覆盖并行工作区修改。
