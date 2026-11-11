"""Missing paper/source labels must not hold up extraction, tagging or review."""
import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile
from xml.sax.saxutils import escape

from workflow import agent_tasks, manifest, metadata_policy, runner, structure_gate
from workbench import server, taxonomy

ROOT = Path(__file__).resolve().parent.parent
BODY = ("Ⅰ.单项填空(共1小题)\n"
        "1. Choose the correct answer: She ____ a book every day after school.\n"
        "A. reads B. read C. reading D. to read")


class MetadataPlaceholderTests(unittest.TestCase):
    def test_defaults_do_not_invent_year_or_source_evidence(self):
        meta = metadata_policy.with_placeholders({})
        self.assertEqual(meta, {"source": "未提供", "source_type": "未提供", "paper_title": "未提供"})
        self.assertNotIn("year", meta)
        self.assertNotIn("evidence", meta)
        self.assertEqual(metadata_policy.with_placeholders(meta), meta)

    def test_identified_values_and_conflicts_are_preserved(self):
        before = {"source": "2024年安徽省中考", "paper_title": "2024年安徽省中考", "year": 2024,
                  "source_type": "中考真题", "region": "安徽省", "conflicts": ["证据冲突"]}
        self.assertEqual(metadata_policy.with_placeholders(before), before)

    def make_batch(self, root, *, extra_issues=()):
        batch = root / 'batch'
        source = root / 'source.txt'
        source.write_text(BODY, encoding='utf-8')
        (batch / '01_source').mkdir(parents=True)
        (batch / 'workflow').mkdir()
        (batch / '01_source/正文.txt').write_text(BODY, encoding='utf-8')
        (batch / '01_source/structure.json').write_text(json.dumps({
            'sections': [{'type': '单项填空', 'questions': [1], 'declared_count': 1}],
            'unresolved': list(extra_issues),
        }, ensure_ascii=False), encoding='utf-8')
        (batch / 'workflow/manifest.json').write_text(json.dumps({'inputs': {'source': {'path': str(source)}}}))
        return batch

    def test_missing_source_is_warning_and_old_missing_issue_does_not_block(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = self.make_batch(Path(temp), extra_issues=[
                {'code': 'SOURCE_METADATA_MISSING', 'message': '旧版缺来源阻断'}])
            report = structure_gate.run_gate(batch)
            self.assertTrue(report['passed'], report['blockers'])
            self.assertEqual(report['meta']['source_type'], '未提供')
            self.assertEqual(report['checks']['source_metadata'], 'placeholder')
            self.assertEqual(report['blockers'], [])
            self.assertTrue(report['metadata_missing'])
            self.assertTrue(all(not w['blocking'] for w in report['warnings']))
            confirmed = json.loads((batch / '01_source/structure.confirmed.json').read_text())
            self.assertEqual(confirmed['meta']['source'], '未提供')
            self.assertEqual(len(confirmed['units']), 1)

    def test_missing_metadata_does_not_clear_real_structure_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = self.make_batch(Path(temp), extra_issues=[
                {'code': 'SOURCE_METADATA_CONFLICT', 'message': '两个标题年份冲突'}])
            report = structure_gate.run_gate(batch)
            self.assertFalse(report['passed'])
            self.assertIn('SOURCE_METADATA_CONFLICT', {b['code'] for b in report['blockers']})

    def test_headerless_docx_reaches_types_task_through_official_runner(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / '未命名试卷.docx'
            body = ''.join('<w:p><w:r><w:t>' + escape(line) + '</w:t></w:r></w:p>' for line in BODY.splitlines())
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('word/document.xml',
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                    + body + '</w:body></w:document>')
            batch = root / 'batch'
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(['init', '--batch-dir', str(batch), '--source', str(source)]), 0)
                self.assertEqual(runner.main(['run', '--batch-dir', str(batch), '--until', 'structure_gate']), 0)
            state = manifest.load(batch)
            self.assertEqual(state['stages']['structure_gate']['status'], 'completed')
            # 正文没有卷头时，试卷名就是原卷 Word 文件名，不再退成占位。
            confirmed = json.loads((batch / '01_source/structure.confirmed.json').read_text())
            self.assertEqual(confirmed['meta']['source'], '未命名试卷')
            self.assertEqual(state['meta']['source'], '未命名试卷')
            task = agent_tasks.build_task(batch, 'types')
            self.assertEqual(task['preconditions'], [])
            self.assertIn('未提供', task['prompt'])

    def test_category_placeholder_is_locally_valid_but_not_an_invented_catalog_label(self):
        self.assertEqual(taxonomy.check_labels({'categories': ['未提供']}), [])
        self.assertTrue(taxonomy.check_labels({'categories': ['自造分类']}))
        spec = importlib.util.spec_from_file_location('metadata_assign_test', ROOT / 'scripts/05-meta/assign_meta.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module._categories({}, {'categories': {}}), ['未提供'])
        self.assertEqual(module._categories({'question_type': '单项填空'}, {'categories': {
            '必考题': {'when_types': ['单项填空']}}}), ['必考题'])

    def test_group_child_difficulty_aliases_stay_equal(self):
        spec = importlib.util.spec_from_file_location('metadata_alias_test', ROOT / 'scripts/05-meta/assign_meta.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            source, out = Path(temp) / 'batch.json', Path(temp) / 'meta.json'
            source.write_text(json.dumps({'items': [{
                'unit_id': 'g', 'unit_kind': 'group', 'difficulty': '适中',
                'filterMetadata': {'subtypes': []},
                'subquestions': [
                    {'number': 1, 'difficulty': '适中', 'filterMetadata': {'difficulty': '容易'}},
                    {'number': 2, 'filterMetadata': {'difficulty': '容易'}},
                    {'number': 3, 'filterMetadata': {}}]}]}, ensure_ascii=False), encoding='utf-8')
            argv = sys.argv
            sys.argv = ['assign_meta', '--batch', str(source), '--out', str(out)]
            try:
                self.assertEqual(module.main(), 0)
            finally:
                sys.argv = argv
            data = json.loads(out.read_text(encoding='utf-8'))
        children = data['items'][0]['subquestions']
        self.assertEqual((children[0]['difficulty'], children[0]['filterMetadata']['difficulty']), ('适中', '适中'))
        self.assertEqual(children[1]['filterMetadata']['difficulty'], '容易')
        self.assertEqual(children[2]['filterMetadata']['difficulty'], '适中')

    def test_review_prompt_does_not_request_manual_confirmation_for_missing_metadata(self):
        from workflow import review_rounds
        for round_id in ('labels', 'sample'):
            prompt = review_rounds._round_prompt(round_id, [], '{}')
            self.assertIn('使用「未提供」是允许的占位', prompt)
            self.assertIn('不得仅因此返回 error/uncertain', prompt)

    def test_workbench_shows_placeholders_instead_of_a_guessed_paper_name(self):
        with tempfile.TemporaryDirectory() as temp:
            result = server.paper_info(Path(temp), {}, {})
            self.assertEqual(result['title'], '未提供')
            self.assertEqual(result['source'], '未提供')
            self.assertEqual(result['source_type'], '未提供')
            result = server.paper_info(Path(temp), {'meta': {'source': '未提供', 'title': '用户提供的试卷名'}}, {})
            self.assertEqual(result['title'], '用户提供的试卷名')


if __name__ == '__main__':
    unittest.main()
