# 卡点：一审局部补审响应在checks数组闭合前多输出单个冗余右花括号导致JSON解析连续失败

- id: 2026-11-25-一审局部补审响应在checks数组闭合前多输出单个冗余右花括号导致JSON解析连续失败
- status: fixed
- batch: 3526秋八上人教版课时A计划Unit5WhataDeliciousMeal!第
- batch_dir: /Users/zjt/Desktop/上传插件版/workflows/3526秋八上人教版课时A计划Unit5WhataDeliciousMeal!第
- stage: review
- check: Invalid final JSON response
- created: 2026-11-25T16:35:10+08:00
- last_seen: 2026-11-25T16:35:10+08:00
- occurrences: 1
- fixed_at: 2026-11-25T16:37:30+08:00
- fix_commit: uncommitted (working tree)
- verification: unittest workflow.test_local_agent + workflow.test_review_rounds: Ran 155 OK (skipped=1); 线上两份真实失败样本(repair-1c88 / repair-json-cb07)均通过 _parse_response(..., strict=True) 及 _partition_check_issues(valid=1, failed=[]); 批次 3526秋八上人教版课时A计划Unit5WhataDeliciousMeal!第 复验 review=completed(27/27), review_gate=needs_review(7项待人工审核), format_gate=completed(passed=True, errors=[]).
- title: 一审局部补审响应在checks数组闭合前多输出单个冗余右花括号导致JSON解析连续失败

## 现象（卡点）

一审分片5(sec-5d00e86fa446c431-u105)局部补审及repair-json重试连续两次在checks数组右方括号前多输出一个右花括号(]}}}]}} 与 "answer_points":[]}}}]}), 触发 Invalid final JSON response: Expecting ',' delimiter: line 1 column 920 (char 919)

## 具体检查与判定

- 失败检查：Invalid final JSON response
- 判定口径：local_agent._parse_response 仅修补末尾尾随多余括号(text[:-trim])，未修补 JSON 内部 JSONDecodeError 位置处可证明的相邻重复闭合括号(如数组内对象结束后的多余 '}' 或根对象逗号前的重复 '}')
- 修复方向：在 local_agent._parse_response 中增加有界冗余重复闭合括号修补(_repair_redundant_closers)，仅在 JSONDecodeError 精确报错点存在相邻重复 '}'/']'（或 Extra data 逗号前双 '}'）时删除单个多余括号并严格复验完整 JSON 与契约；补齐单测并用真实批次复验 review 推进。

## 修复记录

- 修复方式：在 app/workflow/local_agent.py::_parse_response 中增加有界冗余重复闭合括号修补(_repair_redundant_closers)，仅在 JSONDecodeError 报错位置存在可证明的相邻重复 '}'/']'（或 Extra data 逗号前双 '}'）时删除单个冗余括号并严格复验完整 JSON 与契约；补齐 test_local_agent 单测并更新 test_review_rounds 重试测试样本。
- 修复提交：uncommitted (working tree)
- 复验证据：unittest workflow.test_local_agent + workflow.test_review_rounds: Ran 155 OK (skipped=1); 线上两份真实失败样本(repair-1c88 / repair-json-cb07)均通过 _parse_response(..., strict=True) 及 _partition_check_issues(valid=1, failed=[]); 批次 3526秋八上人教版课时A计划Unit5WhataDeliciousMeal!第 复验 review=completed(27/27), review_gate=needs_review(7项待人工审核), format_gate=completed(passed=True, errors=[]).
- 标记时间：2026-11-25T16:37:30+08:00
