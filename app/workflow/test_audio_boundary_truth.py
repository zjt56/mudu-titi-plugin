"""Boundary evidence must never promote invented timing to verification."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audio import segment, bootstrap, config


class BoundaryTruthTest(unittest.TestCase):
    def test_short_pause_cannot_split_two_readings(self):
        self.assertEqual(segment._snap(28, [0, 10, 50, 70], [27]), 10)

    def test_interpolated_unit_and_previous_end_are_unverified(self):
        units = [{"unit_id": f"q-{n}", "numbers": [n]} for n in (1, 2, 3)]
        rows = segment.plan(units, {1: [1], 3: [41]}, [0, 20, 40, 60], 60)
        self.assertFalse(rows[0]["boundary_verified"])
        self.assertFalse(rows[1]["boundary_verified"])
        self.assertIn("unsupported_end", rows[0]["review_reasons"])
        self.assertEqual(rows[1]["quality"], "interpolated")
        self.assertTrue(rows[2]["boundary_verified"])

    def test_segment_precision_does_not_manufacture_word_anchor(self):
        document = {"words": [], "segments": [
            {"text": "Listen and answer questions 16 to 17.", "start": 500, "end": 520}]}
        anchors, coarse = segment.transcript_anchors(document, [])
        self.assertEqual(anchors[16], [500])
        self.assertEqual(coarse, {16, 17})
        rows = segment.plan([{"unit_id": "g", "numbers": [16, 17]}], anchors,
                            [0, 495, 530], 530, coarse_numbers=coarse)
        self.assertFalse(rows[0]["boundary_verified"])
        self.assertEqual(rows[0]["quality"], "coarse")

    def test_section_with_spaces_uses_correct_segment_offset(self):
        units = [{"numbers": [21], "type": "听力短文理解"}]
        words = [{"word": "one long prior paragraph with many spaces", "start": 10},
                 {"word": "四、短文理解 你将听到一篇短文", "start": 700},
                 {"word": "later text", "start": 900}]
        self.assertEqual(segment.section_anchors(units, words), {21: 700})

    def test_word_match_inside_segment_keeps_segment_start(self):
        self.assertEqual(segment.word_anchors([
            {"word": "Listen now and answer questions 16 to 17", "start": 500}]),
            {16: [500], 17: [500]})

    def test_bare_number_bus_is_not_question_even_without_other_cues(self):
        words = [{"word": w, "start": i, "end": i + .2}
                 for i, w in enumerate(("take", "a", "number", "two", "bus"))]
        self.assertEqual(segment.word_anchors(words), {})

    def test_cache_identity_is_content_and_mode_not_path_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory) / "a.mp3", Path(directory) / "b.mp3"
            a.write_bytes(b"audio"); b.write_bytes(b"audio")
            cfg = dict(config.DEFAULTS)
            self.assertEqual(segment._transcript_key(a, cfg), segment._transcript_key(b, cfg))
            self.assertNotEqual(segment._transcript_key(a, cfg, "en", True),
                                segment._transcript_key(a, cfg, "en", False))
            old = segment._transcript_key(a, cfg)
            stat = a.stat(); a.write_bytes(b"other"); os.utime(a, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            self.assertNotEqual(old, segment._transcript_key(a, cfg))

    def test_cache_tamper_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "t.json"
            data = {"schema_version": 2, "segments": [{"start": 0, "end": 1}],
                    "timestamp_precision": "segment", "words": []}
            segment._atomic_json(path, data)
            segment._atomic_json(path.with_name(path.name + ".key.json"),
                                 {"key": {"test": 1}, "sha256": segment._file_digest(path)})
            self.assertTrue(segment._transcript_is_fresh(path, {"test": 1}))
            path.write_text(json.dumps(dict(data, text="changed")))
            self.assertFalse(segment._transcript_is_fresh(path, {"test": 1}))

    def test_same_json_does_not_change_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            segment._atomic_json(path, {"a": 1}); before = path.stat().st_mtime_ns
            segment._atomic_json(path, {"a": 1})
            self.assertEqual(before, path.stat().st_mtime_ns)

    def test_ready_marker_must_match_selected_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); python = root / "python"; python.write_bytes(b"x")
            marker = root / bootstrap.READY_NAME
            marker.write_text(json.dumps({"whisperx": bootstrap.WHISPERX_SPEC,
                                          "asr_model": "small", "align_model": config.DEFAULTS["align_model"]}))
            cfg = dict(config.DEFAULTS, data_dir=directory)
            with patch.object(bootstrap, "venv_python", return_value=python):
                # 默认 small 与 marker 一致时可用；显式切换 large 必须重新 bootstrap。
                self.assertTrue(bootstrap.ready(cfg))
                self.assertFalse(bootstrap.ready(dict(cfg, asr_model="large-v3-turbo")))

    def region_fixture(self):
        units = [{"unit_id": "a", "numbers": [1], "type": "听力关键词语选择"},
                 {"unit_id": "b", "numbers": [2], "type": "听力关键词语选择"},
                 {"unit_id": "c", "numbers": [3, 4], "type": "听力短文理解"}]
        regions = [{"region_id": i, "start": i*10, "end": (i+1)*10} for i in range(4)]
        texts = ["关键词语选择，你将听到两个句子", "one first sentence repeated", "second sentence repeated", "短文理解，你将听到一篇短文"]
        doc = {"regions": regions, "segments": [
            {"region_id": i, "start": i*10+1, "end": (i+1)*10-1, "text": text}
            for i, text in enumerate(texts)]}
        return units, doc, [0, 10, 20, 30, 40]

    def test_source_section_count_and_consistent_anchor_resolves_missing_number(self):
        units, doc, cuts = self.region_fixture()
        resolved, proof = segment.region_bound_anchors(doc, units, cuts, {1: [12]})
        self.assertEqual(resolved, {1: [0], 2: [20], 3: [30]})
        self.assertEqual(proof[2]["source_unit_count"], 2)
        self.assertEqual(proof[2]["speech_region_count"], 2)
        rows = segment.plan(units, resolved, cuts, 40)
        self.assertTrue(all(row["boundary_verified"] for row in rows))
        self.assertTrue(all(row["semantic_verified"] is False for row in rows))

    def test_conflicting_number_prevents_section_order_inference(self):
        units, doc, cuts = self.region_fixture()
        resolved, proof = segment.region_bound_anchors(doc, units, cuts, {1: [22]})
        self.assertNotIn(1, resolved)
        self.assertNotIn(2, resolved)

    def test_missing_speech_or_missing_physical_boundary_prevents_inference(self):
        units, doc, cuts = self.region_fixture()
        doc["segments"].pop(2)
        resolved, _ = segment.region_bound_anchors(doc, units, cuts, {1: [12]})
        self.assertNotIn(2, resolved)
        self.assertEqual(segment.region_bound_anchors(doc, units, [0, 40], {1: [12]}), ({}, {}))

    def test_numberless_source_and_no_region_cannot_be_promoted(self):
        units, doc, cuts = self.region_fixture()
        self.assertEqual(segment.region_bound_anchors(doc, units, cuts, {} )[0], {3: [30]})
        doc.pop("regions")
        self.assertEqual(segment.region_bound_anchors(doc, units, cuts, {1: [12]}), ({}, {}))


if __name__ == "__main__":
    unittest.main()
