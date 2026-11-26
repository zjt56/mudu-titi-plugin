# 卡点：shared_rubric 对看图组题同行多张 [IMG] 匹配失败导致漏导语并误报 [A8]

- id: 2026-11-26-shared_rubric-对看图组题同行多张-[IMG]-匹配失败导致漏导语并误报-[A8]
- status: fixed
- batch: 5526秋七上人教版课时A计划Unit7HappyBirthday!第一课时Se
- batch_dir: /Users/zjt/Desktop/上传插件版/workflows/5526秋七上人教版课时A计划Unit7HappyBirthday!第一课时Se
- stage: assemble
- check: A8
- created: 2026-11-26T10:56:03+08:00
- last_seen: 2026-11-26T10:56:03+08:00
- occurrences: 1
- fixed_at: 2026-11-26T11:02:57+08:00
- fix_commit: -
- verification: unittest test_phonetic_structure + test_assemble_precheck + test_question_content 60 tests OK (skipped=4)；实盘批次 5526秋七上人教版课时A计划Unit7HappyBirthday!第一课时Se 重跑 assemble --force（deterministic=true, model_calls=0）与 validate --force（14/14 项全部通过，0 失败 0 提示）。
- title: shared_rubric 对看图组题同行多张 [IMG] 匹配失败导致漏导语并误报 [A8]

## 现象（卡点）

sec-9ec8766ba031054e-u8 (单词拼写) 报 [A8] 题组 unit_kind=group 但 material 为空，且大题导语丢失

## 具体检查与判定

- 失败检查：A8
- 判定口径：原卷第7行同行并排 3 张图 [IMG image1.jpeg][IMG image2.jpeg][IMG image3.jpeg]，shared_rubric.native_section_rubric 第 134 行用 re.fullmatch(r'\s*\[IMG\s+[^\]]+\]\s*', line) 只匹配单张图，导致返回空导语并连带令 phonetic_structure 豁免失效
- 修复方向：将正则改为 (?:\s*\[IMG\s+[^\]]+\])+\s* 支持同行多张图片

## 修复记录

- 修复方式：1) app/workflow/shared_rubric.py:134 将单图行正则 r'\s*\[IMG\s+[^\]]+\]\s*' 放宽为支持同行多张并排图片 r'(?:\s*\[IMG\s+[^\]]+\])+\s*'，使看图写词/看图写句组题正确保留大题导语；2) app/workflow/question_content.py::restore_source_stems 增加 _already_normalized_with_attachments 判定，若子题题干中原文 [IMG] 已全部转为 placement=question 附件且剥离后的文本与当前题干一致，不再把原始 [IMG] 标签回填进子题 stem，避免 assemble 预检阶段破坏 phonetic_structure.shape 校验并误报 [A8]。
- 修复提交：-
- 复验证据：unittest test_phonetic_structure + test_assemble_precheck + test_question_content 60 tests OK (skipped=4)；实盘批次 5526秋七上人教版课时A计划Unit7HappyBirthday!第一课时Se 重跑 assemble --force（deterministic=true, model_calls=0）与 validate --force（14/14 项全部通过，0 失败 0 提示）。
- 标记时间：2026-11-26T11:02:57+08:00
