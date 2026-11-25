# 卡点：完形填空「10空」+1道追问（新考法/简答/无标记）被误判 SUBQUESTION_COUNT_MISMATCH

- id: 2026-11-25-完形填空「10空」+1道追问(新考法-简答-无标记)被误判-SUBQUESTION_COUNT_
- status: fixed
- batch: 2426秋九上人教版课时A计划Unit3SmartLearning综合阅读提升
- batch_dir: /Users/zjt/Desktop/上传插件版/workflows/2426秋九上人教版课时A计划Unit3SmartLearning综合阅读提升
- stage: format_gate
- check: SUBQUESTION_COUNT_MISMATCH
- created: 2026-11-25T10:22:41+08:00
- last_seen: 2026-11-25T10:22:41+08:00
- occurrences: 1
- fixed_at: 2026-11-25T10:22:55+08:00
- fix_commit: f563182（修复逻辑）+ working-tree（test_upload_payload.py 新增回归用例，未提交）
- verification: test_upload_payload+test_wire_format+test_question_content 139 tests OK (skipped=4)；4 批真实 batch.json 内存副本子类改回 10空 复跑 build_payload 无 SUBQUESTION_COUNT_MISMATCH；对照 7空/选择3题/9空/12空 仍报 SUBQUESTION_COUNT_MISMATCH。
- title: 完形填空「10空」+1道追问（新考法/简答/无标记）被误判 SUBQUESTION_COUNT_MISMATCH

## 现象（卡点）

4批复发：2426秋九上人教版课时A计划Unit3SmartLearning综合阅读提升、4026秋九上人教版课时A计划Unit5PowerofIdeas综合阅读提升、4826秋九上人教版课时A计划Unit6BeyondEarth综合阅读提升、5226秋九上人教版课时A计划Unit7FeeltheRhythm第四课时Sec；对应 unit sec-997217fc0eed624b-u13 / sec-de21cc48e5d101e2-u10 / sec-09d997f92f0782fc-u10 / sec-357a311a771595bf-u79，报「子类10空与 11 个结构化子题不符」。第11题写法分别为 [新考法·写作手法] 选择题、无选项简答、无标记普通选择（48/52）。运维当时将子类改为「其他空数」放行。

## 具体检查与判定

- 失败检查：SUBQUESTION_COUNT_MISMATCH
- 判定口径：原逻辑完形 N空 只按全部子题数计数，仅认 ☆拓展设问 标记排除追问；追问写法变化即被计入空数导致误判。正确口径：N空 = 正文实际编号空位数。
- 修复方向：upload_payload.py：完形 N空 允许值加入正文 material_blank_numbers 空位数（要求空位号全部对应子题号）；不依赖追问标记文字，不放宽其他计数。

## 修复记录

- 修复方式：upload_payload.py 完形 N空 允许计数加入正文编号空位数（cloze_blank_numbers，需空位号⊆子题号），不再依赖 ☆拓展设问 等追问标记；test_upload_payload.py 补 3 条回归：简答追问、无标记选择追问、负向对照（9空/12空仍报错），连同已有 [新考法] 用例共覆盖 4 批的 3 种写法。4 批批次数据未改动（子类仍为运维改写的「其他空数」，按规则 10 不回写）。
- 修复提交：f563182（修复逻辑）+ working-tree（test_upload_payload.py 新增回归用例，未提交）
- 复验证据：test_upload_payload+test_wire_format+test_question_content 139 tests OK (skipped=4)；4 批真实 batch.json 内存副本子类改回 10空 复跑 build_payload 无 SUBQUESTION_COUNT_MISMATCH；对照 7空/选择3题/9空/12空 仍报 SUBQUESTION_COUNT_MISMATCH。
- 标记时间：2026-11-25T10:22:55+08:00
