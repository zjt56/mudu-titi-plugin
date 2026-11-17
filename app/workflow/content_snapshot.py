"""Create the pre-underlines content snapshot used by metadata processing."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(batch_dir: Path) -> Path:
    batch_dir = Path(batch_dir)
    source = batch_dir / "06_batch" / "batch.base.json"
    out = batch_dir / "06_batch" / "content.snapshot.json"
    if not source.is_file():
        raise FileNotFoundError(str(source))
    payload = json.loads(source.read_text(encoding="utf-8-sig"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    build(args.batch_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
