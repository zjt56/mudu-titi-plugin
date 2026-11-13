"""Repository hygiene: keep tracked text files free of trailing blank lines.

``git diff --check`` flags a new blank line at EOF, but it only sees the current
diff. This regression scans the tracked text files so a future edit cannot
reintroduce the same whitespace rejection unnoticed.
"""
from pathlib import Path
import os
import re
import subprocess
import unittest


class RepoHygieneTest(unittest.TestCase):
    def test_tracked_text_files_have_no_trailing_blank_lines(self):
        root = Path(__file__).resolve().parents[2]
        if not (root / ".git").exists():
            self.skipTest("not a git checkout; repository-wide hygiene is unavailable")
        try:
            raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=root,
                                          stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git is unavailable for repository-wide hygiene scan")
        offenders = []
        for item in raw.split(b"\0"):
            if not item:
                continue
            relative = os.fsdecode(item)
            path = root / relative
            if not path.is_file():
                continue
            data = path.read_bytes()
            # Binary files may legitimately contain arbitrary NUL/blank bytes.
            if b"\0" in data[:8192]:
                continue
            if re.search(rb"\n[ \t]*\n+\Z", data):
                offenders.append(relative)
        self.assertEqual(offenders, [],
                         "以下文件末尾有多余空行，请删除后再提交：\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
