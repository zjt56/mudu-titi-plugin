# -*- coding: utf-8 -*-
"""版面图名不是题号 + 提取产物按留存正文重算（不重跑 OCR）的回归。"""
import json
import tempfile
import unittest
from pathlib import Path

from workflow import contracts, extract, manifest as mf, preview, structure_gate

# 一张 OCR 插图会同时出现在 HTML 与 Markdown 两种写法里，文件名自带坐标。
IMG_HTML = ('<div style="text-align: center;"><img src="imgs/'
            'img_in_image_box_52_617_158_734.jpg" alt="Image" width="9%" /></div>')
IMG_MARKDOWN = '[IMG imgs/img_in_image_box_325_793_380_888.jpg]'

OCR_SECTION = '\n'.join([
    '[PAGE 1]',
    '# 第二课时 Pronunciation',
    '## A 基础巩固',
    '### Ⅱ. 根据图片及所给音标写出下列单词所缺的字母',
    '',
    IMG_HTML,
    '',
    '1. _____',
    '',
    '2.',
    '',
    IMG_MARKDOWN,
    '',
    'c ___ /eɪ/ke',
    '',
    '3. _____',
])


class VisualMarkupNumbersTest(unittest.TestCase):
    def test_image_coordinates_are_not_question_numbers(self):
        structure, body, _ = extract.infer_structure(OCR_SECTION)
        self.assertEqual(structure['sections'][0]['questions'], [1, 2, 3])
        # 图片文件名里的坐标不能变成空位号
        self.assertEqual(extract.blank_numbers(IMG_HTML), [])
        self.assertEqual(extract.blank_numbers(IMG_MARKDOWN), [])
        self.assertEqual(preview._weak_numbers(IMG_HTML), [])
        self.assertEqual(preview._weak_numbers(IMG_MARKDOWN), [])
        # 未投影的原始标记不得变成题号（生产路径由 ocr_markup 先把图换成
        # [IMG 媒体名]，这里保底验证「即使漏投影也不会污染锚点」）
        self.assertIn('img_in_image_box_52_617_158_734.jpg', body)

    def test_image_hint_blank_still_a_number(self):
        # 「图注空」`N.[IMG image.jpeg](提示)` 是合法小题号，不能被清洗掉。
        line = '1.[IMG image.jpeg](tree)'
        self.assertEqual(extract.blank_numbers(line), [1])
        self.assertEqual(preview._weak_numbers(line), [1])

    def test_table_coordinates_do_not_leak_into_anchors(self):
        text = '\n'.join([
            'Ⅰ.单项填空(共2小题)',
            '1. A',
            '2. B',
            '<tr><td><img src="imgs/img_in_image_box_566_714_638_765.jpg" />19°C~27°C</td></tr>',
        ])
        structure, body, _ = extract.infer_structure(text)
        self.assertEqual(structure['sections'][0]['questions'], [1, 2])
        anchors = structure_gate._source_members(body.splitlines())
        self.assertNotIn(566, anchors)
        self.assertNotIn(714, anchors)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


