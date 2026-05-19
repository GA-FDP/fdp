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
from contextlib import ExitStack, redirect_stdout
from unittest import mock

from fdp.devices import Device, clear_device_cache


_TEST_D3D = Device(
    name="d3d",
    pelican_root="pelican://test/fdp-d3d",
    origin_server="root://test:8443",
    mds_default_tree_path="pelican://test/fdp-d3d/mds/~t",
    description="test d3d",
)


def _fake_ep(name, value):
    ep = mock.MagicMock()
    ep.name = name
    ep.load.return_value = value
    return ep


def _patch_devices(stack, devices=((_TEST_D3D.name, _TEST_D3D),)):
    """Context-manager hook used by the helpers below.

    fdp no longer ships a fallback Device, so CLI tests that don't
    otherwise care about device discovery still need at least one
    contributor registered for resolve_default_device() to succeed.
    """
    clear_device_cache()
    stack.callback(clear_device_cache)
    stack.enter_context(mock.patch(
        "fdp.devices._entry_points",
        return_value=[_fake_ep(n, d) for n, d in devices],
    ))


def _run_cli(argv, devices=((_TEST_D3D.name, _TEST_D3D),)):
    """Invoke fdp.cli.main with mocks; return (stdout, exit_code)."""
    from fdp import cli
    buf = io.StringIO()
    exit_code = None
    with ExitStack() as stack:
        _patch_devices(stack, devices)
        stack.enter_context(mock.patch.object(sys, "argv", argv))
        stack.enter_context(mock.patch.object(cli, "setup_environment"))
        stack.enter_context(redirect_stdout(buf))
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
        with ExitStack() as stack:
            _patch_devices(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "run", "echo", "hi"]))
            stack.enter_context(mock.patch.object(cli, "setup_environment"))
            run_mock = stack.enter_context(mock.patch.object(
                cli.subprocess, "run",
                return_value=mock.MagicMock(returncode=0)))
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
        with ExitStack() as stack:
            _patch_devices(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "ls", "/some/path"]))
            stack.enter_context(mock.patch.object(cli, "setup_environment"))
            stack.enter_context(mock.patch.object(
                cli, "FdpFileSystem", return_value=fake_fs))
            stack.enter_context(redirect_stdout(io.StringIO()))
            try:
                cli.main()
            except SystemExit:
                pass
        fake_fs.ls.assert_called_once_with("/some/path", dirs_only=False)


class TestCliDevices(unittest.TestCase):
    def test_devices_lists_discovered(self):
        out, _ = _run_cli(["fdp", "devices"])
        self.assertIn("d3d", out)

    def test_devices_empty_when_no_contributors(self):
        # No installed contributors → nothing listed, no crash.
        out, _ = _run_cli(["fdp", "devices"], devices=())
        self.assertEqual(out.strip(), "")


class TestCliChat(unittest.TestCase):
    def test_chat_calls_execvpe(self):
        from fdp import cli
        with ExitStack() as stack:
            _patch_devices(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "chat"]))
            stack.enter_context(mock.patch.object(cli, "setup_environment"))
            ev = stack.enter_context(mock.patch.object(cli.os, "execvpe"))
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
        with ExitStack() as stack:
            _patch_devices(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "query", "hello"]))
            stack.enter_context(mock.patch.object(cli, "setup_environment"))
            ev = stack.enter_context(mock.patch.object(cli.os, "execvpe"))
            try:
                cli.main()
            except SystemExit:
                pass
        argv = ev.call_args.args[1]
        self.assertIn("hello", argv)


class TestDefaultDeviceFlag(unittest.TestCase):
    def test_default_device_passed_to_setup_environment(self):
        from fdp import cli
        with ExitStack() as stack:
            _patch_devices(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv",
                ["fdp", "--default-device", "d3d", "env"]))
            su = stack.enter_context(mock.patch.object(
                cli, "setup_environment"))
            stack.enter_context(redirect_stdout(io.StringIO()))
            try:
                cli.main()
            except SystemExit:
                pass
        kwargs = su.call_args.kwargs
        self.assertEqual(kwargs.get("device"), "d3d")


if __name__ == "__main__":
    unittest.main()
