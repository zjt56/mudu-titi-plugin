"""All transport tests use local fake servers and synthetic credentials."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib import error, request

from workflow.upload_client import APIError, ApiClient, load_config, MAX_UPLOAD_BYTES


TOKEN = "synthetic-test-key-never-a-real-credential"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def handle_request(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.server.calls.append((self.command, self.path, dict(self.headers), body))
        code, headers, data = self.server.reply(self.command, self.path, body)
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        self.send_response(code)
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    do_GET = handle_request
    do_POST = handle_request
    do_HEAD = handle_request


def default_reply(method, path, body):
    if path == "/api/catalog/knowledge-versions":
        return 200, {}, {"versions": [{"id": "english-v1", "subject_id": "english",
                                      "textbook_id": "", "status": "active"}]}
    if path == "/api/catalog/knowledge-points?version_id=english-v1":
        return 200, {}, {"points": [{"id": "p1", "name": "Grammar", "parent_id": None,
                                    "version_id": "english-v1", "active": True}]}
    if path.startswith("/source-media/"):
        return 200, {"Content-Type": "audio/mpeg", "Content-Length": "12"}, b""
    return 201, {}, {"id": "upload-1", "stage": "pending_confirmation"}


class UploadClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.calls = []
        self.server.reply = default_reply
        self.env = patch.dict(os.environ, {"MUDU_API_KEY": TOKEN})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = ApiClient({"base_url": self.origin + "/api"})

    def assert_code(self, code, call, *args, **kwargs):
        with self.assertRaises(APIError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_exact_payload_and_draft_receipt(self):
        payload = b'{ "items": [{"text": "test"}] }\n'
        self.assertEqual(self.client.submit(payload), {"id": "upload-1", "stage": "pending_confirmation"})
        method, path, headers, body = self.server.calls[0]
        self.assertEqual((method, path, body), ("POST", "/api/uploads", payload))
        self.assertEqual(headers["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(len(self.server.calls), 1)

    def test_maximum_payload_is_allowed_without_reencoding(self):
        prefix = b'{"items":[{}]}'
        payload = prefix + b" " * (2_000_000 - len(prefix))
        self.assertEqual(MAX_UPLOAD_BYTES, 2_000_000)
        self.client.submit(payload)
        self.assertEqual(self.server.calls[0][3], payload)

    def test_decimal_limit_plus_one_is_rejected_without_request(self):
        prefix = '{"items":[{"text":"英语"}]}'.encode("utf-8")
        payload = prefix + b" " * (2_000_001 - len(prefix))
        self.assertEqual(len(payload), 2_000_001)
        self.assertLess(len(payload), 2 * 1024 * 1024)
        self.assert_code("invalid_payload", self.client.submit, payload)
        self.assertEqual(self.server.calls, [])

    def test_catalog_snapshot(self):
        result = self.client.fetch_catalog()
        self.assertEqual(result["version"]["id"], "english-v1")
        self.assertEqual(result["points"][0]["id"], "p1")
        self.assertTrue(result["fetched_at"])
        self.assertEqual(len(self.server.calls), 2)
        self.assertTrue(all(call[0] == "GET" for call in self.server.calls))

    def versions(self, value):
        self.server.reply = lambda *args: (200, {}, value)

    def test_explicit_supported_catalog_envelopes(self):
        version = {"id": "v", "active": True, "textbook_id": None}
        point = {"id": "p", "name": "N"}
        for versions in ([version], {"items": [version]}, {"versions": [version]}):
            for points in ([point], {"items": [point]}, {"points": [point]}):
                self.server.reply = lambda method, path, body, v=versions, p=points: (200, {}, v if "versions" in path else p)
                self.assertEqual(self.client.fetch_catalog()["points"], [point])

    def test_unknown_and_paginated_catalog_shape_fails(self):
        for shape, code in [({"data": []}, "unsupported_catalog_shape"),
                            ({"versions": [], "items": []}, "unsupported_catalog_shape"),
                            ({"versions": [], "total": 20}, "unsupported_catalog_pagination"),
                            ({"versions": [], "next_cursor": "next"}, "unsupported_catalog_pagination")]:
            self.versions(shape)
            self.assert_code(code, self.client.fetch_catalog)

    def test_active_general_subject_selection_fails_closed(self):
        for row in ({"id": "v"}, {"id": "v", "active": False},
                    {"id": "v", "status": "retired", "active": True},
                    {"id": "v", "active": True, "textbook_id": "textbook"},
                    {"id": "v", "active": True, "subject_id": "math"}):
            self.versions([row])
            self.assert_code("knowledge_version_not_found", self.client.fetch_catalog)

    def test_multiple_versions_need_explicit_choice(self):
        versions = [{"id": "a", "active": True}, {"id": "b", "status": "active"}]
        self.versions(versions)
        self.assert_code("ambiguous_knowledge_version", self.client.fetch_catalog)
        self.server.reply = lambda method, path, body: (200, {}, versions if "versions" in path else [])
        client = ApiClient({"base_url": self.origin + "/api", "knowledge_version_id": "b"})
        self.assertEqual(client.fetch_catalog()["version"]["id"], "b")
        self.assertEqual(self.server.calls[-1][1], "/api/catalog/knowledge-points?version_id=b")

    def test_duplicate_ids_and_cross_version_points_fail(self):
        self.versions([{"id": "v", "active": True}, {"id": "v", "active": True}])
        self.assert_code("duplicate_catalog_id", self.client.fetch_catalog)
        self.server.reply = lambda method, path, body: default_reply(method, path, body) if "versions" in path else (200, {}, [{"id": "p", "name": "N", "version_id": "other"}])
        self.assert_code("unsupported_catalog_shape", self.client.fetch_catalog)

    def test_local_payload_validation_sends_nothing(self):
        for payload in ("{}", b"", b"{}", b"[]", b'{"items":[]}', b"broken", b"x" * (MAX_UPLOAD_BYTES + 1)):
            self.assert_code("invalid_payload", self.client.submit, payload)
        self.assertEqual(self.server.calls, [])

    def test_definitive_rejections_never_retry(self):
        for status in (400, 401, 403, 409, 413):
            self.server.calls.clear()
            self.server.reply = lambda *args, s=status: (s, {}, {"error": "no", "allowed": ["draft"]})
            exc = self.assert_code("rejected", self.client.submit, b'{"items":[{}]}')
            self.assertEqual(exc.http_status, status)
            self.assertEqual(exc.details["allowed"], ["draft"])
            self.assertEqual(len(self.server.calls), 1)

    def test_unknown_submission_outcomes_never_retry(self):
        for status, value in ((503, {"error": "busy"}), (408, {}), (429, {}),
                              (200, b"not JSON"), (200, {"id": "x", "stage": "published"}),
                              (200, {"stage": "pending_confirmation"})):
            self.server.calls.clear()
            self.server.reply = lambda *args, s=status, v=value: (s, {}, v)
            self.assert_code("submission_unknown", self.client.submit, b'{"items":[{}]}')
            self.assertEqual(len(self.server.calls), 1)

    def test_timeouts_and_connection_errors_are_unknown(self):
        for failure in (TimeoutError("unsafe " + TOKEN), error.URLError("unsafe " + TOKEN)):
            with patch.object(self.client._opener, "open", side_effect=failure) as opening:
                exc = self.assert_code("submission_unknown", self.client.submit, b'{"items":[{}]}')
                self.assertNotIn(TOKEN, str(exc))
                self.assertEqual(opening.call_count, 1)

    def test_redirects_are_never_followed_or_forwarded(self):
        for status in (301, 302, 303, 307, 308):
            self.server.calls.clear()
            self.server.reply = lambda *args, s=status: (s, {"Location": self.origin + "/leak"}, {})
            self.assert_code("unsafe_redirect", self.client.submit, b'{"items":[{}]}')
            self.assertEqual(len(self.server.calls), 1)
            self.assertEqual(self.server.calls[0][1], "/api/uploads")
        self.server.calls.clear()
        self.assert_code("unsafe_redirect", self.client.fetch_catalog)
        self.assertEqual(len(self.server.calls), 1)
        self.server.calls.clear()
        self.assert_code("unsafe_redirect", self.client.check_media, "/source-media/clip.mp3")
        self.assertEqual(len(self.server.calls), 1)
        self.assertNotIn("Authorization", self.server.calls[0][2])

    def test_error_details_are_whitelisted_and_recursively_redacted(self):
        self.server.reply = lambda *args: (403, {}, {"error": "echo " + TOKEN,
                "blockers": [{"message": "Bearer other-token", "authorization": TOKEN,
                              "deep": [TOKEN, {"api_key": "not-for-output"}]}],
                "unrecognized": "not-for-output", "Authorization": TOKEN})
        exc = self.assert_code("rejected", self.client.submit, b'{"items":[{}]}')
        rendered = json.dumps(exc.details) + str(exc) + repr(exc)
        for forbidden in (TOKEN, "other-token", "not-for-output"):
            self.assertNotIn(forbidden, rendered)
        self.assertNotIn("unrecognized", exc.details)

    def test_response_size_cap(self):
        client = ApiClient({"base_url": self.origin + "/api", "max_response_bytes": 16})
        self.server.reply = lambda *args: (200, {}, b"x" * 17)
        self.assert_code("submission_unknown", client.submit, b'{"items":[{}]}')
        self.assert_code("response_too_large", client.fetch_catalog)

    def test_media_head_has_no_bearer(self):
        result = self.client.check_media(self.origin + "/source-media/clip.mp3", kind="audio")
        self.assertEqual(result, {"url": self.origin + "/source-media/clip.mp3", "content_type": "audio/mpeg", "content_length": 12})
        self.assertEqual(self.server.calls[0][0], "HEAD")
        self.assertNotIn("Authorization", self.server.calls[0][2])
        self.assert_code("media_type_mismatch", self.client.check_media, self.origin + "/source-media/clip.mp3", kind="image")

    def test_media_relative_path_resolves_against_origin(self):
        result = self.client.check_media("/source-media/clip.mp3", kind="audio")
        self.assertEqual(result["url"], self.origin + "/source-media/clip.mp3")
        self.assertEqual(self.server.calls[0][1], "/source-media/clip.mp3")
        self.assertNotIn("Authorization", self.server.calls[0][2])

    def test_media_rejects_external_and_traversal_urls_before_request(self):
        for suffix in ("/elsewhere/x", "/source-media/../x", "/source-media/%2e%2e/x",
                       "/source-media/%252e%252e/x", "/source-media/x?q=1", "/source-media/x#f",
                       "/source-media/%5c..%5cx", "/source-media//x", "/source-media/"):
            self.assert_code("invalid_media_url", self.client.check_media, self.origin + suffix)
        for url in ("http://example.com/source-media/x", "file:///source-media/x", "/tmp/clip.mp3", "data:text/plain,x"):
            self.assert_code("invalid_media_url", self.client.check_media, url)
        self.assertEqual(self.server.calls, [])

    def test_no_undocumented_write_routes(self):
        for method, route in (("DELETE", "/uploads"), ("PUT", "/uploads"),
                              ("POST", "/publish"), ("POST", "/media"), ("GET", "/questions")):
            self.assert_code("unsupported_route", self.client._request, method, route)
        self.assertEqual(self.server.calls, [])

    def test_config_does_not_read_credentials_and_key_is_read_at_call_time(self):
        client = ApiClient({"base_url": self.origin + "/api", "api_key_env": "SYNTHETIC_UPLOAD_KEY"})
        with patch.dict(os.environ, {"SYNTHETIC_UPLOAD_KEY": "later-test-key"}):
            client.submit(b'{"items":[{}]}')
        self.assertEqual(self.server.calls[0][2]["Authorization"], "Bearer later-test-key")
        with patch.dict(os.environ, {}, clear=True):
            self.assert_code("missing_api_key", client.fetch_catalog)

    def test_loopback_disables_environment_proxies(self):
        with patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1", "no_proxy": "", "NO_PROXY": ""}):
            client = ApiClient({"base_url": self.origin + "/api"})
            client.fetch_catalog()
            # ProxyHandler({}) is omitted from opener.handlers because it has
            # no protocol methods; a successful request proves the bypass.
            self.assertFalse(any(isinstance(h, request.ProxyHandler) and h.proxies for h in client._opener.handlers))


class ConfigTests(unittest.TestCase):
    def test_portable_defaults_and_path_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.json"
            second = Path(directory) / "second.json"
            first.write_text(json.dumps({"base_url": "https://api.example.test/api"}))
            second.write_text(json.dumps({"subject_id": "test-subject"}))
            with patch.dict(os.environ, {"MUDU_UPLOAD_CONFIG": str(first)}):
                self.assertEqual(load_config()["base_url"], "https://api.example.test/api")
                self.assertEqual(load_config(second)["subject_id"], "test-subject")
                with self.assertRaises(APIError):
                    load_config(Path(directory) / "missing.json")
            with patch.dict(os.environ, {}, clear=True), patch("workflow.upload_client.Path.exists", return_value=False):
                config = load_config()
                self.assertEqual(config["base_url"], "http://127.0.0.1:8089/api")
                self.assertEqual(config["api_key_env"], "MUDU_API_KEY")
                self.assertEqual(config["timeout_seconds"], 30)

    def test_unsafe_config_is_rejected(self):
        unsafe_urls = ["http://example.com:8089/api", "http://127.1/api", "http://2130706433/api",
                       "http://127.0.0.1.evil/api", "http://user:secret@127.0.0.1/api",
                       "http://127.0.0.1/api?x=1", "http://127.0.0.1/api#f", "ftp://127.0.0.1/api",
                       "http://127.0.0.1/../api", "http://127.0.0.1//api", "http://127.0.0.1/%2e/api",
                       "http://127.0.0.1:99999/api", "http://127.0.0.1:0/api", "http://127.0.0.1/api/",
                       "http://127.0.0.1/api?", "http://LOCALHOST/api"]
        unsafe = [{"base_url": url} for url in unsafe_urls]
        unsafe += [{"api_key_env": "not-a-variable"}, {"api_key": TOKEN}, {"timeout_seconds": 0},
                   {"timeout_seconds": float("nan")}, {"max_response_bytes": 8388609}, {"max_response_bytes": True}]
        for config in unsafe:
            with self.subTest(config=config), self.assertRaises(APIError) as caught:
                ApiClient(config)
            self.assertEqual(caught.exception.code, "invalid_config")
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_literal_loopback_and_https_allowed(self):
        for base in ("http://localhost:8089/api", "http://127.0.0.1:8089/api", "http://[::1]:8089/api", "https://api.example.test/api"):
            self.assertEqual(ApiClient({"base_url": base}).config["base_url"], base)


if __name__ == "__main__":
    unittest.main()


class HumanSessionUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.calls = []
        self.server.reply = default_reply
        self.env = patch.dict(os.environ, {"MUDU_API_KEY": TOKEN})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = ApiClient({"base_url": self.origin + "/api"})

    def _reply(self, login=200, upload=(201, {"id": "upload-session", "stage": "pending_confirmation"})):
        def reply(method, path, body):
            if path == "/api/auth/login":
                return login, {"Set-Cookie": "mudu_session=synthetic; Path=/"}, {"user": {"username": "admin"}}
            if path == "/api/uploads":
                return upload[0], {}, upload[1]
            return default_reply(method, path, body)
        return reply

    def test_login_then_submit_with_session_cookie(self):
        self.server.reply = self._reply()
        payload = json.dumps({"items": [{"id": "x"}]}).encode()
        with patch.dict(os.environ, {"MUDU_UPLOAD_ACCOUNT": "admin", "MUDU_UPLOAD_PASSWORD": "synthetic-pass"}):
            client = ApiClient({"base_url": self.origin + "/api"})
            result = client.submit(payload)
        self.assertEqual(result, {"id": "upload-session", "stage": "pending_confirmation"})
        calls = [(m, p) for m, p, _, _ in self.server.calls]
        self.assertEqual(calls[0], ("POST", "/api/auth/login"))
        self.assertEqual(calls[1], ("POST", "/api/uploads"))
        upload_headers = self.server.calls[1][2]
        self.assertIn("mudu_session=synthetic", upload_headers.get("Cookie", ""))
        self.assertNotIn("Authorization", upload_headers)

    def test_bad_login_fails_without_upload(self):
        self.server.reply = self._reply(login=401)
        payload = json.dumps({"items": [{"id": "x"}]}).encode()
        with patch.dict(os.environ, {"MUDU_UPLOAD_ACCOUNT": "admin", "MUDU_UPLOAD_PASSWORD": "wrong"}):
            client = ApiClient({"base_url": self.origin + "/api"})
            with self.assertRaises(APIError) as caught:
                client.submit(payload)
        self.assertEqual(caught.exception.code, "login_failed")
        self.assertEqual([(m, p) for m, p, _, _ in self.server.calls], [("POST", "/api/auth/login")])

    def test_harness_403_is_reported_as_human_session_required(self):
        def reply(method, path, body):
            if path == "/api/uploads":
                return 403, {}, {"error": "human_confirmation_required"}
            return default_reply(method, path, body)
        self.server.reply = reply
        payload = json.dumps({"items": [{"id": "x"}]}).encode()
        with self.assertRaises(APIError) as caught:
            self.client.submit(payload)
        self.assertEqual(caught.exception.code, "human_session_required")

    def test_duplicate_batch_id_blocks_second_submission(self):
        def reply(method, path, body):
            if path == "/api/auth/login":
                return 200, {"Set-Cookie": "mudu_session=synthetic; Path=/"}, {"user": {"username": "admin"}}
            if path == "/api/uploads" and method == "GET":
                return 200, {}, {"items": [{"id": "existing", "batch_id": "b1", "stage": "pending_confirmation"}]}
            if path == "/api/uploads":
                return 201, {}, {"id": "upload-session", "stage": "pending_confirmation"}
            return default_reply(method, path, body)
        self.server.reply = reply
        payload = json.dumps({"batch_id": "b1", "items": [{"id": "x"}]}).encode()
        with patch.dict(os.environ, {"MUDU_UPLOAD_ACCOUNT": "admin", "MUDU_UPLOAD_PASSWORD": "synthetic-pass"}):
            client = ApiClient({"base_url": self.origin + "/api"})
            with self.assertRaises(APIError) as caught:
                client.submit(payload)
        self.assertEqual(caught.exception.code, "duplicate_batch_pending")
        self.assertEqual(caught.exception.details["staging_id"], "existing")
        self.assertNotIn(("POST", "/api/uploads"), [(m, p) for m, p, _, _ in self.server.calls])

    def test_duplicate_guard_can_be_disabled_explicitly(self):
        def reply(method, path, body):
            if path == "/api/auth/login":
                return 200, {"Set-Cookie": "mudu_session=synthetic; Path=/"}, {"user": {"username": "admin"}}
            if path == "/api/uploads" and method == "GET":
                return 200, {}, {"items": [{"id": "existing", "batch_id": "b1", "stage": "pending_confirmation"}]}
            if path == "/api/uploads":
                return 201, {}, {"id": "upload-session", "stage": "pending_confirmation"}
            return default_reply(method, path, body)
        self.server.reply = reply
        payload = json.dumps({"batch_id": "b1", "items": [{"id": "x"}]}).encode()
        with patch.dict(os.environ, {"MUDU_UPLOAD_ACCOUNT": "admin", "MUDU_UPLOAD_PASSWORD": "synthetic-pass",
                                     "MUDU_ALLOW_DUPLICATE_UPLOAD": "1"}):
            client = ApiClient({"base_url": self.origin + "/api"})
            result = client.submit(payload)
        self.assertEqual(result["id"], "upload-session")

    def test_expired_session_relogins_once(self):
        state = {"uploads": 0}
        def reply(method, path, body):
            if path == "/api/auth/login":
                return 200, {"Set-Cookie": "mudu_session=synthetic; Path=/"}, {"user": {"username": "admin"}}
            if path == "/api/uploads" and method == "GET":
                return 200, {}, {"items": []}
            if path == "/api/uploads":
                state["uploads"] += 1
                if state["uploads"] == 1:
                    return 401, {}, {"error": "unauthorized"}
                return 201, {}, {"id": "upload-session", "stage": "pending_confirmation"}
            return default_reply(method, path, body)
        self.server.reply = reply
        payload = json.dumps({"batch_id": "b2", "items": [{"id": "x"}]}).encode()
        with patch.dict(os.environ, {"MUDU_UPLOAD_ACCOUNT": "admin", "MUDU_UPLOAD_PASSWORD": "synthetic-pass"}):
            client = ApiClient({"base_url": self.origin + "/api"})
            result = client.submit(payload)
        self.assertEqual(result["id"], "upload-session")
        logins = [1 for m, p, _, _ in self.server.calls if p == "/api/auth/login"]
        self.assertEqual(len(logins), 2)
