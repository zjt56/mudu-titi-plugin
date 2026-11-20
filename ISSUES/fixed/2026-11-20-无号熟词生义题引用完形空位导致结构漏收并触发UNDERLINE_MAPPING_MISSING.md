# 卡点：无号熟词生义题引用完形空位导致结构漏收并触发UNDERLINE_MAPPING_MISSING

- id: 2026-11-20-无号熟词生义题引用完形空位导致结构漏收并触发UNDERLINE_MAPPING_MISSING
- status: fixed
- batch: 02九上人教版课时A计划Unit1TheChangingWorld第二课时Sec
- batch_dir: workflows/02九上人教版课时A计划Unit1TheChangingWorld第二课时Sec
- stage: underlines
- check: UNDERLINE_MAPPING_MISSING
- created: 2026-11-20T09:03:59+08:00
- last_seen: 2026-11-20T09:03:59+08:00
- occurrences: 1
- fixed_at: 2026-11-20T09:07:00+08:00
- fix_commit: working-tree
- verification: test_golden_layouts + 4个回归模块全部通过；真实批次 02九上人教版课时A计划Unit1TheChangingWorld第二课时Sec 重跑 extract+structure_gate 产出 14 单元(含 sec-65e1661500776033-u116 翻译 unnumbered_section)，mark_underline.py 实测 passed=true issues=[]，已通过工作台 /advance 启动续跑。
- title: 无号熟词生义题引用完形空位导致结构漏收并触发UNDERLINE_MAPPING_MISSING

## 现象（卡点）

underlines 阶段报 UNDERLINE_MAPPING_MISSING（cover 完整原文上下文对应 0 个位置）

## 具体检查与判定

- 失败检查：UNDERLINE_MAPPING_MISSING
- 判定口径：正文 L114-116 熟词生义题引用完形原句含 __<u>1</u>__，extract 误将其收为独立题号 1，question_content.unnumbered_text_responses 将其计入空位数导致漏收整题，进而 mark_underline 在批次中找不到 cover 画线上下文
- 修复方向：extract 不对同步独立题扫 blank_numbers；unnumbered_text_responses 剔除引用完形编号空位后计数作答空位；structure_gate 无强题号独立节清空弱空位号走 unnumbered_section；lesson_bands 将【三、熟词生义】判为容器横幅截断前节

## 修复记录

- 修复方式：1) extract.py 不对同步独立题扫 blank_numbers；2) question_content.unnumbered_text_responses 剔除句中引用完形编号空位(__<u>N</u>__/__N__)后计数作答空位；3) structure_gate._build_scoped_units 与校验对无强题号独立节清空弱空位号走 unnumbered_section；4) lesson_bands._is_step_banner 识别显式中文序号方括号栏目横幅(如【三、熟词生义】)截断前节。
- 修复提交：working-tree
- 复验证据：test_golden_layouts + 4个回归模块全部通过；真实批次 02九上人教版课时A计划Unit1TheChangingWorld第二课时Sec 重跑 extract+structure_gate 产出 14 单元(含 sec-65e1661500776033-u116 翻译 unnumbered_section)，mark_underline.py 实测 passed=true issues=[]，已通过工作台 /advance 启动续跑。
- 标记时间：2026-11-20T09:07:00+08:00
