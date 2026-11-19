# 2026-11-19 加粗题号的画线映射

批次 03 第三课时画线阶段报 `UNDERLINE_MAPPING_MISSING`。Word 原文给题号加粗，源段落的 `**1.**`、`**2.**` 和 `**(　)3.**` 没有被旧的题号剥离规则识别。题号留在比对文本中，导致 `fine`、`Susan` 和 `hello` 末尾的 `o` 三处画线无法定位。

`mark_underline.py` 在完整段落比对前识别带 Word 加粗的行首题号，保留原正文和画线范围。小数仍进入正文比较。缺上下文、多处匹配和画线重叠继续按原规则处理。

`test_underline_layout.py` 加入原生 DOCX 字符格式回归，覆盖画线已存在、缺一处画线时只补该处、基础视图与 Word 原件字节保持一致，以及重复运行保持相同结果。另有上下文缺失、跨单元重复及行首小数的用例。后续修改题号和版式识别时，可复用这些 Word 夹具检查提取与下游映射是否一致。

`workflow.test_underline_layout`、`workflow.test_validation_contracts`、`workflow.test_docx_extract` 共 43 项通过。真实批次经正式 runner 交付画线，三处原有画线无需重写。确定性汇总和本地体检通过，14 项检查，0 个阻断。

回退仅涉及本次画线上下文规则和对应测试。
