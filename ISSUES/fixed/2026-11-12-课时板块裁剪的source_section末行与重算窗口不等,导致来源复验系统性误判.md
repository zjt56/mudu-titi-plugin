# 卡点：课时板块裁剪的source_section末行与重算窗口不等，导致来源复验系统性误判

- id: 2026-11-12-课时板块裁剪的source_section末行与重算窗口不等,导致来源复验系统性误判
- status: fixed
- batch: 07七上人教版课时A计划StarterUnit2KeepTidy!第三课时Sec
- batch_dir: C:\Users\Administrator\.dsh\wf-plugin-data\workflows\07七上人教版课时A计划StarterUnit2KeepTidy!第三课时Sec
- stage: review_gate
- check: REVIEW_SAMPLE_SOURCE_UNVERIFIED
- created: 2026-11-12T19:22:20+08:00
- last_seen: 2026-11-12T19:22:20+08:00
- occurrences: 1
- fixed_at: 2026-11-12T19:23:01+08:00
- fix_commit: d9fe33d
- verification: workflow.test_review_child_source/test_review_local_reuse/test_review_rounds 等 157 项 OK；批次 05/07 失败单元 5/24→0/24、6/26→0/26；批次 07 review_gate 复跑 required_unchecked=0
- title: 课时板块裁剪的source_section末行与重算窗口不等，导致来源复验系统性误判

## 现象（卡点）

review-gate.json errors: REVIEW_SAMPLE_SOURCE_UNVERIFIED [sec-1de4ef313aac00fb-u58]/[u52]：scoped 来源窗口或逐字题干未验证；批次 05 同类单元 5 个、批次 07 为「Ⅳ.单项填空」整大题 6 个（u44/u48/u52/u58/u62/u66）

## 具体检查与判定

- 失败检查：REVIEW_SAMPLE_SOURCE_UNVERIFIED
- 判定口径：lesson_bands 把大题 end_line 裁到课时板块边界 69（板块尾行 70 是纯 [IMG image4.jpeg] 占位），而 review_child_source._scoped_context 要求 source_section 的 title/context/occurrence/start_line/end_line 与 sync_practice.heading_candidates 重算结果逐字段相等；重算按「下一大题 start-1」得 70，与持久化 69 不等即判 uncertain。凡被板块边界裁短的大题全部无法通过 sample 轮来源复验
- 修复方向：允许可解释的板块尾裁剪：新增 _section_end_matches，仅当 end<parsed、lesson_container_end_line==end 且 lines[end:parsed] 全是空行/纯图片占位/课时或大题标题时才接受较短 end；裁到真实内容仍判失败

## 修复记录

- 修复方式：_scoped_context 改用 _section_end_matches：仅当 end<parsed、lesson_container_end_line==end 且尾部行全为空行/纯图片占位/标题时接受较短 end；裁到真实内容仍 uncertain
- 修复提交：d9fe33d
- 复验证据：workflow.test_review_child_source/test_review_local_reuse/test_review_rounds 等 157 项 OK；批次 05/07 失败单元 5/24→0/24、6/26→0/26；批次 07 review_gate 复跑 required_unchecked=0
- 标记时间：2026-11-12T19:23:01+08:00
