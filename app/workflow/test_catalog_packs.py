# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench import taxonomy
from workflow import agent_tasks, catalog_packs, review, review_rounds, unit_tasks


class CatalogPacksTest(unittest.TestCase):
    def test_materialized_packs_verify_against_authoritative_catalog(self):
        report = catalog_packs.verify_packs()
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["total_leaves"], 408)
        self.assertEqual(report["errors"], [])
        loaded = catalog_packs.load_packs()
        self.assertEqual(loaded["source"], "materialized")
        self.assertEqual(set(loaded["packs"]), set(catalog_packs.PACK_ORDER))
        base_union = [p for name in catalog_packs.BASE_PACKS for p in loaded["packs"][name]["paths"]]
        self.assertEqual(len(base_union), len(set(base_union)))
        self.assertEqual(set(base_union), taxonomy.knowledge_index()[1])

    def test_all_27_types_match_taxonomy_selectability_exactly(self):
        by_path, leaves = taxonomy.knowledge_index()
        all_paths = list(by_path.keys())
        for ty in taxonomy.types():
            with self.subTest(question_type=ty):
                expected = [p for p in all_paths if taxonomy._is_selectable_for_type(p, ty, leaves)]
                info = catalog_packs.packs_for_types([ty])
                self.assertFalse(info["fallback"])
                self.assertEqual(info["paths"], expected)
                self.assertNotEqual(catalog_packs.bucket_for_type(ty), "unknown")

    def test_specialized_packs_physically_enforce_hard_rules(self):
        # 单词拼写：必须用 vocab-spelling，物理排除「词汇/词汇辨析/*」和非叶子「语音/基本读音」
        spelling = catalog_packs.packs_for_types(["单词拼写"])
        self.assertEqual(spelling["packs"], ["vocab-spelling", "grammar", "phonetics"])
        self.assertFalse(any(p.startswith("词汇/词汇辨析") for p in spelling["paths"]))
        self.assertIn("词汇/拼写与词形", spelling["paths"])
        self.assertNotIn("语音/基本读音", spelling["paths"])
        self.assertFalse(any(p.startswith(("主题/", "语篇体裁/", "设题角度/", "语用/")) for p in spelling["paths"]))

        # 听力关键词语选择：只能命中精确节点「语音/基本读音」
        kw = catalog_packs.packs_for_types(["听力关键词语选择"])
        self.assertEqual(kw["packs"], ["phonetics-keyword"])
        self.assertEqual(kw["paths"], ["语音/基本读音"])
        self.assertEqual(kw["exact_paths_by_type"], {"听力关键词语选择": ["语音/基本读音"]})

        # 语音题 & 单项填空：不得泄露非叶子精确节点「语音/基本读音」
        phon = catalog_packs.packs_for_types(["语音题"])
        self.assertEqual(phon["packs"], ["phonetics"])
        self.assertEqual(len(phon["paths"]), 12)
        self.assertNotIn("语音/基本读音", phon["paths"])

        mc = catalog_packs.packs_for_types(["单项填空"])
        self.assertEqual(mc["packs"], ["vocab-full", "grammar", "phonetics", "pragmatics"])
        self.assertNotIn("语音/基本读音", mc["paths"])
        self.assertFalse(any(p.startswith(("主题/", "语篇体裁/", "设题角度/")) for p in mc["paths"]))

        # 完形填空：仅 theme + genre，零语法、零设题角度
        cloze = catalog_packs.packs_for_types(["完形填空"])
        self.assertEqual(cloze["packs"], ["theme", "genre"])
        self.assertFalse(any(p.startswith(("语法/", "词汇/", "设题角度/", "语用/", "语音/")) for p in cloze["paths"]))

        # 阅读理解：仅 theme + genre + angle-reading，零语法、零听力角度
        reading = catalog_packs.packs_for_types(["阅读理解"])
        self.assertEqual(reading["packs"], ["theme", "genre", "angle-reading"])
        self.assertFalse(any(p.startswith(("语法/", "设题角度/听力")) for p in reading["paths"]))

        # 听力短对话/长对话：仅 theme + angle-listening，物理不含语篇体裁
        ldlg = catalog_packs.packs_for_types(["听力短对话理解", "听力长对话理解"])
        self.assertEqual(ldlg["packs"], ["theme", "angle-listening"])
        self.assertFalse(any(p.startswith(("语篇体裁/", "语法/", "设题角度/阅读")) for p in ldlg["paths"]))

        # 完成句子：仅 vocab-full + grammar，物理不含语用/语音
        comp = catalog_packs.packs_for_types(["完成句子"])
        self.assertEqual(comp["packs"], ["vocab-full", "grammar"])
        self.assertFalse(any(p.startswith(("语用/", "语音/", "主题/", "语篇体裁/")) for p in comp["paths"]))

    def test_stale_or_corrupted_packs_never_served_silently(self):
        with tempfile.TemporaryDirectory() as tmp:
            packs_dir = Path(tmp) / "catalog-packs"
            report = catalog_packs.write_packs(packs_dir=packs_dir)
            self.assertTrue(report["ok"])
            self.assertEqual(catalog_packs.load_packs(packs_dir=packs_dir)["source"], "materialized")

            # 篡改单个包文件：校验失败且运行时自动回退到 live_fallback，不采用篡改内容
            theme_file = packs_dir / "theme.json"
            doc = json.loads(theme_file.read_text(encoding="utf-8"))
            doc["paths"] = ["主题/伪造路径"]
            theme_file.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            catalog_packs._LOAD_CACHE.clear()
            bad_report = catalog_packs.verify_packs(packs_dir=packs_dir)
            self.assertFalse(bad_report["ok"])
            self.assertTrue(any("theme" in err for err in bad_report["errors"]))
            fallback_loaded = catalog_packs.load_packs(packs_dir=packs_dir)
            self.assertEqual(fallback_loaded["source"], "live_fallback")
            self.assertNotIn("主题/伪造路径", fallback_loaded["packs"]["theme"]["paths"])
            self.assertEqual(len(fallback_loaded["packs"]["theme"]["paths"]), 52)

            # 篡改 manifest source_sha256：同样拒绝陈旧目录包
            catalog_packs.write_packs(packs_dir=packs_dir)
            manifest_file = packs_dir / "manifest.json"
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            manifest["source_sha256"] = "0" * 64
            manifest_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            catalog_packs._LOAD_CACHE.clear()
            self.assertFalse(catalog_packs.verify_packs(packs_dir=packs_dir)["ok"])
            self.assertEqual(catalog_packs.load_packs(packs_dir=packs_dir)["source"], "live_fallback")

    def test_unknown_or_empty_types_fall_back_safely(self):
        full = catalog_packs.packs_for_types([])
        self.assertTrue(full["fallback"])
        for bad in ([], [None], ["未知题型"], ["阅读理解", "未知题型"]):
            with self.subTest(types=bad):
                res = catalog_packs.packs_for_types(bad)
                self.assertTrue(res["fallback"])
                self.assertEqual(res["paths"], full["paths"])
                compact = review.compact_knowledge_catalog(bad)
                self.assertEqual(compact["paths"], full["paths"])

    def test_knowledge_stage_bucket_aligned_sharding_and_order_preservation(self):
        _, leaves = taxonomy.knowledge_index()
        all_paths = sorted(leaves) + ["语音/基本读音"]
        unit_specs = [
            ("u1", "单词拼写"),
            ("u2", "单词拼写"),
            ("u3", "完成句子"),
            ("u4", "完形填空"),
            ("u5", "阅读理解"),
            ("u6", "语音题"),
        ]
        units = [{"unit_id": uid, "numbers": [i], "unit_kind": "standalone"}
                 for i, (uid, _ty) in enumerate(unit_specs, 1)]
        context = {
            "confirmed_units_with_stable_ids": units,
            "01_source/正文.txt": "\n".join(f"{i}. Sample stem {i}." for i in range(1, 7)),
            agent_tasks.ARTIFACTS["types"]: {
                "items": [{"unit_id": uid, "type": ty, "subtypes": []} for uid, ty in unit_specs]
            },
            "references/knowledge-catalog.json": {"paths": all_paths},
            "references/type-knowledge-map.json": {
                "_comment": "map",
                "by_type": {ty: {"dims": []} for _, ty in unit_specs},
            },
        }
        base = {
            "prompt": "header\n" + unit_tasks._BEGIN + json.dumps(context, ensure_ascii=False) + unit_tasks._END,
            "preconditions": [],
            "input_hashes": {"source": "h"},
        }
        with patch.object(agent_tasks, "build_task", return_value=base):
            _, parts = unit_tasks.build_parts("unused", "knowledge", 600000, max_units=8, balance_work=True)

        self.assertEqual(
            [p["unit_ids"] for p in parts],
            [["u1", "u2"], ["u3"], ["u4"], ["u5"], ["u6"]],
        )
        seen_packs = []
        for part in parts:
            body = part["prompt"].split(unit_tasks._BEGIN, 1)[1].rsplit(unit_tasks._END, 1)[0]
            cat = json.loads(body)["references/knowledge-catalog.json"]
            seen_packs.append(cat["packs"])
            self.assertNotIn("语音/基本读音", cat["paths"])
        self.assertEqual(seen_packs, [
            ["vocab-spelling", "grammar", "phonetics"],
            ["vocab-full", "grammar"],
            ["theme", "genre"],
            ["theme", "genre", "angle-reading"],
            ["phonetics"],
        ])

    def test_review_rounds_bucket_aligned_sharding_keeps_listening_split_and_blinding(self):
        units = [
            {"unit_id": "u1", "unit_kind": "standalone", "numbers": [1], "type": "听力短对话理解",
             "stem": "1. Listening Q1?\nA. a B. b C. c"},
            {"unit_id": "u2", "unit_kind": "standalone", "numbers": [2], "type": "单词拼写",
             "stem": "2. Spell word (happy)."},
            {"unit_id": "u3", "unit_kind": "standalone", "numbers": [3], "type": "单词拼写",
             "stem": "3. Spell word (kind)."},
            {"unit_id": "u4", "unit_kind": "standalone", "numbers": [4], "type": "完形填空",
             "stem": "4. Cloze passage."},
            {"unit_id": "u5", "unit_kind": "standalone", "numbers": [5], "type": "阅读理解",
             "stem": "5. Reading passage."},
        ]
        types_items = [
            {"unit_id": u["unit_id"], "type": u["type"], "subtypes": []}
            for u in units
        ]
        batch_items = [
            {"unit_id": u["unit_id"], "question_type": u["type"],
             "knowledge": ["SECRET_KNOWLEDGE"], "filterMetadata": {"subtypes": ["SECRET_SUBTYPE"]},
             "stem": u["stem"]}
            for u in units
        ]
        context = {
            "confirmed_units_with_stable_ids": units,
            "01_source/正文.txt": "\n".join(u["stem"] for u in units),
            "03_types/types.json": {"items": types_items},
            "04_knowledge/knowledge.json": {"items": [{"unit_id": u["unit_id"], "knowledge": ["SECRET_KNOWLEDGE"]} for u in units]},
            "06_batch/batch_meta.json": {"items": batch_items},
            "02_audio/full-transcript.json": {"text": "audio transcript"},
            "references/knowledge-catalog.json": json.loads(taxonomy.KNOWLEDGE_PATH.read_text(encoding="utf-8-sig")),
            "references/type-knowledge-map.json": json.loads(taxonomy.TYPE_KNOWLEDGE_PATH.read_text(encoding="utf-8-sig")),
        }
        base = {
            "prompt": "header\n" + unit_tasks._BEGIN + json.dumps(context, ensure_ascii=False) + unit_tasks._END,
            "preconditions": [],
            "input_hashes": {},
        }
        parts = review_rounds._partition_contexts(base, 600000, max_units=8, round_id="labels")
        self.assertEqual([p["unit_ids"] for p in parts], [["u1"], ["u2", "u3"], ["u4"], ["u5"]])

        packs_by_part = []
        for idx, part in enumerate(parts):
            self.assertNotIn("SECRET_KNOWLEDGE", part["context"])
            self.assertNotIn("SECRET_SUBTYPE", part["context"])
            ctx = json.loads(part["context"].split("\n", 1)[1])
            cat = ctx["references/knowledge-catalog.json"]
            packs_by_part.append(cat["packs"])
            if idx == 0:
                self.assertIn("02_audio/full-transcript.json", ctx)
            else:
                self.assertNotIn("02_audio/full-transcript.json", ctx)
        self.assertEqual(packs_by_part, [
            ["theme", "angle-listening"],
            ["vocab-spelling", "grammar", "phonetics"],
            ["theme", "genre"],
            ["theme", "genre", "angle-reading"],
        ])


if __name__ == "__main__":
    unittest.main()
