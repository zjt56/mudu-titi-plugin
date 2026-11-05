#!/bin/sh
# 工作流模型桥：审核固定 pro；仅普通任务的明确 HTTP 429 允许一次备用路由。
# Python keeps separate raw streams and a redacted metadata record per process.
set -eu
exec python3 - "$0" "$@" <<'PY'
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[2]))
from workflow.local_agent import AgentError, _parse_response, failure_details, is_review_prompt


def main():
    if len(sys.argv) != 4:
        print("wf-agent: expected prompt_file response_file", file=sys.stderr)
        return 2
    os.umask(0o077)
    prompt, response = map(Path, sys.argv[2:])
    payload = prompt.read_bytes()
    review = is_review_prompt(payload.decode("utf-8"))
    profile = "wf-deepseek" if review else "wf-deepseek-fast"
    if response.exists() or response.is_symlink():
        print("wf-agent: response already exists", file=sys.stderr)
        return 2
    evidence = Path(tempfile.mkdtemp(prefix="wf-agent-attempts-", dir=response.parent))
    print("wf-agent: attempt evidence " + str(evidence), file=sys.stderr)
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        # Compatibility with the installed CLI; tests pass an inherited mock key.
        credentials = Path(os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))) / ".credentials.yaml"
        try:
            for line in credentials.read_text(encoding="utf-8").splitlines():
                match = re.fullmatch(r"\s*DEEPSEEK_API_KEY:\s*(.*?)\s*", line)
                if match:
                    key = match.group(1).strip("\"'")
                    break
        except OSError:
            pass
    if not key:
        print('wf-agent-result: {"error_code":"authentication","http_status":null}', file=sys.stderr)
        return 1
    env = {**os.environ, "DEEPSEEK_API_KEY": key}
    executable = Path.home() / ".local" / "bin" / "dsh"
    attempts = []

    def run_once(route):
        directory = evidence / ("attempt-%02d" % (len(attempts) + 1))
        directory.mkdir()
        stdout_path, stderr_path = directory / "stdout.log", directory / "stderr.log"
        meta = {"profile": route, "review": review, "prompt_bytes": len(payload),
                "wall_start": datetime.now(timezone.utc).isoformat(),
                "error_code": None, "error_category": None, "http_status": None}
        started = time.monotonic()
        rc = 1
        exit_code = None
        code, status = "spawn", None
        try:
            with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
                try:
                    try:
                        process = subprocess.run([str(executable), "--profile", route, "-"],
                                                 input=payload, stdout=stdout, stderr=stderr,
                                                 env=env, check=False)
                        rc = exit_code = process.returncode
                    finally:
                        meta["wall_end"] = datetime.now(timezone.utc).isoformat()
                        meta["duration"] = time.monotonic() - started
                except OSError:
                    # Details may contain secrets; preserve only a fixed diagnostic.
                    stderr.write(b"wf-agent: cannot start dsh executable\n")
                else:
                    code, status = failure_details(stderr_path.read_text(encoding="utf-8", errors="replace"))
                    if rc == 0:
                        try:
                            _parse_response(stdout_path.read_text(encoding="utf-8"), strict=True)
                        except (AgentError, UnicodeError):
                            rc, code = 1, "response"
                        else:
                            code, status = None, None
        finally:
            meta.setdefault("wall_end", datetime.now(timezone.utc).isoformat())
            meta.setdefault("duration", time.monotonic() - started)
            meta.update(exit_code=exit_code, bridge_exit_code=rc,
                        error_code=code, error_category=code, http_status=status)
            (directory / "attempt.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        attempts.append(meta)
        return rc, code, status, stdout_path, stderr_path

    result = run_once(profile)
    # Auth/quota/format failures are terminal. Audit never downgrades to fast.
    if result[0] != 0 and result[1] == "rate_limit" and result[2] == 429 and not review:
        print("wf-agent: explicit HTTP 429; retrying once with wf-deepseek", file=sys.stderr)
        result = run_once("wf-deepseek")
    first = attempts[0]
    summary = {"error_code": result[1], "http_status": result[2],
               "first_error_code": first["error_code"], "first_http_status": first["http_status"],
               "attempts": len(attempts), "review": review}
    (evidence / "result.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    # Replay all original diagnostics in order; no fallback can erase first cause.
    for index in range(len(attempts)):
        raw = (evidence / ("attempt-%02d" % (index + 1)) / "stderr.log").read_bytes()
        sys.stderr.buffer.write(raw)
        if raw and not raw.endswith(b"\n"):
            sys.stderr.buffer.write(b"\n")
    sys.stderr.flush()
    if result[0] == 0:
        # Exact model stdout, including findings and whitespace, remains unchanged.
        with result[3].open("rb") as source, response.open("xb") as target:
            shutil.copyfileobj(source, target)
        return 0
    print("wf-agent-result: " + json.dumps(summary), file=sys.stderr)
    return result[0] if result[0] > 0 else 128 - result[0]


try:
    raise SystemExit(main())
except (OSError, UnicodeError):
    print("wf-agent: local bridge I/O failure; inspect attempt evidence", file=sys.stderr)
    raise SystemExit(1)
PY
