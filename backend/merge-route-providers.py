#!/usr/bin/env python3
"""Merge provider definitions missing from a plugin route profile.

The plugin routes every model call through a temporary profile copied from a
base profile (wf-deepseek / wf-deepseek-fast). A provider configured only in the
user's own profile (for example opencode-go) is therefore invisible to routing
even when it is selected. This copies such provider blocks verbatim into the
route patch under the same llm-pi-ai row, without touching any model config that
the base profile already declares.

Usage: merge-route-providers.py <target_patch> <source_patch>
Exit codes: 0 merged or nothing to do; 2 unreadable/malformed input.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROW = re.compile(r"^- id:\s*llm-pi-ai\b")
TOP = re.compile(r"^- ")


def indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def row_range(lines: list[str], index: int) -> tuple[int, int]:
    end = len(lines)
    for j in range(index + 1, len(lines)):
        if TOP.match(lines[j]):
            end = j
            break
    return index, end


def find_row(lines: list[str]) -> int:
    return next((i for i, line in enumerate(lines) if ROW.match(line)), -1)


def providers_index(lines: list[str], row: tuple[int, int]) -> int:
    start, end = row
    for i in range(start + 1, end):
        if lines[i].strip() == "providers:":
            return i
    return -1


def provider_blocks(lines: list[str], row: tuple[int, int], providers_at: int,
                    child_indent: int) -> list[tuple[str, list[str]]]:
    start, end = row
    blocks: list[tuple[str, list[str]]] = []
    key = re.compile(r"^ {%d}([A-Za-z0-9_.-]+):\s*(?:#.*)?$" % child_indent)
    stop = end
    for i in range(providers_at + 1, end):
        stripped = lines[i].strip()
        if stripped and indent_of(lines[i]) <= indent_of(lines[providers_at]):
            stop = i
            break
    current = None
    for i in range(providers_at + 1, stop):
        line = lines[i]
        match = key.match(line)
        if match:
            current = [match.group(1), [line]]
            blocks.append((current[0], current[1]))
            continue
        if current is not None:
            if line.strip() and indent_of(line) <= child_indent:
                current = None
                continue
            current[1].append(line)
    return blocks


def blocks_extent(lines: list[str], row: tuple[int, int], providers_at: int,
                  child_indent: int) -> int:
    """Index just after the last existing provider entry (or of providers:)."""
    blocks = provider_blocks(lines, row, providers_at, child_indent)
    if not blocks:
        return providers_at + 1
    last_len = len(blocks[-1][1])
    offset = providers_at + 1
    for _name, block in blocks[:-1]:
        offset += len(block)
    return offset + last_len


def merge(target: Path, source: Path) -> int:
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
        source_lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        sys.stderr.write("merge-route-providers: %s\n" % exc)
        return 2
    source_row = find_row(source_lines)
    if source_row < 0:
        return 0
    source_providers_at = providers_index(source_lines, row_range(source_lines, source_row))
    if source_providers_at < 0:
        return 0
    child_indent = indent_of(source_lines[source_providers_at]) + 2
    provided = provider_blocks(source_lines, row_range(source_lines, source_row),
                               source_providers_at, child_indent)
    if not provided:
        return 0

    row_at = find_row(lines)
    if row_at < 0:
        lines.append("- id: llm-pi-ai")
        lines.append("  config:")
        lines.append("    providers:")
        for _name, block in provided:
            lines.extend(block)
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return 0
    target_row = row_range(lines, row_at)
    providers_at = providers_index(lines, target_row)
    if providers_at < 0:
        target_row = row_range(lines, row_at)
        lines.insert(row_at + 1, "  config:")
        lines.insert(row_at + 2, "    providers:")
        providers_at = row_at + 2
        target_row = row_range(lines, row_at)
    existing = {name for name, _ in provider_blocks(lines, target_row, providers_at, child_indent)}
    missing = [(name, block) for name, block in provided if name not in existing]
    if not missing:
        return 0
    insert_at = blocks_extent(lines, target_row, providers_at, child_indent)
    payload: list[str] = []
    for _name, block in missing:
        payload.extend(block)
    lines[insert_at:insert_at] = payload
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write(__doc__ or "")
        return 2
    return merge(Path(argv[1]), Path(argv[2]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
