# -*- coding: utf-8 -*-
r"""OCR 版面 Markdown → 项目正文标记的确定性投影回归。

用户 2026-11-13 反馈「最新的任务识别出来在试卷审核页面都是各种代码」：PDF 单页
批次按硬规则 28 直接消费识别服务的 Markdown，`<div><img src=…></div>` 与
`$\underline{…}$` 原样进了正文、题干和原卷预览。这里锁定投影口径：

* `<img src>` → `[IMG 已落盘媒体名]`（图片字节复制到 `01_source/media`）；
* HTML 表格 → `[TABLE n]`（colspan/rowspan 只占位，不猜合并）；
* LaTeX 画线/文字包装 → `<u>…</u>` / 纯文字，真数学式原样保留；
* 原始 Markdown 仍完整留在 `原始正文.txt`（证据），只有 `正文.txt` 被投影。
"""
import json
import tempfile
import unittest
from pathlib import Path

from workflow import extract, manifest as mf, ocr_markup, preview


class ImageProjectionTest(unittest.TestCase):
    def test_image_tag_becomes_published_media_marker(self):
        text = ('<div style="text-align: center;"><img src="imgs/img_in_image_box_52_617_158_734.jpg"'
                ' alt="Image" width="9%" /></div>')
        names = {'imgs/img_in_image_box_52_617_158_734.jpg': 'pdf-page-1-image-1.jpg'}
        projected = ocr_markup.normalize_page(text, names)
        self.assertEqual(projected.strip(), '[IMG pdf-page-1-image-1.jpg]')
        self.assertNotIn('<div', projected)
        self.assertNotIn('<img', projected)
        self.assertEqual(ocr_markup.unresolved_images(text, names), [])

    def test_stale_src_is_kept_and_reported_instead_of_dropped(self):
        text = '<img src="imgs/missing.jpg" />'
        projected = ocr_markup.normalize_page(text, {})
        self.assertIn('missing.jpg', projected)  # 没有证据就不改内容
        self.assertEqual(ocr_markup.unresolved_images(text, {}), ['imgs/missing.jpg'])

    def test_image_inside_table_cell_keeps_cell_position(self):
        text = ('<table border=1><tr><td><img src="imgs/a.jpg" />19°C~27°C</td>'
                '<td>晴天</td></tr></table>')
        projected = ocr_markup.normalize_page(text, {'imgs/a.jpg': 'pdf-page-1-image-2.jpg'})
        self.assertIn('[TABLE 1]', projected)
        self.assertIn('[IMG pdf-page-1-image-2.jpg] 19°C~27°C | 晴天', projected)


class LatexProjectionTest(unittest.TestCase):
    def test_inline_underline_becomes_u_markup(self):
        text = '$ \\underline{Wake} $ up,little $ \\underline{Kate} $!'
        self.assertEqual(ocr_markup.normalize_page(text, {}),
                         '<u>Wake</u> up,little <u>Kate</u>!')

    def test_nested_text_wrapper_is_restored(self):
        text = '$\\underline{\\text{late}}$'
        self.assertEqual(ocr_markup.normalize_page(text, {}), '<u>late</u>')

    def test_real_math_is_left_untouched(self):
        text = '音标 $/eɪ/ke$ 与 $x^{2}$'
        # 纯文字/音标段去掉 $ 包装；带上下标的真数学式原样保留
        self.assertEqual(ocr_markup.normalize_page(text, {}), '音标 /eɪ/ke 与 $x^{2}$')


class TableProjectionTest(unittest.TestCase):
    def test_colspan_is_placed_as_empty_grid_cells(self):
        text = ('<table><tr><td colspan="2">标题</td></tr>'
                '<tr><td>a</td><td>b</td></tr></table>')
        blocks = ocr_markup.normalize_page(text, {})
        self.assertIn('[TABLE 1]', blocks)
        self.assertIn('标题 | \na | b', blocks)
        self.assertIn('[/TABLE]', blocks)

    def test_rowspan_reserves_the_cells_below(self):
        text = ('<table><tr><td rowspan="2">合并</td><td>a</td></tr>'
                '<tr><td>b</td></tr></table>')
        blocks = ocr_markup.normalize_page(text, {})
        self.assertIn('合并 | a', blocks)
        self.assertIn(' | b', blocks)

    def test_existing_table_numbers_are_not_reused(self):
        text = '[TABLE 3]\na | b\n[/TABLE]\n<table><tr><td>c</td></tr></table>'
        blocks = ocr_markup.normalize_page(text, {})
        self.assertIn('[TABLE 4]', blocks)


