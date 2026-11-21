"""Unit tests for the four performance & context optimizations."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import taxonomy
from workflow import agent_tasks, content_agent, local_agent, types_direct, unit_tasks


class FourOptimizationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="opt-test-")
        self.addCleanup(self.tmp.cleanup)
        self.batch = Path(self.tmp.name).resolve()

    def put(self, rel, value):
        path = self.batch / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def _build_exam_batch(self, *, sync_paper=False):
        body = (
            "Unit 1 Section A (1a-2d) 同步练习\n" if sync_paper else "2016年安徽省中考英语试题\n"
        ) + (
            "1. She reads a book. A. Yes B. No C. Good D. Bad\n"
            "2. Listen and choose. [IMG q2.png]\n"
            "3. This is a happy (快乐的) h______ day.\n"
            "4-6. Read passage A.\n"
            "4. Q4? A. a B. b C. c D. d\n"
            "5. Q5? A. a B. b C. c D. d\n"
            "6. Q6? A. a B. b C. c D. d\n"
            "7-9. Read passage B and answer questions.\n"
            "7. Why?\n8. How?\n9. What?\n"
            "10. Write a composition about your school life.\n"
        )
        self.put("01_source/正文.txt", body)
        self.put("01_source/structure.json", {"sections": [], "groups": []})
        units = [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "单项填空",
             "stem": "1. She reads a book. A. Yes B. No C. Good D. Bad"},
            {"unit_id": "q-2", "unit_kind": "standalone", "numbers": [2], "type": "听力短对话理解",
             "stem": "2. Listen and choose. [IMG q2.png]"},
            {"unit_id": "q-3", "unit_kind": "standalone", "numbers": [3], "type": "单词拼写",
             "stem": "3. This is a happy (快乐的) h______ day."},
            {"unit_id": "group-4-5-6", "unit_kind": "group", "numbers": [4, 5, 6], "type": "阅读理解",
             "stem": "Read passage A."},
            {"unit_id": "group-7-8-9", "unit_kind": "group", "numbers": [7, 8, 9], "type": "阅读理解",
             "stem": "Read passage B and answer questions."},
            {"unit_id": "q-10", "unit_kind": "standalone", "numbers": [10], "type": "书面表达",
             "stem": "10. Write a composition about your school life."},
        ]
        self.put("01_source/structure.confirmed.json", {"version": "1.0", "units": units})
        draft_items = [
            {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1],
             "stem": "She reads a book.", "options": ["A. Yes", "B. No", "C. Good", "D. Bad"]},
            {"unit_id": "q-2", "unit_kind": "standalone", "numbers": [2],
             "stem": "What would Tom like?", "options": ["A.", "B.", "C."],
             "attachments": [{"kind": "image", "url": "01_source/media/img1.jpeg", "placement": "option", "optionKey": "A"}]},
            {"unit_id": "q-3", "unit_kind": "standalone", "numbers": [3],
             "stem": "This is a happy <u>h</u>______(快乐的) day.", "options": []},
            {"unit_id": "group-4-5-6", "unit_kind": "group", "numbers": [4, 5, 6],
             "material": "Passage A has several English words in it.",
             "subquestions": [
                 {"number": 4, "stem": "Q4?", "options": ["A. a", "B. b", "C. c", "D. d"]},
                 {"number": 5, "stem": "Q5?", "options": ["A. a", "B. b", "C. c", "D. d"]},
                 {"number": 6, "stem": "Q6?", "options": ["A. a", "B. b", "C. c", "D. d"]},
             ]},
            # 无选项的阅读题组（实为任务型阅读/阅读问答）：必须留给模型判型，不能直判。
            {"unit_id": "group-7-8-9", "unit_kind": "group", "numbers": [7, 8, 9],
             "material": "Passage B without choices.",
             "subquestions": [
                 {"number": 7, "stem": "Why?", "options": []},
                 {"number": 8, "stem": "How?", "options": []},
                 {"number": 9, "stem": "What?", "options": []},
             ]},
            {"unit_id": "q-10", "unit_kind": "standalone", "numbers": [10],
             "stem": "Write a composition about your school life.", "options": []},
        ]
        self.put("06_batch/question-draft.json", {"items": draft_items})
        return units

    def test_types_direct_classifies_unambiguous_exam_units_only(self):
        self._build_exam_batch()
        direct = types_direct.classify_units(self.batch)
        self.assertEqual(set(direct), {"q-1", "q-2", "q-3", "group-4-5-6"})
        self.assertEqual(direct["q-1"]["subtypes"], ["四个选项"])
        self.assertEqual(direct["q-2"]["subtypes"], ["选图片"])
        self.assertEqual(direct["q-3"]["subtypes"], ["汉语及首字母"])
        self.assertEqual(direct["group-4-5-6"]["subtypes"], ["选择3题"])
        self.assertGreater(direct["q-1"]["words"], 0)
        for row in direct.values():
            self.assertEqual(taxonomy.check_labels(row), [])
        # 无选项阅读题组与书面表达留给模型
        self.assertNotIn("group-7-8-9", direct)
        self.assertNotIn("q-10", direct)

    def test_types_direct_skips_sync_practice_papers(self):
        self._build_exam_batch(sync_paper=True)
        self.assertEqual(types_direct.classify_units(self.batch), {})

    def test_local_agent_merges_direct_and_model_types_in_order(self):
        self._build_exam_batch()
        called_ids = []

        def fake_invoke(prompt, run_dir, config, **kwargs):
            ctx = json.loads(prompt.split(unit_tasks._BEGIN, 1)[1].rsplit(unit_tasks._END, 1)[0])
            uids = [u["unit_id"] for u in ctx["confirmed_units_with_stable_ids"]]
            called_ids.extend(uids)
            items = []
            for uid in uids:
                if uid == "group-7-8-9":
                    items.append({"unit_id": uid, "type": "任务型阅读", "subtypes": ["阅读问答"],
                                  "words": 10, "contentComplete": True, "evidence": ["正文 L9"]})
                elif uid == "q-10":
                    items.append({"unit_id": uid, "type": "书面表达", "subtypes": ["列中文要点"],
                                  "words": 8, "contentComplete": True, "evidence": ["正文 L13"]})
            return {"response": {"status": "completed",
                                 "artifacts": {"03_types/types.json": {"items": items}},
                                 "notes": [], "findings": []}}

        cfg = {"max_input_chars": 180000, "timeout_seconds": 30, "retries": 0,
               "label_split": False, "unit_split": False, "types_direct": True}
        with patch.object(local_agent, "load_config", return_value=cfg), \
             patch.object(local_agent, "_agent_lock", return_value=contextlib.nullcontext()), \
             patch.object(local_agent, "_part_model_binding", return_value=None), \
             patch.object(local_agent, "invoke", side_effect=fake_invoke), \
             contextlib.redirect_stdout(io.StringIO()):
            code = local_agent.main(["--stage", "types", "--batch-dir", str(self.batch)])
        self.assertEqual(code, 0)
        self.assertEqual(called_ids, ["group-7-8-9", "q-10"])
        saved = json.loads((self.batch / "03_types/types.json").read_text(encoding="utf-8"))
        self.assertEqual([r["unit_id"] for r in saved["items"]],
                         ["q-1", "q-2", "q-3", "group-4-5-6", "group-7-8-9", "q-10"])

    def test_balanced_groups_spreads_heavy_passages_across_shards(self):
        units = [{"unit_id": f"q-{i}", "numbers": [i]} for i in range(1, 10)]
        units += [
            {"unit_id": "g-1", "numbers": [10, 11, 12, 13]},
            {"unit_id": "g-2", "numbers": [14, 15, 16, 17]},
            {"unit_id": "g-3", "numbers": [18, 19, 20, 21]},
        ]
        drafts = {f"q-{i}": {"stem": "short question"} for i in range(1, 10)}
        for g in ("g-1", "g-2", "g-3"):
            drafts[g] = {"material": "long passage " * 300}
        shards = unit_tasks._balanced_groups(units, 4, drafts)
        self.assertEqual(len(shards), 3)
        # 3 篇重阅读必须分散到 3 个不同分片，而非挤在最后一个分片
        heavy_per_shard = [sum(1 for u in s if u["unit_id"].startswith("g-")) for s in shards]
        self.assertEqual(heavy_per_shard, [1, 1, 1])

    def test_prune_knowledge_catalog_filters_to_shard_types_only(self):
        _, leaves = taxonomy.knowledge_index()
        subset = {
            agent_tasks.ARTIFACTS["types"]: {
                "items": [{"unit_id": "group-4-5-6", "type": "阅读理解", "subtypes": ["选择3题"]}]
            },
            "references/knowledge-catalog.json": {"paths": sorted(leaves)},
            "references/type-knowledge-map.json": {
                "_comment": "map",
                "by_type": {
                    "单项填空": {"allowed": ["词汇", "语法"]},
                    "阅读理解": {"allowed": ["语篇体裁", "主题", "设题角度"]},
                },
            },
        }
        before_count = len(subset["references/knowledge-catalog.json"]["paths"])
        unit_tasks._prune_knowledge_catalog(subset)
        after_paths = subset["references/knowledge-catalog.json"]["paths"]
        self.assertLess(len(after_paths), before_count)
        self.assertTrue(all(taxonomy.knowledge_path_allowed(p, "阅读理解") for p in after_paths))
        self.assertEqual(set(subset["references/type-knowledge-map.json"]["by_type"]), {"阅读理解"})

    def test_child_prompt_omits_static_docs_and_inlines_types_evidence(self):
        context = {
            "confirmed_units_with_stable_ids": [
                {"unit_id": "q-1", "unit_kind": "standalone", "numbers": [1], "type": "单项填空"}
            ],
            "06_batch/question-draft.json": {
                "items": [{"unit_id": "q-1", "stem": "She reads.", "options": ["A", "B", "C", "D"]}]
            },
            "07_review/structure-gate.json": {"passed": True, "details": "x" * 2000},
            "skills-reference/README.md": "Long readme " * 100,
            "docs/content-labeling.md": "Long labeling doc " * 100,
            "references/filter-framework.json": {"types": ["单项填空"]},
        }
        prompt = "Stage: types\n" + unit_tasks._BEGIN + json.dumps(context, ensure_ascii=False) + unit_tasks._END
        rendered = content_agent._child_prompt(prompt, context, "types", [])
        summary = json.loads(rendered.split(unit_tasks._BEGIN, 1)[1].rsplit(unit_tasks._END, 1)[0])
        self.assertNotIn("07_review/structure-gate.json", summary)
        self.assertNotIn("skills-reference/README.md", summary)
        self.assertNotIn("docs/content-labeling.md", summary)
        self.assertNotIn("references/filter-framework.json", summary)
        self.assertIn("supplied_unit_evidence", summary)
        self.assertEqual(len(summary["supplied_unit_evidence"]), 1)
        self.assertIn("Do not call read_unit again", rendered)

    def test_types_direct_classifies_qa_reading_and_chinese_bullet_writing(self):
        self.put("01_source/正文.txt", "Ⅰ.阅读理解\nⅡ.书面表达\n")
        units = [
            {"unit_id": "group-88-89-90", "unit_kind": "group", "numbers": [88, 89, 90], "type": "阅读理解"},
            {"unit_id": "q-96", "unit_kind": "standalone", "numbers": [96], "type": "书面表达"},
            {"unit_id": "q-97", "unit_kind": "standalone", "numbers": [97], "type": "书面表达"},
            {"unit_id": "q-98", "unit_kind": "standalone", "numbers": [98], "type": "书面表达"},
        ]
        self.put("01_source/structure.confirmed.json", {"units": units})
        draft_items = [
            {
                "unit_id": "group-88-89-90",
                "unit_kind": "group",
                "numbers": [88, 89, 90],
                "stem": "阅读下面短文,并用英语回答问题(请注意每小题后面的词数要求)。",
                "material": "There was once an old well.",
                "subquestions": [
                    {"number": 88, "stem": "What were the buckets tied to? (不超过10个词)", "options": []},
                    {"number": 89, "stem": "Where did they talk?", "options": []},
                    {"number": 90, "stem": "What does it tell us?", "options": []},
                ],
            },
            {
                "unit_id": "q-96",
                "unit_kind": "standalone",
                "numbers": [96],
                "stem": (
                    "请你根据以下提示用英文写一封电子邮件。\n"
                    "1.时间:7月1日下午2点;\n"
                    "2.地点:学校操场;\n"
                    "3.内容:文化展览、节目表演等。\n"
                    "注意:1.词数80~100;\n2.可适当增加细节。"
                ),
                "options": [],
            },
            {
                "unit_id": "q-97",
                "unit_kind": "standalone",
                "numbers": [97],
                "stem": "根据下面思维导图提示写一篇短文。\n1.学习方法;\n2.时间安排。",
                "options": [],
            },
            {
                "unit_id": "q-98",
                "unit_kind": "standalone",
                "numbers": [98],
                "stem": "看图写作。[IMG pic.png]\n1.地点:公园;\n2.事件:植树。",
                "options": [],
            },
        ]
        self.put("06_batch/question-draft.json", {"items": draft_items})
        direct = types_direct.classify_units(self.batch)
        self.assertEqual(set(direct), {"group-88-89-90", "q-96"})
        self.assertEqual(direct["group-88-89-90"]["type"], "任务型阅读")
        self.assertEqual(direct["group-88-89-90"]["subtypes"], ["阅读问答"])
        self.assertEqual(direct["q-96"]["type"], "书面表达")
        self.assertEqual(direct["q-96"]["subtypes"], ["列中文要点"])
        for row in direct.values():
            self.assertEqual(taxonomy.check_labels(row), [])


if __name__ == "__main__":
    unittest.main()
