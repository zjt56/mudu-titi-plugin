"""CHANGELOG 保留策略：自动删除超过两天的变更记录。

背景：用户要求把 CHANGELOG（项目变更记录）放进「上传仓库」的忽略名单，
并且定期清理——超过两天的记录直接删除。本模块负责第二部分：判定并执行
按文件名的日期前缀（YYYY-MM-DD）计算年龄，删除超期记录。

设计要点：
- 只看文件名开头的日期（\\d{4}-\\d{2}-\\d{2}），不看文件 mtime，避免
  复制/解压改写 mtime 后误删刚写的记录或漏删旧记录。
- 日期按插入时区（默认由调用方传 tz 或取本地日期）的当天零点为基准。
- 永不删除制度文件与台账（README.md / RULES.md）以及非日期命名的文件。
- dry-run 与真实删除共用同一判定函数，测试无需触碰磁盘时间。
- 删除失败只记录，不抛出让后台线程或 API 崩溃。

入口：
- collect_expired(directory, keep_days=2, today=None) -> list[Path]
- prune(directory, keep_days=2, today=None, dry_run=False) -> dict
- 由 workbench/server.py 的周期线程定期调用 prune()。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

# 制度文件：绝不清理，即便被误命名为日期格式也不删。
PROTECTED_NAMES = frozenset({"README.md", "RULES.md"})

# 记录文件名形如 2026-11-19-<slug>.md
RECORD_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(.+)\.md$")

DEFAULT_KEEP_DAYS = 2


def _parse_record_date(name: str) -> date | None:
    """从文件名解析日期；不是日期化记录则返回 None。"""
    match = RECORD_RE.match(name)
    if not match:
        return None
    year, month, day, slug = match.groups()
    if not slug:
        return None
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def _resolve_today(today) -> date:
    """归一 today 参数：None 取本地日期，date/datetime 取其 date 部分。"""
    if today is None:
        return date.today()
    if isinstance(today, datetime):
        return today.date()
    if isinstance(today, date):
        return today
    raise TypeError("today must be date/datetime/None")


def _today_in_zone(tz_name: str | None) -> date:
    """按给定时区取当天日期；无 tz 或解析失败回退本地日期。"""
    if not tz_name or tz_name == "local":
        return date.today()
    try:
        import zoneinfo  # Python 3.9+
        zone = zoneinfo.ZoneInfo(tz_name)
        return datetime.now(zone).date()
    except Exception:  # noqa: BLE001 - 回退本地日期，绝不因时区解析失败而罢工
        return date.today()


def collect_expired(directory, keep_days: int = DEFAULT_KEEP_DAYS, today=None,
                    tz: str | None = None) -> list[Path]:
    """返回 directory 下超过 keep_days 天的日期化记录文件（按日期升序）。

    超过 keep_days 天 = (今天 - 记录日期) > keep_days。
    keep_days=2 时保留今天、昨天、前天，删除更早的记录。
    """
    if isinstance(keep_days, bool) or not isinstance(keep_days, int) or keep_days < 0:
        raise ValueError("keep_days must be a non-negative integer")
    root = Path(directory)
    if not root.is_dir():
        return []
    ref = _today_in_zone(tz) if (today is None and tz) else _resolve_today(today)
    cutoff = timedelta(days=keep_days)
    expired: list[tuple[date, Path]] = []
    for entry in root.iterdir():
        if not entry.is_file():
            continue
        if entry.name in PROTECTED_NAMES:
            continue
        day = _parse_record_date(entry.name)
        if day is None:
            continue
        if (ref - day) > cutoff:
            expired.append((day, entry))
    expired.sort(key=lambda item: (item[0], item[1].name))
    return [path for _, path in expired]


def prune(directory, keep_days: int = DEFAULT_KEEP_DAYS, today=None,
          dry_run: bool = False, tz: str | None = None) -> dict:
    """删除超过 keep_days 天的记录，返回结构化结果（不抛出单个删除错误）。"""
    path_expired = collect_expired(directory, keep_days=keep_days, today=today, tz=tz)
    removed: list[str] = []
    failed: list[dict] = []
    for path in path_expired:
        if dry_run:
            removed.append(path.name)
            continue
        try:
            path.unlink()
            removed.append(path.name)
        except OSError as exc:  # noqa: BLE001 - 单个失败不阻断整轮清理
            failed.append({"name": path.name, "error": str(exc)})
    return {
        "directory": str(directory),
        "keep_days": keep_days,
        "dry_run": dry_run,
        "expired": [p.name for p in path_expired],
        "removed": removed,
        "failed": failed,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


if __name__ == "__main__":  # 手动运维入口：python -m workflow.changelog_retention [--dry-run]
    import argparse
    import json

    parser = argparse.ArgumentParser(description="删除超过保留期的 CHANGELOG 记录")
    parser.add_argument("--dir", default=str(Path(__file__).resolve().parents[2] / "CHANGELOG"))
    parser.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prune(args.dir, keep_days=args.keep_days, dry_run=args.dry_run),
                     ensure_ascii=False, indent=2))