class StructureMarkerTest(unittest.TestCase):
    def test_headings_and_bold_become_plain_text(self):
        text = '# 第二课时 Pronunciation\n## A 基础巩固\n**注意**：听音'
        self.assertEqual(ocr_markup.normalize_page(text, {}),
                         '第二课时 Pronunciation\nA 基础巩固\n注意：听音')


class DocumentProjectionTest(unittest.TestCase):
    def test_pages_use_their_own_image_maps(self):
        text = ('[PAGE 1]\n<img src="imgs/a.jpg" />\n\n[PAGE 2]\n<img src="imgs/b.jpg" />')
        projected = ocr_markup.normalize_document(text, {
            1: {'imgs/a.jpg': 'pdf-page-1-image-1.jpg'},
            2: {'imgs/b.jpg': 'pdf-page-2-image-1.jpg'},
        })
        self.assertIn('[IMG pdf-page-1-image-1.jpg]', projected)
        self.assertIn('[IMG pdf-page-2-image-1.jpg]', projected)

    def test_document_without_page_markers_uses_the_merged_map(self):
        text = '<img src="imgs/a.jpg" />'
        projected = ocr_markup.normalize_document(
            text, {1: {'imgs/a.jpg': 'pdf-page-1-image-1.jpg'}})
        self.assertEqual(projected.strip(), '[IMG pdf-page-1-image-1.jpg]')


