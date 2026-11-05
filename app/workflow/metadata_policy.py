"""Local placeholders are absence markers, never evidence or invented labels."""
from __future__ import annotations

MISSING = "未提供"
PAPER_TEXT_FIELDS = ("paper_title", "source", "source_type")


def with_placeholders(metadata: dict | None) -> dict:
    """Keep every identified value; fill only missing paper/source text fields.

    Year remains numeric when known and absent when unknown. In particular, an
    absence marker must not become a fabricated year, province, or exam class.
    """
    result = dict(metadata or {})
    for key in PAPER_TEXT_FIELDS:
        value = result.get(key)
        if not isinstance(value, str) or not value.strip():
            result[key] = MISSING
    return result


def missing_source_fields(metadata: dict) -> list[str]:
    return [key for key in ("source", "source_type", "year", "region")
            if metadata.get(key) in (None, "", MISSING)]
