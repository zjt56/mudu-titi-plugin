# 2026-11-11 无题号无图片的题组大题不再被误判为来源区段未覆盖

## 背景

批次「02七上人教版课时A计划StarterUnit1Hello!第二课时Pronun」在结构自动确认被阻断：`SOURCE_SECTION_UNCOVERED`
（Ⅱ.按照字母表顺序写出字母歌中所缺大写字母），并连带 QUESTION_BOUNDARY_MISSING / NUMBERING_RESTART /
GROUP_BOUNDARY_REVIEW。人工核对原卷：Ⅱ 是整节无印刷小题号、无图片的补全题，属合法题组形态。

## 改了什么

- `app/workflow/structure_gate.py::_build_scoped_units`：题组材料窗口此前仅在“有题号成员”或“有 [IMG]/[TABLE]”
  时才生成来源单元，否则 `continue` 跳过；整节既无题号又无图片时该大题窗口没有单元，被判 SOURCE_SECTION_UNCOVERED。
  现改为：无题号成员但窗口有非空正文时，保留一个 `number_binding=unnumbered_section` 的来源单元（内部别名，
  不伪装印刷题号）；纯空白窗口仍跳过，避免造空单元掩盖真实漏题。
- `app/workflow/test_structure_gate.py`：新增两条回归——无题号无图片题组窗口保留来源单元且不报
  SOURCE_SECTION_UNCOVERED；纯空白题组窗口仍跳过不造空单元。

## 没改什么 / 边界

- 不放宽题号唯一绑定、来源窗口、材料省略、题组跨节等任一校验；仅修正“合法题组被静默跳过”的确定性缺陷。
- 未改用户批次数据（manifest 阶段状态由闸门自身跑通写入），未自动上传/发布，未改审核口径。

## 验证

- `workflow.test_structure_gate` 13 项通过（含两条新增）。
- 结构/同步/身份相关 181 项通过（结构闸门、sync_practice、课时形态、原卷标记、提问身份全套、phonetic_structure）。
  其中 `test_question_identity_format` 3 项在 macOS `$TMPDIR`（`/var`→`/private/var` 符号链接）下报路径安全
  错误，改用真实路径 `TMPDIR=/private/var/.../T` 后全部通过，属测试环境路径约定、与本改动无关。
- 真实批次结构闸门重跑：9 单元（独立5/组合4），5 条阻断全部转“每个原题位置已唯一绑定”通过；批次进入 classifying。

## 回退

回退 `structure_gate.py` 该分支与两条测试；该批次结构闸门将重新报 SOURCE_SECTION_UNCOVERED，不影响既有其他批次。
