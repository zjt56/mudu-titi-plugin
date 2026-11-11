"""试卷名与来源：识别值优先，其次原卷 Word 文件名，最后才是「未提供」。

课时拆分件正文只有「七上 · 人教版 · 课时A计划」这类批注，没有试卷卷头。
结构闸门如果只从正文取 meta，就会把已经按文件名识别出的来源整体写成占位，
工作台卡片、题目 filterMetadata 和上传载荷都跟着变成「未提供」。
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workflow import assemble_local, batch_fill, metadata_policy, structure_gate
from workbench import server

BODY = ("Ⅰ.单项填空(共1小题)\n"
        "1. Choose the correct answer: She ____ a book every day after school.\n"
        "A. reads B. read C. reading D. to read")


def _write_batch(root: Path, *, original: str = "", source_path: str = "",
                 identified: dict | None = None) -> Path:
    batch = root / "batch"
    (batch / "01_source").mkdir(parents=True)
    (batch / "workflow").mkdir()
    (batch / "attachments").mkdir()
    (batch / "01_source/正文.txt").write_text(BODY, encoding="utf-8")
    (batch / "01_source/structure.json").write_text(json.dumps({
        "sections": [{"type": "单项填空", "questions": [1], "declared_count": 1}],
        "unresolved": [],
    }, ensure_ascii=False), encoding="utf-8")
    manifest = {"inputs": {"source": {"path": source_path}}}
    (batch / "workflow/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (batch / "attachments/batch.json").write_text(json.dumps(
        {"original_filenames": {"source": original}} if original else {}), encoding="utf-8")
    if identified is not None:
        (batch / "01_source/metadata.json").write_text(
            json.dumps(identified, ensure_ascii=False), encoding="utf-8")
    return batch


class FilenameNameTest(unittest.TestCase):
    def test_filename_name_strips_path_and_suffix(self):
        self.assertEqual(metadata_policy.filename_name(
            "C:\\Users\\x\\Desktop\\01 七上 人教版 课时A计划 Starter Unit 1.docx"),
            "01 七上 人教版 课时A计划 Starter Unit 1")
        self.assertEqual(metadata_policy.filename_name("/tmp/26秋课时七上英语RJ答案.docx"),
                         "26秋课时七上英语RJ答案")
        self.assertEqual(metadata_policy.filename_name(""), "")
        self.assertEqual(metadata_policy.filename_name(None), "")

    def test_paper_meta_prefers_identified_value_over_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = _write_batch(Path(temp),
                                 original="原卷文件名.docx",
                                 identified={"paper_title": "七上人教版课时A计划 Starter Unit 1 课时1",
                                             "source": "七上人教版课时A计划 Starter Unit 1 课时1",
                                             "source_type": "同步练习"})
            meta = metadata_policy.paper_meta({"paper_title": "未提供", "source": "未提供",
                                               "source_type": "未提供"}, batch)
            self.assertEqual(meta["source"], "七上人教版课时A计划 Starter Unit 1 课时1")
            self.assertEqual(meta["paper_title"], "七上人教版课时A计划 Starter Unit 1 课时1")
            self.assertEqual(meta["source_type"], "同步练习")

    def test_paper_meta_falls_back_to_original_word_name(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = _write_batch(Path(temp),
                                 original="01 七上 人教版 课时A计划 Starter Unit 1 Hello! 第一课时 Section A (1a-2d).docx")
            meta = metadata_policy.paper_meta({"paper_title": "未提供", "source": "未提供",
                                               "source_type": "未提供"}, batch)
            self.assertEqual(meta["source"],
                             "01 七上 人教版 课时A计划 Starter Unit 1 Hello! 第一课时 Section A (1a-2d)")
            # source_type 不能用文件名兜底：文件名不证明它是同步练习还是中考卷。
            self.assertEqual(meta["source_type"], "未提供")

    def test_source_path_name_used_when_descriptor_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = _write_batch(Path(temp),
                                 source_path="/inbox/26秋课时八上英语RJ正文(学用).docx")
            meta = metadata_policy.paper_meta({}, batch)
            self.assertEqual(meta["source"], "26秋课时八上英语RJ正文(学用)")

    def test_internal_conversion_files_are_not_name_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = _write_batch(Path(temp), source_path="/tmp/source.txt")
            meta = metadata_policy.paper_meta({}, batch)
            self.assertEqual(meta["source"], "未提供")
            self.assertEqual(meta["paper_title"], "未提供")

    def test_known_values_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = _write_batch(Path(temp), original="原卷文件名.docx",
                                 identified={"source": "别的来源", "source_type": "同步练习"})
            meta = metadata_policy.paper_meta({"source": "2024年安徽省中考",
                                               "paper_title": "2024年安徽省中考",
                                               "source_type": "中考真题"}, batch)
            self.assertEqual(meta["source"], "2024年安徽省中考")
            self.assertEqual(meta["source_type"], "中考真题")
            self.assertEqual(meta["paper_title"], "2024年安徽省中考")


class GateAndAssemblyTest(unittest.TestCase):
    def test_gate_confirmed_meta_keeps_identified_source(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = _write_batch(Path(temp), original="七上 人教版 课时A计划 Starter Unit 1 第一课时.docx",
                                 identified={"paper_title": "七上人教版课时A计划 Starter Unit 1 课时1",
                                             "source": "七上人教版课时A计划 Starter Unit 1 课时1",
                                             "source_type": "同步练习"})
            report = structure_gate.run_gate(batch)
            self.assertTrue(report["passed"], report["blockers"])
            self.assertEqual(report["meta"]["source"], "七上人教版课时A计划 Starter Unit 1 课时1")
            self.assertEqual(report["meta"]["source_type"], "同步练习")
            confirmed = json.loads((batch / "01_source/structure.confirmed.json").read_text())
            self.assertEqual(confirmed["meta"]["source"], "七上人教版课时A计划 Starter Unit 1 课时1")
            self.assertEqual(confirmed["meta"]["paper_title"], "七上人教版课时A计划 Starter Unit 1 课时1")

    def test_batch_fill_source_meta_resolves_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = _write_batch(Path(temp), original="七上 人教版 课时A计划 Unit 2 第二课时.docx")
            (batch / "01_source/structure.confirmed.json").write_text(json.dumps({
                "meta": {"source": "未提供", "paper_title": "未提供", "source_type": "未提供"},
                "identity_version": "scoped-v1", "units": [],
            }, ensure_ascii=False), encoding="utf-8")
            meta = batch_fill._source_meta(batch)
            self.assertEqual(meta["source"], "七上 人教版 课时A计划 Unit 2 第二课时")
            # 占位只是展示用：题目字段不能凭空多出 source_type=未提供。
            self.assertNotIn("source_type", meta)

    def test_assemble_injects_resolved_source_into_items(self):
        with tempfile.TemporaryDirectory() as temp:
            batch = _write_batch(Path(temp), original="七上 人教版 课时A计划 Unit 3 第一课时.docx")
            confirmed = {"meta": {"source": "未提供", "paper_title": "未提供", "source_type": "未提供"},
                         "identity_version": "scoped-v1", "units": []}
            for rel in (assemble_local.STRUCTURE, assemble_local.KNOWLEDGE, assemble_local.EXPLANATIONS,
                        assemble_local.TYPES, assemble_local.ANSWERS):
                payload = confirmed if rel == assemble_local.STRUCTURE else {}
                target = batch / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            draft = {"items": [{"unit_id": "u1", "unit_kind": "standalone", "number": 1}],
                     "source_bindings": {"u1": {"start_line": 2}}}
            with mock.patch("workflow.question_draft.ensure", return_value=draft):
                payload = assemble_local._initial_payload(batch)
            row = payload["items"][0]
            self.assertEqual(row["filterMetadata"]["source"], "七上 人教版 课时A计划 Unit 3 第一课时")
            self.assertNotIn("source_type", row["filterMetadata"])

    def test_workbench_paper_info_uses_word_name(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "01七上人教版课时A计划StarterUnit1Hello!第一课时Sectio"
            (folder / "attachments").mkdir(parents=True)
            (folder / "attachments/batch.json").write_text(json.dumps({
                "original_filenames": {"source":
                    "01 七上 人教版 课时A计划 Starter Unit 1 Hello! 第一课时 Section A (1a-2d).docx"},
                "title": None,
            }, ensure_ascii=False), encoding="utf-8")
            result = server.paper_info(folder, {"meta": {"source": "未提供", "paper_title": "未提供",
                                                         "source_type": "未提供"}}, {"meta": {}})
            self.assertEqual(result["title"],
                             "01 七上 人教版 课时A计划 Starter Unit 1 Hello! 第一课时 Section A (1a-2d)")
            self.assertEqual(result["source"],
                             "01 七上 人教版 课时A计划 Starter Unit 1 Hello! 第一课时 Section A (1a-2d)")
            self.assertEqual(result["source_type"], "未提供")

    def test_workbench_keeps_real_source_over_filename(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / "batch"
            (folder / "attachments").mkdir(parents=True)
            (folder / "attachments/batch.json").write_text(json.dumps({
                "original_filenames": {"source": "随便取的文件名.docx"}}, ensure_ascii=False), encoding="utf-8")
            result = server.paper_info(folder, {"meta": {"source": "2024年安徽省中考",
                                                         "source_type": "中考真题"}}, {})
            self.assertEqual(result["source"], "2024年安徽省中考")
            self.assertEqual(result["source_type"], "中考真题")


if __name__ == "__main__":
    unittest.main()
