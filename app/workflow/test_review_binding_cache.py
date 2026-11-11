# -*- coding: utf-8 -*-
"""审核绑定缓存的成本回归：绑定检查不得重复哈希全部轮次输入。

背景：`review.acceptable_unit_bindings` 原先每次都用 `_plan_input_key` 现算
「所有轮次输入 + 规则文档 + 媒体」的内容摘要，而每个单元都要问一次，13 批的
试卷列表接口因此跑到 50s 以上、前端 15s 就放弃加载。计划键改为只由计划摘要决定
（计划本身就由内容摘要构成），确认单元解析按结构文件状态记忆。这里锁住：
①计划键只由内容决定，内容变化必须换键；②绑定检查不得重新哈希输入；
③兼容绑定缓存返回同一结果。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workflow import review


class PlanInputKeyTest(unittest.TestCase):
    def setUp(self):
        review._COMPAT_BINDINGS_CACHE.clear()
        review._UNITS_CACHE.clear()

    def _batch(self, tmp):
        batch = Path(tmp)
        (batch / "01_source").mkdir(parents=True, exist_ok=True)
        (batch / "01_source/正文.txt").write_text("hello", encoding="utf-8")
        return batch

    def test_same_content_gives_the_same_key(self):
        with tempfile.TemporaryDirectory(dir=tempfile.gettempdir()) as tmp:
            batch = self._batch(tmp)
            units = [{"unit_id": "u1", "numbers": [1]}]
            first = review._plan_input_key(batch, units)
            second = review._plan_input_key(batch, units)
            self.assertEqual(first, second)

    def test_changed_content_gets_a_new_key(self):
        with tempfile.TemporaryDirectory(dir=tempfile.gettempdir()) as tmp:
            batch = self._batch(tmp)
            units = [{"unit_id": "u1", "numbers": [1]}]
            before = review._plan_input_key(batch, units)
            (batch / "01_source/正文.txt").write_text("hello world", encoding="utf-8")
            after = review._plan_input_key(batch, units)
            self.assertNotEqual(before, after, "输入内容变化必须换新计划键")

    def test_binding_checks_use_the_plan_digest_not_a_fresh_file_scan(self):
        with tempfile.TemporaryDirectory(dir=tempfile.gettempdir()) as tmp:
            batch = self._batch(tmp)
            plan = {"unit_bindings": {"u1": "d1"}, "input_hashes": {}}

            def forbidden(*_args, **_kwargs):
                raise AssertionError("绑定检查不得重新哈希全部轮次输入")

            real_plan_key = review._plan_input_key
            real_units = review._units_cached
            real_bindings = review._unit_bindings
            review._plan_input_key = forbidden
            review._units_cached = lambda _batch: [{"unit_id": "u1", "numbers": [1]}]
            review._unit_bindings = lambda *a, **k: {"u1": "legacy"}
            try:
                accepted = review.acceptable_unit_bindings(batch, plan)
                again = review.acceptable_unit_bindings(batch, plan)
            finally:
                review._plan_input_key = real_plan_key
                review._units_cached = real_units
                review._unit_bindings = real_bindings
            self.assertIn("legacy", accepted["u1"])
            self.assertEqual(accepted, again, "第二层缓存必须返回同样的可接受摘要")
            self.assertIn("d1", again["u1"])

    def test_units_are_parsed_once_per_structure_revision(self):
        with tempfile.TemporaryDirectory(dir=tempfile.gettempdir()) as tmp:
            batch = self._batch(tmp)
            (batch / "01_source/structure.confirmed.json").write_text('{"units": []}', encoding="utf-8")
            calls = []

            from workflow import agent_tasks

            real = agent_tasks.confirmed_units
            agent_tasks.confirmed_units = lambda _b: (calls.append(1), [])[1]
            try:
                review._units_cached(batch)
                review._units_cached(batch)
            finally:
                agent_tasks.confirmed_units = real
            self.assertEqual(len(calls), 1, "同一结构修订只解析一次")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