class RefreshFromEvidenceTest(unittest.TestCase):
    BODY = '\n'.join([
        '# 第二课时 Pronunciation',
        '',
        '## A 基础巩固',
        '',
        '### I. 按照字母表顺序写出元音字母',
        '',
        '1. _____',
        '',
        '2. _____',
        '',
        '### Ⅱ. 根据图片写出单词所缺的字母',
        '',
        IMG_HTML,
        '',
        '1. _____',
        '',
        '2. _____',
    ])

    def _batch(self, root: Path) -> Path:
        batch = root / 'batch'
        source = batch / 'attachments' / 'source.pdf'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'%PDF-1.4 stale numbering rules')
        digest = mf.sha256_file(source)
        manifest = mf.new_manifest('wf-refresh', batch, {}, {})
        manifest['inputs'] = {'source': {'path': str(source), 'sha256': digest,
                                         'required': True, 'exists': True}}
        mf.save(batch, manifest)
        evidence = batch / '01_source' / 'extract-proof'
        evidence.mkdir(parents=True, exist_ok=True)
        _write_json(batch / '01_source' / 'extraction.json', {
            'version': extract.VERSION, 'source': str(source), 'source_sha256': digest,
            'evidence_dir': 'extract-proof', 'structure_status': 'candidate',
            'text': self.BODY, 'pages': [{'page': 1, 'method': 'ocr', 'text': self.BODY}],
            'warnings': [],
        })
        (batch / '01_source' / '原始正文.txt').write_text(self.BODY, encoding='utf-8')
        # 旧规则产物：图名坐标被收成题号
        _write_json(batch / '01_source' / 'structure.json',
                    {'sections': [{'source_line': 1, 'source_end_line': 14,
                                   'questions': [1, 2, 52, 158]}], 'unresolved': [], 'groups': []})
        return batch

    def test_refresh_rewrites_structure_without_touching_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = self._batch(Path(tmp))
            report = extract.refresh_from_evidence(batch)
            self.assertTrue(report['passed'])
            body = (batch / '01_source' / '正文.txt').read_text(encoding='utf-8')
            self.assertEqual(body.count('img_in_image_box_52_617_158_734.jpg'), 1)
            structure = json.loads((batch / '01_source' / 'structure.json').read_text(encoding='utf-8'))
            numbers = structure['sections'][-1]['questions']
            self.assertEqual(numbers, [1, 2])
            # 证据声明保持原样，未伪造新来源
            evidence = json.loads((batch / '01_source' / 'extraction.json').read_text(encoding='utf-8'))
            self.assertEqual(evidence['evidence_dir'], 'extract-proof')
            self.assertEqual(evidence['source_sha256'], mf.sha256_file(
                batch / 'attachments' / 'source.pdf'))

    def test_refresh_refuses_changed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = self._batch(Path(tmp))
            (batch / 'attachments' / 'source.pdf').write_bytes(b'%PDF-1.4 changed bytes')
            with self.assertRaises(ValueError) as ctx:
                extract.refresh_from_evidence(batch)
            self.assertIn('SHA-256', str(ctx.exception))

    def test_refresh_refuses_after_downstream_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = self._batch(Path(tmp))
            manifest = mf.load(batch)
            manifest['stages']['structure_gate']['status'] = mf.COMPLETED
            mf.save(batch, manifest)
            with self.assertRaises(ValueError) as ctx:
                extract.refresh_from_evidence(batch)
            self.assertIn('structure_gate', str(ctx.exception))

    def test_refresh_allows_optional_stage_skipped(self):
        # 不含听力的卷 audio=skipped 不算下游交付，可以重算。
        with tempfile.TemporaryDirectory() as tmp:
            batch = self._batch(Path(tmp))
            manifest = mf.load(batch)
            manifest['stages']['audio']['status'] = 'skipped'
            mf.save(batch, manifest)
            report = extract.refresh_from_evidence(batch)
            self.assertTrue(report['passed'])
            self.assertEqual(json.loads((batch / '01_source' / 'structure.json').read_text(
                encoding='utf-8'))['sections'][-1]['questions'], [1, 2])

    def test_refresh_refuses_body_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = self._batch(Path(tmp))
            (batch / '01_source' / '原始正文.txt').write_text('### I. 别的正文\n1. x\n', encoding='utf-8')
            with self.assertRaises(ValueError) as ctx:
                extract.refresh_from_evidence(batch)
            self.assertIn('原始正文', str(ctx.exception))

    def test_refresh_refuses_missing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch = self._batch(Path(tmp))
            (batch / '01_source' / 'extraction.json').unlink()
            with self.assertRaises(ValueError):
                extract.refresh_from_evidence(batch)


if __name__ == '__main__':
    unittest.main()
