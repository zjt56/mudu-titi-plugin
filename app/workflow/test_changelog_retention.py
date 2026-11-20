"""CHANGELOG 保留策略的回归测试。

用户要求：CHANGELOG 进上传仓库忽略名单；超过两天的记录直接删除。
这里用临时目录 + 注入 today 覆核判定与删除，不触碰真实 CHANGELOG。
"""
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from workflow import changelog_retention as cr


class ChangelogRetentionTest(unittest.TestCase):
    def _make(self, root: Path, name: str) -> Path:
        path = root / name
        path.write_text("x", encoding="utf-8")
        return path

    def test_only_records_older_than_keep_days_are_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            today = date(2026, 11, 19)
            keep = [
                self._make(root, "2026-11-19-today.md"),      # 今天
                self._make(root, "2026-11-18-yesterday.md"),  # 昨天
                self._make(root, "2026-11-17-before.md"),     # 前天（保留）
            ]
            drop = [
                self._make(root, "2026-11-16-three-days.md"),  # 3 天前 -> 删
                self._make(root, "2026-11-10-old.md"),         # 更早 -> 删
            ]
            protected = [
                self._make(root, "README.md"),
                self._make(root, "RULES.md"),
                self._make(root, "notes.md"),                  # 非日期命名 -> 不删
            ]
            expired = cr.collect_expired(root, keep_days=2, today=today)
            self.assertEqual([p.name for p in expired],
                             ["2026-11-10-old.md", "2026-11-16-three-days.md"])
            for path in keep + protected:
                self.assertNotIn(path, expired)

    def test_prune_removes_expired_and_keeps_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            today = date(2026, 11, 19)
            recent = self._make(root, "2026-11-18-yesterday.md")
            old = self._make(root, "2026-11-15-old.md")
            readme = self._make(root, "README.md")
            result = cr.prune(root, keep_days=2, today=today)
            self.assertEqual(result["removed"], ["2026-11-15-old.md"])
            self.assertFalse(old.exists(), "超过两天应被删除")
            self.assertTrue(recent.exists(), "昨天应保留")
            self.assertTrue(readme.exists(), "README 永不删")
            self.assertEqual(result["failed"], [])

    def test_dry_run_deletes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = self._make(root, "2026-11-01-ancient.md")
            result = cr.prune(root, keep_days=2, today=date(2026, 11, 19), dry_run=True)
            self.assertEqual(result["expired"], ["2026-11-01-ancient.md"])
            self.assertEqual(result["removed"], ["2026-11-01-ancient.md"])
            self.assertTrue(old.exists(), "dry-run 不得真删")

    def test_missing_directory_is_safe(self):
        result = cr.prune("/nonexistent/changelog/dir", today=date(2026, 11, 19))
        self.assertEqual(result["expired"], [])
        self.assertEqual(result["removed"], [])

    def test_malformed_dates_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weird = self._make(root, "2026-13-40-bad.md")  # 非法日期
            self._make(root, "2026-11-19-ok.md")
            expired = cr.collect_expired(root, keep_days=2, today=date(2026, 11, 19))
            self.assertEqual(expired, [])
            self.assertTrue(weird.exists())

    def test_keep_days_zero_deletes_everything_not_today(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make(root, "2026-11-19-today.md")
            self._make(root, "2026-11-18-yesterday.md")
            expired = cr.collect_expired(root, keep_days=0, today=date(2026, 11, 19))
            self.assertEqual([p.name for p in expired], ["2026-11-18-yesterday.md"])

    def test_datetime_today_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make(root, "2026-11-15-old.md")
            expired = cr.collect_expired(root, keep_days=2,
                                         today=datetime(2026, 11, 19, 23, 59))
            self.assertEqual([p.name for p in expired], ["2026-11-15-old.md"])


if __name__ == "__main__":
    unittest.main()
