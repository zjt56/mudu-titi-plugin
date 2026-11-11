# 2026-11-11 课时提示标记完整性与来源归属

## 背景

用户要求对照私有仓库 `mudujiaoyu/Mudu-English-Upload` main（技能 v0.3.37）的课时补充约束，并明确授权最小实现 D27 标记保留检查。已读取 `references/workbook-recipes.md`、`references/layout-matrix.md`、`references/constraints.md`、`references/codex-workflow.md`。在已有 `0b42780` 课时提取能力上发现：上传前脚本将全批任意同年份/地区 token 当作标记已保留；结构审计把 `source_section.title` 元数据算作交付文本，且遗漏正式 `subquestions`。

## 改了什么

- `app/workflow/sync_practice.py`：`source_marks` 增补无年份的传统文化、安徽人文、榜样人物、经典谚语、熟词生义等，排除答案解析及 IMG/TABLE 标签。完整括号内容比较，不能用年份或地区片段代替。
- `audit_source_marks` 仅对可靠的同步 `scoped-v1` 来源检查本单元/本子题归属；核实真实大题标题坐标与已提供终点/上下文/出现序次、题号身份及来源窗口；显式窗口仅认可 `ai_classified/cleaned_source`，过期或不可信证据走保守检查。成品未带 `source_span` 时只从同一大题唯一题号安全重建；重复题号及多材料组题窗口不唯一时保守处理，不猜。
- 实际交付字段覆盖题干、材料、选项、`children/subquestions` 和表格文字；来源元数据、答案解析不算保留。大题标记检查实际作答指令；完形/填空原材料中的标记保留原位。
- `app/scripts/06-upload/audit_source_marks.py` 复用 `audit_batch_source_marks`；旧协议/无可靠 scope 只检查全批完整标记存在性。
- 同步 `prompt_block`、`app/docs/sync-practice.md`，新增 `app/workflow/test_sync_source_marks.py` 16 个回归。

## 边界

不改题干、材料、题型、组单、审核差异或批次状态；结构 `sync_marks` 仍仅留痕。不恢复教材匹配，不生成答案/解析/范文，不猜 PUA，不引入远端人工停步或线上写入条款。没有模型调用、真实批次写入、上传、发布或提交。其它代理负责的 metadata 文件未修改。

## 验证

`PYTHONPATH=app PYTHONUTF8=1 python -m unittest workflow.test_sync_source_marks workflow.test_sync_lesson_extraction workflow.test_sync_practice workflow.test_structure_gate workflow.test_question_draft`

共 **90 项通过**；覆盖同年异标、错题位置、仅元数据、正式子题与选项、大题公共标记、材料内标记、表格文字、同行题号、普通试卷/无 scope 保守兼容及脚本只读。第一次系统默认 GBK 运行时，5 个既存测试的未指定编码 `read_text()` 报 `UnicodeDecodeError`；UTF-8 模式复跑全部通过。

## 回退

按文件恢复本次 `sync_practice.py`、`audit_source_marks.py` 与文档变更，并删除新增测试；无需任何真实批次迁移或数据恢复。根 `AGENTS.md` 与 `CHANGELOG/RULES.md` 由主线程同步。
