# -*- coding: utf-8 -*-
"""把便携 Python / ffmpeg 二进制拉进 vendor/，供插件随包分发。

在有网络的机器上运行（只在准备发布包时需要；终端用户不执行这个）：

    python app/audio/vendor/fetch_vendor.py                      # 当前平台
    python app/audio/vendor/fetch_vendor.py --platform darwin-arm64 --platform win32-x64

按 vendor/platforms.json 里登记的 URL 下载、校验 sha256（空则下载后打印实际值供回填，
并在本次跳过校验并警告）、解压到：

    vendor/python/<platform>/           便携 CPython（含 bin/python3 或 python.exe）
    vendor/ffmpeg/<platform>/bin/        ffmpeg 与 ffprobe

sha256 为空时会打印 "__SHA256__ <url> <hash>"，把它填回 platforms.json 即锁定版本。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "platforms.json"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "wf-plugin-vendor"})
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {dest.name}: {done * 100 // total}%", end="", flush=True)
    print()
    tmp.replace(dest)
    return dest


def verify(spec: dict, path: Path) -> None:
    want = (spec.get("sha256") or "").strip().lower()
    got = sha256_of(path)
    if not want:
        print(f"  ! sha256 未登记，已下载实际值：__SHA256__ {spec['url']} {got}")
        return
    if want != got:
        path.unlink(missing_ok=True)
        raise SystemExit(f"sha256 不匹配：{spec['url']}\n  期望 {want}\n  实际 {got}")


def extract_python(spec: dict, archive: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    if spec.get("kind") == "tar.gz":
        with tarfile.open(archive, "r:gz") as tf:
            members = tf.getmembers()
            top = {m.name.split("/", 1)[0] for m in members if m.name}
            prefix = top.pop() if len(top) == 1 else None
        with tarfile.open(archive, "r:gz") as tf:
            for m in tf.getmembers():
                name = m.name
                if prefix and name.startswith(prefix + "/"):
                    name = name[len(prefix) + 1:]
                if not name:
                    continue
                m.name = name
                tf.extract(m, target, filter="data") if hasattr(tarfile, "data_filter") else tf.extract(m, target)
    else:
        raise SystemExit("便携 Python 目前只支持 tar.gz 包")


def extract_ffmpeg(entry: dict, archive: Path, bindir: Path) -> None:
    bindir.mkdir(parents=True, exist_ok=True)
    targets = entry.get("files") or {}
    if entry.get("kind") == "zip":
        with zipfile.ZipFile(archive) as zf:
            for member in zf.namelist():
                base = member.rsplit("/", 1)[-1]
                for dest_name, want_base in targets.items():
                    if base == want_base:
                        out = bindir / (dest_name + (".exe" if want_base.endswith(".exe") else ""))
                        with zf.open(member) as src, out.open("wb") as dst:
                            shutil.copyfileobj(src, dst)
                        if not out.name.endswith(".exe"):
                            out.chmod(0o755)
                        print(f"  + {out}")
    else:
        raise SystemExit("ffmpeg 目前只支持 zip 包")


def handle_python(manifest: dict, plat: str, workdir: Path) -> None:
    spec = (manifest.get("python") or {}).get(plat)
    if not spec:
        print(f"  python {plat}: 清单未登记，跳过")
        return
    print(f"python {plat}: {spec['url']}")
    archive = download(spec["url"], workdir / f"python-{plat}.tar.gz")
    verify(spec, archive)
    extract_python(spec, archive, HERE / "python" / plat)
    print(f"  ✓ vendor/python/{plat}")


def handle_ffmpeg(manifest: dict, plat: str, workdir: Path) -> None:
    entries = (manifest.get("ffmpeg") or {}).get(plat) or []
    if not entries:
        print(f"  ffmpeg {plat}: 清单未登记，跳过")
        return
    bindir = HERE / "ffmpeg" / plat / "bin"
    for i, entry in enumerate(entries):
        print(f"ffmpeg {plat} [{i + 1}/{len(entries)}]: {entry['url']}")
        archive = download(entry["url"], workdir / f"ffmpeg-{plat}-{i}.zip")
        verify(entry, archive)
        extract_ffmpeg(entry, archive, bindir)
    print(f"  ✓ vendor/ffmpeg/{plat}/bin")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="下载并校验随插件分发的运行时二进制")
    ap.add_argument("--platform", action="append", default=None,
                    help="darwin-arm64 / darwin-x64 / win32-x64 / linux-x64，可多次")
    ap.add_argument("--only", choices=("python", "ffmpeg"), default=None)
    args = ap.parse_args(argv)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    platforms = args.platform or sorted(set(manifest.get("python", {})) | set(manifest.get("ffmpeg", {})))
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        for plat in platforms:
            print(f"== {plat} ==")
            if args.only != "ffmpeg":
                handle_python(manifest, plat, workdir)
            if args.only != "python":
                handle_ffmpeg(manifest, plat, workdir)
    print("\n完成。把上一步打印的 __SHA256__ 值填回 platforms.json 可锁定版本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
