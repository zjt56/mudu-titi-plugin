"""Upload staging contract: exact catalogs, provenance, media and preserved layout."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from workbench import taxonomy
from workflow import upload_payload


def catalog_snapshot():
    points = taxonomy.knowledge_points()
    by_path = {p["path"]: p for p in points}
    ids = {path: "REMOTE-" + point["id"] for path, point in by_path.items()}
    nodes = []
    for path, point in by_path.items():
        # Longest real prefix: the slash in 谚语/习语 is part of its name.
        parents = [p for p in by_path if path.startswith(p + "/")]
        parent = max(parents, key=len) if parents else None
        nodes.append({"id": ids[path], "name": point["name"], "parent_id": ids.get(parent),
                      "version_id": "REMOTE-V1", "active": True, "subject_id": "english"})
    return {"version": {"id": "REMOTE-V1", "status": "active", "textbook_id": None, "subject_id": "english"}, "points": nodes}


def question():
    path = "词汇/词汇辨析/动词辨析"
    return {"id": "Q1", "unit_id": "local-Q1", "number": 81, "numbers": [81],
            "unit_kind": "standalone", "question_type": "单项填空", "difficulty": "适中",
            "filterMetadata": {"subtypes": ["四个选项"], "difficultyValue": 0.65},
            "stem": "Choose the correct word.", "options": [{"label": "A", "text": "go"}, {"label": "B", "text": "see"},
                                                       {"label": "C", "text": "read"}, {"label": "D", "text": "write"}],
            "answer": "A", "explanation": "本题考查动词辨析，根据句意选 go。", "knowledge": [path],
            "knowledgeIds": [taxonomy.knowledge_index()[0][path]["id"]],
            "source_type": "中考真题", "year": 2024, "source": "2024 安徽英语原卷",
            "location": "第一部分 第1题", "version": "原卷扫描版", "authorization": "已获授权"}


def batch(item=None):
    return {"batch_id": "batch-1", "name": "fixture", "upload_mode": "source", "upload_target": "历年考题", "items": [item or question()]}


def reading_group():
    q = question()
    q.update(unit_kind="group", number=56, numbers=[56, 57, 58], question_type="阅读理解", material="Two children visit a museum.",
             articleSummary="两个孩子去博物馆参观。",
             knowledge=["语篇体裁/记叙文/故事", "主题/人与自我/生活与学习/学校生活"],
             subquestions=[{"number": n, "stem": "What did they visit?", "options": ["A. Museum", "B. Park"],
                            "answer": "A", "explanation": "根据第一段可知参观了博物馆。", "knowledge": [path]}
                           for n, path in ((56, "设题角度/阅读/细节理解"), (57, "设题角度/阅读/推理判断"),
                                           (58, "设题角度/阅读/主旨大意"))])
    q.pop("knowledgeIds")
    q["filterMetadata"]["subtypes"] = ["选择3题"]
    return q


class UploadPayloadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = catalog_snapshot()

    def build(self, data=None, catalog=None, **kwargs):
        return upload_payload.build_payload(data if data is not None else batch(), self.catalog if catalog is None else catalog, **kwargs)

    def codes(self, report):
        return {e["code"] for e in report["errors"]}

    def test_group_material_does_not_require_an_invented_stem(self):
        item = reading_group()
        item['stem'] = ''
        report = self.build(batch(item))
        self.assertNotIn('MISSING_STEM', self.codes(report))
        self.assertNotIn('GROUP_INCOMPLETE', self.codes(report))
        self.assertEqual(report['payload']['items'][0]['stem'], '')

    def test_listening_group_does_not_require_written_transcript(self):
        item = reading_group()
        item.update(question_type='听力长对话理解', material='', stem='听下面一段对话,回答第56至58小题。')
        item['filterMetadata']['subtypes'] = ['选择3题']
        report = self.build(batch(item))
        self.assertNotIn('GROUP_INCOMPLETE', self.codes(report))
        item['stem'] = ''
        self.assertIn('GROUP_INCOMPLETE', self.codes(self.build(batch(item))))

    def test_valid_conversion_is_read_only_and_allowlisted(self):
        data = batch()
        data["items"][0].update(status="reviewed", warnings=["local warning"], knowledgeStatus="已确认", human_confirmed=True)
        original, catalog = deepcopy(data), deepcopy(self.catalog)
        report = self.build(data)
        self.assertTrue(report["passed"], report["errors"])
        wire = report["payload"]["items"][0]
        self.assertEqual(wire["id"], "Q1")
        self.assertEqual(wire["type"], "单项填空")
        self.assertEqual(wire["difficulty"], "中")
        self.assertEqual(wire["filterMetadata"]["difficultyValue"], 0.65)
        self.assertEqual(wire["options"], ["A. go", "B. see", "C. read", "D. write"])
        self.assertEqual(wire["knowledge_version_id"], "REMOTE-V1")
        self.assertTrue(wire["knowledge_ids"][0].startswith("REMOTE-"))
        self.assertEqual(wire["articleSummary"], "")
        self.assertEqual(report["payload"]["upload_target"], "中考真题")
        for key in ("unit_id", "number", "numbers", "status", "warnings", "knowledgeStatus", "human_confirmed", "question_type", "knowledgeIds"):
            self.assertNotIn(key, wire)
        self.assertEqual(report["summary"], {"units": 1, "questions": 1, "attachments": 0})
        self.assertEqual(report["warnings"], [])
        self.assertEqual(data, original)
        self.assertEqual(self.catalog, catalog)
        canonical = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.assertEqual(report["catalog_sha256"], hashlib.sha256(canonical.encode()).hexdigest())

    def test_group_audits_local_before_wire_union(self):
        data = batch(reading_group())
        before = deepcopy(data)
        real_audit = upload_payload.label_audit.inspect_batch
        with patch.object(upload_payload.label_audit, "inspect_batch", wraps=real_audit) as audit:
            report = self.build(data)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(audit.call_count, 1)
        self.assertEqual(audit.call_args.args[0]["items"][0]["knowledge"], before["items"][0]["knowledge"])
        group = report["payload"]["items"][0]
        self.assertEqual(group["knowledge"][:2], before["items"][0]["knowledge"])
        self.assertEqual(len(group["knowledge"]), 5)
        self.assertEqual(len(group["knowledge_ids"]), 5)
        self.assertEqual(len(group["subquestions"][0]["knowledge_ids"]), 1)
        self.assertEqual(len(group["subquestions"][1]["knowledge_ids"]), 1)
        self.assertNotEqual(group["subquestions"][0]["knowledge_ids"], group["subquestions"][1]["knowledge_ids"])
        self.assertEqual(data, before)
        self.assertEqual(report["summary"]["questions"], 3)

    def test_empty_group_answer_is_aggregated_only_on_wire_in_number_order(self):
        q = reading_group()
        q['answer'] = ''
        q['subquestions'][0]['answer'] = 'A'
        q['subquestions'][1]['answer'] = 'Museum; park'
        q['subquestions'][2]['answer'] = 'C'
        q['subquestions'] = [q['subquestions'][2], q['subquestions'][0], q['subquestions'][1]]
        data = batch(q)
        before = deepcopy(data)
        report = self.build(data)
        self.assertTrue(report['passed'], report['errors'])
        wire = report['payload']['items'][0]
        self.assertEqual(wire['answer'], '56. A\n57. Museum; park\n58. C')
        self.assertEqual([(c['number'], c['answer']) for c in wire['subquestions']],
                         [(c['number'], c['answer']) for c in q['subquestions']])
        self.assertEqual(data, before)

    def test_nonempty_group_answer_is_preserved_without_hiding_bad_children(self):
        q = reading_group()
        q['answer'] = 'Provided group answer.'
        report = self.build(batch(q))
        self.assertTrue(report['passed'], report['errors'])
        self.assertEqual(report['payload']['items'][0]['answer'], q['answer'])
        q['subquestions'][0]['answer'] = ''
        report = self.build(batch(q))
        self.assertTrue(report['passed'], report['errors'])
        self.assertEqual(report['payload']['items'][0]['subquestions'][0]['answer'], '无答案')
        q['subquestions'][0]['answer'] = 'A'
        q['numbers'] = [56, 57, 59]
        self.assertIn('GROUP_NUMBER_COVERAGE', self.codes(self.build(batch(q))))

    def test_group_aggregate_rejects_missing_duplicate_invalid_numbers_and_answers(self):
        for change in ('missing_child', 'duplicate_child', 'missing_number', 'bool_number',
                       'duplicate_expected', 'wrong_expected', 'missing_expected'):
            q = reading_group()
            q['answer'] = ''
            if change == 'missing_child': q['subquestions'].pop()
            elif change == 'duplicate_child': q['subquestions'][1]['number'] = 56
            elif change == 'missing_number': q['subquestions'][0].pop('number')
            elif change == 'bool_number': q['subquestions'][0]['number'] = True
            elif change == 'duplicate_expected': q['numbers'] = [56, 56, 58]
            elif change == 'wrong_expected': q['numbers'] = [56, 57, 59]
            elif change == 'missing_expected': q.pop('numbers')

            before = deepcopy(q)
            report = self.build(batch(q))
            with self.subTest(change=change):
                self.assertFalse(report['passed'])
                self.assertIn('MISSING_ANSWER', self.codes(report))
                self.assertFalse(report['payload']['items'][0].get('answer'))
                self.assertEqual(q, before)

    def test_slash_in_leaf_name_matches_parent_chain(self):
        q = question()
        q.update(question_type="翻译", knowledge=["语用/谚语/习语"])
        q.pop("knowledgeIds")
        q["filterMetadata"]["subtypes"] = ["汉翻英"]
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        path = "语用/谚语/习语"
        remote = "REMOTE-" + taxonomy.knowledge_index()[0][path]["id"]
        self.assertEqual(report["payload"]["items"][0]["knowledge_ids"], [remote])

    def test_filter_metadata_provenance_promoted_without_guessing(self):
        q = question()
        for key in upload_payload.SOURCE_FIELDS + ("difficulty",):
            if key in q:
                q["filterMetadata"][key] = q.pop(key)
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        wire = report["payload"]["items"][0]
        self.assertEqual(wire["source"], "2024 安徽英语原卷")
        self.assertEqual(wire["difficulty"], "中")
        self.assertEqual(set(wire["filterMetadata"]), {"subtypes", "difficultyValue"})

    def test_missing_metadata_is_explicit_error(self):
        q = question()
        for key in ("source_type", "source", "location", "version", "authorization"):
            q.pop(key)
        report = self.build(batch(q))
        self.assertFalse(report["passed"])
        for key in ("SOURCE_TYPE", "SOURCE"):
            self.assertIn("MISSING_" + key, self.codes(report))
        # 服务端对 location/version/authorization 有默认值：本地不再硬阻断，
        # 只保留警告，避免为可选来源字段反复重跑。
        self.assertEqual([w["code"] for w in report["warnings"]].count("SOURCE_FIELD_EMPTY"), 3)
        self.assertNotIn("source_type", report["payload"]["items"][0])

    def test_mock_metadata_and_year_must_be_truthful(self):
        q = question()
        q.update(source_type="中考模拟", year="2026")
        data = batch(q)
        data["upload_target"] = "模拟卷"
        report = self.build(data)
        self.assertIn("MISSING_YEAR", self.codes(report))
        for key in ("INSTITUTION", "SCHOOL", "REGIONAL_VERSION"):
            self.assertIn("MISSING_" + key, self.codes(report))
        self.assertEqual(report["payload"]["items"][0]["year"], "2026")
        q.update(year=2026, institution="真实教研室", school="真实学校", regional_version="合肥模拟A卷")
        data["items"] = [q]
        self.assertTrue(self.build(data)["passed"])

    def test_source_target_conflict(self):
        data = batch()
        data["upload_target"] = "模拟卷"
        self.assertIn("SOURCE_TARGET_CONFLICT", self.codes(self.build(data)))

    def test_legacy_source_values_and_business_aliases(self):
        for target, source in (("精品资料", "精品题源"), ("课本原创", "课本原创")):
            q = question()
            q["source_type"] = source
            data = batch(q)
            data["upload_target"] = target
            report = self.build(data)
            self.assertTrue(report["passed"], report["errors"])
            self.assertEqual(report["payload"]["items"][0]["source_type"], source)

    def test_alias_conflicts_do_not_shadow(self):
        for changes in ({"type": "阅读理解"}, {"knowledge_ids": ["bad"]},
                        {"filter_metadata": {"subtypes": ["三个选项"]}},
                        {"articleSummary": "one", "article_summary": "two"},
                        {"materialBlocks": [], "material_blocks": [{"type": "table"}]}):
            with self.subTest(changes=changes):
                q = question()
                q.update(changes)
                self.assertIn("ALIAS_CONFLICT", self.codes(self.build(batch(q))))
        q = question()
        q["filterMetadata"]["year"] = 2023
        self.assertIn("ALIAS_CONFLICT", self.codes(self.build(batch(q))))

    def test_missing_answer_is_filled_with_placeholder(self):
        report = self.build()
        self.assertTrue(report["passed"])
        q = question()
        q["answer"] = ""
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["payload"]["items"][0]["answer"], "无答案")
        q = reading_group()
        q["subquestions"][1]["answer"] = ""
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["payload"]["items"][0]["subquestions"][1]["answer"], "无答案")

    def test_catalog_required_offline_and_invalid_snapshots_block(self):
        report = upload_payload.build_payload(batch())
        self.assertIn("CATALOG_REQUIRED", self.codes(report))
        self.assertIsInstance(report["payload"], dict)
        for catalog in ({}, {"version": {}, "points": []},
                        {"version": {"id": "X", "status": "archived"}, "points": []},
                        {"version": {"id": "X", "status": "active", "textbook_id": "TB"}, "points": []}):
            self.assertFalse(self.build(catalog=catalog)["passed"])

    def test_remote_mismatch_inactive_nonleaf_and_ambiguity(self):
        point_id = question()["knowledgeIds"][0]
        remote_id = "REMOTE-" + point_id
        for change, expected in (({"active": False}, "REMOTE_KNOWLEDGE_MISSING"),
                                 ({"name": "unregistered remote name"}, "REMOTE_KNOWLEDGE_MISSING"),
                                 ({"version_id": "other"}, "CATALOG_POINT_VERSION"),
                                 ({"subject_id": "math"}, "CATALOG_SUBJECT_MISMATCH")):
            catalog = deepcopy(self.catalog)
            next(p for p in catalog["points"] if p["id"] == remote_id).update(change)
            self.assertIn(expected, self.codes(self.build(catalog=catalog)))
        catalog = deepcopy(self.catalog)
        p = deepcopy(next(p for p in catalog["points"] if p["id"] == remote_id))
        p["id"] = "duplicate-path"
        catalog["points"].append(p)
        self.assertIn("REMOTE_KNOWLEDGE_AMBIGUOUS", self.codes(self.build(catalog=catalog)))
        catalog = deepcopy(self.catalog)
        catalog["points"].append({"id": "extra", "name": "child", "parent_id": remote_id})
        self.assertIn("REMOTE_KNOWLEDGE_NOT_LEAF", self.codes(self.build(catalog=catalog)))

    def test_catalog_parent_missing_cycle_and_duplicates(self):
        for extra, code in (({"id": "extra", "name": "bad", "parent_id": "missing"}, "CATALOG_TREE_INVALID"),
                            ({"id": "extra", "name": "bad", "parent_id": "extra"}, "CATALOG_TREE_INVALID"),
                            (deepcopy(self.catalog["points"][0]), "CATALOG_POINT_DUPLICATE")):
            catalog = deepcopy(self.catalog)
            catalog["points"].append(extra)
            self.assertIn(code, self.codes(self.build(catalog=catalog)))

    def test_explicit_remote_version_mismatch_never_silently_rebinds(self):
        q = question()
        q["knowledge_version_id"] = "OLD-VERSION"
        report = self.build(batch(q))
        self.assertIn("KNOWLEDGE_VERSION_MISMATCH", self.codes(report))
        self.assertFalse(report["passed"])

    def test_full_paths_required_no_leaf_name_fallback(self):
        q = question()
        q["knowledge"] = ["动词辨析"]
        q.pop("knowledgeIds")
        report = self.build(batch(q))
        self.assertIn("LABEL_RULE_VIOLATION", self.codes(report))
        self.assertIn("REMOTE_KNOWLEDGE_MISSING", self.codes(report))

    def test_declared_deployed_attachments_preserved(self):
        q = question()
        q["attachments"] = [{"kind": "image", "url": "/source-media/paper/image%20A.png", "name": "A图", "placement": "option", "optionKey": "Z"},
                            {"kind": "audio", "url": "/source-media/cut-1.mp3", "placement": "question"}]
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["payload"]["items"][0]["attachments"], q["attachments"])
        self.assertEqual(report["summary"]["attachments"], 2)

    def test_existing_media_map_remaps_parent_and_child_without_mutation(self):
        q = reading_group()
        q['attachments'] = [
            {'kind': 'audio', 'url': '/source-media/old/cut.mp3', 'placement': 'material'},
            {'kind': 'image', 'url': '/source-media/old/picture%20A.png', 'placement': 'option', 'optionKey': 'B'},
            {'kind': 'image', 'url': '/source-media/other/unrelated.png'},
        ]
        q['subquestions'][0]['attachments'] = [
            {'kind': 'image', 'url': '/source-media/old/picture%20A.png', 'placement': 'option', 'optionKey': 'A'}]
        data = batch(q)
        before = deepcopy(data)
        options = {'media_map': {'cut.mp3': '/source-media/new/cut.mp3',
                                 'picture A.png': '/source-media/new/picture%20A.png',
                                 'different.png': '/source-media/new/unrelated.png'}}
        report = self.build(data, **options)
        self.assertTrue(report['passed'], report['errors'])
        wire = report['payload']['items'][0]
        self.assertEqual(wire['attachments'][0], {**q['attachments'][0], 'url': options['media_map']['cut.mp3']})
        self.assertEqual(wire['attachments'][1], {**q['attachments'][1], 'url': options['media_map']['picture A.png']})
        self.assertEqual(wire['attachments'][2], q['attachments'][2])
        self.assertEqual(wire['subquestions'][0]['attachments'][0],
                         {**q['subquestions'][0]['attachments'][0], 'url': options['media_map']['picture A.png']})
        self.assertEqual(data, before)
        self.assertEqual(self.build(data, **json.loads(json.dumps(options)))['payload'], report['payload'])
        self.assertEqual(self.build(data)['payload']['items'][0]['attachments'], q['attachments'])

    def test_upload_verify_replays_frozen_media_mapping_options(self):
        from pathlib import Path
        import tempfile
        from workflow import manifest, upload, upload_client

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / '06_batch' / 'batch_meta.json'
            source.parent.mkdir()
            q = question()
            q['attachments'] = [{'kind': 'image', 'url': '/source-media/old/a.png',
                                 'placement': 'option', 'optionKey': 'B'}]
            data = batch(q)
            source.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            manifest.save(root, manifest.new_manifest('fixture-upload', root, {}, {}))
            options = {'a.png': '/source-media/new/a.png'}
            settings = upload_client.load_config()
            content = {'passed': True, 'errors': [], 'source_sha256': {}}
            with patch.object(upload_client, 'ApiClient') as client, \
                    patch('workflow.upload_content.inspect_content', return_value=content):
                client.return_value.check_media.return_value = {'url': options['a.png']}
                report = upload.prepare(root, catalog=self.catalog, config=settings, media_map=options)
                self.assertTrue(report['passed'], report)
                result = upload.verify(root, config=settings)
            self.assertEqual(result['plan']['mapping_options']['media_map'], options)
            self.assertEqual(json.loads(result['payload'])['items'][0]['attachments'][0]['url'], options['a.png'])
            self.assertEqual(json.loads(source.read_text(encoding='utf-8')), data)
            client.return_value.check_media.assert_called_once_with(options['a.png'], kind='image')
            client.return_value.submit.assert_not_called()

    def test_ambiguous_basename_does_not_remap(self):
        q = reading_group()
        q['attachments'] = [{'kind': 'image', 'url': '/source-media/first/a.png'}]
        q['subquestions'][0]['attachments'] = [{'kind': 'image', 'url': '/source-media/second/a.png'}]
        report = self.build(batch(q), media_map={'a.png': '/source-media/new/a.png'})
        self.assertIn('MEDIA_MAP_AMBIGUOUS', self.codes(report))
        wire = report['payload']['items'][0]
        self.assertEqual(wire['attachments'][0]['url'], '/source-media/first/a.png')
        self.assertEqual(wire['subquestions'][0]['attachments'][0]['url'], '/source-media/second/a.png')

    def test_mapping_never_hides_unsafe_source_or_destination(self):
        for source in ('https://evil.test/source-media/a.png', '/other/a.png',
                       '/source-media/%252e%252e/a.png', '/source-media/folder%252fa.png',
                       '/source-media/a.png?x=1'):
            with self.subTest(source=source):
                q = question()
                q['attachments'] = [{'kind': 'image', 'url': source}]
                report = self.build(batch(q), media_map={'a.png': '/source-media/new/a.png'})
                self.assertIn('ATTACHMENT_URL_INVALID', self.codes(report))
                self.assertNotIn('url', report['payload']['items'][0]['attachments'][0])
        for target in ('https://evil.test/a.png', '/source-media/../a.png', '/source-media/a.png?x=1', None):
            with self.subTest(target=target):
                q = question()
                q['attachments'] = [{'kind': 'image', 'url': '/source-media/old/a.png'}]
                report = self.build(batch(q), media_map={'a.png': target})
                self.assertIn('ATTACHMENT_URL_INVALID', self.codes(report))
        q = question()
        q['stem'] = 'Look at [IMG a.png]'
        report = self.build(batch(q), media_map={'a.png': 'https://evil.test/a.png'})
        self.assertIn('ATTACHMENT_URL_INVALID', self.codes(report))

    def test_invalid_media_paths_and_option_keys_rejected(self):
        for url in ("file:///tmp/a.png", "/Users/me/a.png", "https://example.com/source-media/a.png", "//example.com/a.png",
                    "/source-media/../secret", "/source-media/%2e%2e/secret", "/source-media/%252e%252e/secret",
                    "/source-media/a%5cb.png", "/source-media/a//b", "/source-media/a.png?token=x", "/source-media/a%3fx",
                    "/source-media/a.png#fragment", "data:image/png;base64,AAAA", "/source-media/", "/source-media/a%00.png"):
            with self.subTest(url=url):
                q = question()
                q["attachments"] = [{"kind": "image", "url": url}]
                self.assertIn("ATTACHMENT_URL_INVALID", self.codes(self.build(batch(q))))
        for key in ("a", "AA", "1", "", None):
            q = question()
            q["attachments"] = [{"kind": "image", "url": "/source-media/a.png", "placement": "option", "optionKey": key}]
            self.assertIn("ATTACHMENT_OPTION_INVALID", self.codes(self.build(batch(q))))

    def test_unresolved_media_and_old_maps_not_auto_deployed(self):
        for key, value in (("stem", "Look at [IMG a.png]"), ("material", "![image](../images/a.png)"),
                           ("explanation", "file:///tmp/a.png"), ("audio", {"file": "cut.mp3"}),
                           ("images", ["a.png"])):
            q = question()
            q[key] = value
            report = self.build(batch(q))
            self.assertFalse(report["passed"])
            self.assertNotIn("attachments", report["payload"]["items"][0])

    def test_material_table_structure_and_summary_preserved(self):
        q = reading_group()
        q["articleSummary"] = "原文提供的大意"
        q["materialBlocks"] = [{"type": "table", "variant": "poster", "rows": [
            {"cells": [{"text": "Title", "colspan": 2, "header": True, "items": []}]},
            {"cells": [{"text": "", "rowspan": 2}, {"text": "56. ___", "items": [{"text": "first", "items": ["nested"]}]}]},
            {"cells": [{"text": "57. ___", "colspan": 1}]}]}]
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        wire = report["payload"]["items"][0]
        self.assertEqual(wire["materialBlocks"], q["materialBlocks"])
        self.assertEqual(wire["articleSummary"], q["articleSummary"])

    def test_bad_table_spans_dangling_overlap_and_holes(self):
        rows_sets = [
            [{"cells": [{"text": "x", "colspan": 0}]}],
            [{"cells": [{"text": "x", "rowspan": True}]}],
            [{"cells": [{"text": "x", "rowspan": 2}]}],
            [{"cells": [{"text": "x"}, {"text": "y", "rowspan": 2}]}, {"cells": [{"text": "z", "colspan": 2}]}],
            [{"cells": [{"text": "x", "colspan": 2}]}, {"cells": [{"text": "y"}]}],
        ]
        for rows in rows_sets:
            q = reading_group()
            q["materialBlocks"] = [{"type": "table", "rows": rows}]
            report = self.build(batch(q))
            self.assertTrue(any(c.startswith("MATERIAL_TABLE") for c in self.codes(report)), report)
            self.assertFalse(report["passed"])

    def test_group_structure_and_number_uniqueness(self):
        for children in (["simple string"], [{"number": 0}], [{"number": 1}, {"number": 1}]):
            q = reading_group()
            q["subquestions"] = children
            self.assertFalse(self.build(batch(q))["passed"])
        q = reading_group()
        q["material"] = ""
        self.assertIn("GROUP_INCOMPLETE", self.codes(self.build(batch(q))))

    def test_multiple_textbook_mappings_not_dropped_or_rebound(self):
        q = question()
        kid = "REMOTE-" + q["knowledgeIds"][0]
        q["textbook_id"] = "TB-EN-PEP-NEW"
        q["textbookMappings"] = [{"textbookId": "TB-EN-PEP-NEW", "knowledgeVersionId": "REMOTE-V1", "knowledgeIds": [kid], "curriculum": pos}
                                 for pos in ("7年级上册 · Unit 1", "8年级下册 · Unit 2")]
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["payload"]["items"][0]["textbookMappings"], q["textbookMappings"])
        q["textbookMappings"][1]["knowledgeVersionId"] = "TEXTBOOK-VERSION"
        report = self.build(batch(q))
        self.assertIn("TEXTBOOK_CATALOG_UNSUPPORTED", self.codes(report))
        self.assertEqual(report["payload"]["items"][0]["textbookMappings"][1]["knowledgeVersionId"], "TEXTBOOK-VERSION")

    def test_unknown_extensions_block_and_do_not_leak(self):
        q = question()
        q["unexpectedExtension"] = {"secret": "not an API field"}
        report = self.build(batch(q))
        self.assertIn("UNKNOWN_FIELD", self.codes(report))
        self.assertNotIn("unexpectedExtension", report["payload"]["items"][0])

    def test_duplicate_ids_and_json_bytes_fail_closed(self):
        data = batch()
        data["items"].append(deepcopy(data["items"][0]))
        self.assertIn("QUESTION_ID_DUPLICATE", self.codes(self.build(data)))
        q = question()
        q["stem"] = b"media bytes"
        report = self.build(batch(q))
        self.assertIn("PAYLOAD_NOT_JSON", self.codes(report))
        self.assertIsNone(report["payload"])

    def test_root_explicit_parameters_and_manual_mode(self):
        data = {"items": [question()]}
        report = self.build(data, batch_id="from-directory", name="Explicit title", upload_mode="manual")
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["payload"]["batch_id"], "from-directory")
        self.assertEqual(report["payload"]["name"], "Explicit title")
        self.assertEqual(report["payload"]["upload_mode"], "manual")

    def test_transport_aliases_and_separator_whitespace_match(self):
        q = question()
        q["filter_metadata"] = q.pop("filterMetadata")
        q["difficulty"] = "中"
        q["knowledge"] = ["词汇 / 词汇辨析 / 动词辨析"]
        q["article_summary"] = ""
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["payload"]["items"][0]["knowledge"], ["词汇/词汇辨析/动词辨析"])

    def test_catalog_transport_timestamp_not_part_of_content_digest(self):
        snapshot = deepcopy(self.catalog)
        snapshot["fetched_at"] = "2026-11-03T08:00:00+08:00"
        self.assertEqual(self.build()["catalog_sha256"], self.build(catalog=snapshot)["catalog_sha256"])

    def test_structural_subtype_counts_reject_contradictions(self):
        q = question()
        q["options"] = q["options"][:2]
        self.assertIn("OPTION_COUNT_MISMATCH", self.codes(self.build(batch(q))))
        q = reading_group()
        q["subquestions"] = q["subquestions"][:2]
        self.assertIn("SUBQUESTION_COUNT_MISMATCH", self.codes(self.build(batch(q))))

    def test_unprinted_writing_number_is_not_exported_or_fabricated(self):
        q = question()
        q.update(question_type="书面表达", options=[], knowledge=["语篇体裁/记叙文/故事", "主题/人与自我/生活与学习/学校生活"])
        q.pop("knowledgeIds")
        q["filterMetadata"]["subtypes"] = ["其他"]
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        wire = report["payload"]["items"][0]
        for key in ("number", "numbers", "unit_id", "paperId"):
            self.assertNotIn(key, wire)
        self.assertEqual(wire["id"], "Q1")

    def test_unknown_nested_controls_are_blocked_and_excluded(self):
        q = reading_group()
        q["materialBlocks"] = [{"type": "table", "human_confirmed": True, "rows": [{"status": "published", "cells": [
            {"text": "Body", "warnings": [], "items": [{"text": "List", "knowledgeStatus": "已确认"}]}]}]}]
        report = self.build(batch(q))
        self.assertIn("UNKNOWN_FIELD", self.codes(report))
        serialized = json.dumps(report["payload"], ensure_ascii=False)
        for key in ("human_confirmed", "knowledgeStatus", "warnings", "status"):
            self.assertNotIn(key, serialized)

    def test_subject_mismatch_and_snapshot_subject(self):
        report = self.build()
        self.assertEqual(report["payload"]["subject_id"], "english")
        data = batch()
        data["subject_id"] = "math"
        self.assertIn("SUBJECT_MISMATCH", self.codes(self.build(data)))

    def test_answer_and_explanation_do_not_repeat_field_titles(self):
        for key, value in (("answer", "答案：A"), ("explanation", "解析: The passage says so.")):
            q = question()
            q[key] = value
            self.assertIn("CONTENT_PREFIX_INVALID", self.codes(self.build(batch(q))))

    def test_padded_wire_type_and_subtype_cannot_bypass_zero_options(self):
        for type_name, subtype in ((" 单项填空", "四个选项"), ("单项填空 ", "四个选项"),
                                   ("单项填空", " 四个选项"), ("单项填空", "四个选项 ")):
            with self.subTest(type_name=type_name, subtype=subtype):
                q = question()
                q.update(question_type=type_name, options=[])
                q["filterMetadata"]["subtypes"] = [subtype]
                data = batch(q)
                before = deepcopy(data)
                report = self.build(data)
                self.assertFalse(report["passed"])
                self.assertTrue(self.codes(report) & {"TYPE_INVALID", "SUBTYPE_INVALID"})
                self.assertEqual(report["payload"]["items"][0]["type"], type_name)
                self.assertEqual(report["payload"]["items"][0]["filterMetadata"]["subtypes"], [subtype])
                self.assertEqual(data, before)

    def test_child_wire_type_subtype_and_category_are_exact_catalog_values(self):
        for changes, code in (({"type": " 阅读理解"}, "TYPE_INVALID"),
                              ({"filterMetadata": {"subtypes": ["选择3题 "]}}, "SUBTYPE_INVALID"),
                              ({"filterMetadata": {"subtypes": ["四个选项"]}}, "SUBTYPE_INVALID"),
                              ({"filterMetadata": {"categories": [" 优选题"]}}, "CATEGORY_INVALID"),
                              ({"filterMetadata": {"categories": ["自造分类"]}}, "CATEGORY_INVALID")):
            with self.subTest(changes=changes):
                q = reading_group()
                q["subquestions"][0].update(changes)
                report = self.build(batch(q))
                self.assertFalse(report["passed"])
                self.assertTrue(any(e["code"] == code and e["unit_id"] == "Q1/56" for e in report["errors"]))

    def test_missing_category_placeholder_is_removed_only_from_wire(self):
        q = reading_group()
        q["filterMetadata"]["categories"] = ["未提供", "优选题", "未提供"]
        q["subquestions"][0]["filterMetadata"] = {"categories": ["未提供"]}
        data = batch(q)
        before = deepcopy(data)
        report = self.build(data)
        self.assertTrue(report["passed"], report["errors"])
        wire = report["payload"]["items"][0]
        self.assertEqual(wire["filterMetadata"]["categories"], ["优选题"])
        self.assertEqual(wire["subquestions"][0]["filterMetadata"]["categories"], [])
        self.assertEqual(data, before)

    def test_missing_category_placeholder_does_not_hide_invalid_values(self):
        for invalid in (" 未提供", "未提供 ", "自造分类", "", None):
            with self.subTest(invalid=invalid):
                q = question()
                q["filterMetadata"]["categories"] = ["未提供", "优选题", invalid]
                data = batch(q)
                before = deepcopy(data)
                report = self.build(data)
                self.assertFalse(report["passed"])
                self.assertIn("CATEGORY_INVALID", self.codes(report))
                self.assertEqual(report["payload"]["items"][0]["filterMetadata"]["categories"],
                                 ["优选题", invalid])
                self.assertEqual(data, before)

    def test_parent_label_audit_remains_required(self):
        q = reading_group()
        q["knowledge"] = ["语篇体裁/记叙文/故事"]
        report = self.build(batch(q))
        self.assertIn("LABEL_RULE_VIOLATION", self.codes(report))

    def test_valid_child_explicit_labels_and_inherited_labels(self):
        q = reading_group()
        q["subquestions"][0].update(type="阅读理解", filterMetadata={"subtypes": ["选择3题"], "categories": ["优选题"]})
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["payload"]["items"][0]["subquestions"][0]["type"], "阅读理解")
        self.assertNotIn("type", report["payload"]["items"][0]["subquestions"][1])

    def listening(self, grouped=False):
        q = reading_group()
        q["question_type"] = "听力长对话理解" if grouped else "听力短对话理解"
        q["knowledge"] = ["主题/人与自我/生活与学习/学校生活"]
        q["filterMetadata"]["subtypes"] = ["选择3题" if grouped else "选文字"]
        for child in q["subquestions"]:
            child["knowledge"] = ["主题/人与自我/生活与学习/学校生活"]
        if not grouped:
            q.pop("subquestions")
            q["unit_kind"] = "standalone"
        return q

    def test_listening_standalone_and_group_require_parent_audio(self):
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                q = self.listening(grouped)
                report = self.build(batch(q))
                self.assertTrue(report["passed"], report["errors"])
                self.assertIn("MISSING_AUDIO_ATTACHMENT", {w["code"] for w in report["warnings"]})
                q["attachments"] = [{"kind": "image", "url": "/source-media/picture.png"}]
                report = self.build(batch(q))
                self.assertTrue(report["passed"], report["errors"])
                self.assertIn("MISSING_AUDIO_ATTACHMENT", {w["code"] for w in report["warnings"]})
                q["attachments"].append({"kind": "audio", "url": "/source-media/corresponding-cut.mp3"})
                data = batch(q)
                before = deepcopy(data)
                report = self.build(data)
                self.assertTrue(report["passed"], report["errors"])
                self.assertEqual(data, before)

    def test_child_audio_and_file_metadata_do_not_supply_shared_audio(self):
        q = self.listening(True)
        for child in q["subquestions"]:
            child["attachments"] = [{"kind": "audio", "url": "/source-media/child-cut.mp3"}]
        q["audio"] = {"file": "whole-paper.mp3", "verified": True}
        report = self.build(batch(q))
        self.assertIn("MISSING_AUDIO_ATTACHMENT", {w["code"] for w in report["warnings"]})
        self.assertIn("MEDIA_DEPLOYMENT_REQUIRED", self.codes(report))
        self.assertNotIn("attachments", report["payload"]["items"][0])

    def test_nonlistening_plain_question_needs_no_audio(self):
        report = self.build()
        self.assertTrue(report["passed"], report["errors"])
        self.assertNotIn("attachments", report["payload"]["items"][0])

    def test_all_embedded_media_markup_rejected_even_deployed_paths(self):
        values = ["<img src=https://example.com/image.png>", "<IMG src=data:image/png;base64,AAAA>",
                  "<img src='/source-media/image.png'>", "<audio src=https://example.com/cut.mp3></audio>",
                  '<audio src="/source-media/cut.mp3"></audio>', "<video src=data:video/mp4,AAAA>",
                  "<source src=https://example.com/cut.mp3>", "<img>",
                  "![picture](/source-media/image.png)", "![picture](https://example.com/a.png)",
                  "![picture][reference]", "![reference]"]
        for value in values:
            with self.subTest(value=value):
                q = question()
                q["stem"] = value
                report = self.build(batch(q))
                self.assertIn("UNRESOLVED_MEDIA_REFERENCE", self.codes(report))
                self.assertEqual(report["payload"]["items"][0]["stem"], value)
                self.assertNotIn("attachments", report["payload"]["items"][0])

    def test_child_and_nested_table_embedded_media_are_rejected(self):
        q = reading_group()
        q["subquestions"][0]["options"] = ["<audio src=https://example.com/a.mp3>", "B. Park"]
        q["materialBlocks"] = [{"type": "table", "rows": [{"cells": [{"items": [{"text": "![x][ref]"}]}]}]}]
        report = self.build(batch(q))
        errors = [e for e in report["errors"] if e["code"] == "UNRESOLVED_MEDIA_REFERENCE"]
        self.assertEqual({e["unit_id"] for e in errors}, {"Q1", "Q1/56"})

    def test_passage_urls_and_inline_formatting_remain_source_content(self):
        q = reading_group()
        q["material"] = "Read https://example.com/about. AI生成技术 is discussed with <u>care</u> and x<sup>2</sup>."
        q["subquestions"][0]["explanation"] = "The word <u>答案</u> appears later in this explanation."
        before = deepcopy(q)
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["payload"]["items"][0]["material"], before["material"])
        self.assertEqual(q, before)

    def test_formatted_field_title_prefixes_rejected_parent_and_child(self):
        for child in (False, True):
            for field, label in (("answer", "答案"), ("explanation", "解析")):
                for value in (f"  {label}：body", f" <strong>{label}</strong>: body",
                              f"<p><span class='label'>{label}：</span>body</p>", f" **{label}**：body"):
                    with self.subTest(child=child, field=field, value=value):
                        q = reading_group() if child else question()
                        target = q["subquestions"][0] if child else q
                        target[field] = value
                        report = self.build(batch(q))
                        self.assertTrue(any(e["code"] == "CONTENT_PREFIX_INVALID" and e["unit_id"] == ("Q1/56" if child else "Q1") for e in report["errors"]))
                        wire = report["payload"]["items"][0]
                        self.assertEqual((wire["subquestions"][0] if child else wire)[field], value)

    def test_generated_summary_is_dropped_without_gate(self):
        # 用户规则：不采用 AI 生成大意，直接按缺失处理，不再校验 AI 标记。
        for child in (False, True):
            for summary in ("AI生成", "AI生成待校对：故事梗概", "摘要（AI生成）"):
                with self.subTest(child=child, summary=summary):
                    q = reading_group()
                    target = q["subquestions"][0] if child else q
                    target["articleSummary"] = summary
                    report = self.build(batch(q))
                    self.assertNotIn("GENERATED_ARTICLE_SUMMARY", self.codes(report))
                    wire = report["payload"]["items"][0]
                    self.assertEqual((wire["subquestions"][0] if child else wire)["articleSummary"], "")

    def test_generated_summary_with_full_f5_marker_is_also_dropped(self):
        # 即使带完整旧标记，也不采用 AI 生成内容。
        for child in (False, True):
            with self.subTest(child=child):
                q = reading_group()
                target = q["subquestions"][0] if child else q
                target["articleSummary"] = "本文讲述一个人靠坚持实现梦想的小故事。（AI生成，待校对）"
                report = self.build(batch(q))
                wire = report["payload"]["items"][0]
                self.assertEqual((wire["subquestions"][0] if child else wire)["articleSummary"], "")

    def test_sqlite_active_integers_accept_all_one_reject_selected_zero(self):
        catalog = deepcopy(self.catalog)
        for point in catalog["points"]:
            point["active"] = 1
        data = batch()
        before_data, before_catalog = deepcopy(data), deepcopy(catalog)
        report = self.build(data, catalog=catalog)
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(data, before_data)
        self.assertEqual(catalog, before_catalog)
        selected = "REMOTE-" + question()["knowledgeIds"][0]
        next(p for p in catalog["points"] if p["id"] == selected)["active"] = 0
        before_catalog = deepcopy(catalog)
        report = self.build(data, catalog=catalog)
        self.assertIn("REMOTE_KNOWLEDGE_MISSING", self.codes(report))
        self.assertEqual(catalog, before_catalog)

    def test_invalid_active_types_fail_without_mutating_snapshot(self):
        for flag in (2, -1, "1", "0", "true", 1.0, 0.0):
            with self.subTest(flag=flag):
                catalog = deepcopy(self.catalog)
                catalog["points"][0]["active"] = flag
                before = deepcopy(catalog)
                report = self.build(catalog=catalog)
                self.assertIn("CATALOG_POINT_INVALID", self.codes(report))
                self.assertFalse(report["passed"])
                self.assertEqual(catalog, before)

    def test_malformed_declared_fields_return_reports_instead_of_exceptions(self):
        for field in ("source_type", "difficulty", "filterMetadata", "unit_kind", "knowledge", "knowledgeIds",
                      "attachments", "subquestions", "materialBlocks", "options", "relations", "year"):
            for value in (None, True, 0, {}, [], [{}]):
                with self.subTest(field=field, value=value):
                    data = batch()
                    data["items"][0][field] = deepcopy(value)
                    report = self.build(data)
                    self.assertIsInstance(report["errors"], list)


if __name__ == "__main__":
    unittest.main()

    def test_group_children_may_omit_knowledge_for_whole_unit(self):
        q = question()
        q.update(id="g1", unit_id="g1", unit_kind="group", numbers=[1, 2], number=1,
                 question_type="听力长对话理解",
                 filterMetadata={"subtypes": ["选择2题"]},
                 stem="听下面一段对话，回答第1、2题。", material="M: Hello.",
                 answer="", options=[],
                 attachments=[{"kind": "audio", "url": "/source-media/batch/seg-1.mp3"}])
        q["subquestions"] = [
            {"number": 1, "stem": "Q1", "options": [{"label": "A", "text": "a"}], "answer": "A"},
            {"number": 2, "stem": "Q2", "options": [{"label": "A", "text": "a"}], "answer": "B"},
        ]
        report = self.build(batch(q))
        self.assertTrue(report["passed"], report["errors"])


class AliasAlignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = catalog_snapshot()

    def build(self, item):
        data = batch(item)
        data["upload_mode"] = "manual"
        data["upload_target"] = ""
        return upload_payload.build_payload(data, self.catalog)

    def test_source_alias_and_type_alias_are_normalized(self):
        item = question()
        item.update(source_type="精品资料", question_type="单项选择", type="单项选择")
        item.pop("question_type", None)
        report = self.build(item)
        self.assertTrue(report["passed"], report["errors"])
        wired = report["payload"]["items"][0]
        self.assertEqual(wired["source_type"], "精品题源")
        self.assertEqual(wired["type"], "单项填空")

    def test_legacy_source_aliases_are_normalized(self):
        for alias, canonical in (("历年考题", "中考真题"), ("模拟卷", "中考模拟")):
            item = question()
            item["source_type"] = alias
            if canonical == "中考模拟":
                item.update(institution="某校", school="某中学", regional_version="安徽")
            report = self.build(item)
            self.assertTrue(report["passed"], report["errors"])
            self.assertEqual(report["payload"]["items"][0]["source_type"], canonical)

    def test_taxonomy_sources_include_server_only_entries(self):
        self.assertIn("精品题源", taxonomy.sources())
        self.assertIn("课本原创", taxonomy.sources())
        self.assertEqual(len(taxonomy.sources()), 13)
