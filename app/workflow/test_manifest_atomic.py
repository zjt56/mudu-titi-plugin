import os
import stat
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from . import manifest as mf


class AtomicManifestTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.batch = Path(self.temp.name)
        mf.save(self.batch, {"value": "old"})
        self.path = mf.manifest_path(self.batch)

    def test_reader_sees_old_document_until_replace(self):
        ready = threading.Event()
        release = threading.Event()
        replace = os.replace

        def delayed_replace(source, target):
            ready.set()
            if not release.wait(10):
                raise TimeoutError("reader did not release writer")
            replace(source, target)

        with mock.patch.object(mf.os, "replace", side_effect=delayed_replace):
            with ThreadPoolExecutor(max_workers=1) as pool:
                writer = pool.submit(mf.save, self.batch, {"value": "new" * 100000})
                try:
                    self.assertTrue(ready.wait(10))
                    self.assertEqual(mf.load(self.batch)["value"], "old")
                finally:
                    release.set()
                self.assertEqual(writer.result(timeout=10), self.path)
        self.assertEqual(mf.load(self.batch)["value"], "new" * 100000)

    def test_concurrent_readers_and_writers_observe_complete_documents(self):
        payloads = [str(i) * 100000 for i in range(4)]
        mf.save(self.batch, {"value": payloads[0]})
        start = threading.Barrier(5)

        def writer(value):
            start.wait(timeout=10)
            for _ in range(30):
                mf.save(self.batch, {"value": value})

        def reader():
            start.wait(timeout=10)
            for _ in range(250):
                self.assertIn(mf.load(self.batch)["value"], payloads)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(writer, value) for value in payloads]
            futures.append(pool.submit(reader))
            for future in futures:
                future.result(timeout=30)

    def test_failure_preserves_document_and_removes_temporary_file(self):
        before = self.path.read_bytes()
        with mock.patch.object(mf.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                mf.save(self.batch, {"value": "new"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    @unittest.skipIf(os.name == "nt", "POSIX permission bits")
    def test_existing_permissions_are_preserved(self):
        self.path.chmod(0o640)
        mf.save(self.batch, {"value": "new"})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o640)

    @unittest.skipIf(os.name == "nt", "symlink creation may require privileges")
    def test_symlink_target_is_updated(self):
        target = self.path.with_name("actual.json")
        self.path.rename(target)
        self.path.symlink_to(target.name)
        mf.save(self.batch, {"value": "new"})
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(mf.load(self.batch)["value"], "new")

    def test_sha256_cache_invalidates_when_file_changes(self):
        path = self.batch / "payload.txt"
        path.write_text("before", encoding="utf-8")
        first = mf.sha256_file(path)
        self.assertEqual(mf.sha256_file(path), first)
        path.write_text("after", encoding="utf-8")
        self.assertNotEqual(mf.sha256_file(path), first)
