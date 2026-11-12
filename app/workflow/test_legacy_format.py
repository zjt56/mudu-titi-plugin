"""Deterministic, isolated legacy compatibility regressions; no real batch writes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from workflow import denoise, extract, legacy_format, structure_gate
from workflow.test_format_gate import group, question


class LegacyFormatTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="legacy-format-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.env = patch.dict(os.environ, {"TMPDIR": str(self.root), "WF_STRUCTURE_AI": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.network = patch("socket.socket.connect", side_effect=AssertionError("network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.process = patch("subprocess.Popen", side_effect=AssertionError("model/process forbidden"))
        self.process.start()
        self.addCleanup(self.process.stop)

    def put(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def fixture(self, listening=False):
        if listening:
            body = ("Ⅰ.关键词语选择(共5小题)\n"
                    "你将听到五个句子。请在每小题所给的A、B、C三个选项中选出一个你所听到的单词或短语。\n"
                    + "\n".join(f"{n}.A.part{n}\tB.pass{n}\tC.place{n}\t()" for n in range(1, 6)))
        else:
            body = ("Ⅰ.阅读理解(共3小题)\nA\n"
                    "The source title.\nThe source introduction.\n"
                    "[TABLE 1]\nFirst game<br>Children play outside.\n"
                    "Second game<br>Children play inside.\n[/TABLE]\n"
                    "71.Where do children play?\nA.Outside B.Inside C.Home D.School\n"
                    "72.What do children do?\nA.Play B.Read C.Sleep D.Walk\n"
                    "73.Who plays games?\nA.Children B.Cats C.Birds D.Dogs")
        structure, body, _ = extract.infer_structure(body)
        cleaned = denoise.clean_body(body)[0]
        units = structure_gate._build_units(structure.get("sections") or [],
                                            structure.get("groups") or [], cleaned.splitlines())
        self.assertEqual([u["numbers"] for u in units], [[n] for n in range(1, 6)] if listening else [[71, 72, 73]])
        self.put("01_source/structure.json", structure)
        self.put("01_source/structure.confirmed.json", {"units": units})
        (self.root / legacy_format.SOURCE_PATHS[0]).write_text(body, encoding="utf-8")
        items = []
        for unit in units:
            ns = unit["numbers"]
            row = question(ns[0]) if listening else group()
            row.update(id=f"q-{ns[0]}", unit_id=f"q-{ns[0]}", number=ns[0], numbers=ns,
                       question_type=unit["type"], unit_kind=unit["unit_kind"],
                       attachments=[{"kind": "audio", "url": f"/source-media/f/seg-{ns[0]}.mp3"}],
                       material="", subquestions=[])
            parts = legacy_format.question_content.audit_source_unit(unit)["questions"]
            if listening:
                part = parts[ns[0]]
                row.update(stem="", filterMetadata={"subtypes": []},
                           options=[f"{o['label']}. {o['text']}" for o in part["options"]])
            else:
                row.update(stem="The source title.",
                           material="The source introduction.\nFirst game\nChildren play outside.\nSecond game\nChildren play inside.",
                           options=[], filterMetadata={"subtypes": ["选择3题"]},
                           materialBlocks=[{"type": "paragraph", "rows": []},
                                           {"type": "table", "rows": [
                                               {"cells": [{"text": "First game\nChildren play outside."}]},
                                               {"cells": [{"text": "Second game\nChildren play inside."}]}]}],
                           subquestions=[{"number": n, "stem": parts[n]["stem"],
                                          "options": [f"{o['label']}. {o['text']}" for o in parts[n]["options"]],
                                          "answer": "A", "explanation": "无", "knowledge": []} for n in ns])
            items.append(row)
        batch = {"batch_id": "fixture", "name": "fixture source", "items": items}
        for relative in legacy_format.BATCH_PATHS:
            self.put(relative, batch)
        return batch

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def modify(self, batch, func):
        changed = deepcopy(batch)
        func(changed["items"][0])
        for path in legacy_format.BATCH_PATHS:
            self.put(path, changed)
        return changed

    def assert_refused(self, result):
        self.assertFalse(result["passed"], result)
        self.assertEqual(result["files"], {})
        self.assertTrue(result["refused"])

    def test_empty_paragraph_removed_tables_attachments_and_everything_else_preserved(self):
        original = self.fixture()
        before = self.snapshot()
        result = legacy_format.prepare(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(result["installed"])
        self.assertFalse(result["quality_passed"])
        self.assertEqual(len(result["changes"]), 3)
        expected = deepcopy(original)
        del expected["items"][0]["materialBlocks"][0]
        for relative, payload in result["files"].items():
            self.assertEqual(payload, expected)
            self.assertTrue(result["checks"][relative]["source_before"]["passed"])
            self.assertTrue(result["checks"][relative]["source_after"]["passed"])
        self.assertEqual(set(result["files"]), set(legacy_format.BATCH_PATHS))
        self.assertTrue(result["checks"]["final_format"]["passed"])
        self.assertTrue(result["checks"]["final_format"]["prepare_upload_required"])
        self.assertEqual(result, legacy_format.prepare(self.root))

    def test_duplicate_text_requires_unique_main_owner_and_exact_source(self):
        batch = self.fixture()
        self.modify(batch, lambda row: row["materialBlocks"].__setitem__(0,
                    {"type": "text", "text": "The source introduction."}))
        result = legacy_format.prepare(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["changes"][0]["evidence"]["unique_owner"], "material")
        self.assertEqual(result["changes"][0]["action"], "remove_duplicate_block")

    def test_nonduplicate_prose_is_refused_instead_of_lost_or_guessed_merge(self):
        batch = self.fixture()
        for text in ("Missing new prose.", "The source introduction!", "the source introduction."):
            with self.subTest(text=text):
                self.modify(batch, lambda row: row["materialBlocks"].__setitem__(0,
                            {"type": "paragraph", "text": text}))
                before = self.snapshot()
                self.assert_refused(legacy_format.prepare(self.root))
                self.assertEqual(self.snapshot(), before)

    def test_ambiguous_unknown_or_attached_blocks_are_not_discarded(self):
        batch = self.fixture()
        for block in ({"type": "paragraph", "text": "The source introduction.", "attachments": []},
                      {"type": "paragraph", "rows": [{"text": "x"}]},
                      {"type": "paragraph", "text": "", "image": "x.png"},
                      {"type": "paragraph", "text": None},
                      {"type": "image", "src": "/source-media/f/x.png"},
                      {"type": "text", "text": "The source title.", "value": "The source introduction."}):
            with self.subTest(block=block):
                self.modify(batch, lambda row: row["materialBlocks"].__setitem__(0, block))
                self.assert_refused(legacy_format.prepare(self.root))

    def test_multiple_main_occurrences_are_ambiguous(self):
        batch = self.fixture()
        def mutate(row):
            row["stem"] += "\nThe source introduction."
            row["materialBlocks"][0] = {"type": "text", "text": "The source introduction."}
        self.modify(batch, mutate)
        self.assert_refused(legacy_format.prepare(self.root))

    def test_listening_retains_only_exact_printed_number_and_preserves_audio_options(self):
        batch = self.fixture(listening=True)
        before = self.snapshot()
        result = legacy_format.prepare(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(result["changes"]), 15)
        expected = deepcopy(batch)
        for n, row in enumerate(expected["items"], 1):
            row["stem"] = f"{n}."
        for payload in result["files"].values():
            self.assertEqual(payload, expected)
        for change in result["changes"]:
            self.assertEqual(change["evidence"]["option_labels"], list("ABC"))
            self.assertEqual(change["evidence"]["source_stem"], "")

    def test_actual_section_and_printed_punctuation_are_required(self):
        self.fixture(listening=True)
        source = self.root / legacy_format.SOURCE_PATHS[0]
        source.write_text(source.read_text().replace("1.A.", "1．A."), encoding="utf-8")
        confirmed = json.loads((self.root / legacy_format.SOURCE_PATHS[2]).read_text())
        confirmed["units"][0]["stem"] = confirmed["units"][0]["stem"].replace("1.A.", "1．A.")
        self.put(legacy_format.SOURCE_PATHS[2], confirmed)
        result = legacy_format.prepare(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["files"][legacy_format.BATCH_PATHS[-1]]["items"][0]["stem"], "1．")
        source.write_text(source.read_text().replace("Ⅰ.关键词语选择", "Ⅰ.其他选择"), encoding="utf-8")
        self.assert_refused(legacy_format.prepare(self.root))

    def test_true_missing_stem_wrong_type_or_options_refused(self):
        batch = self.fixture(listening=True)
        for mutation in (lambda row: row.update(question_type="听力短对话理解"),
                         lambda row: row["options"].__setitem__(0, "A. changed"),
                         lambda row: row.update(options=["A. part1", "C. place1"]),
                         lambda row: row.update(number=2)):
            with self.subTest(mutation=mutation):
                self.modify(batch, mutation)
                self.assert_refused(legacy_format.prepare(self.root))

    def test_real_printed_question_cannot_be_replaced_by_number(self):
        self.fixture(listening=True)
        path = self.root / legacy_format.SOURCE_PATHS[0]
        path.write_text(path.read_text().replace("1.A.", "1.What did you hear? A."), encoding="utf-8")
        # Even if confirmed data were also changed, the parsed source has a stem.
        confirmed = json.loads((self.root / legacy_format.SOURCE_PATHS[2]).read_text())
        confirmed["units"][0]["stem"] = confirmed["units"][0]["stem"].replace("1.A.", "1.What did you hear? A.")
        self.put(legacy_format.SOURCE_PATHS[2], confirmed)
        self.assert_refused(legacy_format.prepare(self.root))

    def test_stale_confirmation_duplicate_source_and_missing_source_refused(self):
        batch = self.fixture(listening=True)
        confirmed_path = self.root / legacy_format.SOURCE_PATHS[2]
        original = confirmed_path.read_bytes()
        data = json.loads(original)
        data["units"][0]["stem"] = data["units"][0]["stem"].replace("part1", "other")
        self.put(legacy_format.SOURCE_PATHS[2], data)
        self.assert_refused(legacy_format.prepare(self.root))
        confirmed_path.write_bytes(original)
        source_path = self.root / legacy_format.SOURCE_PATHS[0]
        source = source_path.read_text()
        source_path.write_text(source + "\n1.A.part1\tB.pass1\tC.place1\t()", encoding="utf-8")
        self.assert_refused(legacy_format.prepare(self.root))
        source_path.write_text(source, encoding="utf-8")
        source_path.unlink()  # Only this test's verified temporary fixture.
        self.assert_refused(legacy_format.prepare(self.root))

    def test_final_format_failure_cannot_be_claimed_as_pass(self):
        batch = self.fixture()
        self.modify(batch, lambda row: row.update(difficulty="invalid"))
        result = legacy_format.prepare(self.root)
        self.assert_refused(result)
        self.assertFalse(result["checks"]["final_format"]["passed"])

    def test_idempotent_noop_after_external_install_in_temporary_fixture(self):
        self.fixture(listening=True)
        result = legacy_format.prepare(self.root)
        self.assertTrue(result["passed"], result)
        for relative, payload in result["files"].items():
            self.put(relative, payload)
        before = self.snapshot()
        result = legacy_format.prepare(self.root)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["files"], {})
        self.assertEqual(result["changes"], [])
        self.assertEqual(self.snapshot(), before)

    def test_duplicate_json_keys_symlinks_and_input_drift_refused(self):
        self.fixture()
        target = self.root / legacy_format.BATCH_PATHS[0]
        target.write_text('{"items":[],"items":[]}', encoding="utf-8")
        self.assert_refused(legacy_format.prepare(self.root))
        self.fixture()
        real = legacy_format.question_content.audit_batch_source
        changed = False
        def drift(root, data):
            nonlocal changed
            audit = real(root, data)
            if not changed:
                changed = True
                path = self.root / legacy_format.SOURCE_PATHS[0]
                path.write_text(path.read_text() + "\n", encoding="utf-8")
            return audit
        with patch.object(legacy_format.question_content, "audit_batch_source", side_effect=drift):
            self.assert_refused(legacy_format.prepare(self.root))
        link = self.root / "batch-link"
        link.symlink_to(self.root, target_is_directory=True)
        self.assert_refused(legacy_format.prepare(link))


class SectionScopedIdentityTest(unittest.TestCase):
    """同步练习课时题号每大题从 1 重排，身份键必须纳入题号作用域。

    回归：Ⅰ.1 与 Ⅱ.1 都写作 numbers=[1]，旧实现只按题号做键，会把跨大题的
    同一题号误判为「来源单元题号重复」，导致 repair_source_tables/options 连
    预演都跑不动（同步练习册结构闸门 + 答案阶段反复卡）。
    """

    def test_identity_key_distinguishes_same_numbers_across_sections(self):
        a = {"section_id": "sec-aaaa", "numbers": [1]}
        b = {"section_id": "sec-bbbb", "numbers": [1]}
        self.assertEqual(legacy_format._key(a), legacy_format._key(b))  # 纯题号相同
        self.assertNotEqual(legacy_format._identity_key(a), legacy_format._identity_key(b))

    def test_identity_key_falls_back_to_numbers_without_section(self):
        row = {"numbers": [1]}
        self.assertEqual(legacy_format._identity_key(row), (None, (1,)))

    def test_source_units_accepts_number_restart_across_sections(self):
        # 两个大题各自从 1 开始；旧实现第二次遇到 (1,) 就抛「来源单元题号重复」。
        confirmed = {"units": [
            {"section_id": "sec-aaaa", "numbers": [1], "unit_kind": "standalone"},
            {"section_id": "sec-bbbb", "numbers": [1], "unit_kind": "standalone"},
        ]}
        structure = {"sections": [], "groups": []}
        by_key, rebuilt, _ = legacy_format._source_units(confirmed, structure, "")
        keys = set(by_key)
        self.assertEqual(len(keys), 2)  # 两个作用域各自独立，不再合并/误判重复
        self.assertIn(("sec-aaaa", (1,)), keys)
        self.assertIn(("sec-bbbb", (1,)), keys)

    def test_source_units_still_rejects_true_duplicate_within_section(self):
        # 同一 section 内真正重复的题号仍必须报错，不能因作用域放宽而漏判。
        confirmed = {"units": [
            {"section_id": "sec-aaaa", "numbers": [1], "unit_kind": "standalone"},
            {"section_id": "sec-aaaa", "numbers": [1], "unit_kind": "standalone"},
        ]}
        with self.assertRaisesRegex(ValueError, "来源单元题号重复"):
            legacy_format._source_units(confirmed, {"sections": [], "groups": []}, "")


if __name__ == "__main__":
    unittest.main()
