"""变更记录与规则台账必须真实存在、可解析、且与 AGENTS 硬规则对应。

用户要求：每次重大改动都要记录，强调/反复强调的规则也要记录。
这些约束不是口号，用测试钉住，避免以后悄悄失效。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG"


class ChangeLogPolicyTest(unittest.TestCase):
    def test_agents_entry_point_exists_and_lists_hard_rules(self):
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## 二、硬规则", text)
        self.assertIn("CHANGELOG/", text)
        # 单点失败不得停住/不得连累依赖无关分支，以及不自动上传，必须在总入口可见。
        self.assertIn("卡点必须自愈", text)
        self.assertTrue("无关" in text or "不能停住" in text or "不得让整条流程停住" in text)
        self.assertIn("不自动上传", text)

    def test_changelog_scaffold_exists(self):
        self.assertTrue(CHANGELOG.is_dir(), "缺少 CHANGELOG 目录")
        for name in ("README.md", "RULES.md"):
            self.assertTrue((CHANGELOG / name).is_file(), name)

    def test_rules_ledger_has_table_rows_with_date_and_location(self):
        text = (CHANGELOG / "RULES.md").read_text(encoding="utf-8")
        rows = [line for line in text.splitlines() if line.startswith("| 2")]
        self.assertGreaterEqual(len(rows), 5, "规则台账条数过少")
        for row in rows:
            cells = [c.strip() for c in row.strip("|").split("|")]
            self.assertGreaterEqual(len(cells), 3, row)
            self.assertRegex(cells[0], r"\d{4}-\d{2}-\d{2}", row)  # 有日期
            self.assertTrue(cells[1], row)                         # 有要点
            self.assertTrue(cells[2], row)                         # 有落地位置

    def test_every_dated_record_has_the_required_sections(self):
        records = sorted(CHANGELOG.glob("20*-*.md"))
        self.assertTrue(records, "没有任何日期化变更记录")
        for path in records:
            text = path.read_text(encoding="utf-8")
            for section in ("背景", "改了什么", "验证"):
                self.assertIn(section, text, f"{path.name} 缺少 {section}")
            # 至少一处真实测试数字或明确说明未触及代码。
            self.assertTrue(re.search(r"\d+\s*项|\d+\s*个用例|无需跑测试|未触及代码|通过", text),
                            f"{path.name} 未写明验证结果")

    def test_index_lists_every_dated_record(self):
        readme = (CHANGELOG / "README.md").read_text(encoding="utf-8")
        for path in CHANGELOG.glob("20*-*.md"):
            self.assertIn(path.name, readme, f"索引缺少 {path.name}")


if __name__ == "__main__":
    unittest.main()
