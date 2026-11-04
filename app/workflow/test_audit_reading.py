"""audit_reading 的 C27：阅读类单元必须有文章大意（F3）。"""
import contextlib
import importlib.util
import io
import json
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("audit_reading", ROOT / "scripts/06-upload/audit_reading.py")
audit_reading = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit_reading)

MARK = "（AI生成，待校对）"


def _reading(summary, *, sub_angle="设题角度/阅读/细节理解"):
    return {"unit_id": "R1", "question_type": "阅读理解", "articleSummary": summary,
            "knowledge": ["语篇体裁/记叙文/故事"],
            "subquestions": [{"number": 56, "knowledge": [sub_angle], "answer": "D",
                              "options": ["A", "B", "C", "D"]}]}


class AuditReadingC27Test(unittest.TestCase):
    def run_audit(self, items):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.json"
            path.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                code = audit_reading.main(["--batch", str(path)])
            return code, buf.getvalue()

    def test_reading_with_summary_passes(self):
        code, out = self.run_audit([_reading("本文讲述一个关于坚持的小故事。")])
        self.assertEqual(code, 0, out)

    def test_reading_with_marked_ai_summary_passes(self):
        code, out = self.run_audit([_reading("本文讲述一个关于坚持的小故事。" + MARK)])
        self.assertEqual(code, 0, out)

    def test_reading_without_summary_fails_c26(self):
        for missing in ("", "   ", None):
            with self.subTest(missing=missing):
                item = _reading(missing)
                if missing is None:
                    item.pop("articleSummary")
                code, out = self.run_audit([item])
                self.assertEqual(code, 1, out)
                self.assertIn("C27", out)

    def test_cloze_and_grammar_selection_also_require_summary(self):
        # 用户要求「阅读完型」都要有大意：完形/语法选择缺大意同样报 C27。
        for qtype in ("完形填空", "语法选择"):
            with self.subTest(qtype=qtype):
                item = {"unit_id": "C1", "question_type": qtype,
                        "knowledge": ["语篇体裁/记叙文/故事"],
                        "subquestions": [{"number": 31, "knowledge": ["词汇/词汇辨析/动词辨析"], "answer": "A"}]}
                code, out = self.run_audit([item])
                self.assertEqual(code, 1, out)
                self.assertIn("C27", out)
                item["articleSummary"] = "本文讲述一次校园活动。（AI生成，待校对）"
                code, out = self.run_audit([item])
                self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
