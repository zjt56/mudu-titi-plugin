# 2026-11-12 上传独立题用原卷题号补 location，整卷档案不再回退单元序号

## 背景 / 触发

线上 2024 安徽卷的整卷页面把单项填空显示成 `10.`–`19.`（应为 `21.`–`30.`），
听力短对话 1–5、单词拼写 76–80 同样没有真实题号。根因是：

1. 本地批次有原卷题号（`q-21` 的 `number=21`），但上传载荷把
   `number / numbers / numberStart / numberEnd` 当本地记账字段剥掉；
2. 远端 `questions` 没有题号列，整卷档案 `paper_items` 由
   `bootstrap_legacy_papers → inferred_paper_numbers` 自动生成；
3. 该推断只看子题号（题组）或 `location` 里的「第 N 题」。独立题没有子题、
   `location` 为空 → `number_start/number_end` 为 NULL，页面回退显示
   `paperOrder`（上传单元序号）。

用户要求：不能把「第几个审核/上传单元」误当原卷题号；要给这些单元补
`numberStart/numberEnd`。

## 改了什么

- `app/workflow/upload_payload.py::_Builder.item()`：独立题（无子题）且
  `location` 为空时，用原卷印刷题号补 `location = "第N题"`。
  - 只对 legacy 整卷批次生效（`identity_version` 为空）；
  - `书面表达` 不补：`number` 是装配续号，原卷没有印刷数字题号；
  - scoped 课时批次不补：同号分节重复，不进入整卷档案；
  - 已有 `location` 不覆盖。
- `app/docs/upload-api.md`：写明该映射与两条豁免。
- `app/workflow/test_upload_payload.py`：新增 3 条用例（独立题补号、已有
  location 不覆盖、书面表达/scoped 不补），并更新缺字段警告数（location
  不再报空）。

## 没改什么 / 边界

- 不按上传顺序或单元序号推断题号；只用结构确认得到的原卷题号。
- 不改题组（子题号已能推断范围）、不改题目内容/答案/解析/附件。
- 不自动上传、确认或发布；远端仍走人工确认。
- 已上传的历史卷不会因本改动自动回填；2024 卷由服务端数据修复单独处理。

## 如何验证

- `cd app && PYTHONPATH=. python3 -m unittest workflow.test_upload_payload`：
  **Ran 70 tests, OK（70 项通过）**，含新增 3 条用例；`test_missing_metadata_is_explicit_error`
  改为断言 `location=第81题` 且只剩 2 条来源空字段警告。
- 用 2024 真实批次 + 冻结目录只读重建载荷：`q-1 → 第1题`、`q-21 → 第21题`、
  `q-30 → 第30题`、`q-76 → 第76题`、`q-81 → 空`（书面表达不补）。
- 远端推断链已人工核对：`inferred_paper_numbers` 的正则为
  `第\s*(\d+)\s*(?:[—–－~～至-]\s*(\d+)\s*)?题`，`第21题` → `21/21`；
  `paper_source_identity` 不含 `location`，补 location 不改变整卷来源身份。

## 回退方式

回退 `upload_payload.py` 的 location 补号块与对应测试/文档即可；已上传数据不受影响。
