"""Read-only operations candidate evidence stays scoped and bounded."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import operations as op


class CandidateEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        (self.folder / 'workflow').mkdir()
        self.put('workflow/operations.json', {'identity': op._identity(self.folder),
                                              'incident': {'stage': 'explanations'}})

    def put(self, relative, value):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
        return relative

    def test_candidate_and_rejection_evidence_are_readable_but_not_accepted(self):
        paths = [
            'workflow/agent-runs/explanations-run/response.json',
            'workflow/agent-runs/explanations-run/repair-progress.json',
            'workflow/agent-runs/explanations-run/round-01-part-0000/response.json',
            'workflow/agent-runs/explanations-parts/abc.failure.json',
        ]
        for relative in paths:
            self.put(relative, {'validation_error': 'rejected candidate'})
        before = {p: (self.folder / p).read_bytes() for p in paths}
        rows = op._candidate_evidence(self.folder, 'explanations')
        self.assertEqual({r['path'] for r in rows}, set(paths))
        self.assertTrue(all(r['accepted'] is False for r in rows))
        for relative in paths:
            self.assertIn('rejected candidate', op._read_evidence(self.folder, {'path': relative})['text'])
        self.assertEqual(before, {p: (self.folder / p).read_bytes() for p in paths})

    def test_unindexed_inputs_other_stages_and_traversal_remain_forbidden(self):
        for relative in ['workflow/agent-runs/answers-run/response.json',
                         'workflow/agent-runs/explanations-run/input.txt',
                         'workflow/agent-runs/explanations-run/secrets.json']:
            self.put(relative, {'secret': 'not evidence'})
            with self.assertRaisesRegex(ValueError, 'evidence_not_allowlisted'):
                op._read_evidence(self.folder, {'path': relative})
        with self.assertRaisesRegex(ValueError, 'unsafe_evidence_path'):
            op._read_evidence(self.folder, {'path': '../outside.json'})

    def test_listing_is_bounded_and_non_content_stage_has_no_candidates(self):
        for index in range(45):
            self.put('workflow/agent-runs/explanations-run/round-01-part-%04d/response.json' % index, {})
        self.assertEqual(len(op._candidate_evidence(self.folder, 'explanations')), 40)
        self.assertEqual(op._candidate_evidence(self.folder, 'validate'), [])

    def test_numbered_progress_uses_latest_bounded_report(self):
        old = self.put('workflow/agent-runs/explanations-run/repair-progress-00.json', {'round': 0})
        new = self.put('workflow/agent-runs/explanations-run/repair-progress-01.json', {'round': 1})
        import os
        os.utime(self.folder / old, (100, 100))
        os.utime(self.folder / new, (200, 200))
        rows = op._candidate_evidence(self.folder, 'explanations')
        self.assertEqual([r['path'] for r in rows], [new])
        self.assertIn('"round": 1', op._read_evidence(self.folder, {'path': new})['text'])

    def test_junction_cannot_expose_another_batch(self):
        outside = self.folder.parent / (self.folder.name + '-outside')
        outside.mkdir()
        self.addCleanup(lambda: __import__('shutil').rmtree(outside, ignore_errors=True))
        (outside / 'response.json').write_text(json.dumps({'leak': 'other batch'}), encoding='utf-8')
        runs = self.folder / 'workflow' / 'agent-runs'
        runs.mkdir(parents=True)
        if __import__('os').name != 'nt' or not hasattr(Path, 'is_junction'):
            self.skipTest('junction detection is Windows-only')
        import subprocess
        made = subprocess.run(['cmd', '/c', 'mklink', '/J',
                               str(runs / 'explanations-run'), str(outside)],
                              capture_output=True)
        if made.returncode != 0:
            self.skipTest('junction creation is unavailable')
        self.assertTrue((runs / 'explanations-run').is_junction())
        self.assertEqual(op._candidate_evidence(self.folder, 'explanations'), [])
        self.assertIsNone(op.supervisor._regular_file(
            self.folder, 'workflow/agent-runs/explanations-run/response.json'))

    def test_regular_file_cannot_escape_the_batch(self):
        self.put('workflow/agent-runs/explanations-run/response.json', {'safe': True})
        self.assertIsNotNone(op.supervisor._regular_file(
            self.folder, 'workflow/agent-runs/explanations-run/response.json'))
        self.assertIsNone(op.supervisor._regular_file(
            self.folder, 'workflow/agent-runs/../../outside.json'))

    def test_symlink_candidate_is_not_exposed(self):
        relative = self.put('workflow/agent-runs/explanations-run/response.json', {})
        original = Path.is_symlink
        def is_link(path):
            return path.name == 'response.json' or original(path)
        with patch.object(Path, 'is_symlink', is_link):
            self.assertEqual(op._candidate_evidence(self.folder, 'explanations'), [])
            with self.assertRaisesRegex(ValueError, 'evidence_not_allowlisted'):
                op._read_evidence(self.folder, {'path': relative})


if __name__ == '__main__':
    unittest.main()
