"""并行阶段在进度视图里必须同时显示“处理中”，完成阶段不被并发导入压回。"""
import unittest

from workbench import server


def _manifest(statuses, *, unknown=(), **overrides):
    """Build a manifest where every stage starts completed (upstream met).

    ``statuses`` overrides the stages under test; ``unknown`` marks stages with
    no record at all (brand-new, never dispatched).
    """
    stages = {sid: {"status": "completed"} for sid in server.STAGE_ORDER}
    for sid in unknown:
        stages.pop(sid, None)
    for sid, status in statuses.items():
        record = {"status": status}
        record.update(overrides.get(sid, {}))
        stages[sid] = record
    return {"stages": stages}


class StageParallelDisplayTest(unittest.TestCase):
    def _active(self, manifest, **cards):
        info = server.stage_cards(manifest, has_output=True, advance_running=True)
        return {card["id"] for card in info["items"] if card.get("active")}, info

    def test_parallel_agents_report_every_running_stage_as_active(self):
        # explanations 与 knowledge 均已实际启动，必须同时显示“处理中”。
        manifest = _manifest({"types": "completed", "answers": "completed",
                               "explanations": "running", "knowledge": "running"})
        active, info = self._active(manifest)
        self.assertIn("explanations", active)
        self.assertIn("knowledge", active)
        # 单点 current 仍存在（用于队列摘要），但不是唯一活动指针
        self.assertIsNotNone(info["current"])

    def test_completed_stage_is_not_marked_active(self):
        manifest = _manifest({"types": "completed", "answers": "completed",
                               "explanations": "running", "knowledge": "running"})
        active, _ = self._active(manifest)
        self.assertNotIn("types", active)
        self.assertNotIn("answers", active)

    def test_stage_with_unmet_dependency_is_not_active(self):
        # answers 未完成时 explanations（依赖 answers）不得显示为并行处理中。
        manifest = _manifest({"types": "completed", "answers": "pending",
                               "explanations": "pending", "knowledge": "running"})
        active, _ = self._active(manifest)
        self.assertIn("knowledge", active)
        self.assertNotIn("explanations", active)

    def test_rebuild_marked_dependency_blocks_activity(self):
        manifest = _manifest({"types": "running", "answers": "completed",
                               "explanations": "pending"},
                              types={"rebuild_required": True})
        active, _ = self._active(manifest)
        self.assertNotIn("explanations", active)

    def test_skipped_and_finished_stages_are_never_active(self):
        manifest = _manifest({"audio": "skipped", "types": "completed"})
        active, _ = self._active(manifest)
        self.assertNotIn("audio", active)
        self.assertNotIn("types", active)

    def test_unstarted_batch_has_no_active_stage(self):
        for manifest in ({"stages": {}}, _manifest({sid: "pending" for sid in server.STAGE_ORDER})):
            with self.subTest(manifest=manifest):
                active, info = self._active(manifest)
                self.assertEqual(active, set())
                self.assertEqual(info["current"]["id"], "extract")
                self.assertEqual(info["current"]["status"], "pending")

    def test_ready_pending_stages_stay_idle_even_while_batch_advances(self):
        manifest = _manifest({"answers": "pending", "audio": "pending",
                              "knowledge": "pending", "explanations": "pending"})
        for advancing in (False, True):
            with self.subTest(advancing=advancing):
                info = server.stage_cards(manifest, advance_running=advancing)
                self.assertFalse(any(card["active"] for card in info["items"]))
                self.assertEqual(server.active_stage_ids(manifest), set())

    def test_only_dispatched_parallel_worker_is_active(self):
        manifest = _manifest({"answers": "running", "audio": "pending",
                              "knowledge": "pending", "explanations": "pending"})
        active, _ = self._active(manifest)
        self.assertEqual(active, {"answers"})
        self.assertEqual(server.active_stage_ids(manifest), {"answers"})

    def test_unqualified_types_does_not_satisfy_running_consumers(self):
        manifest = _manifest({"types": "needs_review", "audio": "running",
                              "knowledge": "running", "explanations": "running"},
                             types={"execution_completed": True,
                                    "continue_unqualified": True,
                                    "release_ready": False})
        active, _ = self._active(manifest)
        self.assertFalse(server.manifest_dependency_satisfied(manifest, "types"))
        self.assertEqual(active, set())

    def test_unqualified_review_gate_counts_as_met_dependency(self):
        # review_gate「已执行、差异待人工」应解锁 format_gate 等下游，而不是卡住显示。
        manifest = _manifest({"review_gate": "needs_review", "format_gate": "running"},
                              review_gate={"execution_completed": True,
                                           "continue_unqualified": True,
                                           "release_ready": False})
        active, _ = self._active(manifest)
        self.assertIn("format_gate", active)

    def test_cards_carry_active_flag_and_keep_status(self):
        manifest = _manifest({"explanations": "running", "types": "completed"})
        info = server.stage_cards(manifest, has_output=True, advance_running=True)
        by_id = {card["id"]: card for card in info["items"]}
        self.assertTrue(by_id["explanations"]["active"])
        self.assertFalse(by_id["types"]["active"])
        # 只加显示标记，不把完成态改成其它状态
        self.assertEqual(by_id["types"]["status"], "completed")
        self.assertEqual(by_id["explanations"]["status"], "running")


if __name__ == "__main__":
    unittest.main()
