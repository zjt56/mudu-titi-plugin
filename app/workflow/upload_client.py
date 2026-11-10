"""Restricted draft-upload transport; no publishing or question-media upload endpoints.

Catalog envelopes below are explicit supported adapters, not a claim about a
live deployment's response. All calls are single attempts. A submission_unknown
must be reconciled by an operator before attempting the same submission again.
"""
from __future__ import annotations

from http.client import HTTPException
from http.cookiejar import CookieJar
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import socket
from urllib import error, parse, request

MAX_UPLOAD_BYTES = 2_000_000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 20_000_000
MAX_ATTACHMENTS = 20
ATTACHMENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
DEFAULT_CONFIG = {
    "base_url": "http://127.0.0.1:8089/api",
    "api_key_env": "MUDU_API_KEY",
    "timeout_seconds": 30,
    "subject_id": "english",
    "max_response_bytes": MAX_RESPONSE_BYTES,
}
_ERROR_FIELDS = frozenset({"error", "code", "message", "detail", "details", "allowed", "blockers"})
_SENSITIVE = re.compile(r"authorization|api.?key|token|secret|password|cookie", re.I)


class APIError(Exception):
    """Safe public failure with a machine-readable code and bounded details."""

    def __init__(self, code, http_status=None, details=None):
        self.code = code
        self.http_status = http_status
        self.details = details if details is not None else {}
        super().__init__(f"Upload API request failed ({code})" +
                         (f"; HTTP {http_status}" if http_status is not None else ""))


def _invalid_config():
    return APIError("invalid_config")


def _base_url(value):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise _invalid_config()
    try:
        parts = parse.urlsplit(value)
        port = parts.port  # Force validation, including out-of-range ports.
    except ValueError:
        raise _invalid_config() from None
    if (parts.scheme not in {"http", "https"} or not parts.hostname or
            parts.username is not None or parts.password is not None or
            parts.query or parts.fragment or "?" in value or "#" in value or
            "\\" in value or not parts.path.endswith("/api") or
            "%" in parts.path or "//" in parts.path or
            any(segment in {".", ".."} for segment in parts.path.split("/"))):
        raise _invalid_config()
    if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise _invalid_config()
    # Only the literal spelling of loopback is accepted for cleartext HTTP.
    if parts.scheme == "http" and parts.netloc.split(":")[0] not in {"localhost", "127.0.0.1", "["}:
        raise _invalid_config()
    if port is not None and port == 0:
        raise _invalid_config()
    return value


