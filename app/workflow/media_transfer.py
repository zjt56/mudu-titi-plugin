"""Stage explicitly selected batch media over SSH without replacing remote files.

stage_media returns the basename-to-URL mapping accepted by upload.prepare's
media_map option. The remote static directory must already exist, be served at
/source-media/, and be writable only by trusted principals. A failed attempt may
leave newly created files; this module never removes them. Retries reuse the same batch_id and verify existing files. Remote Python 3 and POSIX O_NOFOLLOW/dir_fd support are required.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path, PurePosixPath


MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_BATCH_BYTES = 32 * 1024 * 1024
MAX_FILES = 256


class MediaTransferError(Exception):
    """A deliberately sanitized staging failure."""


# No caller-controlled text is interpolated into the shell command. All input
# travels on stdin. Open every directory without following symlinks, then create
# each leaf exclusively. Read back both the open file and its directory entry.
_REMOTE = r'''
import base64, hashlib, json, os, stat, sys
try:
    request = json.load(sys.stdin)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open('/', flags)
    for part in request['root'].split('/')[1:]:
        next_fd = os.open(part, flags, dir_fd=directory)
        os.close(directory)
        directory = next_fd
    opened = []
    for item in request['files']:
        name = item['name']
        if not name or '/' in name or name in ('.', '..'):
            raise ValueError()
        data = base64.b64decode(item['data'], validate=True)
        if hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError()
        try:
            fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o644, dir_fd=directory)
            created = True
        except FileExistsError:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            created = False
        opened.append((fd, name, item['sha256']))
        if created:
            view = memoryview(data)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise ValueError()
                view = view[count:]
            os.fsync(fd)
    for fd, name, expected in opened:
        original = os.fstat(fd)
        if not stat.S_ISREG(original.st_mode) or original.st_size > 8 * 1024 * 1024:
            raise ValueError()
        entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(entry.st_mode) or (entry.st_dev, entry.st_ino) != (original.st_dev, original.st_ino):
            raise ValueError()
        os.lseek(fd, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ValueError()
        entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
        after = os.fstat(fd)
        fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if fingerprint(entry) != fingerprint(original) or fingerprint(after) != fingerprint(original):
            raise ValueError()
    print(json.dumps([item[2] for item in opened]))
except Exception:
    sys.exit(1)
'''


def _settings():
    """Read the same private settings source and precedence as ssh_tunnel."""
    path = os.environ.get('MUDU_UPLOAD_SSH_CONFIG')
    settings = None
    if path:
        try:
            with open(path, encoding='utf-8') as handle:
                settings = json.load(handle)
            if not isinstance(settings, dict):
                raise ValueError()
        except FileNotFoundError:
            settings = None
        except (OSError, ValueError):
            raise MediaTransferError('Cannot read SSH settings') from None
    def get(key, env, default=''):
        return str(settings.get(key, default) if settings is not None else os.environ.get(env, default))
    host = get('ssh_host', 'MUDU_UPLOAD_SSH_HOST').strip()
    port = get('ssh_port', 'MUDU_UPLOAD_SSH_PORT', '22')
    identity = get('ssh_identity', 'MUDU_UPLOAD_SSH_IDENTITY')
    if not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9.:-]*', host):
        raise MediaTransferError('Invalid SSH destination')
    if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise MediaTransferError('Invalid SSH port')
    if identity and not os.path.isfile(identity):
        raise MediaTransferError('SSH identity file unavailable')
    return host, port, identity


def _parts(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise MediaTransferError('Invalid media path')
    parts = value.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise MediaTransferError('Invalid media path')
    return parts


def _open_root(root):
    raw = os.fspath(root)
    if not isinstance(raw, str) or '..' in raw.split('/'):
        raise ValueError()
    path = os.path.abspath(raw)
    current = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.split('/')[1:]:
            if not part:
                continue
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = next_fd
        return current
    except BaseException:
        os.close(current)
        raise


def _read_file(root_fd, relative):
    parts = _parts(relative)
    current = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = next_fd
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                raise ValueError()
            with os.fdopen(fd, 'rb') as stream:
                fd = None
                content = stream.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    raise ValueError()
                after = os.fstat(stream.fileno())
                entry = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
                fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
                if fingerprint(before) != fingerprint(after) or fingerprint(before) != fingerprint(entry):
                    raise ValueError()
                return content
        finally:
            if fd is not None:
                os.close(fd)
    finally:
        os.close(current)


def stage_media(batch_root, references, *, static_root, batch_id, timeout=60, expected_destination=None, settings=None):
    """Stage explicit relative file references; return {basename: verified URL}.

    references contains inventory descriptors with path and sha256 fields. static_root is the absolute remote /source-media/ filesystem
    root, supplied by the operator. Reuse batch_id for safe retries.
    The caller must separately confirm the static server maps that root and run
    the existing upload availability checks before freezing/submitting a payload.
    """
    if not isinstance(static_root, str) or not static_root.startswith('/'):
        raise MediaTransferError('Remote static root must be absolute')
    _parts(static_root[1:])
    if not isinstance(batch_id, str) or not batch_id or len(batch_id) > 4096:
        raise MediaTransferError('Invalid batch identifier')
    if isinstance(references, (str, bytes)):
        raise MediaTransferError('Invalid media references')
    namespace = hashlib.sha256(batch_id.encode('utf-8')).hexdigest()
    files, mapping, seen_paths, bindings = {}, {}, set(), []
    total = 0
    try:
        root_fd = _open_root(batch_root)
        try:
            for reference in references:
                if not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}:
                    raise MediaTransferError('Invalid inventory descriptor')
                relative, expected = reference['path'], reference['sha256']
                if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                    raise MediaTransferError('Invalid inventory digest')
                parts = _parts(relative)
                name = parts[-1]
                if relative in seen_paths:
                    raise MediaTransferError('Duplicate inventory path')
                if len(seen_paths) >= MAX_FILES:
                    raise MediaTransferError('Too many media files')
                if name in mapping:
                    raise MediaTransferError('Duplicate media basename')
                seen_paths.add(relative)
                suffix = Path(name).suffix
                if not re.fullmatch(r'\.[A-Za-z0-9]{1,10}', suffix):
                    raise MediaTransferError('Invalid media extension')
                content = _read_file(root_fd, relative)
                digest = hashlib.sha256(content).hexdigest()
                if digest != expected:
                    raise MediaTransferError('Media changed since inventory')
                bindings.append((relative, expected))
                total += len(content)
                if total > MAX_BATCH_BYTES:
                    raise MediaTransferError('Media batch too large')
                target = namespace + '-' + digest + suffix
                files[target] = {'name': target, 'sha256': digest,
                                 'data': base64.b64encode(content).decode('ascii')}
                mapping[name] = '/source-media/' + target
        finally:
            os.close(root_fd)
    except MediaTransferError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError):
        raise MediaTransferError('Cannot read media safely') from None
    if not files:
        return {}
    host, port, identity = settings if settings is not None else _settings()
    if expected_destination is not None and host != expected_destination:
        raise MediaTransferError('SSH destination changed before media transfer')
    args = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', '-p', port]
    if identity:
        args += ['-i', identity, '-o', 'IdentitiesOnly=yes']
    args += [host, 'python3 -c ' + shlex.quote(_REMOTE)]
    request = json.dumps({'root': static_root, 'files': list(files.values())}, ensure_ascii=True).encode('ascii')
    try:
        result = subprocess.run(args, input=request, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=timeout, check=False)
        expected = [item['sha256'] for item in files.values()]
        if result.returncode != 0 or json.loads(result.stdout) != expected:
            raise ValueError()
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise MediaTransferError('Media transfer or verification failed') from None
    try:
        root_fd = _open_root(batch_root)
        try:
            for relative, expected_digest in bindings:
                if hashlib.sha256(_read_file(root_fd, relative)).hexdigest() != expected_digest:
                    raise ValueError()
        finally:
            os.close(root_fd)
    except (OSError, ValueError, TypeError):
        raise MediaTransferError('Media changed during transfer') from None
    return mapping
