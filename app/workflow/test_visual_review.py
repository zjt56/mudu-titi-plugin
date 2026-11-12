"""视觉复核模块回归：只测解析/降级/产物，不依赖真实网关。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import visual_review


class VisualReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "01_source" / "media").mkdir(parents=True)
        # 建两张最小图片（内容不参与识别，识别被 mock）。
        for name in ("image1.jpeg", "image2.png"):
            (self.root / "01_source" / "media" / name).write_bytes(b"\xff\xd8\xff\xe0stub")

    def test_parse_json_tolerates_wrapped_unescaped_newlines(self):
        text = '说明文字 ```json\n{"kind":"page","title":"版面","contains_text":true,"readable_text":"行1\n行2","summary":"x"}\n``` 结尾'
        obj = visual_review._parse_json(text)
        self.assertIsNotNone(obj)
        self.assertEqual(obj["kind"], "page")
        self.assertIn("行1", obj["readable_text"])

    def test_parse_json_none_on_truncated(self):
        # 被截断的 JSON 无法解析，返回 None（不能伪造）。
        self.assertIsNone(visual_review._parse_json('{"kind":"page","title":"未完成'))

    def test_build_writes_candidate_artifact(self):
        with patch.object(visual_review, "_load_key", return_value="k"), \
             patch.object(visual_review, "_identify",
                          side_effect=lambda path, rel, *a, **kw: {
                              "kind": "icon", "title": "天气图标", "contains_text": False,
                              "readable_text": "", "summary": "图标"}):
            result = visual_review.build(self.root, endpoint="http://explicit.test/v1/chat/completions", force=True)
        self.assertEqual(result["status"], "built")
        self.assertEqual(result["image_count"], 2)
        artifact = json.loads((self.root / visual_review.ARTIFACT).read_text(encoding="utf-8"))
        self.assertEqual(artifact["image_count"], 2)
        self.assertTrue(all(it["kind"] == "icon" for it in artifact["items"]))
        # 候选证据边界：note 必须声明不据此放行、原文为准。
        self.assertIn("原文为准", artifact["note"])

    def test_unavailable_without_key_does_not_write(self):
        with patch.object(visual_review, "_load_key", return_value=""):
            result = visual_review.build(self.root, endpoint="http://explicit.test/v1/chat/completions", force=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse((self.root / visual_review.ARTIFACT).exists())

    def test_unavailable_when_all_images_fail(self):
        with patch.object(visual_review, "_load_key", return_value="k"), \
             patch.object(visual_review, "_identify",
                          side_effect=visual_review.VisualReviewError("网关挂了")):
            result = visual_review.build(self.root, endpoint="http://explicit.test/v1/chat/completions", force=True)
        self.assertEqual(result["status"], "unavailable")
        self.assertFalse((self.root / visual_review.ARTIFACT).exists())

    def test_cached_is_idempotent(self):
        with patch.object(visual_review, "_load_key", return_value="k"), \
             patch.object(visual_review, "_identify",
                          side_effect=lambda path, rel, *a, **kw: {
                              "kind": "other", "title": "t", "contains_text": False,
                              "readable_text": "", "summary": "s"}):
            first = visual_review.build(self.root, endpoint="http://explicit.test/v1/chat/completions", force=True)
            second = visual_review.build(self.root, endpoint="http://explicit.test/v1/chat/completions")  # 不加 force = 复用
        self.assertEqual(first["status"], "built")
        self.assertEqual(second["status"], "cached")

    def test_temp_extract_dirs_are_skipped(self):
        tmpdir = self.root / "01_source" / "extract-abc" / "media"
        tmpdir.mkdir(parents=True)
        (tmpdir / "page-1.jpeg").write_bytes(b"\xff\xd8stub")
        rels = [rel for _, rel in visual_review._iter_images(self.root)]
        self.assertFalse(any("extract-abc" in r for r in rels))
        self.assertEqual(len(rels), 2)


class RoutedVisualReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.media = self.root / "01_source/media"
        self.media.mkdir(parents=True)
        self.route = {"provider": "selected", "model": "vision", "profile": "p"}
        self.identities = patch("workflow.visual_transport.route_identity", side_effect=lambda **kw: dict(self.route))
        self.identities.start()
        self.addCleanup(self.identities.stop)
        self.env = patch.dict(os.environ, {"VISUAL_REVIEW_ENDPOINT": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def image(self, name, data):
        (self.media / name).write_bytes(data)

    def answer(self, images, prompt, **kw):
        return {"provider": self.route["provider"], "model": self.route["model"],
                "text": json.dumps({"items": [{"id": row["id"], "kind": "icon", "title": "天气",
                    "contains_text": False, "readable_text": "", "summary": "天气图标"} for row in images]})}

    @patch("workflow.visual_transport.identify")
    def test_default_follows_selected_route_deduplicates_bytes_and_keeps_paths(self, call):
        self.image("a.jpg", b"same")
        self.image("b.jpg", b"same")
        self.image("c.jpg", b"different")
        outside = self.root / "attachments"
        outside.mkdir()
        (outside / "ignored.jpg").write_bytes(b"ignored")
        call.side_effect = self.answer
        result = visual_review.build(self.root)
        self.assertEqual(result["image_count"], 3)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(call.call_args.args[0]), 2)
        artifact = json.loads((self.root / visual_review.ARTIFACT).read_text())
        self.assertEqual(artifact["provider"], "selected")
        self.assertEqual({r["path"] for r in artifact["items"]}, {"01_source/media/a.jpg", "01_source/media/b.jpg", "01_source/media/c.jpg"})
        self.assertTrue(all(r.get("image_sha256") and r["route"] == self.route for r in artifact["items"]))

    @patch("workflow.visual_transport.identify")
    def test_cache_binds_bytes_route_and_prompt(self, call):
        self.image("a.jpg", b"a")
        call.side_effect = self.answer
        visual_review.build(self.root)
        self.assertEqual(visual_review.build(self.root)["status"], "cached")
        self.assertEqual(call.call_count, 1)
        self.route["model"] = "new-model"
        visual_review.build(self.root)
        self.assertEqual(call.call_count, 2)
        self.image("a.jpg", b"new bytes")
        visual_review.build(self.root)
        self.assertEqual(call.call_count, 3)
        with patch.object(visual_review, "BATCH_PROMPT", "new prompt"):
            visual_review.build(self.root)
        self.assertEqual(call.call_count, 4)

    @patch("workflow.visual_transport.identify")
    def test_batches_at_eight_and_first_failure_stops(self, call):
        for index in range(17):
            self.image(f"{index:02}.jpg", str(index).encode())
        call.side_effect = RuntimeError("selected provider failed")
        result = visual_review.build(self.root)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(call.call_args.args[0]), 8)
        self.assertFalse((self.root / visual_review.ARTIFACT).exists())
        call.side_effect = self.answer
        visual_review.build(self.root)
        self.assertEqual([len(c.args[0]) for c in call.call_args_list[1:]], [8, 8, 1])

    @patch("workflow.lesson_bands.load_verified")
    @patch("workflow.visual_transport.identify")
    def test_verified_same_route_band_reused_without_model(self, call, verified):
        import hashlib
        self.image("banner.jpg", b"banner")
        verified.return_value = {"status": "verified", "provider_route": dict(self.route), "bands": [{
            "image_sha256": hashlib.sha256(b"banner").hexdigest(), "label": "基础巩固",
            "evidence": {"visible_text": "A 基础巩固"}}]}
        result = visual_review.build(self.root)
        self.assertEqual(result["model_calls"], 0)
        self.assertFalse(call.called)
        artifact = json.loads((self.root / visual_review.ARTIFACT).read_text())
        self.assertEqual(artifact["items"][0]["kind"], "band")
        self.assertEqual(artifact["items"][0]["readable_text"], "A 基础巩固")

    @patch("workflow.visual_transport.identify")
    def test_changed_route_failure_retires_old_artifact_without_losing_evidence(self, call):
        self.image("a.jpg", b"a")
        call.side_effect = self.answer
        visual_review.build(self.root)
        old_bytes = (self.root / visual_review.ARTIFACT).read_bytes()
        self.route["model"] = "unavailable-new-model"
        call.side_effect = RuntimeError("provider failed")
        self.assertEqual(visual_review.build(self.root)["status"], "unavailable")
        artifact = json.loads((self.root / visual_review.ARTIFACT).read_text())
        self.assertEqual(artifact["items"], [])
        archives = list((self.root / "01_source/visual-semantics-history").glob("*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), old_bytes)
        # Failing results are never cached as recognized images.
        call.side_effect = self.answer
        self.assertEqual(visual_review.build(self.root)["status"], "built")
        self.assertEqual(call.call_count, 3)

    @patch("workflow.visual_transport.identify")
    def test_explicit_model_without_endpoint_is_not_silently_ignored(self, call):
        self.image("a.jpg", b"a")
        result = visual_review.build(self.root, model="explicit-old-model")
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("endpoint", result["reason"])
        self.assertFalse(call.called)

    @patch("workflow.visual_transport.identify")
    def test_wrong_ids_and_route_changes_fail_closed(self, call):
        self.image("a.jpg", b"a")
        call.return_value = {"provider": "different", "model": "vision", "text": '{"items":[]}'}
        self.assertEqual(visual_review.build(self.root)["status"], "unavailable")
        self.assertFalse((self.root / visual_review.ARTIFACT).exists())
        call.return_value = {"provider": "selected", "model": "vision", "text": '{"items":[{"id":"wrong"}]}'}
        self.assertEqual(visual_review.build(self.root)["status"], "unavailable")
        self.assertFalse((self.root / visual_review.ARTIFACT).exists())


if __name__ == "__main__":
    unittest.main()