def _validate_config(values):
    if not isinstance(values, dict) or set(values) - (set(DEFAULT_CONFIG) | {"knowledge_version_id", "account"}):
        raise _invalid_config()
    config = dict(DEFAULT_CONFIG, **values)
    _base_url(config["base_url"])
    if not isinstance(config["api_key_env"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config["api_key_env"]):
        raise _invalid_config()
    timeout = config["timeout_seconds"]
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise _invalid_config()
    cap = config["max_response_bytes"]
    if isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= MAX_RESPONSE_BYTES:
        raise _invalid_config()
    for field in ("subject_id", "knowledge_version_id"):
        if field in config and (not isinstance(config[field], str) or not config[field].strip()):
            raise _invalid_config()
    account = config.get("account")
    if account is not None and (not isinstance(account, str) or len(account) > 256
                                or re.search(r"[\x00-\x1f\x7f]", account)):
        raise _invalid_config()
    return config


def load_config(path=None):
    """Load explicit path, MUDU_UPLOAD_CONFIG, local config, then defaults.

    Loading validates destinations and environment variable names but never
    reads API credentials. Explicit/mapped missing files fail rather than fall
    back to a different deployment.
    """
    chosen = path if path is not None else os.environ.get("MUDU_UPLOAD_CONFIG")
    if chosen is None:
        default = Path(__file__).with_name("upload-config.json")
        if not default.exists():
            return _validate_config({})
        chosen = default
    try:
        with Path(chosen).open(encoding="utf-8") as handle:
            values = json.load(handle)
    except (OSError, ValueError, TypeError):
        raise _invalid_config() from None
    return _validate_config(values)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _redact(value, secret):
    if isinstance(value, dict):
        return {_redact(str(key), secret): ("[REDACTED]" if _SENSITIVE.search(str(key)) else _redact(item, secret))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    if isinstance(value, str):
        if secret:
            value = value.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)\bbearer\s+[^\s\"',;<>]+", "Bearer [REDACTED]", value)
    return value


def _error_details(raw, secret):
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        return {}
    if not isinstance(value, dict):
        return {}
    try:
        return _redact({key: val for key, val in value.items() if key in _ERROR_FIELDS}, secret)
    except RecursionError:
        return {}


def _rows(value, envelopes):
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        found = [value[key] for key in envelopes if key in value]
        if len(found) != 1 or not isinstance(found[0], list):
            raise APIError("unsupported_catalog_shape")
        rows = found[0]
        # Never silently present a partial paginated catalog as complete.
        if (value.get("has_more") or value.get("next") or value.get("next_cursor") or
                (isinstance(value.get("total"), int) and value["total"] > len(rows))):
            raise APIError("unsupported_catalog_pagination")
    else:
        raise APIError("unsupported_catalog_shape")
    if not all(isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"] for row in rows):
        raise APIError("unsupported_catalog_shape")
    if len({row["id"] for row in rows}) != len(rows):
        raise APIError("duplicate_catalog_id")
    return rows


class ApiClient:
    def __init__(self, config=None):
        self.config = load_config() if config is None else _validate_config(config)
        self._base = self.config["base_url"]
        parts = parse.urlsplit(self._base)
        self._origin = f"{parts.scheme}://{parts.netloc}"
        self._cookies = CookieJar()
        handlers = [_NoRedirect(), request.HTTPCookieProcessor(self._cookies)]
        if parts.hostname in {"localhost", "127.0.0.1", "::1"}:
            handlers.append(request.ProxyHandler({}))
        self._opener = request.build_opener(*handlers)
        # 人工账号会话：API Key 被服务端视为 harness，不能创建上传暂存。
        self._account = (os.environ.get("MUDU_UPLOAD_ACCOUNT") or self.config.get("account") or "").strip()
        self._password = os.environ.get("MUDU_UPLOAD_PASSWORD", "")
        self._logged_in = False

    def _api_key(self):
        secret = os.environ.get(self.config["api_key_env"], "")
        if not secret or secret.strip() != secret or any(ord(c) < 33 or ord(c) > 126 for c in secret):
            raise APIError("missing_api_key")
        return secret

    def _request(self, method, route, payload=None, *, media=False):
        allowed = ((method == "GET" and route == "/catalog/knowledge-versions") or
                   (method == "GET" and route.startswith("/catalog/knowledge-points?version_id=")) or
                   (method == "POST" and route == "/uploads"))
        if media:
            allowed = method == "HEAD" and route.startswith("/source-media/")
        if not allowed:
            raise APIError("unsupported_route")
        secret = ""
        headers = {"Accept": "application/json"}
        if not media:
            secret = self._api_key()
            headers["Authorization"] = "Bearer " + secret
        if payload is not None:
            headers["Content-Type"] = "application/json"
        req = request.Request((self._origin if media else self._base) + route,
                              data=payload, headers=headers, method=method)
        submitting = method == "POST"
        try:
            with self._opener.open(req, timeout=self.config["timeout_seconds"]) as response:
                status = response.status
                if not 200 <= status < 300:
                    raise APIError("submission_unknown" if submitting else "http_error", status)
                if media:
                    return {"content_type": response.headers.get("Content-Type", ""),
                            "content_length": response.headers.get("Content-Length")}
                raw = response.read(self.config["max_response_bytes"] + 1)
                if len(raw) > self.config["max_response_bytes"]:
                    raise APIError("submission_unknown" if submitting else "response_too_large", status)
                try:
                    value = json.loads(raw)
                except (ValueError, UnicodeError, RecursionError):
                    raise APIError("submission_unknown" if submitting else "invalid_response", status) from None
                if submitting and (not isinstance(value, dict) or not isinstance(value.get("id"), str) or
                                   not value["id"].strip() or value.get("stage") != "pending_confirmation"):
                    raise APIError("submission_unknown", status)
                return value
        except error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read(self.config["max_response_bytes"] + 1)
            except (OSError, ValueError):
                raw = b""
            finally:
                exc.close()
            details = _error_details(raw[:self.config["max_response_bytes"]], secret)
            if 300 <= status < 400:
                code = "unsafe_redirect"
            elif submitting and (status >= 500 or status in {408, 425, 429}):
                code = "submission_unknown"
            else:
                code = "rejected" if submitting else "http_error"
            raise APIError(code, status, details) from None
        except (error.URLError, TimeoutError, socket.timeout, HTTPException, OSError, ValueError):
            # Never expose urllib's URL, exception message or response headers.
            raise APIError("submission_unknown" if submitting else "network_error") from None

    def _media_get(self, route):
        """Bounded GET probe used when the static handler rejects HEAD."""
        req = request.Request(self._origin + route, method="GET",
                              headers={"Range": "bytes=0-0", "Accept": "*/*"})
        try:
            with self._opener.open(req, timeout=self.config["timeout_seconds"]) as response:
                status = response.status
                if not 200 <= status < 300:
                    raise APIError("http_error", status)
                content_type = response.headers.get("Content-Type", "")
                length = response.headers.get("Content-Length")
                total = re.search(r"/(\d+)\s*$", response.headers.get("Content-Range") or "")
                if total:
                    length = total.group(1)
                try:
                    response.read(1)
                except (OSError, ValueError):
                    pass
                return {"content_type": content_type, "content_length": length}
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise APIError("http_error", status) from None
        except (error.URLError, TimeoutError, socket.timeout, HTTPException, OSError, ValueError):
            raise APIError("network_error") from None

    def fetch_catalog(self):
        versions = _rows(self._request("GET", "/catalog/knowledge-versions"), ("versions", "items"))
        candidates = []
        for row in versions:
            active = (row.get("active") is True or row.get("status") == "active")
            # Contradictory lifecycle declarations fail closed.
            if row.get("active") is False or ("status" in row and row["status"] != "active"):
                active = False
            if (active and row.get("textbook_id") in (None, "") and
                    ("subject_id" not in row or row["subject_id"] == self.config["subject_id"])):
                candidates.append(row)
        selected = self.config.get("knowledge_version_id")
        if selected is not None:
            candidates = [row for row in candidates if row["id"] == selected]
        if len(candidates) != 1:
            raise APIError("ambiguous_knowledge_version" if len(candidates) > 1 else "knowledge_version_not_found")
        version = candidates[0]
        route = "/catalog/knowledge-points?" + parse.urlencode({"version_id": version["id"]})
        points = _rows(self._request("GET", route), ("points", "items"))
        for point in points:
            if (not isinstance(point.get("name"), str) or not point["name"].strip() or
                    ("version_id" in point and point["version_id"] != version["id"]) or
                    (point.get("parent_id") is not None and not isinstance(point["parent_id"], str))):
                raise APIError("unsupported_catalog_shape")
        # Preserve the server rows, including active flags, for the caller's
        # leaf/path validation; do not manufacture missing metadata.
        return {"version": version, "points": points,
                "fetched_at": datetime.now(timezone.utc).isoformat()}

    def _login(self):
        """Create a human session when admin account/password are configured."""
        if self._logged_in:
            return
        if not self._account or not self._password:
            raise APIError("human_session_required")
        body = json.dumps({"username": self._account, "password": self._password}).encode("utf-8")
        req = request.Request(self._base + "/auth/login", data=body, method="POST",
                              headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=self.config["timeout_seconds"]) as response:
                if response.status != 200:
                    raise APIError("login_failed", response.status)
                response.read(4096)
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise APIError("login_failed", status) from None
        except (error.URLError, TimeoutError, socket.timeout, HTTPException, OSError, ValueError):
            raise APIError("network_error") from None
        self._logged_in = True

    def _pending_by_batch(self, batch_id):
        """Return an existing pending staging id with the same batch_id, if any."""
        if not batch_id:
            return None
        req = request.Request(self._base + "/uploads", method="GET",
                              headers={"Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=self.config["timeout_seconds"]) as response:
                if response.status != 200:
                    return None
                value = json.loads(response.read(self.config["max_response_bytes"] + 1))
        except (error.HTTPError, error.URLError, TimeoutError, socket.timeout,
                HTTPException, OSError, ValueError):
            return None
        rows = value.get("items") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            return None
        for row in rows:
            if isinstance(row, dict) and str(row.get("batch_id") or "") == str(batch_id) \
                    and row.get("stage") in (None, "pending_confirmation"):
                return str(row.get("id") or "")
        return None

    def _session_submit(self, payload, retry=True):
        req = request.Request(self._base + "/uploads", data=payload, method="POST",
                              headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=self.config["timeout_seconds"]) as response:
                status = response.status
                if not 200 <= status < 300:
                    raise APIError("rejected", status)
                raw = response.read(self.config["max_response_bytes"] + 1)
                value = json.loads(raw)
                if (not isinstance(value, dict) or not isinstance(value.get("id"), str)
                        or not value["id"].strip() or value.get("stage") != "pending_confirmation"):
                    raise APIError("submission_unknown", status)
                return {"id": value["id"], "stage": value["stage"]}
        except error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read(self.config["max_response_bytes"] + 1)
            except (OSError, ValueError):
                raw = b""
            finally:
                exc.close()
            # 会话 8 小时过期后，重新登录一次再提交（仅一次，避免循环）。
            if status == 401 and retry and self._account and self._password:
                self._logged_in = False
                self._login()
                return self._session_submit(payload, retry=False)
            details = _error_details(raw, "")
            if status == 403 and details.get("error") == "human_confirmation_required":
                raise APIError("human_session_required", status, details) from None
            code = "submission_unknown" if status >= 500 or status in {408, 425, 429} else "rejected"
            raise APIError(code, status, details) from None
        except (error.URLError, TimeoutError, socket.timeout, HTTPException, OSError, ValueError):
            raise APIError("submission_unknown") from None

    def submit(self, payload):
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_UPLOAD_BYTES:
            raise APIError("invalid_payload")
        try:
            value = json.loads(payload)
        except (ValueError, UnicodeError, RecursionError):
            raise APIError("invalid_payload") from None
        if not isinstance(value, dict) or not isinstance(value.get("items"), list) or not value["items"]:
            raise APIError("invalid_payload")
        if self._account and self._password:
            self._login()
            try:
                batch_id = json.loads(payload).get("batch_id")
            except (ValueError, UnicodeError):
                batch_id = None
            allow_duplicate = str(os.environ.get("MUDU_ALLOW_DUPLICATE_UPLOAD", "0")).strip().lower() in ("1", "true", "yes", "on")
            existing = None if allow_duplicate else self._pending_by_batch(batch_id)
            if existing:
                raise APIError("duplicate_batch_pending", 409, {"batch_id": batch_id, "staging_id": existing})
            return self._session_submit(payload)
        try:
            value = self._request("POST", "/uploads", payload)
        except APIError as exc:
            if exc.code == "rejected" and exc.http_status == 403 and \
                    (exc.details or {}).get("error") == "human_confirmation_required":
                raise APIError("human_session_required", 403, exc.details) from None
            raise
        return {"id": value["id"], "stage": value["stage"]}

    @staticmethod
    def _staging_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", value):
            raise APIError("invalid_staging_id")
        return value

    @staticmethod
    def _batch_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,200}", value):
            raise APIError("invalid_batch_id")
        return value

    def _session_headers(self):
        """Human-session cookies or the same-account draft API key."""
        if self._account and self._password:
            self._login()
            return {"Accept": "application/json"}
        return {"Accept": "application/json", "Authorization": "Bearer " + self._api_key()}

    def _staging_rows(self, retry=True):
        route = "/uploads"
        headers = self._session_headers()
        req = request.Request(self._base + route, method="GET", headers=headers)
        try:
            with self._opener.open(req, timeout=self.config["timeout_seconds"]) as response:
                if response.status != 200:
                    raise APIError("staging_list_unavailable", response.status)
                raw = response.read(self.config["max_response_bytes"] + 1)
                if len(raw) > self.config["max_response_bytes"]:
                    raise APIError("staging_list_unavailable", response.status)
                value = json.loads(raw)
        except error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read(self.config["max_response_bytes"] + 1)
            except (OSError, ValueError):
                raw = b""
            finally:
                exc.close()
            if status == 401 and retry and self._account and self._password:
                self._logged_in = False
                self._login()
                return self._staging_rows(retry=False)
            raise APIError("staging_list_unavailable", status,
                           _error_details(raw, "")) from None
        except (error.URLError, TimeoutError, socket.timeout, HTTPException, OSError, ValueError):
            raise APIError("staging_list_unavailable") from None
        rows = value.get("items") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            raise APIError("staging_list_unavailable")
        return [row for row in rows if isinstance(row, dict)]

    def staging_attachments(self, staging_id):
        """Return attachment names on one staging record; None is never guessed."""
        staging_id = self._staging_id(staging_id)
        row = next((item for item in self._staging_rows()
                    if str(item.get("id") or "") == staging_id), None)
        if row is None:
            raise APIError("staging_not_found")
        raw = row.get("attachments")
        if raw is None:
            raw = row.get("files")
        if raw is None:
            try:
                empty = int(row.get("attachment_count") or 0) == 0
            except (TypeError, ValueError):
                empty = False
            if empty:
                return []
            raise APIError("staging_attachments_unavailable")
        if not isinstance(raw, list):
            raise APIError("staging_attachments_unavailable")
        names = []
        for item in raw:
            if isinstance(item, str):
                name = item
            elif isinstance(item, dict):
                name = item.get("name") or item.get("filename") or item.get("original_name")
            else:
                raise APIError("staging_attachments_unavailable")
            if not isinstance(name, str) or not name.strip():
                raise APIError("staging_attachments_unavailable")
            names.append(name)
        return names

    def append_attachment(self, staging_id, data, filename):
        """Append one binary review attachment; non-idempotent, one attempt."""
        staging_id = self._staging_id(staging_id)
        if not isinstance(data, bytes) or not data or len(data) > MAX_ATTACHMENT_BYTES:
            raise APIError("attachment_too_large")
        if not isinstance(filename, str) or not filename.strip():
            raise APIError("attachment_name_invalid")
        if (filename != Path(filename).name or "/" in filename or "\\" in filename
                or any(ord(c) < 32 for c in filename)):
            raise APIError("attachment_name_invalid")
        suffix = Path(filename).suffix.lower()
        if suffix not in ATTACHMENT_TYPES:
            raise APIError("attachment_type_invalid")
        headers = self._session_headers()
        headers.update({"Content-Type": ATTACHMENT_TYPES[suffix],
                        "X-Filename": parse.quote(filename, safe="")})
        req = request.Request(self._base + f"/uploads/{staging_id}/attachments",
                              data=data, method="POST", headers=headers)
        try:
            with self._opener.open(req, timeout=self.config["timeout_seconds"]) as response:
                status = response.status
                if not 200 <= status < 300:
                    raise APIError("submission_unknown", status)
                response.read(self.config["max_response_bytes"] + 1)
                return {"staging_id": staging_id, "filename": filename}
        except error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read(self.config["max_response_bytes"] + 1)
            except (OSError, ValueError):
                raw = b""
            finally:
                exc.close()
            if status == 401 and self._account and self._password:
                self._logged_in = False
                self._login()
            code = "submission_unknown" if status >= 500 or status in {408, 425, 429} else "rejected"
            raise APIError(code, status, _error_details(raw, "")) from None
        except (error.URLError, TimeoutError, socket.timeout, HTTPException, OSError, ValueError):
            raise APIError("submission_unknown") from None

    def append_evidence(self, staging_id, files):
        """Append planned files after reconciling the staging list first.

        A lost response is resolved by reading the staging list; an attachment
        that is already present exactly once is never uploaded again.
        """
        staging_id = self._staging_id(staging_id)
        if not isinstance(files, list) or not 1 <= len(files) <= MAX_ATTACHMENTS:
            raise APIError("attachments_limit_invalid")
        names = []
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("data"), bytes):
                raise APIError("attachment_invalid")
            name = item.get("filename")
            if not isinstance(name, str) or name in names:
                raise APIError("attachment_name_duplicate")
            names.append(name)
        current = self.staging_attachments(staging_id)
        uploaded = []
        for item in files:
            name = item["filename"]
            count = current.count(name)
            if count == 1:
                continue
            if count > 1:
                raise APIError("attachment_duplicate_on_staging",
                               details={"filename": name, "uploaded": uploaded})
            try:
                self.append_attachment(staging_id, item["data"], name)
            except APIError as exc:
                if exc.code != "submission_unknown":
                    raise
                # The append may have succeeded before the response was lost.
                refreshed = self.staging_attachments(staging_id)
                if refreshed.count(name) != 1:
                    raise APIError("attachment_unknown",
                                   details={"filename": name, "uploaded": uploaded}) from None
                current = refreshed
                uploaded.append(name)
                continue
            refreshed = self.staging_attachments(staging_id)
            if refreshed.count(name) != 1:
                raise APIError("attachment_unknown",
                               details={"filename": name, "uploaded": uploaded})
            current = refreshed
            uploaded.append(name)
        return {"staging_id": staging_id, "attachments": names,
                "uploaded": uploaded, "all": current}

    def clear_review_evidence(self, batch_id):
        """Human-session only: clear review attachments after settlement."""
        batch_id = self._batch_id(batch_id)
        if not self._account or not self._password:
            raise APIError("human_session_required")
        self._login()
        req = request.Request(self._base + f"/reviews/{batch_id}/evidence",
                              method="DELETE", headers={"Accept": "application/json"})
        try:
            with self._opener.open(req, timeout=self.config["timeout_seconds"]) as response:
                if not 200 <= response.status < 300:
                    raise APIError("review_evidence_failed", response.status)
                response.read(self.config["max_response_bytes"] + 1)
        except error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read(self.config["max_response_bytes"] + 1)
            except (OSError, ValueError):
                raw = b""
            finally:
                exc.close()
            raise APIError("review_evidence_failed", status,
                           _error_details(raw, "")) from None
        except (error.URLError, TimeoutError, socket.timeout, HTTPException, OSError, ValueError):
            raise APIError("submission_unknown") from None
        return {"batch_id": batch_id, "cleared": True}

    def check_media(self, url, kind=None):
        """HEAD a source-media path or same-origin URL without credentials.

        Root-relative /source-media/ paths resolve against the API origin,
        independently of the /api prefix. Other local paths are forbidden.
        """
        if kind not in {None, "audio", "image"} or not isinstance(url, str):
            raise APIError("invalid_media_url")
        resolved = self._origin + url if url.startswith("/source-media/") else url
        try:
            parts = parse.urlsplit(resolved)
            origin = f"{parts.scheme}://{parts.netloc}"
            parts.port
        except ValueError:
            raise APIError("invalid_media_url") from None
        path = parts.path
        decoded = path
        for _ in range(4):
            new = parse.unquote(decoded)
            if new == decoded:
                break
            decoded = new
        if (origin != self._origin or parts.username is not None or parts.password is not None or
                parts.query or parts.fragment or "?" in url or "#" in url or
                not path.startswith("/source-media/") or path == "/source-media/" or
                any(c.isspace() or ord(c) < 32 for c in url) or
                "\\" in decoded or "//" in decoded or "%" in decoded or
                any(segment in {".", ".."} for segment in decoded.split("/"))):
            raise APIError("invalid_media_url")
        try:
            info = self._request("HEAD", path, media=True)
        except APIError as exc:
            # 该题库的静态目录由后端处理，HEAD 返回 501/405；用带 Range 的 GET
            # 探测存在性与类型，只读 1 字节，不下载整文件，也不发送凭据。
            if exc.http_status not in (405, 501):
                raise
            info = self._media_get(path)
        content_type = info["content_type"].split(";", 1)[0].strip().lower()
        if kind and not content_type.startswith(kind + "/"):
            raise APIError("media_type_mismatch")
        raw_length = info["content_length"]
        if raw_length is not None and (not raw_length.isdigit() or int(raw_length) < 0):
            raise APIError("invalid_response")
        return {"url": resolved, "content_type": content_type,
                "content_length": int(raw_length) if raw_length is not None else None}
