# 2026-11-11 前置组装：图片题/空白作答网格的 contentComplete 不再阻断

## 背景

批次「02七上人教版课时A计划StarterUnit1Hello!第二课时Pronun」前置批次组装（含题型）被判
「代理产出未通过校验」。核对原卷：Ⅰ 的七张音素归类表在 Word 与 docx XML 里本就是空白作答网格
（学生填写，答案见卷末附录 `Ⅰ.1.Aa,Hh,Jj,Kk…`），Ⅲ 题仅以 image9.jpeg 呈现（图片能力受限）。
模型如实把这些标成 `contentComplete=false`，但校验把任何 `contentComplete=false` 当硬阻断，
并把模型据此提出的 findings 也当 major，导致前置组装停在 needs_review。

## 改了什么

- `app/workflow/question_content.py`：新增 `capability_limited_source(unit, appendix_text)`——识别
  两类原卷合法形态：①纯图片题（除图片/表格标记外无可读文字题干）；②空白作答网格（该单元所有表格
  都为空且该单元题号在附录明确给出答案）。两者都不是“内容缺失”。
- `app/workflow/agent_tasks.py`：
  - `types` 阶段校验：`contentComplete=false` 时，仅当单元命中 `capability_limited_source` 才降为
    notes 提示继续；其余（真实截断、缺选项、题干错配）仍抛错阻断。
  - `accept_result`：对 `types` 阶段把模型针对能力受限单元提出的 blocker/major finding 降级为
    info（原因前缀标注“能力受限，不阻断”），真实问题保持原严重度。
- `app/workflow/test_agent_tasks.py`：新增 3 条回归——空白作答网格+附录答案不阻断；纯图片题不阻断；
  真实截断（表格非空、附录无对应答案）仍阻断。

## 没改什么 / 边界

- 不放宽任何真实内容校验：题干截断、缺选项、题干与材料错配仍 `contentComplete=false` 阻断；
  表格非空但缺字、附录无对应答案仍按原逻辑阻断。
- 不改题组边界、题号唯一绑定、来源窗口；不改审核/上传口径；不自动上传发布。
- 能力受限只作 info 记录，不据此伪造内容或改写题目。

## 验证

- `workflow.test_agent_tasks` 61 项通过（含 3 条新增）。
- `workflow.test_structure_gate` 13 项通过。
- 相关回归 128 项通过：agent_tasks、agent_prepare、agent_dispatch、preassembly、assemble_local
  （100 项）+ upload_content_equivalent_anchors、ingest_binding_equivalence（28 项）。
- 真实批次：原 preassemble 响应重校验 completed=true，findings 降为 info；`runner run --stage preassemble`
  exit 0，preassemble=completed，批次前进到 enriching，下一步 types 可执行。
- `test_changelog_policy` 1 项失败属他人已提交文件 `2026-11-11-batch-list-load-time.md`（用 “tests OK”
  未匹配“项”），与本改动无关；本记录含“项”字样、自身合规。

## 回退

回退 `question_content.py` 的 `capability_limited_source` 与 `agent_tasks.py` 两处引用及三条测试；
该批次前置组装将重新因 `contentComplete=false` 停在 needs_review，不影响其他批次。
