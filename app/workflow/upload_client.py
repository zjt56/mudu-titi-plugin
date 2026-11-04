"""Restricted draft-upload API transport; no publishing or media-upload endpoints.

Catalog envelopes below are explicit supported adapters, not a claim about a
live deployment's response. All calls are single attempts. A submission_unknown
must be reconciled by an operator before attempting the same submission again.
"""
from __future__ import annotations

from http.client import HTTPException
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
    if not isinstance(values, dict) or set(values) - (set(DEFAULT_CONFIG) | {"knowledge_version_id"}):
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
        handlers = [_NoRedirect()]
        if parts.hostname in {"localhost", "127.0.0.1", "::1"}:
            handlers.append(request.ProxyHandler({}))
        self._opener = request.build_opener(*handlers)

    def _request(self, method, route, payload=None, *, media=False):
        allowed = ((method == "GET" and route in {"/catalog/knowledge-versions"}) or
                   (method == "GET" and route.startswith("/catalog/knowledge-points?version_id=")) or
                   (method == "POST" and route == "/uploads"))
        if media:
            allowed = method == "HEAD" and route.startswith("/source-media/")
        if not allowed:
            raise APIError("unsupported_route")
        secret = ""
        headers = {"Accept": "application/json"}
        if not media:
            secret = os.environ.get(self.config["api_key_env"], "")
            if not secret or secret.strip() != secret or any(ord(c) < 33 or ord(c) > 126 for c in secret):
                raise APIError("missing_api_key")
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

    def submit(self, payload):
        if not isinstance(payload, bytes) or not payload or len(payload) > MAX_UPLOAD_BYTES:
            raise APIError("invalid_payload")
        try:
            value = json.loads(payload)
        except (ValueError, UnicodeError, RecursionError):
            raise APIError("invalid_payload") from None
        if not isinstance(value, dict) or not isinstance(value.get("items"), list) or not value["items"]:
            raise APIError("invalid_payload")
        value = self._request("POST", "/uploads", payload)
        return {"id": value["id"], "stage": value["stage"]}

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
        info = self._request("HEAD", path, media=True)
        content_type = info["content_type"].split(";", 1)[0].strip().lower()
        if kind and not content_type.startswith(kind + "/"):
            raise APIError("media_type_mismatch")
        raw_length = info["content_length"]
        if raw_length is not None and (not raw_length.isdigit() or int(raw_length) < 0):
            raise APIError("invalid_response")
        return {"url": resolved, "content_type": content_type,
                "content_length": int(raw_length) if raw_length is not None else None}
