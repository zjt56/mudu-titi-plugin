"""Stage output must survive a GBK console and never wedge a stage in `running`.

Regression for the 2020 Anhui paper: `extract` finished and wrote every artefact,
then the executor died echoing the log line to a cp936 stdout. The stage stayed
`running` forever and the supervisor re-ran the whole extraction (including the
PDF OCR) every 30 seconds.
"""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from workflow import contracts, manifest as mf, runner


class StageConsoleEncodingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="stage-console-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        mf.save(self.root, mf.new_manifest("console-fixture", self.root, {}, {},
                                          {"source": "2020年安徽省中考英语"}))

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    # ------------------------------------------------------------- 子进程 UTF-8
    def test_stage_env_makes_child_print_utf8(self):
        env = runner._stage_env()
        self.assertEqual(env.get("PYTHONIOENCODING"), "utf-8")
        proc = subprocess.run(
            [sys.executable, "-c", "print('提取完成 16150 字符')"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "提取完成 16150 字符")
        self.assertNotIn("\ufffd", proc.stdout)

    def test_stage_env_does_not_mutate_parent_env(self):
        before = dict(os.environ)
        runner._stage_env()
        self.assertEqual(dict(os.environ), before)

    # ------------------------------------------------------------- 回显兜底
    def test_echo_survives_strict_gbk_console(self):
        buf = io.BytesIO()
        console = io.TextIOWrapper(buf, encoding="gbk", errors="strict")
        with patch.object(sys, "stdout", console):
            runner._echo("    提取完成 16150 字符 \ufffd")
        console.flush()
        written = buf.getvalue().decode("gbk")
        self.assertIn("16150", written)
        self.assertNotIn("\ufffd", written)

    def test_echo_swallows_closed_stream(self):
        class Closed:
            def write(self, _text):
                raise ValueError("I/O operation on closed file")

            def flush(self):
                raise ValueError("I/O operation on closed file")

        with patch.object(sys, "stdout", Closed()):
            runner._echo("任意内容")

    # ------------------------------------------------------------- 整条执行链
    def test_command_output_reaches_log_as_utf8(self):
        seen = {}

        def fake_run(argv, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, "提取完成 16150 字符", "")

        buf = io.BytesIO()
        console = io.TextIOWrapper(buf, encoding="gbk", errors="strict")
        with patch("subprocess.run", side_effect=fake_run), patch.object(sys, "stdout", console):
            code = runner._execute_command(self.root, mf.load(self.root),
                                           contracts.STAGE_BY_ID["preassemble"])
        console.flush()
        self.assertEqual(seen.get("env", {}).get("PYTHONIOENCODING"), "utf-8")
        # 预装配产物不存在，命令成功也不算完成；关键是日志与回显没炸。
        self.assertEqual(code, 1)
        log = (self.root / "workflow" / "logs" / "preassemble.log").read_text(encoding="utf-8")
        self.assertIn("提取完成 16150 字符", log)
        self.assertNotIn("\ufffd", log)
        self.assertEqual(mf.load(self.root)["stages"]["preassemble"]["status"], mf.FAILED)

    def test_real_child_output_lands_in_log_without_replacement(self):
        script = self.put("echo_zh.py", "print('提取完成 16150 字符')\n")
        stage = replace(contracts.STAGE_BY_ID["preassemble"], command=(str(script),))
        code = runner._execute_command(self.root, mf.load(self.root), stage)
        log = (self.root / "workflow" / "logs" / "preassemble.log").read_text(encoding="utf-8")
        # 子进程真的按 UTF-8 输出，父进程按 UTF-8 解码：中文原样进日志。
        self.assertIn("提取完成 16150 字符", log)
        self.assertNotIn("\ufffd", log)
        self.assertEqual(code, 1)  # 没有产物，命令成功也不算完成

    def test_executor_crash_leaves_failed_not_running(self):
        def boom(*_args, **_kwargs):
            raise RuntimeError("模拟执行器崩溃")

        with patch("subprocess.run", side_effect=boom):
            with self.assertRaises(RuntimeError):
                runner._execute_command(self.root, mf.load(self.root),
                                        contracts.STAGE_BY_ID["preassemble"])
        stage = mf.load(self.root)["stages"]["preassemble"]
        self.assertEqual(stage["status"], mf.FAILED)
        self.assertTrue(stage["notes"].startswith("执行器异常"), stage["notes"])
        self.assertEqual(stage["exit_code"], 1)


if __name__ == "__main__":
    unittest.main()
