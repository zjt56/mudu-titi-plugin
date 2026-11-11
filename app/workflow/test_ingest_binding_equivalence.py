# -*- coding: utf-8 -*-
"""绑定升级不得让真实未裁决差异无声退休。

背景：来源占位清理改动了审核语义投影，全局 input binding 因此变化。旧实现
把绑定变了的未裁决发现直接归档出队列，用户原本要求继续 open 的 11 条差异
从活动队列消失。这里锁定：只有能证明单元内容等价的旧差异才保留；证明不了
或内容真的变了，仍然照旧归档。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from . import ingest, review

OLD_BINDING = "4d8b97cdcd50beec8324c7b3a94e0ca72bac6b4c263b807cb8561ab4ccba03db"
NEW_BINDING = "cbc5cdc558692e87d1104ffaad572db32cd80be92884ec2af847061694612c36"


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _finding(uid="unit-1", binding=OLD_BINDING, fid="WF-0001"):
    return {
        "finding_id": fid,
        "unit_id": uid,
        "field": "type",
        "severity": "major",
        "blocking": True,
        "status": "open",
        "reason": "题组 type 判断不一致",
        "round_sources": [{
            "round_id": "labels",
            "execution_id": "round-old",
            "input_binding": binding,
            "protocol": review.ROUND_PROTOCOL,
            "target_number": None,
            "field": "type",
            "verdict": "ok",
            "value": "其他",
            "rationale": "旧观察",
            "evidence": [],
        }],
    }


class EquivalenceUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ingest-equivalence-")
        self.batch = Path(self.tmp.name) / "batch"
        self.batch.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _history(self, binding=OLD_BINDING, bindings=None):
        _write(self.batch / "workflow/review-history/old.json",
               {"plan": {"input_binding": binding,
                         "unit_bindings": bindings or {"unit-1": "digest-old"}},
                "rounds": []})

    def test_equivalent_digest_is_preserved(self):
        self._history()
        record = _finding()
        accepted = {"unit-1": {"digest-new", "digest-old"}}
        self.assertTrue(ingest._content_equivalent(self.batch, record, accepted))

    def test_changed_digest_is_not_preserved(self):
        self._history()
        record = _finding()
        self.assertFalse(ingest._content_equivalent(self.batch, record, {"unit-1": {"digest-new"}}))

    def test_missing_history_is_not_assumed_equivalent(self):
        record = _finding()
        self.assertFalse(ingest._content_equivalent(self.batch, record, {"unit-1": {"digest-old"}}))

    def test_unit_outside_current_plan_is_not_preserved(self):
        self._history()
        record = _finding(uid="unit-9")
        self.assertFalse(ingest._content_equivalent(self.batch, record, {"unit-1": {"digest-old"}}))

    def test_incomplete_old_plan_is_not_preserved(self):
        # 旧计划里没有这个单元的内容摘要 → 不能证明等价。
        self._history(bindings={"unit-2": "digest-old"})
        record = _finding()
        self.assertFalse(ingest._content_equivalent(self.batch, record, {"unit-1": {"digest-old"}}))


class BuildReviewFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ingest-queue-")
        self.batch = Path(self.tmp.name) / "batch"
        review_dir = self.batch / ingest.REVIEW_DIR
        review_dir.mkdir(parents=True)
        _write(review_dir / "content-review.json", {"findings": []})
        _write(review_dir / "metadata-review.json", {"findings": []})
        _write(review_dir / "manual-review-queue.json",
               {"queue_version": "1.0", "items": [_finding()]})
        _write(self.batch / "workflow/review-history/old.json",
               {"plan": {"input_binding": OLD_BINDING,
                         "unit_bindings": {"unit-1": "digest-old"}},
                "rounds": []})
        _write(review_dir / review.ROUND_FILE,
               {"plan": {"input_binding": NEW_BINDING,
                         "unit_bindings": {"unit-1": "digest-new"}},
                "rounds": []})

    def tearDown(self):
        self.tmp.cleanup()

    def _build(self, accepted):
        result = {"findings": [], "summary": {}}
        with mock.patch.object(review, "acceptable_unit_bindings", return_value=accepted), \
                mock.patch.object(review, "evaluate_rounds",
                                  return_value={"unchecked": [], "findings": []}):
            report = ingest.build_review_files(self.batch, result)
        queue = json.loads((self.batch / ingest.REVIEW_DIR / "manual-review-queue.json")
                           .read_text(encoding="utf-8-sig"))
        archive_path = self.batch / ingest.REVIEW_DIR / "retired-round-findings.json"
        archive = (json.loads(archive_path.read_text(encoding="utf-8-sig"))
                   if archive_path.is_file() else {"records": []})
        return report, queue, archive

    def test_equivalent_finding_stays_in_active_queue(self):
        report, queue, archive = self._build({"unit-1": {"digest-new", "digest-old"}})
        ids = [item["finding_id"] for item in queue["items"]]
        self.assertIn("WF-0001", ids)
        self.assertEqual(report["queue"], 1)
        self.assertFalse([r for r in archive["records"] if r["finding_id"] == "WF-0001"])

    def test_restored_finding_never_reuses_an_active_id(self):
        # 真实批次：恢复的旧差异与被保留的活跃差异恰好都叫 WF-0015（不同单元）。
        # 队列 ID 是界面识别人工条目的键，撞号会让两条差异看起来是同一条。
        retired = _finding(uid="unit-2", fid="WF-0005")
        retired["round_sources"][0]["execution_id"] = "round-old-2"
        retired["round_sources"][0]["target_number"] = 1
        retired["retired_from"] = "manual-review-queue.json"
        retired["retired_reason"] = ingest.RETIRED_BINDING_REASON
        _write(self.batch / ingest.REVIEW_DIR / "retired-round-findings.json",
               {"records": [retired]})
        _write(self.batch / ingest.REVIEW_DIR / "manual-review-queue.json",
               {"queue_version": "1.0", "items": [_finding(uid="unit-1", fid="WF-0005")]})
        _write(self.batch / "workflow/review-history/old.json",
               {"plan": {"input_binding": OLD_BINDING,
                         "unit_bindings": {"unit-1": "digest-old", "unit-2": "digest-old"}},
                "rounds": []})
        report, queue, _ = self._build({"unit-1": {"digest-old", "digest-new"},
                                        "unit-2": {"digest-old", "digest-new"}})
        opened = [item for item in queue["items"] if item.get("status") == "open"]
        self.assertEqual(len(opened), 2, report)
        self.assertEqual(len({item["finding_id"] for item in opened}), 2)
        self.assertEqual(sorted(item["unit_id"] for item in opened), ["unit-1", "unit-2"])

    def test_restoring_twice_keeps_one_copy(self):
        retired = _finding(uid="unit-2", fid="WF-0001")
        retired["round_sources"][0]["execution_id"] = "round-old-2"
        retired["round_sources"][0]["target_number"] = 1
        retired["retired_from"] = "manual-review-queue.json"
        retired["retired_reason"] = ingest.RETIRED_BINDING_REASON
        _write(self.batch / ingest.REVIEW_DIR / "retired-round-findings.json",
               {"records": [retired]})
        _write(self.batch / ingest.REVIEW_DIR / "manual-review-queue.json",
               {"queue_version": "1.0", "items": [_finding(uid="unit-1", fid="WF-0001")]})
        _write(self.batch / "workflow/review-history/old.json",
               {"plan": {"input_binding": OLD_BINDING,
                         "unit_bindings": {"unit-1": "digest-old", "unit-2": "digest-old"}},
                "rounds": []})
        accepted = {"unit-1": {"digest-old", "digest-new"},
                    "unit-2": {"digest-old", "digest-new"}}
        self._build(accepted)
        report, queue, _ = self._build(accepted)
        opened = [item for item in queue["items"] if item.get("status") == "open"]
        self.assertEqual(len(opened), 2, report)
        self.assertEqual(len({item["finding_id"] for item in opened}), 2, report)

    def test_unproven_finding_is_archived_not_dropped(self):
        report, queue, archive = self._build({"unit-1": {"digest-new"}})
        self.assertNotIn("WF-0001", [item["finding_id"] for item in queue["items"]])
        kept = [r for r in archive["records"] if r["finding_id"] == "WF-0001"]
        self.assertEqual(len(kept), 1)
        self.assertEqual(report["queue"], 0)

    def test_proof_failure_falls_back_to_archive(self):
        result = {"findings": [], "summary": {}}
        with mock.patch.object(review, "acceptable_unit_bindings",
                               side_effect=RuntimeError("boom")), \
                mock.patch.object(review, "evaluate_rounds",
                                  return_value={"unchecked": [], "findings": []}):
            ingest.build_review_files(self.batch, result)
        queue = json.loads((self.batch / ingest.REVIEW_DIR / "manual-review-queue.json")
                           .read_text(encoding="utf-8-sig"))
        self.assertNotIn("WF-0001", [item["finding_id"] for item in queue["items"]])


if __name__ == "__main__":
    unittest.main()
