"""处理意见与上传的回归测试：不能因为改状态把问题字段写丢。"""
import json
import pathlib
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from workbench import server


def make_batch(root: pathlib.Path) -> pathlib.Path:
    folder = root / "FIX-001"
    (folder / "07_review").mkdir(parents=True)
    (folder / "workflow").mkdir(parents=True)
    queue = {
        "queue_version": "1.0",
        "items": [
            {
                "finding_id": "WF-0001",
                "unit_id": "Q005",
                "field": "knowledge",
                "severity": "major",
                "blocking": True,
                "status": "open",
                "question": "主题由答案反推，题干无线索。",
            }
        ],
    }
    (folder / "07_review" / "manual-review-queue.json").write_text(
        json.dumps(queue, ensure_ascii=False), encoding="utf-8"
    )
    return folder


class DecisionMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = make_batch(pathlib.Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _queue(self):
        return json.loads((self.folder / "07_review" / "manual-review-queue.json").read_text(encoding="utf-8"))

    def test_partial_item_keeps_original_fields(self):
        item = server.apply_decision(self.folder, {"finding_id": "WF-0001", "status": "fixed"}, "tester")
        self.assertEqual(item["status"], "fixed")
        self.assertEqual(item["decided_by"], "tester")
        stored = self._queue()["items"][0]
        self.assertEqual(stored["unit_id"], "Q005")
        self.assertEqual(stored["field"], "knowledge")
        self.assertEqual(stored["severity"], "major")
        self.assertTrue(stored["question"])
        self.assertEqual(len(self._queue()["items"]), 1)

    def test_decision_syncs_same_finding_to_gate_files(self):
        path = self.folder / '07_review/metadata-review.json'
        path.write_text(json.dumps({'findings': [{'finding_id': 'WF-0001', 'status': 'needs_review',
            'reason': '原问题', 'blocking': True}]}), encoding='utf-8')
        server.apply_decision(self.folder, {'finding_id': 'WF-0001', 'status': 'fixed',
            'resolution_note': '已据原卷修正并验证'}, 'tester')
        finding = json.loads(path.read_text())['findings'][0]
        self.assertEqual(finding['status'], 'fixed')
        self.assertEqual(finding['resolution_note'], '已据原卷修正并验证')
        self.assertEqual(finding['decided_by'], 'tester')
        self.assertTrue(finding['blocking'])

    def test_unattributed_queue_status_does_not_resolve_gate_finding(self):
        path = self.folder / '07_review/metadata-review.json'
        path.write_text(json.dumps({'findings': [{'finding_id': 'WF-0001', 'status': 'needs_review'}]}))
        server.sync_review_decisions(self.folder, {'items': [{'finding_id': 'WF-0001', 'status': 'fixed'}]})
        self.assertEqual(json.loads(path.read_text())['findings'][0]['status'], 'needs_review')

    def test_note_is_stored_and_backup_written(self):
        server.apply_decision(self.folder, {"finding_id": "WF-0001", "status": "accepted", "note": "已改知识点"})
        self.assertEqual(self._queue()["items"][0]["note"], "已改知识点")
        self.assertTrue((self.folder / "07_review" / "manual-review-queue.json.bak").exists())
        log = (self.folder / "workflow" / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(log), 1)
        self.assertIn("Q005", log[0])

    def test_unknown_finding_is_rejected(self):
        with self.assertRaises(ValueError):
            server.apply_decision(self.folder, {"finding_id": "WF-9999", "status": "fixed"})

    def test_bad_status_is_rejected(self):
        with self.assertRaises(ValueError):
            server.apply_decision(self.folder, {"finding_id": "WF-0001", "status": "done"})


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self._inbox = server.INBOX
        self._workflows = server.WORKFLOWS
        self._advance = server.start_advance
        server.INBOX = root / "inbox"
        server.WORKFLOWS = root / "workflows"
        server.INBOX.mkdir()
        server.WORKFLOWS.mkdir()
        self.started = []
        server.start_advance = lambda name: self.started.append(name) or {"started": True, "running": True}
        self.root = root

    def tearDown(self):
        server.INBOX = self._inbox
        server.WORKFLOWS = self._workflows
        server.start_advance = self._advance
        self.tmp.cleanup()

    def _docx(self, name: str) -> dict:
        path = self.root / name
        with zipfile.ZipFile(path, "w") as zf:
            # Include the filename in the payload so different fixture docs are
            # not mistaken for the same source by upload deduplication.
            zf.writestr("word/document.xml",
                        f"<w:document><w:body><w:p><w:r><w:t>{name}</w:t></w:r></w:p></w:body></w:document>")
        return {"field": "files", "filename": name, "data": path.read_bytes()}

    def test_upload_creates_batch_and_starts_flow(self):
        result = server.handle_upload(
            {"title": "2026年安徽省中考英语", "year": "2026", "region": "安徽", "subject": "english"},
            [self._docx("2026年安徽省中考英语.docx")],
        )
        self.assertEqual(result["name"], "2026年安徽省中考英语")
        self.assertEqual(result["paper"]["title"], "2026年安徽省中考英语")
        attachments = server.WORKFLOWS / result["name"] / "attachments"
        self.assertTrue((attachments / "source.docx").exists())
        self.assertTrue((attachments / "batch.json").exists())
        self.assertEqual(self.started, [result["name"]])
        # 试卷名不填时用地区+年份兜底，重名自动加后缀
        again = server.handle_upload({"title": "2026年安徽省中考英语"}, [self._docx("A.docx")])
        self.assertNotEqual(again["name"], result["name"])

    def test_identical_upload_reuses_existing_batch_instead_of_creating_copy(self):
        document = self._docx("same-paper.docx")
        first = server.handle_upload({"title": "2016年安徽省中考英语"}, [document])
        second = server.handle_upload({"title": "2016年安徽省中考英语"}, [document])
        self.assertEqual(second["name"], first["name"])
        self.assertTrue(second.get("duplicate"))
        self.assertTrue(second.get("reused"))
        self.assertEqual(self.started, [first["name"]])
        self.assertEqual(len(list(server.WORKFLOWS.iterdir())), 1)

    def test_dedupe_requires_present_unchanged_local_inputs(self):
        document = self._docx("same-paper.docx")
        first = server.handle_upload({}, [document])
        source = server.WORKFLOWS / first["name"] / "attachments/source.docx"
        source.write_bytes(b"changed since original upload")
        second = server.handle_upload({}, [document])
        self.assertNotEqual(first["name"], second["name"])
        (server.WORKFLOWS / second["name"] / "attachments/source.docx").unlink()
        third = server.handle_upload({}, [document])
        self.assertNotIn(third["name"], (first["name"], second["name"]))
        self.assertEqual(len(self.started), 3)

    def test_dedupe_skips_malformed_and_symlinked_ledgers_and_inputs(self):
        document = self._docx("same-paper.docx")
        for index, state in enumerate(([], {"inputs": []}, {"inputs": {"source": "bad"}},
                                      {"inputs": {"source": {"sha256": server._upload_part_sha(document)}}})):
            server.save_json(server.WORKFLOWS / ("broken-" + str(index)) / "workflow/manifest.json", state)
        first = server.handle_upload({}, [document])
        batch = server.WORKFLOWS / first["name"]
        local = batch / "attachments/source.docx"
        outside = self.root / "external.docx"
        outside.write_bytes(document["data"])
        local.unlink()
        local.symlink_to(outside)
        second = server.handle_upload({}, [document])
        self.assertNotEqual(first["name"], second["name"])
        self.assertEqual(outside.read_bytes(), document["data"])
        other = server.WORKFLOWS / second["name"]
        workflow = other / "workflow"
        workflow.rename(other / "saved-workflow")
        workflow.symlink_to(other / "saved-workflow", target_is_directory=True)
        third = server.handle_upload({}, [document])
        self.assertNotIn(third["name"], (first["name"], second["name"]))

    def test_dedupe_compares_all_attachments_and_audio_multiplicity(self):
        source = self._docx("paper.docx")
        answer = self._docx("answer.docx")
        pdf = {"filename": "support.pdf", "data": b"%PDF companion"}
        audio = {"filename": "first.mp3", "data": b"audio one"}
        extra = {"filename": "second.wav", "data": b"audio two"}
        parts = [source, answer, pdf, audio, extra]
        first = server.handle_upload({}, parts)
        repeated = server.handle_upload({}, parts)
        self.assertEqual(first["name"], repeated["name"])
        for changed in (parts[:-1], parts + [dict(extra)],
                        [source, answer, dict(pdf, data=b"different"), audio, extra],
                        [source, dict(answer, data=b"different"), pdf, audio, extra]):
            with self.subTest(parts=changed):
                result = server.handle_upload({}, changed)
                self.assertNotEqual(first["name"], result["name"])
        self.assertEqual(len(self.started), 5)

    def test_upload_keeps_paper_data_and_metadata_isolated_per_batch(self):
        first = server.handle_upload(
            {"title": "同名练习", "source_type": "同步练习", "grade": "七年级"},
            [self._docx("first.docx")])
        second = server.handle_upload(
            {"title": "同名练习", "source_type": "中考真题", "grade": "九年级"},
            [self._docx("second.docx")])
        self.assertNotEqual(first['name'], second['name'])
        for result, expected_type, grade in ((first, '同步练习', '七年级'),
                                              (second, '中考真题', '九年级')):
            name = result['name']
            descriptor = json.loads((server.WORKFLOWS / name / 'attachments/batch.json').read_text())
            manifest = json.loads((server.WORKFLOWS / name / 'workflow/manifest.json').read_text())
            self.assertEqual(manifest['meta']['source_type'], expected_type)
            self.assertEqual(manifest['meta']['grade'], grade)
            self.assertEqual(descriptor['source_type'], expected_type)
            self.assertEqual(pathlib.Path(manifest['inputs']['source']['path']),
                             (server.WORKFLOWS / name / 'attachments/source.docx').resolve())
        self.assertEqual(self.started, [first['name'], second['name']])
        self.assertFalse((server.INBOX / first['name']).exists())
        self.assertFalse((server.INBOX / second['name']).exists())

    def test_upload_without_title_preserves_original_filename(self):
        source_name = "【木牍教育】14年安徽中考英语.docx"
        audio_name = "【木牍教育】14年安徽中考英语音频.mp3"
        result = server.handle_upload({}, [self._docx(source_name),
            {"field": "files", "filename": audio_name, "data": b"ID3 test audio"}])
        self.assertEqual(result["name"], pathlib.Path(source_name).stem)
        descriptor = json.loads((server.WORKFLOWS / result["name"] / "attachments/batch.json").read_text())
        self.assertEqual(descriptor["original_filenames"]["source"], source_name)
        self.assertEqual(descriptor["original_filenames"]["audio"], audio_name)
        self.assertEqual(descriptor["source"], "source.docx", "内部文件路径保持稳定")
        self.assertEqual(self.started, [result["name"]])

    def test_all_supported_attachments_saved_with_batch_and_no_unhinted_document_lost(self):
        source = self._docx('paper.docx')
        extra = self._docx('supporting.docx')
        more = self._docx('解析.docx')
        audio = {'field': 'files', 'filename': 'sound.mp3', 'data': b'ID3 audio'}
        result = server.handle_upload({'title': '练习'}, [source, extra, more, audio])
        batch = server.WORKFLOWS / result['name']
        attachments = batch / 'attachments'
        state = json.loads((batch / 'workflow/manifest.json').read_text())
        self.assertEqual((attachments / 'source.docx').read_bytes(), source['data'])
        self.assertEqual((attachments / 'answers.docx').read_bytes(), more['data'])
        self.assertEqual((attachments / 'attachment_001.docx').read_bytes(), extra['data'])
        self.assertEqual((attachments / 'audio.mp3').read_bytes(), audio['data'])
        for key in ('source', 'answers', 'attachment_001', 'audio', 'docx'):
            self.assertEqual(pathlib.Path(state['inputs'][key]['path']).parent, attachments.resolve())
        from workflow import material_classify
        with patch.object(material_classify, '_document_text', side_effect=lambda batch, path: pathlib.Path(path).name):
            roles = material_classify.classify_documents(batch)
        self.assertEqual({row['input'] for row in roles['documents']}, {'source', 'answers', 'attachment_001'})
        self.assertTrue((batch / '01_source/materials/attachment_001.txt').is_file())
        with patch.object(material_classify, '_document_text', return_value=None):
            failed = material_classify.classify_documents(batch)
        self.assertTrue(all(row.get('error') for row in failed['documents']))
        self.assertTrue((attachments / 'attachment_001.docx').exists())
        self.assertEqual(self.started, [result['name']])

    def test_discovered_inbox_batch_copies_inputs_into_its_workspace(self):
        from workflow import auto
        inbox = server.INBOX / '手工收件'
        inbox.mkdir()
        (inbox / 'paper.pdf').write_bytes(b'%PDF original')
        (inbox / 'answer.pdf').write_bytes(b'%PDF answer')
        (inbox / 'batch.json').write_text(json.dumps({
            'source': 'paper.pdf', 'answers': 'answer.pdf', 'source_type': '同步练习',
            'grade': '七年级'}, ensure_ascii=False), encoding='utf-8')
        candidate = auto.discover(server.INBOX)[0]
        batch = auto.ensure_batch(candidate, server.WORKFLOWS)
        state = json.loads((batch / 'workflow/manifest.json').read_text())
        for key, content in (('source', b'%PDF original'), ('answers', b'%PDF answer')):
            path = pathlib.Path(state['inputs'][key]['path'])
            self.assertEqual(path.parent, (batch / 'attachments').resolve())
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(state['meta']['source_type'], '同步练习')
        self.assertTrue((batch / 'attachments/batch.json').is_file())
        self.assertEqual(auto.ensure_batch(candidate, server.WORKFLOWS), batch)
        other = server.INBOX / '另一份试卷'
        other.mkdir()
        (other / 'paper.pdf').write_bytes(b'%PDF different')
        with self.assertRaisesRegex(ValueError, '另一份上传'):
            auto.ensure_batch({'name': candidate['name'], 'folder': str(other),
                               'source': str(other / 'paper.pdf')}, server.WORKFLOWS)
        self.assertEqual(pathlib.Path(state['inputs']['source']['path']).read_bytes(), b'%PDF original')

    def test_multiple_audio_files_are_saved_without_fabricating_mapping(self):
        from workflow import auto
        from audio import segment
        source = self._docx('paper.docx')
        files = [source, {'filename': 'a.mp3', 'data': b'first'},
                 {'filename': 'b.wav', 'data': b'second'}]
        result = server.handle_upload({}, files)
        batch = server.WORKFLOWS / result['name']
        state = json.loads((batch / 'workflow/manifest.json').read_text())
        self.assertEqual(pathlib.Path(state['inputs']['audio']['path']).read_bytes(), b'first')
        self.assertEqual(pathlib.Path(state['inputs']['audio_001']['path']).read_bytes(), b'second')
        with self.assertRaisesRegex(RuntimeError, '多份音频'):
            segment.run(batch, cfg={})
        self.assertFalse((batch / '02_audio/audio_map.json').exists())

    def test_word_and_pdf_upload_keeps_word_source_regardless_of_order(self):
        pdf = {'filename': 'print.pdf', 'data': b'%PDF fixture'}
        word = self._docx('paper.docx')
        result = server.handle_upload({'title': '配套试卷'}, [pdf, word])
        batch = server.WORKFLOWS / result['name']
        state = json.loads((batch / 'workflow/manifest.json').read_text())
        self.assertEqual(pathlib.Path(state['inputs']['source']['path']).read_bytes(), word['data'])
        self.assertEqual(pathlib.Path(state['inputs']['attachment_001']['path']).read_bytes(), pdf['data'])
        self.assertNotIn('answers', state['inputs'])
        # 来源优先级（用户 2026-11-11）：Word 主源、PDF 只作版面核对，并回传到前端提示。
        self.assertEqual(result['source_kind'], 'word')
        self.assertIn('Word 原卷作为提取主源', result['source_notice'])
        self.assertIn('不覆盖 Word 原文', result['source_notice'])

    def test_unsupported_attachment_does_not_create_batch(self):
        with self.assertRaisesRegex(ValueError, '不支持的附件'):
            server.handle_upload({}, [self._docx('paper.docx'),
                                      {'filename': 'notes.txt', 'data': b'notes'}])
        self.assertEqual(list(server.WORKFLOWS.iterdir()), [])

    def test_upload_requires_document(self):
        with self.assertRaises(ValueError):
            server.handle_upload({"title": "x"}, [{"field": "files", "filename": "a.mp3", "data": b""}])


if __name__ == "__main__":
    unittest.main()
