# 表格填空题号与栏目边界识别

## 背景
批次 `23七上人教版课时A计划Unit2We’reFamily!第四课时Section` 的教材语篇表格包含 `2.<u>h　</u>` 等题号在前、首字母及空档在下划线内的写法，原识别漏掉 2、4、10；同时 `[TABLE 1]` 被误认作栏目标题，把首大题来源窗口截断，结构确认报告来源单元未覆盖。

## 改了什么
`preview._weak_numbers` 识别题号后的首字母加空档，供提取和结构来源锚点共用；`lesson_bands.canonical_feature` 排除 TABLE/IMG/PAGE 等结构标记。视觉栏目检查针对四数齐全的 bbox 缺闭括号作语法修复，对纯下划线空白宽度作等价判断；视觉标为无号但 Word 空位号唯一连续且与全部锚点一致时以原文补证，并记录来源；明确的、紧邻的视觉附属设问扩展父范围并留存原末行。原始响应、坐标、原文、其他归属与冲突检查保留。

## 验证
`workflow.test_visual_columns workflow.test_structure_visual_binding workflow.test_lesson_bands workflow.test_structure_gate`：59 项通过；`workflow.test_lesson_bands workflow.test_structure_gate workflow.test_table_blank_structure`：41 项通过。指定批次经 Word 原件重提及真实整页视觉归属验证后，首节题号完整为 1–10，真实结构闸门通过，构建 19 个单元（15 独立、4 组题）；原有重号与完形分组阻断已逐项核对。

## 回退
撤销上述两个识别函数及对应回归测试；批次已有提取产物按常规工作流从原件重建。
