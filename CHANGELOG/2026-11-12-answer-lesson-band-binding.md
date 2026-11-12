# 2026-11-12 答案按课时板块隔离并保留紧凑答案完整坐标

## 背景

用户授权课时A计划 Word 视觉辅助结构与答案匹配。答案侧原有完整 Roman 序列可证明大题顺序，但没有将正文 A基础巩固/B能力提升（含已验证图片证据）与答案区板块逐项比较，可能在相反板块下借同号答案。紧凑答案的 `(n)` 延续行也只保存了首行引用。

## 改了什么

- `app/workflow/upload_content.py`：答案候选实际消费者读取 `lesson_bands.load_verified(root)` 并通过 `annotate_sections` 投影当前正文板块证据；不信任单元自行声称的旧板块。正文答案识别视图仅逐行清格式，行号与原正文一致，原件/正文/图片摘要仍由板块模块验证。
- 显式正文板块或已验证视觉板块存在时，紧凑 Roman 序列与完整标题答案两条路径均核对同一 canonical 板块；A/B 字母与文字矛盾、错板块、缺板块、跨专题同文均不作为归属证据。
- 一个课时内显式 Pronunciation 专题可以消费完整紧凑序列；附录必须逐节明确对应专题。专题边界不继承前一个 A/B 板块，多个课时与不完整来源继续拒绝猜配。
- 板块/专题感知的紧凑序列核对所有大题的真实印刷题号覆盖；重复完整同文答案只在已绑定同一 scope 内按内容等价，跨 scope 不借用。既有无板块旧协议与历史批次保持原入口。
- 紧凑答案 `(n)` 多行延续记录每条实际源行；即使候选把换行写成空格，校验仍要求完整来源覆盖。直接交付、普通答案校验、缺失决定均复用实际消费者。
- 新增 `app/workflow/test_answer_lesson_bands.py`：真实来源摘要/媒体摘要证据 → `answers_direct` → `validate_scoped_answer` 的只读回归、板块/专题错配拒绝、同 scope 重复、缺号与多行坐标覆盖。
- 后续集成补充：已有 `lesson-bands.json` 却原件/正文/媒体摘要过期、边界无效或 partial 带问题时，答案不得退回旧 Roman 顺序猜配；没有过该文件的旧批次仍兼容。实际消费者只采纳 annotation 的板块元数据，来源身份核验保留原始标题窗口，结构侧对横幅边界的裁剪不被移除。

## 边界

仅修改答案来源绑定及其回归。未改真实 `workflows/` 数据、manifest、路由、凭据、上传或 mark 状态；未调用模型整课重跑；未修改 `sync_practice.py`、`question_identity.py`、`visual_review.py`、`extract.py`、`structure_gate.py`。板块构建与视觉识别由主代理负责。本记录不代表真实试卷内容审核通过。

## 验证

从仓库根目录运行：

```bash
PYTHONPATH=app python3 -m unittest workflow.test_answer_lesson_bands workflow.test_scoped_lesson_answers workflow.test_scoped_lesson_mixed_answers workflow.test_answers_direct workflow.test_answer_scope_blank_evidence workflow.test_question_identity_answers workflow.test_upload_content_equivalent_anchors workflow.test_question_identity_content workflow.test_roman_answer_appendix workflow.test_table_spans_and_pdf_answers
```

初次结果：**132 tests / 4.574s / OK**。补充真实 stale/partial 证据回归后，连同 `workflow.test_extract_sync_dialogue_blanks` 再跑：**137 tests / 4.642s / OK**。`git diff --check` 和 `py_compile` 通过。

额外尝试加入 `workflow.test_source_answer_only` 的 11 模块命令在后段超过两分钟未完成，已终止；不将该未完成运行算作通过。随后上面的 10 模块独立完成。`git fetch` 因 GitHub HTTP2 framing layer 失败，本地记录 HEAD 与 origin/master 为 0/0；未提交 commit。

## 回退

只回退本次 `upload_content.py` 答案相关差异和新增答案测试即可；不要覆盖共享工作树中其它代理的改动。真实数据无迁移或回写。
