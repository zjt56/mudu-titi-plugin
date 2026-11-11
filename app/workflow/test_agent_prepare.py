import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import agent_prepare, extract, manifest as mf


class AgentPrepareTest(unittest.TestCase):
    def test_extract_direct_script_fallback_imports_answer_preparer(self):
        with patch.object(extract, 'agent_prepare', create=True) as _unused:
            pass
        source = Path(extract.__file__).read_text(encoding='utf-8')
        self.assertIn('from workflow import agent_prepare', source)

    def test_explicit_answer_docx_keeps_full_raw_text_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp); source = batch / 'answer.docx'; source.write_bytes(b'docx')
            state = mf.new_manifest('x', batch, {'answers': {'path': str(source)}}, {})
            mf.save(batch, state)
            def produce(src, out):
                (out / '原始正文.txt').write_text('I. Answers\n1. B\nI. More answers\n2. C')
                (out / '正文.txt').write_text('I. Answers\n1. B')
                return {'passed': True}
            with patch.object(extract, 'extract_source', side_effect=produce) as call:
                first = agent_prepare.prepare_answers(batch)
                second = agent_prepare.prepare_answers(batch)
                self.assertEqual(call.call_count, 1)
            self.assertFalse(first['cached']); self.assertTrue(second['cached'])
            self.assertIn('2. C', (batch / '01_source_ans' / '正文.txt').read_text())
            self.assertEqual(source.read_bytes(), b'docx')

    def test_explicit_answer_text_prepares_readonly_shared_source_without_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp)
            source = batch / 'answer.txt'
            source.write_text('1.A 【解析】保留原文解析。', encoding='utf-8')
            original = source.read_bytes()
            mf.save(batch, mf.new_manifest('x', batch, {'answers': {'path': str(source)}}, {}))
            with patch.object(extract, 'extract_source') as call:
                self.assertTrue(agent_prepare.prepare_answers(batch)['prepared'])
                self.assertTrue(agent_prepare.prepare_answers(batch)['cached'])
            call.assert_not_called()
            self.assertEqual((batch / '01_source_ans/正文.txt').read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse((batch / '01_source_ans/answers.json').exists())

    def test_no_separate_answer_retains_appendix_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp); mf.save(batch, mf.new_manifest('x', batch, {}, {}))
            self.assertFalse(agent_prepare.prepare_answers(batch)['prepared'])
            self.assertFalse((batch / '01_source_ans').exists())
