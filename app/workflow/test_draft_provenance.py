"""Source-equivalent parser refresh must not rewrite an in-flight input."""
import copy
import hashlib
import json
import unittest
from unittest import mock

from workflow import agent_tasks, local_agent, question_draft
from workflow import test_question_draft as fixtures


class DraftProvenanceTest(unittest.TestCase):
    setUp = fixtures.QuestionDraftTest.setUp
    write = fixtures.QuestionDraftTest.write
    source = fixtures.QuestionDraftTest.source
    standalone = fixtures.QuestionDraftTest.standalone

    def setUp(self):
        fixtures.QuestionDraftTest.setUp(self)
        self.standalone()
        self.first = question_draft.ensure(self.root)
        self.path = self.root / question_draft.PATH
        self.bound = {question_draft.PATH: hashlib.sha256(self.path.read_bytes()).hexdigest()}
        self.original = (self.path.read_bytes(), self.path.stat().st_mtime_ns)
        self.hashes = question_draft._hashes

    def parser_refresh(self, root, media=()):
        hashes = self.hashes(root, media)
        hashes['parser:question_draft.py'] = 'a' * 64
        return hashes

    def test_parser_only_refresh_keeps_original_receipt_bytes_and_inflight_guard(self):
        with mock.patch.object(question_draft, '_hashes', side_effect=self.parser_refresh):
            for _ in range(3):
                self.assertEqual(question_draft.ensure(self.root), self.first)
                local_agent._check_input_hashes(self.root, self.bound)
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), self.original)

    def test_concurrent_stage_collects_do_not_refresh_equal_draft(self):
        with mock.patch.object(question_draft, '_hashes', side_effect=self.parser_refresh):
            for stage in ('answers', 'explanations', 'knowledge', 'types'):
                agent_tasks._collect(self.root, stage)
                local_agent._check_input_hashes(self.root, self.bound)
        self.assertEqual(self.path.read_bytes(), self.original[0])

    def test_real_source_and_optional_ocr_changes_keep_strict_guard(self):
        for relative, value in [('01_source/正文.txt', '1. Different. A. Yes B. No'),
                                ('01_source/pdf-ocr.json', {'pages': [{'text': 'reference'}]}),
                                ('01_source/structure.json', {'sections': [], 'groups': [], 'changed_evidence': True})]:
            with self.subTest(relative=relative):
                prior = self.path.read_bytes()
                bound = {question_draft.PATH: hashlib.sha256(prior).hexdigest()}
                self.write(relative, value)
                with mock.patch.object(question_draft, '_hashes', side_effect=self.parser_refresh):
                    question_draft.ensure(self.root)
                with self.assertRaises(local_agent.AgentError):
                    local_agent._check_input_hashes(self.root, bound)

    def test_media_byte_change_refreshes_draft_and_rejects_inflight(self):
        self.source('1. Choose [IMG one.png]. A. Yes B. No', [([1], 'standalone', '单项填空', 1, 1)])
        self.write('01_source/media/one.png', 'one')
        question_draft.ensure(self.root)
        bound = {question_draft.PATH: hashlib.sha256(self.path.read_bytes()).hexdigest()}
        self.write('01_source/media/one.png', 'two')
        with mock.patch.object(question_draft, '_hashes', side_effect=self.parser_refresh):
            question_draft.ensure(self.root)
        with self.assertRaises(local_agent.AgentError):
            local_agent._check_input_hashes(self.root, bound)

    def test_corrupt_receipt_and_missing_parser_key_cannot_be_retained(self):
        for mutation in ('digest', 'keys', 'object'):
            with self.subTest(mutation=mutation):
                old = copy.deepcopy(self.first)
                if mutation == 'digest':
                    old['content_sha256'] = '0' * 64
                else:
                    old.pop('content_sha256')
                    if mutation == 'keys':
                        old['input_hashes'].pop('parser:question_draft.py')
                    else:
                        old['items'][0]['stem'] = 'tampered'
                    old['content_sha256'] = question_draft._digest(old)
                self.write(question_draft.PATH, old)
                with mock.patch.object(question_draft, '_hashes', side_effect=self.parser_refresh):
                    result = question_draft.ensure(self.root)
                self.assertEqual(result['items'], self.first['items'])
                self.assertEqual(result['input_hashes']['parser:question_draft.py'], 'a' * 64)
                self.assertNotEqual(json.loads(self.path.read_text(encoding='utf-8')), old)

    def test_real_parser_output_change_is_not_treated_as_provenance_only(self):
        build = question_draft.build
        def changed(*args, **kwargs):
            result = build(*args, **kwargs)
            result['issues'].append({'code': 'NEW_SOURCE_FAILURE', 'unit_id': 'q-1'})
            return result
        with mock.patch.object(question_draft, '_hashes', side_effect=self.parser_refresh), \
                mock.patch.object(question_draft, 'build', side_effect=changed):
            result = question_draft.ensure(self.root)
        self.assertEqual(result['issues'][-1]['code'], 'NEW_SOURCE_FAILURE')
        with self.assertRaises(local_agent.AgentError):
            local_agent._check_input_hashes(self.root, self.bound)


if __name__ == '__main__':
    unittest.main()
