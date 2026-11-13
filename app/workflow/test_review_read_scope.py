"""One evaluation shares source reads; no read/verdict survives the next call."""
import copy
import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from workflow import review, agent_tasks


class ReviewReadScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name)
        (self.batch / '01_source').mkdir()
        self.source = self.batch / '01_source/正文.txt'
        self.source.write_text('1. A source sentence.', encoding='utf-8')
        (self.batch / '01_source/structure.confirmed.json').write_text(json.dumps({'units': [{
            'unit_id': 'q-1', 'unit_kind': 'standalone', 'numbers': [1],
            'stem': '1. A source sentence.'}]}))

    def test_scope_reads_each_file_and_parses_units_once_and_next_call_sees_changes(self):
        reads = Counter()
        original = Path.read_bytes
        def counted(path):
            reads[str(path.resolve())] += 1
            return original(path)
        @review._evaluation_reads
        def observed():
            first = review._file_digest(self.source)
            texts = review._bound_source_texts(self.batch, {'input_hashes': {'01_source/正文.txt': first}})
            self.assertEqual(texts['01_source/正文.txt'], self.source.read_text())
            self.assertEqual(review._read_bound_bytes(self.source), texts['01_source/正文.txt'].encode())
            units = review._confirmed_review_units(self.batch)
            units[0]['stem'] = 'mutated return'
            self.assertNotEqual(review._confirmed_review_units(self.batch)[0]['stem'], 'mutated return')
            return first
        with patch.object(Path, 'read_bytes', counted), \
             patch.object(agent_tasks, 'confirmed_units', wraps=agent_tasks.confirmed_units) as units:
            first = observed()
            self.assertEqual(reads[str(self.source.resolve())], 1)
            self.assertEqual(units.call_count, 1)
            self.source.write_text('1. B source sentence.', encoding='utf-8')
            second = observed()
            self.assertNotEqual(first, second)
            self.assertEqual(reads[str(self.source.resolve())], 2)
            # 正文变化不重解析结构：单元解析缓存按 structure.confirmed.json 的
            # 版本判定，同一份结构在两次评估间不必重复解析（确定性快路）。
            self.assertEqual(units.call_count, 1)
            # 真正改变单元输入时才必须重新解析，并让下一次评估看到新单元。
            (self.batch / '01_source/structure.confirmed.json').write_text(json.dumps({'units': [{
                'unit_id': 'q-1', 'unit_kind': 'standalone', 'numbers': [1],
                'stem': '1. B source sentence.'}]}), encoding='utf-8')
            observed()
            self.assertEqual(units.call_count, 2)
            self.assertEqual(review._confirmed_review_units(self.batch)[0]['stem'], '1. B source sentence.')
        self.assertIsNone(review._EVALUATION_READS.get())

    def test_evaluate_rounds_owns_scope_without_caching_verdict_or_masking_changed_source(self):
        plan = {'input_binding': 'binding', 'input_hashes': {
            '01_source/正文.txt': review._file_digest(self.source)}, 'units': ['q-1'],
            'sample': [{'unit_id': 'q-1', 'number': None}]}
        payload = {'plan': plan, 'rounds': [{'round_id': rid, 'input_binding': 'binding',
            'execution_id': rid, 'checks': [{'unit_id': 'q-1', 'number': None, 'evidence': []}]}
            for rid in review.ROUND_IDS]}
        scopes = []
        def checked(*args, **kwargs):
            scopes.append(review._EVALUATION_READS.get())
            review._file_digest(self.source)
            return []
        with patch.object(review, 'prepare_round_plan', return_value=plan), \
             patch.object(review, 'resolve_round_plan', side_effect=lambda b, p, v: (p, {})), \
             patch.object(review, '_round_checks', side_effect=checked), \
             patch.object(review, '_verify_round_sources'), \
             patch('workflow.review_consensus.compare', return_value=[]):
            first = review.evaluate_rounds(self.batch, copy.deepcopy(payload))
            self.assertEqual(len(scopes), len(review.ROUND_IDS))
            self.assertTrue(all(s is scopes[0] for s in scopes))
            self.source.write_text('changed source', encoding='utf-8')
            second = review.evaluate_rounds(self.batch, copy.deepcopy(payload))
            self.assertIsNot(scopes[0], scopes[-1])
            self.assertTrue(any(e.get('code') == 'REVIEW_SAMPLE_SOURCE_UNVERIFIED'
                                for e in second['errors']))
            self.assertNotEqual(first['sample_source_bindings'], second['sample_source_bindings'])
        self.assertIsNone(review._EVALUATION_READS.get())

    @unittest.skipIf(sys.platform == 'win32',
                     'Windows 的 st_ctime 是创建时间，恢复 mtime 的同尺寸改写无法从 stat 版本发现；'
                     '要覆盖需按内容哈希校验，代价高于该缓存的目的（生产写入必然改变 mtime）')
    def test_source_change_inside_scope_is_not_hidden_even_with_restored_mtime(self):
        import os
        @review._evaluation_reads
        def observed():
            before = review._file_digest(self.source)
            stat = self.source.stat()
            self.source.write_text('1. B source sentence.', encoding='utf-8')
            os.utime(self.source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return before, review._file_digest(self.source)
        before, after = observed()
        self.assertNotEqual(before, after)

    def test_scope_cleans_up_on_exception(self):
        @review._evaluation_reads
        def broken():
            review._read_bound_bytes(self.source)
            raise ValueError('bad evidence')
        with self.assertRaises(ValueError):
            broken()
        self.assertIsNone(review._EVALUATION_READS.get())


if __name__ == '__main__':
    unittest.main()
