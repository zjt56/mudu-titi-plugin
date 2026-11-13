"""卡点台账（ISSUES/）回归：登记、去重、标记已修复、正文无损与安全边界。

用户要求（2026-11-10）：试卷流程每次遇到卡点都要记录，并跟踪是否已修复；
未修复的留在待修复清单里，已修复的标注已修复。
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import issue_log


class IssueLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def add(self, **kwargs):
        params = dict(batch="【木牍教育】20年安徽中考英语", stage="explanations",
                      title="书面表达思路点拨解析被拒",
                      check="EXPLANATION_SOURCE_UNVERIFIED",
                      symptom="q-81 报 EXPLANATION_SOURCE_UNVERIFIED，evidence 为空",
                      why="source_ref 指向附录第115行，【思路点拨】标题未被剥离",
                      how="剥离【思路点拨】等标题，严格模式同样接受书面表达思路点拨原文",
                      batch_dir="workflows/【木牍教育】20年安徽中考英语",
                      root=self.root)
        params.update(kwargs)
        return issue_log.add(**params)

    def test_add_creates_open_record_with_checkable_fields(self):
        result = self.add()
        self.assertFalse(result["deduplicated"])
        path = Path(result["path"])
        self.assertEqual(path.parent.name, "open")
        self.assertTrue(path.is_file())
        record = issue_log.parse(path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "open")
        self.assertEqual(record["stage"], "explanations")
        self.assertEqual(record["check"], "EXPLANATION_SOURCE_UNVERIFIED")
        self.assertEqual(record["occurrences"], 1)
        # 必须能说清“哪条检查、为什么失败、怎么修”，不能只写一句“按阻断项修复”。
        self.assertIn("EXPLANATION_SOURCE_UNVERIFIED", record["symptom"])
        self.assertIn("标题未被剥离", record["why"])
        self.assertIn("剥离", record["how"])
        self.assertIn("（未修复）", record["resolution"])

    def test_same_blocker_is_counted_not_duplicated(self):
        first = self.add()
        second = self.add(title="同一卡点再次出现")  # 同批次+阶段+检查 → 累加
        self.assertTrue(second["deduplicated"])
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["occurrences"], 2)
        self.assertEqual(len(list((self.root / "open").glob("*.md"))), 1)
        text = Path(second["path"]).read_text(encoding="utf-8")
        # 去重只累加次数，正文（现象/判定口径/修复方向）必须逐字保留。
        self.assertIn("q-81 报 EXPLANATION_SOURCE_UNVERIFIED", text)
        self.assertIn("source_ref 指向附录第115行", text)
        self.assertIn("剥离【思路点拨】等标题", text)

    def test_fix_moves_to_fixed_and_keeps_body(self):
        added = self.add()
        fixed = issue_log.fix(added["id"], resolution="剥离标题后复验通过",
                              commit="bb6e869", verify="test_upload_content 47 项通过",
                              root=self.root)
        self.assertEqual(fixed["status"], "fixed")
        self.assertFalse((self.root / "open" / (added["id"] + ".md")).exists())
        path = Path(fixed["path"])
        self.assertEqual(path.parent.name, "fixed")
        record = issue_log.parse(path.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "fixed")
        self.assertEqual(record["fix_commit"], "bb6e869")
        self.assertEqual(record["verification"], "test_upload_content 47 项通过")
        self.assertNotEqual(record["fixed_at"], "")
        # 正文与出现次数不因“标记已修复”而丢失。
        self.assertIn("q-81 报 EXPLANATION_SOURCE_UNVERIFIED", record["symptom"])
        self.assertEqual(record["occurrences"], 1)
        self.assertIn("剥离标题后复验通过", record["resolution"])
        # 标记修复会重写已有“修复记录”段，文末必须只保留一个换行；
        # 否则 git diff --check 会报 new blank line at EOF。
        raw = path.read_text(encoding="utf-8")
        self.assertTrue(raw.endswith("\n"))
        self.assertFalse(raw.endswith("\n\n"))

    def test_fix_is_one_way_and_requires_known_issue(self):
        added = self.add()
        issue_log.fix(added["id"], root=self.root)
        with self.assertRaisesRegex(issue_log.IssueError, "已标记为已修复"):
            issue_log.fix(added["id"], root=self.root)
        with self.assertRaisesRegex(issue_log.IssueError, "找不到问题记录"):
            issue_log.fix("2026-11-10-不存在的问题", root=self.root)

    def test_ids_cannot_escape_the_ledger(self):
        for bad in ("../etc/passwd", "a/b", "..", "", "2026-11-10-x/../y"):
            with self.subTest(value=bad):
                with self.assertRaises(issue_log.IssueError):
                    issue_log.find(bad, root=self.root)

    def test_add_requires_a_title(self):
        with self.assertRaisesRegex(issue_log.IssueError, "标题"):
            self.add(title="   ")

    def test_duplicate_title_gets_distinct_id_and_list_filters(self):
        first = self.add(check="CHECK_A")
        second = self.add(check="CHECK_B")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(issue_log.listing("open", self.root)), 2)
        issue_log.fix(first["id"], root=self.root)
        self.assertEqual(len(issue_log.listing("open", self.root)), 1)
        self.assertEqual(len(issue_log.listing("fixed", self.root)), 1)
        self.assertEqual(issue_log.summary(self.root), {"open": 1, "fixed": 1, "total": 2})

    def test_cli_round_trip_through_env_override(self):
        env = {"WF_ISSUES_DIR": str(self.root)}
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(issue_log.main([
                "add", "--batch", "B20", "--stage", "knowledge",
                "--title", "知识点 JSON 多写花括号",
                "--check", "Invalid final JSON response",
                "--symptom", "Extra data: line 1 column 10274",
                "--why", "根对象多写一个 }", "--how", "去掉多余尾随括号后重解析"]) , 0)
            issues = issue_log.listing("open")
            self.assertEqual(len(issues), 1)
            self.assertEqual(issue_log.main(["fix", "--id", issues[0]["id"],
                                             "--resolution", "解析器兼容",
                                             "--commit", "bb6e869",
                                             "--verify", "test_local_agent 通过"]), 0)
            self.assertEqual(issue_log.summary()["fixed"], 1)
            self.assertEqual(issue_log.main(["list", "--status", "open"]), 0)

    def test_record_blocker_upserts_per_batch_without_repo_pollution(self):
        batch = self.root / "batch"
        (batch / "workflow").mkdir(parents=True)
        first = issue_log.record_blocker(batch, "explanations",
                                         check="EXPLANATION_SOURCE_UNVERIFIED",
                                         symptom="q-81 未溯源", why="标题未剥离",
                                         how="剥离标题", source="self_heal")
        second = issue_log.record_blocker(batch, "explanations",
                                          check="EXPLANATION_SOURCE_UNVERIFIED",
                                          symptom="q-81 未溯源", why="标题未剥离",
                                          how="剥离标题", source="self_heal")
        self.assertEqual(first["occurrences"], 1)
        self.assertEqual(second["occurrences"], 2)  # 同卡点累加，不重复建条目
        data = issue_log.load_blockers(batch)
        self.assertEqual(len(data["blockers"]), 1)
        # 只写批次内台账，仓库 ISSUES/ 不受影响（测试/离线操作不污染）。
        self.assertFalse((self.root / "open").exists())

    def test_record_blocker_is_best_effort(self):
        # 批次目录不可写/不存在时返回 None，绝不抛异常打断自愈。
        missing = self.root / "nope"
        self.assertIsNone(issue_log.record_blocker(missing, "knowledge", check="x"))
        self.assertEqual(issue_log.load_blockers(missing)["blockers"], {})

    def test_sync_moves_batch_blockers_into_open_ledger(self):
        batch = self.root / "batch"
        (batch / "workflow").mkdir(parents=True)
        issue_log.record_blocker(batch, "explanations", check="EXPLANATION_SOURCE_UNVERIFIED",
                                 symptom="q-81 未溯源", why="标题未剥离", how="剥离标题")
        issue_log.record_blocker(batch, "knowledge", check="Extra data", symptom="多写括号")
        result = issue_log.sync(batch, root=self.root)
        self.assertEqual(len(result["created"]), 2)
        self.assertEqual(result["updated"], [])
        # 再同步一次只更新计数，不重复建单。
        again = issue_log.sync(batch, root=self.root)
        self.assertEqual(again["created"], [])
        self.assertEqual(len(again["updated"]), 2)
        self.assertEqual(len(issue_log.listing("open", self.root)), 2)
        text = Path(again["updated"] and (self.root / "open" / (again["updated"][0] + ".md"))
                    or "").read_text(encoding="utf-8")
        self.assertIn("q-81 未溯源", text)

    def test_self_heal_records_only_real_blockers(self):
        from workflow import self_heal
        batch = self.root / "batch"
        (batch / "workflow").mkdir(parents=True)
        ledger = {"events": [{"at": "2026-11-10T10:00:00+08:00", "stage": "knowledge",
                              "outcome": "repair_not_applicable",
                              "plan": {"reason": "自愈无法定位"},
                              "repair": {"action": "rejudge_knowledge", "status": "refused",
                                         "errors": [{"code": "KNOWLEDGE_REPAIR_FAILED",
                                                     "message": "batch_meta.json 不存在"}]}}]}
        self_heal._save(batch, ledger)
        data = issue_log.load_blockers(batch)
        entries = list(data["blockers"].values())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["stage"], "knowledge")
        self.assertEqual(entries[0]["check"], "KNOWLEDGE_REPAIR_FAILED")
        # 同一事件重复 _save 不重复计数（去重状态随 self-heal.json 持久化）。
        self_heal._save(batch, ledger)
        self.assertEqual(list(issue_log.load_blockers(batch)["blockers"].values())[0]["occurrences"], 1)
        # 正常推进（retry_scheduled）不是卡点，不登记。
        progressed = {"events": [{"at": "2026-11-10T10:05:00+08:00", "stage": "knowledge",
                                  "outcome": "retry_scheduled"}]}
        self_heal._save(batch, progressed)
        self.assertEqual(len(issue_log.load_blockers(batch)["blockers"]), 1)

    def test_supervisor_exhaustion_records_blocker(self):
        from workflow import supervisor
        batch = self.root / "batch"
        (batch / "workflow").mkdir(parents=True)
        supervisor._record_exhausted_blocker(
            batch, {"stages": {"knowledge": {"status": "failed"}}},
            {"escalation": "连续调度无有效本地进展"}, 6)
        entries = list(issue_log.load_blockers(batch)["blockers"].values())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["stage"], "knowledge")
        self.assertEqual(entries[0]["check"], "no_progress_exhausted")

    def test_repository_ships_the_ledger_folders(self):
        base = Path(issue_log.__file__).resolve().parents[2] / "ISSUES"
        self.assertTrue((base / "README.md").is_file())
        self.assertTrue((base / "open").is_dir())
        self.assertTrue((base / "fixed").is_dir())


if __name__ == "__main__":
    unittest.main()
