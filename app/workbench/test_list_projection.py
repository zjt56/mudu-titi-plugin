"""List projection must never hold the task dispatch lock while doing I/O."""
import threading
import unittest
from unittest.mock import Mock, patch

from workbench import server


class ListProjectionTest(unittest.TestCase):
    def setUp(self):
        for item in (patch.object(server, '_batches_snapshot', None),
                     patch.object(server, '_batches_epoch', 0),
                     patch.object(server, '_batches_read_lock', threading.Lock())):
            item.start()
            self.addCleanup(item.stop)

    def test_slow_list_does_not_block_supervised_dispatch(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def build():
            entered.set()
            if not release.wait(3):
                raise AssertionError('test did not release projection')
            return [{'name': 'batch'}]
        with patch.object(server, 'list_batches', side_effect=build), \
             patch.object(server, 'start_advance', return_value={'started': True}) as start:
            thread = threading.Thread(target=lambda: results.append(server.cached_list_batches()))
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertTrue(server._dispatch_supervised('batch')['started'])
                start.assert_called_once_with('batch')
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(results, [[{'name': 'batch'}]])

    def test_write_invalidates_inflight_projection_before_it_can_be_cached(self):
        write = server.serialized(lambda: None)
        old, fresh = [{'revision': 'old'}], [{'revision': 'fresh'}]
        def build_old():
            write()
            return old
        with patch.object(server, 'list_batches', side_effect=build_old):
            self.assertEqual(server.cached_list_batches(), old)
        self.assertIsNone(server._batches_snapshot)
        with patch.object(server, 'list_batches', return_value=fresh) as build:
            self.assertEqual(server.cached_list_batches(), fresh)
            self.assertEqual(server.cached_list_batches(), fresh)
            build.assert_called_once()

    def test_overlapping_readers_coalesce_without_repeating_media_projection(self):
        entered, release = threading.Event(), threading.Event()
        result = [{'name': 'batch'}]
        replies = []
        def build():
            entered.set()
            if not release.wait(3):
                raise AssertionError('test did not release projection')
            return result
        with patch.object(server, 'list_batches', side_effect=build) as build_mock:
            threads = [threading.Thread(target=lambda: replies.append(server.cached_list_batches())) for _ in range(2)]
            try:
                for thread in threads:
                    thread.start()
                self.assertTrue(entered.wait(2))
            finally:
                release.set()
                for thread in threads:
                    thread.join(3)
            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(replies, [result, result])
            build_mock.assert_called_once()


if __name__ == '__main__':
    unittest.main()
