"""Select locally owned attachments for explicit media staging.

This module performs no writes or network operations. Inventory and staging are
separate reads; returned SHA-256 values bind staging to the inventory.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote

from .media_transfer import _open_root, _read_file, MAX_FILES, MAX_BATCH_BYTES


class MediaInventoryError(ValueError):
    """A reference cannot be safely attributed to a local batch file."""


_DIRECTORIES = ('01_source/media', '01_source_ans/media', '02_audio')
_SUFFIXES = {
    'image': {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'},
    'audio': {'.mp3', '.wav', '.m4a', '.ogg'},
}


def referenced_media(batch_root, batch_meta):
    """Return path and SHA-256 descriptors for explicitly declared attachments.

    Only the canonical URL /source-media/<quoted batch folder>/<quoted filename>
    establishes local ownership. Every attachment must satisfy this contract;
    external or otherwise unrelated references are rejected for caller review.
    Search is limited to direct files in the three conventional directories.
    Duplicate candidate basenames are rejected as ambiguous.
    """
    root = Path(batch_root)
    if not isinstance(batch_meta, dict):
        raise MediaInventoryError('Batch metadata must be an object')
    prefix = '/source-media/' + quote(root.name, safe='') + '/'
    names = set()

    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'attachments':
                    if not isinstance(child, list):
                        raise MediaInventoryError('Attachments must be a list')
                    for attachment in child:
                        if not isinstance(attachment, dict):
                            raise MediaInventoryError('Invalid attachment')
                        url = attachment.get('url')
                        kind = attachment.get('kind')
                        if not isinstance(url, str) or not url.startswith(prefix):
                            raise MediaInventoryError('Attachment URL is not owned by this batch')
                        encoded = url[len(prefix):]
                        try:
                            name = unquote(encoded, encoding='utf-8', errors='strict')
                        except UnicodeError:
                            raise MediaInventoryError('Invalid attachment URL') from None
                        if (not name or name in ('.', '..') or '/' in name or '\\' in name
                                or '\x00' in name or quote(name, safe='') != encoded
                                or not re.fullmatch(r'\.[A-Za-z0-9]{1,10}', Path(name).suffix)):
                            raise MediaInventoryError('Invalid attachment filename')
                        if not isinstance(kind, str) or kind not in _SUFFIXES or Path(name).suffix.lower() not in _SUFFIXES[kind]:
                            raise MediaInventoryError('Attachment kind and suffix do not match')
                        names.add(name)
                        visit(attachment)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            for match in re.finditer(r'\[IMG\s+([^\]]+)\]', value):
                name = match.group(1).strip()
                if (not name or name in ('.', '..') or '/' in name or '\\' in name
                        or '\x00' in name or Path(name).suffix.lower() not in _SUFFIXES['image']):
                    raise MediaInventoryError('Invalid image marker')
                names.add(name)
            if '[IMG' in re.sub(r'\[IMG\s+[^\]]+\]', '', value):
                raise MediaInventoryError('Invalid image marker')

    visit(batch_meta)
    try:
        if len(names) > MAX_FILES:
            raise MediaInventoryError('Too many media files')
        root_fd = _open_root(root)
        try:
            selected = []
            total = 0
            for name in sorted(names):
                candidates = []
                for directory in _DIRECTORIES:
                    relative = directory + '/' + name
                    try:
                        content = _read_file(root_fd, relative)
                    except FileNotFoundError:
                        continue
                    total += len(content)
                    if total > MAX_BATCH_BYTES:
                        raise MediaInventoryError('Media batch too large')
                    candidates.append((relative, hashlib.sha256(content).hexdigest()))
                if not candidates:
                    raise MediaInventoryError('Referenced media is missing: ' + name)
                if len(candidates) != 1:
                    raise MediaInventoryError('Ambiguous media basename: ' + name)
                selected.append({'path': candidates[0][0], 'sha256': candidates[0][1]})
            return selected
        finally:
            os.close(root_fd)
    except MediaInventoryError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise MediaInventoryError('Cannot safely read candidate media') from exc
