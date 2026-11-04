import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workflow import agent_prepare, extract, manifest as mf


class AgentPrepareTest(unittest.TestCase):
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

    def test_no_separate_answer_retains_appendix_route(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = Path(tmp); mf.save(batch, mf.new_manifest('x', batch, {}, {}))
            self.assertFalse(agent_prepare.prepare_answers(batch)['prepared'])
            self.assertFalse((batch / '01_source_ans').exists())
