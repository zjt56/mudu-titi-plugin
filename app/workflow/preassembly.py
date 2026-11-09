"""Preassembly owns source objects and type classification; types is a legacy checkpoint."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def prepare(batch_dir):
    """Build source-bound objects before exporting/invoking the assembly model task.

    This does not complete a stage. The existing type task supplies catalog labels
    in this same preassembly execution; import and handoff verify both deliveries.
    """
    from . import batch_fill
    require_current_source(batch_dir)
    return batch_fill.initialize(batch_dir)


def require_current_source(batch_dir):
    """Local and portable assembly share the same upstream consumer checks."""
    from . import contracts, manifest as mf
    root = Path(batch_dir)
    state = mf.load(root)
    for dep in contracts.STAGE_BY_ID["preassemble"].depends_on:
        if not mf.dependency_satisfied(state, dep):
            raise ValueError(f"前置组装上游交接未通过：{dep}；批次：{root.name}；目录：{root}")
    return state


def original_input_hashes(batch_dir):
    """Bind registered originals during local calls just as portable tasks do."""
    from . import manifest as mf
    root = Path(batch_dir)
    state = mf.load(root)
    hashes = {}
    for record in state.get("inputs", {}).values():
        if record.get("path"):
            path = Path(record["path"]).expanduser()
            path = (path if path.is_absolute() else root / path).resolve()
            hashes[str(path)] = mf.sha256_file(path)
    return hashes


def check(batch_dir):
    """Read-only legacy checkpoint; never classify again or manufacture labels."""
    from . import handoff, manifest as mf
    root = Path(batch_dir)
    result = handoff.inspect_stage(root, mf.load(root), "types")
    if not result.get("passed"):
        raise ValueError("前置组装题型交接未通过：" + "; ".join(
            row["message"] for row in result.get("errors") or []))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check",))
    parser.add_argument("--batch-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(check(args.batch_dir), ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"passed": False, "batch_dir": str(args.batch_dir), "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
