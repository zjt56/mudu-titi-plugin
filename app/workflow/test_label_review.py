"""标签审核口径、目录校验与图片体检的静态测试。不写任何批次文件。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from workbench import media_audit, server, taxonomy


class TaxonomyTest(unittest.TestCase):
    def test_types_are_the_fixed_27(self):
        self.assertEqual(len(taxonomy.types()), 27)

    def test_user_group_types_are_whole_unit_only(self):
        # 用户口径（2026-11-05）：这些题型只在组级打标签，子题 knowledge=[]。
        group_types = ("完形填空", "阅读理解", "补全对话", "任务型阅读", "听力信息转换",
                       "教材语篇填空", "短文填空", "选词填空", "听力短文理解", "听力长对话理解")
        for name in group_types:
            self.assertIn(name, taxonomy.types())
            self.assertTrue(taxonomy.whole_unit_type(name), name)
        for name in ("单项填空", "单词拼写", "完成句子", "情景运用", "翻译", "书面表达"):
            self.assertFalse(taxonomy.whole_unit_type(name), name)

    def test_knowledge_search_returns_leaf_paths_only(self):
        by_path, leaves = taxonomy.knowledge_index()
        results = taxonomy.knowledge_search("兴趣爱好", limit=10)
        self.assertTrue(results)
        for item in results:
            self.assertIn(item["path"], leaves)
            self.assertEqual(by_path[item["path"]]["path"], item["path"])

    def test_resolve_reports_unknown_and_non_leaf(self):
        resolved = taxonomy.resolve_knowledge(["主题/人与自我", "主题/不存在/假节点/叶子"])
        self.assertEqual(resolved["resolved"], [])
        self.assertEqual(len(resolved["non_leaf"]), 1)
        self.assertEqual(len(resolved["unknown"]), 1)

    def test_resolve_migrates_unique_legacy_leaf_suffix(self):
        result = taxonomy.resolve_knowledge([
            "基本读音/字母在单词中的基本读音",
            "词汇辨析/名词辨析",
        ])
        self.assertEqual(
            [item["path"] for item in result["resolved"]],
            ["语音/基本读音/字母在单词中的基本读音", "词汇/词汇辨析/名词辨析"],
        )
        self.assertEqual(result["unknown"], [])
        self.assertEqual(result["non_leaf"], [])

    def test_check_labels_rejects_values_outside_catalog(self):
        problems = taxonomy.check_labels({
            "type": "不存在的题型",
            "subtypes": ["不存在的子类"],
            "difficulty": "很难",
            "categories": ["必考题"],
        })
        self.assertEqual(len(problems), 4)
        joined = " | ".join(problems)
        self.assertIn("不在 27 个固定类型里", joined)
        self.assertIn("不合法", joined)
        self.assertIn("目录里没有子类", joined)

    def test_check_labels_accepts_catalog_values(self):
        self.assertEqual(taxonomy.check_labels({
            "type": "听力短对话理解", "subtypes": ["选图片"], "difficulty": "适中", "categories": ["优选题"],
        }), [])

    def test_unit_dims_forbid_vocab_in_cloze_unit(self):
        problems = taxonomy.check_unit_dims("完形填空", [
            "语篇体裁/记叙文/故事", "主题/人与自我/做人与做事/自我认识与提升", "词汇/词汇辨析/副词辨析",
        ])
        joined = "\n".join(problems)
        self.assertIn("组级不挂 词汇/语法/设题角度", joined)
        self.assertIn("副词辨析", joined)

    def test_unit_dims_require_genre_and_theme_for_reading(self):
        problems = taxonomy.check_unit_dims("阅读理解", ["设题角度/阅读/细节理解"])
        self.assertEqual(len(problems), 1)
        self.assertIn("缺必带维度：语篇体裁/主题", problems[0])
        self.assertEqual(taxonomy.check_unit_dims("阅读理解", [
            "语篇体裁/说明文", "主题/人与自然/自然生态/动物与植物", "设题角度/阅读/细节理解",
        ]), [])

    def test_unit_dims_ignore_standalone_types(self):
        self.assertEqual(taxonomy.check_unit_dims("单项填空", ["词汇/词汇辨析/动词辨析"]), [])


class CompleteTaxonomyTest(unittest.TestCase):
    GENRE = "语篇体裁/记叙文/故事"
    THEME = "主题/人与自我/生活与学习/兴趣爱好"
    VOCAB = "词汇/词汇辨析/动词辨析"
    READING = "设题角度/阅读/细节理解"
    LISTENING = "设题角度/听力/细节理解"

    def question(self, main_type="阅读理解", paths=None, children=None):
        result = {
            "question_type": main_type,
            "knowledge": paths if paths is not None else [self.GENRE, self.THEME],
            "filterMetadata": {"subtypes": taxonomy.subtypes(main_type)[:1], "categories": ["优选题"]},
        }
        if children is not None:
            result["subquestions"] = children
        return result

    def test_partial_drafts_still_allow_missing_labels(self):
        for draft in ({}, {"type": "阅读理解"}, {"knowledge": [self.READING]}):
            self.assertEqual(taxonomy.check_labels(draft), [])

    def test_all_27_types_enforce_declared_prefixes(self):
        self.assertEqual(set(taxonomy.types()), set(taxonomy.type_knowledge_rules()))
        self.assertEqual(set(taxonomy.types()), set(taxonomy.UNIT_DIMS))
        _, leaves = taxonomy.knowledge_index()
        for main_type in taxonomy.types():
            prefixes = []
            for dim in taxonomy.type_knowledge_rules()[main_type]["dims"]:
                for branch in dim["branch"].split("/"):
                    prefix = branch + ("/" + dim["scope"] if dim.get("scope") else "")
                    prefixes.append("设题角度/阅读" if prefix == "阅读技能" else prefix)
            for path in leaves:
                allowed = any(path.startswith(prefix + "/") for prefix in prefixes)
                with self.subTest(main_type=main_type, path=path):
                    problems = taxonomy.check_labels({"type": main_type, "knowledge": [path]})
                    self.assertEqual(bool(problems), not allowed)

    def test_scoped_angles_in_draft_and_unit_checks(self):
        for main_type, good, bad in (("阅读理解", self.READING, self.LISTENING),
                                     ("听力短文理解", self.LISTENING, self.READING)):
            self.assertEqual(taxonomy.check_labels({"type": main_type, "knowledge": [good]}), [])
            self.assertTrue(taxonomy.check_labels({"type": main_type, "knowledge": [bad]}))
            self.assertIn("超出允许维度/范围", " ".join(taxonomy.check_unit_dims(
                main_type, [self.GENRE, self.THEME, bad])))
        self.assertIn("超出允许维度", " ".join(taxonomy.check_unit_dims("单项填空", [self.THEME])))
        self.assertIn("超出允许维度", " ".join(taxonomy.check_unit_dims("阅读理解", [
            self.GENRE, self.THEME, "未知维度/假叶子"])))

    def test_subtype_cardinality_and_required_values(self):
        self.assertTrue(taxonomy.check_labels({"type": "阅读理解", "subtypes": ["选择3题", "选择4题"]}))
        self.assertEqual(taxonomy.check_labels({"type": "单词拼写", "subtypes": ["首字母", "适当形式"]}), [])
        for main_type in taxonomy.types():
            paths = [self.VOCAB] if main_type == "单词拼写" else [self.THEME]
            item = self.question(main_type, paths)
            item["filterMetadata"]["subtypes"] = []
            joined = " ".join(taxonomy.check_question_labels(item))
            self.assertEqual("必须填写目录子类" in joined, bool(taxonomy.subtypes(main_type)))

    def test_strict_requires_type_and_knowledge(self):
        for item in ({}, {"type": ""}, {"type": "不存在"}, {"type": []},
                     {"type": "阅读理解", "knowledge": []}):
            self.assertTrue(taxonomy.check_question_labels(item))
        item = self.question()
        item["type"] = "单项填空"
        self.assertIn("question_type 与 type 不一致", " ".join(taxonomy.check_question_labels(item)))
        item = self.question("单项填空", [self.VOCAB])
        item["type"] = item.pop("question_type")
        self.assertEqual(taxonomy.check_question_labels(item), [])

    def test_malformed_values_report_instead_of_throwing(self):
        for field in ("knowledge", "subtypes", "categories"):
            for value in ("abc", 1, {}, True, None, [None], [[]], [""]):
                with self.subTest(field=field, value=value):
                    self.assertTrue(taxonomy.check_labels({"type": "阅读理解", field: value}))
        for field in ("knowledge", "subquestions", "filterMetadata", "knowledgeIds", "knowledge_ids"):
            for value in ("abc", 1, {}, True, None):
                item = self.question()
                item[field] = value
                with self.subTest(field=field, value=value):
                    self.assertTrue(taxonomy.check_question_labels(item))
        for value in (None, [], "abc", 1):
            self.assertTrue(taxonomy.check_labels(value))
            self.assertTrue(taxonomy.check_question_labels(value))
            self.assertTrue(taxonomy.check_unit_dims("阅读理解", value))
        self.assertTrue(taxonomy.check_question_labels(self.question(children=["bad", None, []])))

    def test_slashes_inside_leaf_names_are_preserved(self):
        for path in ("设题角度/阅读/单词/句子回填", "语法/代词/不定代词/普通不定代词/some/any",
                     "语篇体裁/说明文/指令/指南/手册"):
            self.assertEqual(taxonomy.resolve_knowledge([path])["resolved"][0]["path"], path)
        for path in ("设题角度/阅读", "语法/代词/不定代词"):
            self.assertEqual(taxonomy.resolve_knowledge([path])["non_leaf"], [path])
        self.assertTrue(taxonomy.check_question_labels(self.question(paths=["设题角度/阅读"])))

    def test_cloze_and_grammar_children_have_answer_rules(self):
        for main_type in ("完形填空", "语法选择"):
            item = self.question(main_type, children=[{"knowledge": [self.VOCAB]}])
            self.assertEqual(taxonomy.check_question_labels(item), [])
            item["knowledge"].append(self.VOCAB)
            self.assertTrue(taxonomy.check_question_labels(item))
            item["knowledge"].pop()
            # 整篇题型子题留空是标准形态；显式标签仍按子题维度校验。
            item["subquestions"] = [{"knowledge": []}]
            self.assertEqual(taxonomy.check_question_labels(item), [])
            for child_paths in ([self.THEME], [self.GENRE], [self.READING]):
                item["subquestions"] = [{"knowledge": child_paths}]
                self.assertTrue(taxonomy.check_question_labels(item))

    def test_reading_child_angle_mandatory_and_genre_forbidden(self):
        for main_type in ("阅读理解", "任务型阅读", "读写综合"):
            item = self.question(main_type, children=[{"knowledge": [self.READING, self.THEME]}])
            self.assertEqual(taxonomy.check_question_labels(item), [])
            # 整篇类题型的子题不做细分标签；给了标签则仍须合法且符合子题维度。
            item["subquestions"] = [{"knowledge": []}]
            self.assertEqual(taxonomy.check_question_labels(item), [])
            item["subquestions"] = [{"knowledge": [self.THEME]}]
            self.assertEqual(taxonomy.check_question_labels(item), [])
            for child_paths in ([self.READING, self.GENRE], [self.LISTENING]):
                item["subquestions"] = [{"knowledge": child_paths}]
                self.assertTrue(taxonomy.check_question_labels(item))

    def test_group_needs_genre_and_theme_and_allows_many_angles(self):
        angles = [p for p in taxonomy.knowledge_index()[1] if p.startswith("设题角度/阅读/")]
        item = self.question(paths=[self.GENRE, self.THEME] + angles,
                             children=[{"knowledge": [angle]} for angle in angles])
        self.assertGreater(len(angles), 10)
        self.assertEqual(taxonomy.check_question_labels(item), [])
        for missing in (self.GENRE, self.THEME):
            item["knowledge"] = [path for path in [self.GENRE, self.THEME] if path != missing]
            self.assertIn("缺必带维度", " ".join(taxonomy.check_question_labels(item)))

    def test_optional_ids_match_resolved_paths_for_units_and_children(self):
        for key in ("knowledgeIds", "knowledge_ids"):
            item = self.question(children=[{"knowledge": [self.READING]}])
            item[key] = [p["id"] for p in taxonomy.resolve_knowledge(item["knowledge"])["resolved"]][::-1]
            child = item["subquestions"][0]
            child[key] = [p["id"] for p in taxonomy.resolve_knowledge(child["knowledge"])["resolved"]]
            self.assertEqual(taxonomy.check_question_labels(item), [])
            child[key] = []
            self.assertIn("与 knowledge", " ".join(taxonomy.check_question_labels(item)))
            child.pop(key)
            item[key] = ["not-a-catalog-id"]
            self.assertIn("与 knowledge", " ".join(taxonomy.check_question_labels(item)))


class MediaAuditTest(unittest.TestCase):
    def test_banner_and_header_are_not_question_images(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            media = folder / "01_source" / "media"
            media.mkdir(parents=True)
            (folder / "01_source" / "正文.txt").write_text(
                "[IMG 木牍中考.png]\n1. What is this?\n[IMG 题图.png]\n( )2. Next?\n", encoding="utf-8")
            # 1x1 PNG 与横幅比例，仅用于体检分类，不校验内容
            png = bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                                "01f15c4890000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082")
            (media / "木牍中考.png").write_bytes(png)
            (media / "题图.png").write_bytes(png)
            result = media_audit.audit(folder, [1, 2])
            verdicts = {item["name"]: item["verdict"] for item in result["items"]}
            self.assertEqual(verdicts["木牍中考.png"], "header")
            self.assertEqual(verdicts["题图.png"], "question")

    def test_exclusions_roundtrip(self):
        with tempfile.TemporaryDirectory() as raw:
            folder = Path(raw)
            (folder / "workflow").mkdir()
            media_audit.save_exclusions(folder, {"excluded": ["image9.jpeg"], "notes": {"image9.jpeg": "装饰"}})
            data = media_audit.load_exclusions(folder)
            self.assertEqual(data["excluded"], ["image9.jpeg"])


class FindingBucketTest(unittest.TestCase):
    def test_label_and_format_fields_split(self):
        findings = [
            {"finding_id": "A", "field": "knowledge", "status": "accepted"},
            {"finding_id": "B", "field": "options", "status": "accepted"},
            {"finding_id": "C", "field": "difficulty", "status": "fixed"},
            {"finding_id": "D", "field": "stem", "status": "open"},
        ]
        label, fmt, other = server.split_findings(findings)
        self.assertEqual([f["finding_id"] for f in label], ["A", "C"])
        self.assertEqual([f["finding_id"] for f in fmt], ["B", "D"])
        self.assertEqual(other, [])

    def test_only_label_findings_mark_row_for_human(self):
        row = {"unit_id": "AH-2025-001", "number": 1, "numbers": [1],
               "stem": "x", "options": [], "answer": "A", "knowledge": [], "type": "听力短对话理解"}
        findings = [{"finding_id": "WF-1", "unit_id": "AH-2025-001", "field": "options", "status": "accepted",
                     "severity": "minor", "question": "选项图片需核对"}]
        marked = server.mark_row(dict(row), findings)
        self.assertFalse(marked["needs_human"])
        self.assertEqual(marked["format_count"], 1)
        findings[0]["field"] = "knowledge"
        marked = server.mark_row(dict(row), findings)
        self.assertTrue(marked["needs_human"])
        self.assertEqual(marked["open_count"], 1)


SAMPLE = Path(__file__).resolve().parent.parent / "workflows" / "_real_2025"
requires_sample = unittest.skipUnless(SAMPLE.is_dir(), "需要本地样例批次 workflows/_real_2025")


@requires_sample
class RealBatchLabelTest(unittest.TestCase):
    def test_overview_counts_label_and_format_separately(self):
        overview = server.overview("_real_2025")
        self.assertEqual(overview["unit_total"], 34)
        self.assertLess(overview["needs_human"], overview["unit_total"])
        self.assertGreater(overview["format_units"], 0)
        self.assertGreater(overview["media"]["suspicious"], 0)

    def test_detail_carries_labels_and_hides_decoration(self):
        detail = server.detail("_real_2025", "AH-2025-073")
        self.assertEqual(detail["numbers"], [73, 74, 75])
        self.assertTrue(detail["labels"]["knowledge"])
        self.assertTrue(detail["labels"]["knowledgeIds"])
        names = {block.get("name") for block in detail["preview_blocks"] if block.get("type") == "image"}
        self.assertNotIn("image20.jpeg", names)
        self.assertIn("image20.jpeg", detail["hidden_media"])

    def test_knowledge_ids_are_resolved_leaves(self):
        detail = server.detail("_real_2025", "AH-2025-001")
        by_path, leaves = taxonomy.knowledge_index()
        for path in detail["labels"]["knowledge"]:
            self.assertIn(path, leaves)


if __name__ == "__main__":
    unittest.main()
