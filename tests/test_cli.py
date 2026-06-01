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


# Minimal catalog YAML for a fake d3d test tokamak.
_D3D_TEST_YAML = """\
schema_version: 1
name: d3d
description: DIII-D tokamak (test)
locators:
  - kind: mds_tree
    name: main
    transport: pelican
    search_path: [pelican://test/fdp-d3d/mds/~t]
  - kind: ptdata_indexed
    name: main
    transport: pelican
    index_dir: pelican://test/fdp-d3d/ptdata/index
  - kind: sql
    name: d3drdb
    driver: mssql
    host: d3drdb.gat.com
    port: 8001
    database: d3drdb
extra_env: {D3DATA: /d3d/data}
"""


def _make_catalog_ep(name: str, yaml_text: str):
    src = mock.MagicMock()
    src.read_text.return_value = yaml_text
    ep = mock.MagicMock()
    ep.name = name
    ep.load.return_value = src
    return ep


def _patch_catalog(stack, yaml_text: str = _D3D_TEST_YAML):
    """Patch the catalog entry points and reset the cache."""
    from fdp.catalog import catalog as _cat
    ep = _make_catalog_ep("d3d", yaml_text)
    stack.enter_context(mock.patch("fdp.catalog.entry_points",
                                    return_value=[ep]))
    _cat._cache = None
    stack.callback(lambda: setattr(_cat, "_cache", None))


def _run_cli(argv, yaml_text: str = _D3D_TEST_YAML):
    """Invoke fdp.cli.main with mocks; return (stdout, exit_code)."""
    from fdp import cli
    buf = io.StringIO()
    exit_code = None
    with ExitStack() as stack:
        _patch_catalog(stack, yaml_text)
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
            _patch_catalog(stack)
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
            _patch_catalog(stack)
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


class TestCliCatalog(unittest.TestCase):
    """Tests for the 'fdp catalog' subcommands."""

    _CATALOG_YAML = """\
schema_version: 1
name: d3d
description: DIII-D tokamak
locators:
  - kind: mds_tree
    name: main
    transport: pelican
    search_path: [pelican://test/fdp-d3d/mds/~t]
  - kind: ptdata_indexed
    name: main
    transport: pelican
    index_dir: pelican://test/fdp-d3d/ptdata/index
  - kind: sql
    name: d3drdb
    driver: mssql
    host: d3drdb.gat.com
    port: 8001
    database: d3drdb
extra_env: {D3DATA: /d3d/data}
"""

    def _make_mock_ep(self):
        ep = mock.MagicMock()
        ep.name = "d3d"
        ep.value = "mock:d3d"
        src = mock.MagicMock()
        src.read_text.return_value = self._CATALOG_YAML
        ep.load.return_value = src
        return ep

    def test_catalog_list_prints_name_and_description(self):
        from fdp import cli
        from fdp.catalog import _Catalog
        ep = self._make_mock_ep()
        mock_catalog = _Catalog()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", ["fdp", "catalog", "list"]))
            stack.enter_context(mock.patch.object(cli, "setup_environment"))
            stack.enter_context(mock.patch("fdp.catalog.entry_points", return_value=[ep]))
            stack.enter_context(mock.patch.object(cli, "catalog", mock_catalog))
            buf = io.StringIO()
            stack.enter_context(redirect_stdout(buf))
            try:
                cli.main()
            except SystemExit:
                pass
        output = buf.getvalue()
        self.assertIn("d3d", output)
        self.assertIn("DIII-D", output)

    def test_catalog_show_prints_yaml(self):
        from fdp import cli
        from fdp.catalog import _Catalog
        ep = self._make_mock_ep()
        mock_catalog = _Catalog()
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(sys, "argv", ["fdp", "catalog", "show", "d3d"]))
            stack.enter_context(mock.patch.object(cli, "setup_environment"))
            stack.enter_context(mock.patch("fdp.catalog.entry_points", return_value=[ep]))
            stack.enter_context(mock.patch.object(cli, "catalog", mock_catalog))
            buf = io.StringIO()
            stack.enter_context(redirect_stdout(buf))
            try:
                cli.main()
            except SystemExit:
                pass
        output = buf.getvalue()
        self.assertIn("mds_tree", output)
        self.assertIn("ptdata_indexed", output)


class TestCliChat(unittest.TestCase):
    def test_chat_calls_execvpe(self):
        from fdp import cli
        with ExitStack() as stack:
            _patch_catalog(stack)
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
            _patch_catalog(stack)
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
            _patch_catalog(stack)
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
