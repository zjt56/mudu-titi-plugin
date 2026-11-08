"""Freeze/approve/submit binding, uncertain outcomes and no hidden retries."""
from copy import deepcopy
import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import contracts, manifest as mf, runner, upload, upload_client
from workflow.test_upload_payload import batch as payload_fixture, catalog_snapshot


def valid_batch():
    # Freeze/approval/network cases use absent optional content; AI-generated
    # text is forbidden and would hide the boundary each case exercises.
    data = payload_fixture()
    data['items'][0]['explanation'] = '无'
    return data


class UploadFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.batch = self.root / 'batch'; self.batch.mkdir()
        self.source = self.batch / '06_batch' / 'batch_meta.json'; self.source.parent.mkdir()
        self.source.write_text(json.dumps(valid_batch(), ensure_ascii=False))
        self.catalog = catalog_snapshot()
        self.settings = upload_client.load_config()
        self.state = mf.new_manifest('fixture-upload', self.batch, {}, {})
        mf.save(self.batch, self.state)
        self.client = patch.object(upload_client, 'ApiClient').start(); self.addCleanup(patch.stopall)
        self.client.return_value.fetch_catalog.side_effect = lambda: deepcopy(self.catalog)
        self.client.return_value.submit.return_value = {'id': 'STAGE-123', 'stage': 'pending_confirmation'}

    def prepare(self):
        result = upload.prepare(self.batch, catalog=self.catalog, config=self.settings)
        self.assertTrue(result['passed'], result)
        return result

    def approve(self):
        gate = self.state['stages']['upload_gate']
        gate.update(status=mf.APPROVED, approved_batch_sha256=mf.sha256_file(self.source))
        gate.update(upload.approval_binding(self.batch, config=self.settings))

    def test_local_preflight_writes_offline_summary(self):
        report = upload.local_preflight(self.batch)
        self.assertIn("passed", report)
        self.assertIn("network_checked", report)
        self.assertTrue((self.batch / "07_upload/local-preflight.json").is_file())

    def test_frozen_wire_preserves_source_and_submits_exact_approved_bytes(self):
        original = self.source.read_bytes(); self.prepare(); self.approve()
        frozen = upload.verify(self.batch, config=self.settings)
        result = upload.submit_approved(self.batch, self.state, config=self.settings)
        self.client.return_value.submit.assert_called_once_with(frozen['payload'])
        self.assertEqual(self.source.read_bytes(), original)
        wire = json.loads(frozen['payload'])
        self.assertEqual(wire['items'][0]['type'], '单项填空')
        self.assertEqual(wire['items'][0]['difficulty'], '中')
        self.assertEqual(result['id'], 'STAGE-123')
        self.assertTrue(result['remote_confirmation_required'])
        self.assertEqual(json.loads((self.batch / '07_upload/receipt.json').read_text())['stage'], 'pending_confirmation')

    def test_missing_batch_name_uses_only_hash_bound_original_title(self):
        source = valid_batch(); source.pop('name')
        self.source.write_text(json.dumps(source, ensure_ascii=False))
        folder = self.batch / '01_source'; folder.mkdir()
        body = folder / '正文.txt'; body.write_text('原卷明确标题')
        (folder / 'metadata.json').write_text(json.dumps({'paper_title': '原卷明确标题',
            'source_sha256': mf.sha256_file(body)}, ensure_ascii=False))
        self.prepare()
        wire = json.loads(upload.verify(self.batch, config=self.settings)['payload'])
        self.assertEqual(wire['name'], '原卷明确标题')
        body.write_text('已经换成另一份原卷')
        with self.assertRaisesRegex(ValueError, '来源不符'):
            upload.verify(self.batch, config=self.settings)

    def test_changed_source_or_target_or_plan_cannot_reuse_approval(self):
        self.prepare(); self.approve()
        self.source.write_text(self.source.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, '最终批次已变化'):
            upload.submit_approved(self.batch, self.state, config=self.settings)
        self.client.return_value.submit.assert_not_called()

    def test_changed_api_base_requires_new_plan(self):
        self.prepare(); self.approve()
        changed = {**self.settings, 'base_url': 'http://127.0.0.1:18089/api'}
        with self.assertRaisesRegex(ValueError, '目标 API'):
            upload.submit_approved(self.batch, self.state, config=changed)
        self.client.return_value.submit.assert_not_called()

    def test_frozen_tampering_is_rejected(self):
        result = self.prepare(); self.approve()
        path = self.batch / result['payload_file']; path.write_text(path.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, '冻结文件'):
            upload.submit_approved(self.batch, self.state, config=self.settings)
        self.client.return_value.submit.assert_not_called()

    def test_unknown_outcome_is_persisted_and_never_automatically_resent(self):
        self.prepare(); self.approve()
        self.client.return_value.submit.side_effect = upload_client.APIError('submission_unknown')
        with self.assertRaises(upload_client.APIError):
            upload.submit_approved(self.batch, self.state, config=self.settings)
        self.client.return_value.submit.side_effect = None
        with self.assertRaisesRegex(ValueError, '前次提交结果未知'):
            upload.submit_approved(self.batch, self.state, config=self.settings)
        self.assertEqual(self.client.return_value.submit.call_count, 1)
        self.assertFalse((self.batch / '07_upload/receipt.json').exists())

    def test_changed_payload_cannot_bypass_unknown_submission_in_same_batch(self):
        self.prepare(); self.approve()
        self.client.return_value.submit.side_effect = upload_client.APIError('submission_unknown')
        with self.assertRaises(upload_client.APIError):
            upload.submit_approved(self.batch, self.state, config=self.settings)
        source = json.loads(self.source.read_text()); source['name'] = 'changed candidate'
        self.source.write_text(json.dumps(source, ensure_ascii=False))
        self.prepare(); self.approve()
        self.client.return_value.submit.side_effect = None
        with self.assertRaisesRegex(ValueError, '该批另一次提交结果未知'):
            upload.submit_approved(self.batch, self.state, config=self.settings)
        self.assertEqual(self.client.return_value.submit.call_count, 1)

    def test_confirmed_success_is_idempotent(self):
        self.prepare(); self.approve()
        first = upload.submit_approved(self.batch, self.state, config=self.settings)
        self.assertEqual(upload.submit_approved(self.batch, self.state, config=self.settings), first)
        self.assertEqual(self.client.return_value.submit.call_count, 1)

    def test_current_catalog_change_invalidates_freeze_before_post(self):
        self.prepare(); self.approve()
        changed = deepcopy(self.catalog); changed['points'][0]['name'] += ' changed'
        self.client.return_value.fetch_catalog.side_effect = lambda: changed
        with self.assertRaisesRegex(ValueError, '远端目录已变化'):
            upload.submit_approved(self.batch, self.state, config=self.settings)
        self.client.return_value.submit.assert_not_called()

    def test_fetch_timestamp_alone_does_not_invalidate_catalog(self):
        self.catalog['fetched_at'] = 'before'; self.prepare(); self.approve()
        self.catalog['fetched_at'] = 'after'
        self.assertEqual(upload.submit_approved(self.batch, self.state, config=self.settings)['id'], 'STAGE-123')

    def test_media_must_be_online_before_freeze_and_checked_again_before_post(self):
        source = valid_batch(); source['items'][0]['attachments'] = [
            {'kind': 'image', 'url': '/source-media/batch/picture.png', 'placement': 'option', 'optionKey': 'A'}]
        self.source.write_text(json.dumps(source, ensure_ascii=False))
        self.client.return_value.check_media.return_value = {'url': '/source-media/batch/picture.png', 'content_type': 'image/png'}
        self.prepare(); self.approve()
        upload.submit_approved(self.batch, self.state, config=self.settings)
        self.assertEqual(self.client.return_value.check_media.call_count, 2)

    def test_missing_media_does_not_freeze_new_payload(self):
        source = valid_batch(); source['items'][0]['attachments'] = [
            {'kind': 'image', 'url': '/source-media/batch/picture.png'}]
        self.source.write_text(json.dumps(source, ensure_ascii=False))
        self.client.return_value.check_media.side_effect = upload_client.APIError('media_unavailable', 404)
        report = upload.prepare(self.batch, catalog=self.catalog, config=self.settings)
        self.assertFalse(report['passed'])
        self.assertFalse((self.batch / '07_upload/plan.json').exists())

    def test_failed_new_preparation_cannot_leave_old_approval_uploadable(self):
        self.prepare(); self.approve()
        self.client.return_value.fetch_catalog.side_effect = upload_client.APIError('catalog_unavailable')
        with self.assertRaises(upload_client.APIError):
            upload.prepare(self.batch, config=self.settings)
        with self.assertRaisesRegex(ValueError, '最近一次上传预检失败'):
            upload.submit_approved(self.batch, self.state, config=self.settings)
        self.client.return_value.submit.assert_not_called()

    def test_mapping_failure_never_posts_or_freezes(self):
        source = valid_batch(); source['items'][0].pop('source')
        self.source.write_text(json.dumps(source, ensure_ascii=False))
        report = upload.prepare(self.batch, catalog=self.catalog, config=self.settings)
        self.assertFalse(report['passed'])
        self.assertFalse((self.batch / '07_upload/plan.json').exists())
        self.client.return_value.submit.assert_not_called()

    def test_generated_or_unverified_content_never_freezes_or_posts(self):
        for field in ('explanation', 'articleSummary'):
            for value in ('自撰内容没有原文来源。', '生成内容（AI生成，待校对）'):
                with self.subTest(field=field, value=value):
                    source = valid_batch()
                    source['items'][0][field] = value
                    self.source.write_text(json.dumps(source, ensure_ascii=False))
                    report = upload.prepare(self.batch, catalog=self.catalog, config=self.settings)
                    self.assertFalse(report['passed'], report)
                    self.assertFalse(report['content_source']['passed'])
                    self.assertFalse((self.batch / '07_upload/plan.json').exists())
                    self.assertFalse(upload.local_preflight(self.batch)['content']['passed'])
                    self.client.return_value.submit.assert_not_called()

    def test_original_explanation_cannot_be_omitted_before_freezing(self):
        source_dir = self.batch / '01_source'
        source_dir.mkdir()
        (source_dir / '正文.txt').write_text('81. She reads a book.', encoding='utf-8')
        (source_dir / '附录.txt').write_text('81.A 【解析】来源提供的完整解析。', encoding='utf-8')
        report = upload.prepare(self.batch, catalog=self.catalog, config=self.settings)
        self.assertFalse(report['passed'], report)
        self.assertIn('ORIGINAL_EXPLANATION_OMITTED', {row['code'] for row in report['content_source']['errors']})
        self.assertFalse((self.batch / '07_upload/plan.json').exists())
        self.client.return_value.submit.assert_not_called()

    def test_explanation_source_is_bound_to_frozen_upload(self):
        source_dir = self.batch / '01_source'; source_dir.mkdir()
        # 解析归属要求：附录题号与真实题号一致，且正文有唯一同号锚点，防止跨材料串用。
        (source_dir / '正文.txt').write_text('81. She ______ a book. A. reads B. read C. reading D. to read')
        appendix = source_dir / '附录.txt'; appendix.write_text('81.A 【解析】原文可靠解析。')
        source = valid_batch(); source['items'][0]['explanation'] = '原文可靠解析。'
        self.source.write_text(json.dumps(source, ensure_ascii=False))
        self.prepare(); self.approve()
        appendix.write_text('81.A 【解析】来源已经改变。')
        with self.assertRaisesRegex(ValueError, '来源不符'):
            upload.submit_approved(self.batch, self.state, config=self.settings)
        self.client.return_value.submit.assert_not_called()

    def test_missing_or_legacy_approval_refuses_post(self):
        self.prepare()
        self.state['stages']['upload_gate'].update(status=mf.APPROVED,
                                                 approved_batch_sha256=mf.sha256_file(self.source))
        with self.assertRaisesRegex(ValueError, '未批准本次冻结'):
            upload.submit_approved(self.batch, self.state, config=self.settings)
        self.client.return_value.submit.assert_not_called()

    def test_runner_preserves_review_gate_checks_before_network(self):
        self.prepare(); self.approve(); mf.save(self.batch, self.state)
        args = argparse.Namespace(batch_dir=str(self.batch), yes=True, dry_run=False)
        with patch.object(mf, 'is_stale', return_value=(False, '')), \
             patch.object(runner.review, 'evaluate', return_value={'passed': True}), \
             patch.object(runner, 'flowchart_checks', return_value={'passed': False, 'errors': [
                 {'code': 'SOURCE_SLICE_MISMATCH', 'message': 'source conflict'}]}):
            self.assertEqual(runner.cmd_upload(args), 1)
        self.client.return_value.submit.assert_not_called()

    def test_human_approval_records_frozen_request_and_target(self):
        report = self.prepare()
        args = argparse.Namespace(batch_dir=str(self.batch), stage='upload_gate', status=mf.APPROVED,
                                  actor='operator', note='approve exact prepared file', allow_missing=False,
                                  invalidate_downstream=False)
        with patch.object(runner, 'do_review_gate', return_value=0):
            self.assertEqual(runner.cmd_mark(args), 0)
        gate = mf.load(self.batch)['stages']['upload_gate']
        self.assertEqual(gate['approved_payload_sha256'], report['payload_sha256'])
        self.assertEqual(gate['approved_api_base'], self.settings['base_url'])
        self.assertEqual(gate['approved_batch_sha256'], mf.sha256_file(self.source))
        self.client.return_value.submit.assert_not_called()

    def test_dry_run_never_fetches_catalog_or_posts(self):
        self.prepare(); self.approve(); mf.save(self.batch, self.state)
        args = argparse.Namespace(batch_dir=str(self.batch), yes=False, dry_run=True)
        with patch.object(mf, 'is_stale', return_value=(False, '')), \
             patch.object(runner.review, 'evaluate', return_value={'passed': True}), \
             patch.object(runner, 'flowchart_checks', return_value={'passed': True}):
            self.assertEqual(runner.cmd_upload(args), 0)
        self.client.return_value.fetch_catalog.assert_not_called()
        self.client.return_value.submit.assert_not_called()

    def test_runner_records_staging_receipt_and_never_marks_published(self):
        self.prepare(); self.approve(); mf.save(self.batch, self.state)
        args = argparse.Namespace(batch_dir=str(self.batch), yes=True, dry_run=False)
        with patch.object(mf, 'is_stale', return_value=(False, '')), \
             patch.object(runner.review, 'evaluate', return_value={'passed': True}), \
             patch.object(runner, 'flowchart_checks', return_value={'passed': True}):
            self.assertEqual(runner.cmd_upload(args), 0)
        state = mf.load(self.batch)
        self.assertEqual(state['stages']['upload']['status'], mf.COMPLETED)
        self.assertEqual(state['status'], 'staged')
        self.assertEqual(state['stages']['publish_gate']['status'], mf.PENDING)
