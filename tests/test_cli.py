# Copyright 2024 General Atomics
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for fdp.cli argparse plumbing.

Tests mock os.execvpe, subprocess.run, FdpFileSystem, and setup_environment
to verify the CLI dispatches correctly without spawning subprocesses.
"""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock


def _run_cli(argv):
    """Invoke fdp.cli.main with mocks; return (stdout, exit_code)."""
    from fdp import cli
    buf = io.StringIO()
    exit_code = None
    with mock.patch.object(sys, "argv", argv), \
            mock.patch.object(cli, "setup_environment"), \
            redirect_stdout(buf):
        try:
            cli.main()
        except SystemExit as e:
            exit_code = e.code
    return buf.getvalue(), exit_code


class TestCliEnv(unittest.TestCase):
    def test_env_prints_export_lines(self):
        out, _ = _run_cli(["fdp", "env"])
        # At least one export line for a generic var
        self.assertTrue(
            any(line.startswith("export ") for line in out.splitlines()),
            f"no export lines in output: {out!r}",
        )


class TestCliRun(unittest.TestCase):
    def test_run_invokes_subprocess(self):
        from fdp import cli
        with mock.patch.object(sys, "argv",
                                ["fdp", "run", "echo", "hi"]), \
                mock.patch.object(cli, "setup_environment"), \
                mock.patch.object(cli.subprocess, "run",
                                   return_value=mock.MagicMock(
                                       returncode=0)) as run_mock:
            try:
                cli.main()
            except SystemExit:
                pass
        run_mock.assert_called_once()
        cmd = run_mock.call_args.args[0]
        self.assertEqual(cmd, ["echo", "hi"])


class TestCliLs(unittest.TestCase):
    def test_ls_calls_fdpfilesystem(self):
        from fdp import cli
        fake_fs = mock.MagicMock()
        fake_fs.ls.return_value = [mock.MagicMock(__str__=lambda self: "x")]
        with mock.patch.object(sys, "argv",
                                ["fdp", "ls", "/some/path"]), \
                mock.patch.object(cli, "setup_environment"), \
                mock.patch.object(cli, "FdpFileSystem",
                                   return_value=fake_fs), \
                redirect_stdout(io.StringIO()):
            try:
                cli.main()
            except SystemExit:
                pass
        fake_fs.ls.assert_called_once_with("/some/path", dirs_only=False)


class TestCliDevices(unittest.TestCase):
    def test_devices_lists_discovered(self):
        out, _ = _run_cli(["fdp", "devices"])
        # The fallback d3d is always present
        self.assertIn("d3d", out)


class TestCliChat(unittest.TestCase):
    def test_chat_calls_execvpe(self):
        from fdp import cli
        with mock.patch.object(sys, "argv", ["fdp", "chat"]), \
                mock.patch.object(cli, "setup_environment"), \
                mock.patch.object(cli.os, "execvpe") as ev:
            try:
                cli.main()
            except SystemExit:
                pass
        ev.assert_called_once()
        argv = ev.call_args.args[1]
        self.assertEqual(argv[:4],
                         [sys.executable, "-m", "toksearch.llm.cli",
                          "chat"])


class TestCliQuery(unittest.TestCase):
    def test_query_forwards_prompt(self):
        from fdp import cli
        with mock.patch.object(sys, "argv",
                                ["fdp", "query", "hello"]), \
                mock.patch.object(cli, "setup_environment"), \
                mock.patch.object(cli.os, "execvpe") as ev:
            try:
                cli.main()
            except SystemExit:
                pass
        argv = ev.call_args.args[1]
        self.assertIn("hello", argv)


class TestDefaultDeviceFlag(unittest.TestCase):
    def test_default_device_passed_to_setup_environment(self):
        from fdp import cli
        with mock.patch.object(sys, "argv",
                                ["fdp", "--default-device", "d3d",
                                 "env"]), \
                mock.patch.object(cli,
                                   "setup_environment") as su, \
                redirect_stdout(io.StringIO()):
            try:
                cli.main()
            except SystemExit:
                pass
        # setup_environment should have been called with device="d3d"
        kwargs = su.call_args.kwargs
        self.assertEqual(kwargs.get("device"), "d3d")


if __name__ == "__main__":
    unittest.main()
