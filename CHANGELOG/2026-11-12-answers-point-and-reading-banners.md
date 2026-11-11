# 答案填充：紧凑 Roman 答案键含 重要考点集训/创新阅读专练 版块横幅时无法唯一绑定

## 背景 / 触发
批次 `04七上人教版课时A计划StarterUnit1Hello!第四课时Projec`（Starter Unit 1 课时4 Project）
答案填充阶段 `needs_review`，报：
`sec-b3e78c1cabae7307-u12/题1: scoped answer source is not uniquely bound; 答案来源缺少可核验的大题scope/原文坐标`
共 15 道题（Ⅰ 6 题、Ⅱ 5 题、任务型阅读 4 题）全部无法绑定。

该课时同步练习册的答案附录形态：
```
参考答案
[IMG image158.jpeg]重要考点集训[IMG image159.jpeg]
Ⅰ.1.Are;am　2.to meet　3.thanks　4.my　5.an　6.your
Ⅱ.1~5 ADCBD
[IMG image158.jpeg]创新阅读专练[IMG image159.jpeg]
1.(1)British…　(2)chopsticks…（跨 3 行，(3)~(8) 续行）
2.He is from the UK.
3.The right side.
☆拓展设问
4.A
```
根源是确定性解析器能力缺口（`failure_class=source_binding`，与模型无关）：
- `_LESSON_BAND` 只认 `基础巩固`/`能力提升`，不认 `重要考点集训`/`创新阅读专练` 版块横幅；
  横幅行被当成块内无效内容，把上一块标 `invalid`。
- 任务型阅读答案的 `(n)` 子空续行（`(5)…`/`(6)…`）与 `☆拓展设问` 子标题被误判无效，
  块被拆分，`[block.ordinal] != skeleton`，所有题候选为空。

## 改了什么
`app/workflow/upload_content.py`
- `_LESSON_BAND` 增加 `重要考点集训`、`创新阅读专练` 两种版块横幅。
- 引入 `_EXTENSION_BANDS = ('能力提升', '创新阅读专练')`，把 trailing 扩展块判定通用化：
  尾部扩展块可用 `能力提升` 或 `创新阅读专练`；前缀至多一个基础版块（`基础巩固`/`重要考点集训`）；
  重复/错位版块横幅仍拒绝（保持既有“重复基础横幅不可隐藏、尾部需唯一扩展横幅”不变量）。
- 新增 `_SUBITEM_CONT`，把 `(n)` 子空续行并入当前题 material，不当作新题/无效内容。
- 跳过 `☆拓展设问`/`拓展设问` 子标题，不切块、不污染（与 sync_practice 既有口径一致）。

`app/workflow/test_scoped_lesson_answers.py`
- 新增 `TestPointBannerAnswersTest`：复刻真实形态，断言 Ⅰ/Ⅱ/任务型阅读全部答案按
  真实坐标绑定、`answers_direct.build_payload` 产 9 答案；并断言重复 `重要考点集训`
  横幅仍 unbound。

## 没改什么 / 边界
- 未放宽任何绑定口径：仍需完整有序 Roman skeleton + 唯一 printed anchors + 真实坐标；
  重复/缺失/错位横幅、跨节借号、多值冲突仍一律 unbound。
- 只影响答案来源解析；未改审核、上传、结构、题型判定。
- 未自动上传/发布；批次 answers 经确定性直抄（`answers_direct`，不调用模型）交付。

## 如何验证
- `PYTHONPATH=. python3 -m unittest workflow.test_scoped_lesson_answers workflow.test_scoped_lesson_mixed_answers workflow.test_roman_answer_appendix workflow.test_upload_content workflow.test_source_answer_only workflow.test_question_identity_answers` → **117 tests OK**。
- 批次复跑：`answers` 阶段 `needs_review → completed`，`01_source_ans/answers.json` 含 15 答案、0 缺失，
  notes=`按当前大题身份与原文坐标直接抄录完整答案，未调用模型`。

## 回退方式
回退本提交 `1581c9e` 即恢复原解析器；不影响已交付的批次数据与历史批次。
