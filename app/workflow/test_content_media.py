"""All vision/route calls are mocked; fixtures live only in temporary directories."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from workflow import content_media as cm

PNG = b"\x89PNG\r\n\x1a\nfixture"
ROUTE = {"provider": "selected", "model": "exact", "profile": "fixture"}


class ContentMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "01_source/media/a.png"
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(PNG)
        self.context = {"01_source/正文.txt": "[IMG a.png]", "01_source/extraction.json": {
            "media": [{"name": "a.png", "path": str(self.root / "extract-evidence/a.png")}]}}
        self.hashes = {"01_source/media/a.png": hashlib.sha256(PNG).hexdigest()}
        self.snap = cm.build_snapshot(self.root, self.context, self.hashes)
        self.task = {"batch_dir": str(self.root), "context": self.context, "input_hashes": self.hashes,
                     "media_snapshot": self.snap, "route": dict(ROUTE), "stage": "answers"}
        self.args = {"media_id": self.snap["media"][0]["media_id"], "question": "What is visible?"}
        self.route = Mock(return_value=dict(ROUTE))
        self.identify = Mock(return_value={"provider": "selected", "model": "exact", "text": "A visible chart."})
        self.cache = {}

    def inspect(self, **kwargs):
        return cm.inspect_media(self.task, self.args, identify=self.identify, route_identity=self.route,
                                cache=self.cache, now=100, **kwargs)

    def test_snapshot_freezes_real_hashes_without_mutating_input(self):
        old = {}
        snapshot = cm.build_snapshot(self.root, self.context, old)
        self.assertEqual(old, {})
        self.assertEqual(snapshot["input_hashes"], self.hashes)
        self.assertEqual(len(snapshot["media"]), 1)

    def test_coalesced_image_marker_binds_nothing_but_real_refs_still_do(self):
        # Evidence prose may merge images as `[IMG image4/5/6.jpeg]`; that is not
        # a real path and must not reject the artifact. Individual files bind.
        self.assertEqual(cm._candidate('image4/5/6.jpeg', '01_source/media', self.root), None)
        self.assertEqual(cm._candidate('[IMG image7/8/9.jpeg]'.replace('[IMG ', '').replace(']', ''),
                                       '01_source/media', self.root), None)
        self.assertEqual(cm._candidate('image4.jpeg', '01_source/media', self.root),
                         '01_source/media/image4.jpeg')
        with self.assertRaises(cm.MediaError):
            cm._candidate('01_source/other/image4.jpeg', '01_source/media', self.root)
        context = dict(self.context, types={"items": [{"evidence": ["[IMG image4/5/6.jpeg]"]}]})
        snapshot = cm.build_snapshot(self.root, context, self.hashes)
        self.assertEqual(len(snapshot["media"]), 1)
        self.assertEqual(snapshot["media"][0]["path"], "01_source/media/a.png")

    def test_marker_alone_is_not_evidence(self):
        snapshot = cm.build_snapshot(self.root, {"text": "[IMG a.png]"}, {})
        self.assertEqual(snapshot["media"], [])
        snapshot = cm.build_snapshot(self.root, {"text": "[IMG a.png]"}, self.hashes)
        self.assertEqual(len(snapshot["media"]), 1)

    def test_local_url_inventory(self):
        snapshot = cm.build_snapshot(self.root, {"draft": {"localURL": "01_source/media/a.png"}}, {})
        self.assertEqual(len(snapshot["media"]), 1)

    def test_unknown_text_and_path_arguments_rejected(self):
        self.args["path"] = str(self.path)
        self.assertEqual(self.inspect()["status"], "rejected")
        self.identify.assert_not_called()

    def test_unknown_media_and_new_file_rejected(self):
        (self.path.parent / "new.png").write_bytes(PNG)
        self.args["media_id"] = "new.png"
        self.assertEqual(self.inspect()["code"], "MEDIA_ID_UNKNOWN")
        self.identify.assert_not_called()

    def test_changed_bytes_reject_even_cached(self):
        first = self.inspect()
        self.assertEqual(first["status"], "rejected")
        self.assertEqual(first["code"], "MEDIA_VISION_DISABLED")
        self.path.write_bytes(PNG + b"changed")
        self.assertNotEqual(self.inspect()["code"], "MEDIA_VISION_DISABLED")
        self.identify.assert_not_called()

    def test_changed_bytes_new_snapshot_invalidates_cache(self):
        self.inspect()
        self.path.write_bytes(PNG + b"changed")
        self.hashes["01_source/media/a.png"] = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.task["media_snapshot"] = cm.build_snapshot(self.root, self.context, self.hashes)
        # 视觉停用：只要字节与快照一致就进入统一停用拒绝，绝不调用图片模型。
        self.assertEqual(self.inspect()["status"], "rejected")
        self.identify.assert_not_called()

    def test_unbound_hash_rejects(self):
        self.task["input_hashes"] = {}
        self.assertEqual(self.inspect()["code"], "MEDIA_HASH_NOT_TASK_BOUND")
        self.identify.assert_not_called()

    def test_traversal_and_outside_directories_rejected(self):
        for name in ("../a.png", "01_source/media/../../a.png", "C:/secret.png", "other/a.png", "a\\b.png"):
            with self.subTest(name=name), self.assertRaises(cm.MediaError):
                cm.build_snapshot(self.root, {"media": [{"name": name}]}, {})

    def test_bad_and_stale_declared_file_rejected(self):
        with self.assertRaises(cm.MediaError):
            cm.build_snapshot(self.root, {"media": ["absent.png"]}, {})
        self.path.write_bytes(b"not-image")
        with self.assertRaises(cm.MediaError):
            cm.build_snapshot(self.root, self.context, {})

    def test_symlink_escape_rejected(self):
        outside = self.root / "outside.png"
        outside.write_bytes(PNG)
        link = self.path.parent / "link.png"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(cm.MediaError):
            cm.build_snapshot(self.root, {"media": ["link.png"]}, {})
        self.path.unlink()
        self.path.symlink_to(outside)
        self.assertEqual(self.inspect()["status"], "rejected")

    def test_media_vision_is_disabled_without_model_call(self):
        # 图片视觉理解已停用：inspect_media 一律拒绝且不调用图片模型。
        result = cm.inspect_media(self.task, self.args, identify=self.identify, cache=self.cache, now=100)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["code"], "MEDIA_VISION_DISABLED")
        self.assertEqual(result["model_calls"], 0)
        self.identify.assert_not_called()

    def test_transcripts_and_audio_not_exposed_as_images(self):
        snapshot = cm.build_snapshot(self.root, {"02_audio/full-transcript.json": {"text": "Already transcribed"},
                                               "existing_audio_files": ["02_audio/a.mp3"]}, {})
        self.assertEqual(snapshot["media"], [])


if __name__ == "__main__":
    unittest.main()
