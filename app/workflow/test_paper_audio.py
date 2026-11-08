"""Paper naming and listening audio attachments in the review workbench."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from workbench import server


def _make_batch(root, name, source_title=None):
    batch = root / name
    src = batch / '01_source'
    src.mkdir(parents=True)
    (src / '正文.txt').write_text('2022**年安徽省初中学业水平考试**\nⅠ.单项填空\n1.A. a\n', encoding='utf-8')
    (batch / '06_batch').mkdir()
    (batch / '06_batch/batch.json').write_text(json.dumps({'source': source_title, 'items': [{
        'id': 'q-1', 'unit_kind': 'standalone', 'number': 1, 'numbers': [1],
        'question_type': '单项填空', 'stem': '1.A. a', 'subquestions': [], 'knowledge': []}]}), encoding='utf-8')
    (batch / '06_batch/batch_meta.json').write_bytes((batch / '06_batch/batch.json').read_bytes())
    return batch


class PaperNamingTest(unittest.TestCase):
    def test_title_falls_back_to_source_header_not_folder_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_batch(root, '2026')
            with mock.patch.object(server, 'WORKFLOWS', root):
                info = server.paper_info(root / '2026', {}, {})
            self.assertEqual(info['title'], '2022年安徽省初中学业水平考试')
            self.assertNotEqual(info['title'], '2026')
            self.assertEqual(info['year'], 2022)


class AudioAttachmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.batch = _make_batch(self.root, 'paper')
        audio = self.batch / '02_audio'; audio.mkdir()
        (audio / 'q-1.mp3').write_bytes(b'ID3fakeaudio')
        (audio / 'group-6-7-8-9-10.mp3').write_bytes(b'ID3group')
        # The workbench only exposes cuts that carry a current type/unit binding.
        import hashlib
        (self.batch / '01_source' / 'structure.confirmed.json').write_text(json.dumps({
            'units': [{'unit_id': 'q-1', 'unit_kind': 'standalone', 'numbers': [1],
                       'type': '听力短对话理解'}]}), encoding='utf-8')
        (self.batch / '03_types').mkdir(exist_ok=True)
        (self.batch / '03_types' / 'types.json').write_text(json.dumps({'items': [
            {'unit_id': 'q-1', 'type': '听力短对话理解', 'subtypes': ['选文字'], 'words': 3,
             'contentComplete': True, 'evidence': ['L2']}]}), encoding='utf-8')
        from audio.binding import listening_context
        binding, _listening = listening_context(self.batch)
        def _sha(name):
            return hashlib.sha256((audio / name).read_bytes()).hexdigest()
        (audio / 'audio_map.json').write_text(json.dumps({
            'unit_binding': binding,
            'items': [
                {'unit_id': 'q-1', 'file': '02_audio/q-1.mp3', 'start': 21.9, 'end': 84.0}]}),
            encoding='utf-8')
        (audio / 'segment-evidence.json').write_text(json.dumps({
            'generator': 'audio.segment/multi-v1', 'unit_binding': binding,
            'items': [
                {'unit_id': 'q-1', 'file': '02_audio/q-1.mp3', 'start': 21.9, 'end': 84.0,
                 'file_sha256': _sha('q-1.mp3'), 'evidence': ['x']}]}), encoding='utf-8')

    def test_single_unit_audio_attached_with_playable_url(self):
        with mock.patch.object(server, 'WORKFLOWS', self.root):
            data = server.detail('paper', 'q-1')
        audio = [b for b in data['preview_blocks'] if b['type'] == 'audio']
        self.assertEqual(len(audio), 1)
        self.assertTrue(audio[0]['url'].endswith('/audio/q-1.mp3'))
        self.assertEqual(audio[0]['start'], 21.9)
        self.assertEqual(data['audio'][0]['name'], 'q-1.mp3')

    def test_split_group_reuses_parent_audio_attachment(self):
        # Split group-6-7 uses the parent merged audio segment as attachment.
        import hashlib
        from audio.binding import listening_context
        (self.batch / '01_source' / 'structure.confirmed.json').write_text(json.dumps({
            'units': [{'unit_id': 'group-6-7-8-9-10', 'unit_kind': 'group',
                       'numbers': [6, 7, 8, 9, 10], 'type': '听力长对话理解'}]}), encoding='utf-8')
        (self.batch / '03_types' / 'types.json').write_text(json.dumps({'items': [
            {'unit_id': 'group-6-7-8-9-10', 'type': '听力长对话理解', 'subtypes': ['选择2题'],
             'words': 9, 'contentComplete': True, 'evidence': ['L2']}]}), encoding='utf-8')
        binding, _ = listening_context(self.batch)
        audio = self.batch / '02_audio'
        (audio / 'audio_map.json').write_text(json.dumps({'unit_binding': binding, 'items': [
            {'unit_id': 'group-6-7-8-9-10', 'file': '02_audio/group-6-7-8-9-10.mp3',
             'start': 0.0, 'end': 10.0}]}), encoding='utf-8')
        (audio / 'segment-evidence.json').write_text(json.dumps({
            'generator': 'audio.segment/multi-v1', 'unit_binding': binding, 'items': [
                {'unit_id': 'group-6-7-8-9-10', 'file': '02_audio/group-6-7-8-9-10.mp3',
                 'start': 0.0, 'end': 10.0, 'evidence': ['x'],
                 'file_sha256': hashlib.sha256((audio / 'group-6-7-8-9-10.mp3').read_bytes()).hexdigest()}]}),
            encoding='utf-8')
        rows = [{'unit_id': 'group-6-7', 'source_parent_id': 'group-6-7-8-9-10',
                 'numbers': [6, 7], 'number': '6', 'label': '6–7'}]
        with mock.patch.object(server, 'merged_rows', return_value=rows), \
             mock.patch.object(server, 'WORKFLOWS', self.root), \
             mock.patch.object(server, 'source_slice', return_value=['6.First', '7.Second']):
            data = server.detail('paper', 'group-6-7')
        audio = [b for b in data['preview_blocks'] if b['type'] == 'audio']
        self.assertTrue(audio and audio[0]['name'] == 'group-6-7-8-9-10.mp3')

    def test_missing_audio_file_is_not_fabricated(self):
        (self.batch / '02_audio/q-1.mp3').unlink()
        self.assertEqual(server.audio_blocks(self.batch, 'paper', ['q-1']), [])


if __name__ == '__main__':
    unittest.main()