class PublishOcrBatchTest(unittest.TestCase):
    """端到端：OCR 页图落盘 + 正文/结构/预览都拿到 [IMG 媒体名]（硬规则 14）。"""

    RAW = '\n'.join([
        '[PAGE 1]',
        '# 第二课时 Pronunciation',
        '### Ⅱ. 根据图片及所给音标写出下列单词所缺的字母',
        '',
        '<div style="text-align: center;"><img src="imgs/img_in_image_box_52_617_158_734.jpg"'
        ' alt="Image" width="9%" /></div>',
        '',
        '1. $ \\underline{Wake} $ up,little $ \\underline{Kate} $!',
        '',
        '<table border=1><tr><td>天气</td><td>合肥</td></tr></table>',
    ])

    def _batch(self, root: Path):
        batch = root / 'batch'
        source = batch / 'attachments' / 'source.pdf'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'%PDF-1.4 pronunciation page')
        digest = mf.sha256_file(source)
        ocr_dir = Path('01_source') / 'pdf-ocr' / 'hash-result'
        image = batch / ocr_dir / 'media' / 'page-0001-images-001.jpg'
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b'\xff\xd8\xff\xe0jpeg-bytes')
        result = {
            'version': 'composite-extract-1', 'source': str(source), 'source_sha256': digest,
            'text': self.RAW, 'image_dir': str(ocr_dir),
            'pages': [{'page': 1, 'method': 'ocr', 'text': self.RAW,
                       'images': {'imgs/img_in_image_box_52_617_158_734.jpg':
                                  'media/page-0001-images-001.jpg'}}],
            'warnings': [],
        }
        return batch, source, digest, result

    def test_publish_projects_body_and_keeps_raw_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch, source, digest, result = self._batch(Path(tmp))
            evidence = batch / '01_source' / 'extract-proof'
            evidence.mkdir(parents=True, exist_ok=True)
            extract._publish_analysis(source, batch / '01_source', digest, result, 'extract-proof')
            body = (batch / '01_source' / '正文.txt').read_text(encoding='utf-8')
            raw = (batch / '01_source' / '原始正文.txt').read_text(encoding='utf-8')
            # 证据仍是识别服务原文，正文是投影结果
            self.assertIn('<img src="imgs/img_in_image_box_52_617_158_734.jpg"', raw)
            self.assertNotIn('<img', body)
            self.assertNotIn('<div', body)
            self.assertNotIn('\\underline', body)
            self.assertNotIn('<table', body)
            self.assertIn('[IMG pdf-page-1-image-1.jpg]', body)
            self.assertIn('<u>Wake</u> up,little <u>Kate</u>!', body)
            self.assertIn('[TABLE 1]', body)
            # 图片已按同一套命名落盘到 01_source/media（预览/上传都从这里取）
            media = batch / '01_source' / 'media' / 'pdf-page-1-image-1.jpg'
            self.assertTrue(media.is_file())
            self.assertEqual(media.read_bytes(), b'\xff\xd8\xff\xe0jpeg-bytes')
            structure = json.loads((batch / '01_source' / 'structure.json').read_text(encoding='utf-8'))
            self.assertFalse([i for i in structure['unresolved']
                              if i.get('code') == 'OCR_IMAGE_UNRESOLVED'])
            slices = preview.slice_range(body.splitlines(), [1])
            text = '\n'.join(line if isinstance(line, str) else json.dumps(line, ensure_ascii=False)
                             for line in slices)
            self.assertIn('[IMG pdf-page-1-image-1.jpg]', text)

    def test_missing_image_bytes_keep_marker_and_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            batch, source, digest, result = self._batch(Path(tmp))
            (batch / '01_source' / 'pdf-ocr' / 'hash-result' / 'media'
             / 'page-0001-images-001.jpg').unlink()
            evidence = batch / '01_source' / 'extract-proof'
            evidence.mkdir(parents=True, exist_ok=True)
            extract._publish_analysis(source, batch / '01_source', digest, result, 'extract-proof')
            structure = json.loads((batch / '01_source' / 'structure.json').read_text(encoding='utf-8'))
            codes = [i.get('code') for i in structure['unresolved']]
            self.assertIn('OCR_IMAGE_UNRESOLVED', codes)
            # 没有证据就不删原标记，留待人工/视觉核对
            body = (batch / '01_source' / '正文.txt').read_text(encoding='utf-8')
            self.assertIn('img_in_image_box_52_617_158_734.jpg', body)


    def test_report_binds_images_when_result_lacks_image_dir(self):
        # 真实批次正是这样失败的：_pdf 组装的 result 没有 image_dir，必须按
        # source_sha256 回查 01_source/pdf-ocr.json 的 result_dir 才能落盘。
        with tempfile.TemporaryDirectory() as tmp:
            batch, source, digest, result = self._batch(Path(tmp))
            result.pop('image_dir', None)
            report = {'sources': [{'path': str(source), 'sha256': digest,
                                   'status': 'completed',
                                   'result_dir': '01_source/pdf-ocr/hash-result'}]}
            (batch / '01_source' / 'pdf-ocr.json').write_text(
                json.dumps(report, ensure_ascii=False), encoding='utf-8')
            evidence = batch / '01_source' / 'extract-proof'
            evidence.mkdir(parents=True, exist_ok=True)
            extract._publish_analysis(source, batch / '01_source', digest, result, 'extract-proof')
            body = (batch / '01_source' / '正文.txt').read_text(encoding='utf-8')
            self.assertIn('[IMG pdf-page-1-image-1.jpg]', body)
            self.assertNotIn('<img', body)
            self.assertTrue((batch / '01_source' / 'media' / 'pdf-page-1-image-1.jpg').is_file())


    def test_artifacts_attempt_dir_locates_nested_service_media(self):
        # 识别服务把页图放在 <acquisition>/paddle-ocr-xxx/media/ 下，result_dir
        # 本身没有 media/；必须按 result.artifacts.attempt_dir 才能找到。
        with tempfile.TemporaryDirectory() as tmp:
            batch, source, digest, result = self._batch(Path(tmp))
            nested = (batch / '01_source' / 'pdf-ocr' / 'hash-result' / 'paddle-ocr-abc')
            nested_media = nested / 'media'
            nested_media.mkdir(parents=True, exist_ok=True)
            (nested_media / 'page-0001-images-001.jpg').write_bytes(b'\xff\xd8nested')
            result.pop('image_dir', None)
            result['artifacts'] = {'attempt_dir': str(nested)}
            evidence = batch / '01_source' / 'extract-proof'
            evidence.mkdir(parents=True, exist_ok=True)
            extract._publish_analysis(source, batch / '01_source', digest, result, 'extract-proof')
            body = (batch / '01_source' / '正文.txt').read_text(encoding='utf-8')
            self.assertIn('[IMG pdf-page-1-image-1.jpg]', body)
            self.assertNotIn('<img', body)
            published = batch / '01_source' / 'media' / 'pdf-page-1-image-1.jpg'
            self.assertEqual(published.read_bytes(), b'\xff\xd8nested')


if __name__ == '__main__':
    unittest.main()
