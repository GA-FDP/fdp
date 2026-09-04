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
from types import SimpleNamespace
from unittest import mock


# Minimal catalog YAML for a fake d3d test tokamak. origin_server is set so
# that capability-scoped commands (`fdp ls`) have a device to resolve to --
# the real d3d.yaml always sets it. The mds_tree locator declares bearer_token
# auth for the same reason: `fdp login`/`fdp logout` are capability-scoped to
# bearer-auth devices, and the real d3d.yaml always sets this too (mirrors
# tests/test_environment.py::_D3D_TEST_YAML and
# tests/test_capabilities.py::_D3D_YAML).
_D3D_TEST_YAML = """\
schema_version: 1
name: d3d
description: DIII-D tokamak (test)
origin_server: root://d3d-origin.example.org:8443
locators:
  - kind: mds_tree
    name: main
    transport: pelican
    search_path: [pelican://test/fdp-d3d/mds/~t]
    auth: { kind: bearer_token, env: BEARER_TOKEN }
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


# Minimal catalog YAML for a fake mast test tokamak: a public, no-XRootD,
# no-token device (zarr_store + http_catalog, auth kind none). Mirrors
# tests/test_environment.py::_MAST_TEST_YAML.
_MAST_TEST_YAML = """\
schema_version: 1
name: mast
description: MAST tokamak (test)
locators:
  - kind: zarr_store
    name: main
    protocol: s3
    base_url: s3://mast/level2/shots
    endpoint: https://s3.echo.stfc.ac.uk
    auth: { kind: none }
  - kind: http_catalog
    name: metadata
    base_url: https://mastapp.site
    shots_path: parquet/level2/shots
    signals_path: parquet/level2/signals
    auth: { kind: none }
"""


def _make_catalog_ep(name: str, yaml_text: str):
    src = mock.MagicMock()
    src.read_text.return_value = yaml_text
    ep = mock.MagicMock()
    ep.name = name
    ep.load.return_value = src
    return ep


def _patch_entry_points(stack, eps):
    """Patch the catalog entry points to *eps* and reset the cache."""
    from fdp.catalog import catalog as _cat
    stack.enter_context(mock.patch("fdp.catalog.entry_points",
                                    return_value=eps))
    _cat._cache = None
    stack.callback(lambda: setattr(_cat, "_cache", None))


def _patch_catalog(stack, yaml_text: str = _D3D_TEST_YAML,
                   name: str = "d3d"):
    """Patch the catalog to a single fake device."""
    _patch_entry_points(stack, [_make_catalog_ep(name, yaml_text)])


def _patch_empty_catalog(stack):
    """Patch the catalog to no registered devices at all -- fdp's own dev
    env, where no contributor package is installed."""
    _patch_entry_points(stack, [])


def _run_cli(argv, yaml_text: str = _D3D_TEST_YAML, name: str = "d3d"):
    """Invoke fdp.cli.main with mocks; return (stdout, exit_code)."""
    from fdp import cli
    buf = io.StringIO()
    exit_code = None
    with ExitStack() as stack:
        _patch_catalog(stack, yaml_text, name)
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

    def test_env_mast_clean_device_has_no_gated_exports(self):
        """A public, no-XRootD/no-token device prints only the universal +
        MAST_* exports — no XRootD/PTData/MDS/TDSVER/BEARER_TOKEN lines."""
        out, _ = _run_cli(["fdp", "env"], _MAST_TEST_YAML, name="mast")
        lines = out.splitlines()
        forbidden = ("export XRD", "export PTDATA", "export TDSVER",
                     "export BEARER_TOKEN", "export MDS_PATH",
                     "export default_tree_path")
        for line in lines:
            for prefix in forbidden:
                self.assertFalse(
                    line.startswith(prefix),
                    f"unexpected gated export in clean device env: {line!r}",
                )
        self.assertTrue(
            any(line.startswith("export MKL_NUM_THREADS=") for line in lines),
            f"missing universal MKL export: {out!r}",
        )
        self.assertTrue(
            any(line.startswith("export MAST_ZARR_BASE_URL=")
                for line in lines),
            f"missing MAST_ZARR_BASE_URL export: {out!r}",
        )

    def test_env_d3d_device_emits_gated_exports(self):
        """The d3d-like device DOES print TDSVER + XRootD exports, pinning
        the CLI path's locator-gating in both directions."""
        out, _ = _run_cli(["fdp", "env"])
        lines = out.splitlines()
        self.assertTrue(
            any(line.startswith("export TDSVER=") for line in lines),
            f"missing TDSVER export for d3d: {out!r}",
        )
        self.assertTrue(
            any(line.startswith("export XRDCP_ALLOW_HTTP=")
                for line in lines),
            f"missing XRootD export for d3d: {out!r}",
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


class TestCliLoginLogout(unittest.TestCase):
    def test_login_dispatches_to_auth_login(self):
        from fdp import cli, auth
        ct = auth.CachedToken(device="d3d", scope="read", exp=None)
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "login"]))
            login_mock = stack.enter_context(
                mock.patch.object(cli.auth, "login", return_value=ct))
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main()
            login_mock.assert_called_once()
            self.assertEqual(
                login_mock.call_args.args[0].schema.name, "d3d")
            self.assertEqual(login_mock.call_args.kwargs.get("write"), False)

    def test_login_write_flag(self):
        from fdp import cli, auth
        ct = auth.CachedToken(device="d3d", scope="write", exp=None)
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "login", "--write"]))
            login_mock = stack.enter_context(
                mock.patch.object(cli.auth, "login", return_value=ct))
            with redirect_stdout(io.StringIO()):
                cli.main()
            self.assertEqual(login_mock.call_args.kwargs.get("write"), True)

    def test_logout_dispatches(self):
        from fdp import cli
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "logout"]))
            logout_mock = stack.enter_context(
                mock.patch.object(cli.auth, "logout", return_value=True))
            with redirect_stdout(io.StringIO()):
                cli.main()
            logout_mock.assert_called_once()

    def test_run_sets_auto_login_true(self):
        from fdp import cli
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "run", "true"]))
            setup_mock = stack.enter_context(
                mock.patch.object(cli, "setup_environment"))
            stack.enter_context(mock.patch.object(
                cli.subprocess, "run",
                return_value=SimpleNamespace(returncode=0)))
            with self.assertRaises(SystemExit):
                cli.main()
            self.assertEqual(
                setup_mock.call_args.kwargs.get("auto_login"), True)


class TestDeviceFlagPlacement(unittest.TestCase):
    """-D must work on either side of the subcommand, and must not leak into
    the command `fdp run` executes."""

    def test_flag_before_subcommand(self):
        from fdp.cli import build_parser
        args = build_parser().parse_args(["-D", "d3d", "env"])
        self.assertEqual(args.device, "d3d")

    def test_flag_after_subcommand(self):
        from fdp.cli import build_parser
        args = build_parser().parse_args(["env", "-D", "d3d"])
        self.assertEqual(args.device, "d3d")

    def test_equals_and_attached_forms(self):
        # `--device=d3d` and `-Dd3d` must resolve identically to the spaced
        # form; locks these against a future custom argparse action.
        from fdp.cli import build_parser
        self.assertEqual(
            build_parser().parse_args(["env", "--device=d3d"]).device, "d3d")
        self.assertEqual(
            build_parser().parse_args(["-Dd3d", "env"]).device, "d3d")

    def test_subcommand_flag_wins_over_toplevel(self):
        from fdp.cli import build_parser
        args = build_parser().parse_args(["-D", "mast", "env", "-D", "d3d"])
        self.assertEqual(args.device, "d3d")

    def test_absent_flag_is_none(self):
        from fdp.cli import build_parser
        self.assertIsNone(build_parser().parse_args(["env"]).device)

    def test_run_flag_after_subcommand_not_passed_to_child(self):
        from fdp.cli import build_parser
        args = build_parser().parse_args(
            ["run", "-D", "d3d", "echo", "hi"])
        self.assertEqual(args.device, "d3d")
        self.assertEqual(args.command_args, ["echo", "hi"])

    def test_run_child_args_keep_their_own_flags(self):
        # A -D belonging to the child command must survive untouched.
        from fdp.cli import build_parser
        args = build_parser().parse_args(
            ["-D", "d3d", "run", "mytool", "-D", "childvalue"])
        self.assertEqual(args.device, "d3d")
        self.assertEqual(args.command_args, ["mytool", "-D", "childvalue"])

    def test_deprecated_alias_still_works(self):
        from fdp.cli import build_parser
        args = build_parser().parse_args(["--default-device", "d3d", "env"])
        self.assertEqual(args.device, "d3d")


class TestChatQueryEnvironment(unittest.TestCase):
    """chat/query set up the FDP environment when a device is available,
    and degrade to a warning when none is (fdp's own dev env)."""

    def test_chat_sets_up_environment(self):
        from fdp import cli
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "chat"]))
            setup_mock = stack.enter_context(
                mock.patch.object(cli, "setup_environment"))
            ev = stack.enter_context(
                mock.patch.object(cli.os, "execvpe"))
            with redirect_stdout(io.StringIO()):
                cli.main()
        setup_mock.assert_called_once()
        self.assertEqual(setup_mock.call_args.kwargs.get("auto_login"), True)
        ev.assert_called_once()

    def test_query_sets_up_environment(self):
        from fdp import cli
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "query", "hello"]))
            setup_mock = stack.enter_context(
                mock.patch.object(cli, "setup_environment"))
            ev = stack.enter_context(
                mock.patch.object(cli.os, "execvpe"))
            with redirect_stdout(io.StringIO()):
                cli.main()
        setup_mock.assert_called_once()
        self.assertEqual(setup_mock.call_args.kwargs.get("auto_login"), True)
        ev.assert_called_once()

    def test_chat_survives_missing_device(self):
        """The fe72baa case: no contributor installed. Warn, then exec.

        Runs the *real* setup_environment against an empty catalog, so the
        NoDevicesError raise site in devices.py is what is under test. Faking
        it with a side_effect on a mocked setup_environment would pin nothing
        about the type actually raised there -- and the type is the whole
        point, since the message is identical either way.

        Hermetic despite touching the real code path: active_handles() raises
        out of _registered_names() before explicit_device_name() consults
        $FDP_DEFAULT_DEVICE or ~/.fdp/config.toml, and before
        apply_environment() writes anything. The os.environ assertion below
        holds that second half in place.
        """
        from fdp import cli
        buf = io.StringIO()
        before = dict(os.environ)
        with ExitStack() as stack:
            _patch_empty_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "chat"]))
            ev = stack.enter_context(
                mock.patch.object(cli.os, "execvpe"))
            stack.enter_context(mock.patch.object(sys, "stderr", buf))
            with redirect_stdout(io.StringIO()):
                cli.main()
        ev.assert_called_once()
        self.assertIn("Warning", buf.getvalue())
        self.assertIn("No tokamak contributors", buf.getvalue())
        self.assertEqual(dict(os.environ), before)

    def test_chat_survives_auth_error(self):
        from fdp import cli, auth
        buf = io.StringIO()
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "chat"]))
            stack.enter_context(mock.patch.object(
                cli, "setup_environment",
                side_effect=auth.AuthError("token acquisition failed")))
            ev = stack.enter_context(
                mock.patch.object(cli.os, "execvpe"))
            stack.enter_context(mock.patch.object(sys, "stderr", buf))
            with redirect_stdout(io.StringIO()):
                cli.main()
        ev.assert_called_once()
        self.assertIn("token acquisition failed", buf.getvalue())

    def test_strict_subcommand_still_exits_on_missing_device(self):
        """Regression guard: the new branch must not soften `fdp ls`."""
        from fdp import cli
        buf = io.StringIO()
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "ls", "/"]))
            stack.enter_context(mock.patch.object(
                cli, "setup_environment",
                side_effect=ValueError("no device contributors installed")))
            stack.enter_context(mock.patch.object(sys, "stderr", buf))
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    cli.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Error:", buf.getvalue())
        self.assertNotIn("Warning", buf.getvalue())

    def test_chat_exits_on_unknown_device(self):
        """A mistyped --device is not a "no device available here" failure:
        it is a typo, and the useful answer is a clean error rather than a
        chat session that silently cannot fetch anything. Runs the real
        setup_environment so the raise site in devices.py is what is under
        test. Spelled with the flag before the subcommand; the sibling
        test_chat_device_after_subcommand covers the other position."""
        from fdp import cli
        buf = io.StringIO()
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "--device", "nosuch", "chat"]))
            ev = stack.enter_context(
                mock.patch.object(cli.os, "execvpe"))
            stack.enter_context(mock.patch.object(sys, "stderr", buf))
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    cli.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("Unknown device", buf.getvalue())
        self.assertNotIn("Warning", buf.getvalue())
        ev.assert_not_called()

    def test_chat_device_after_subcommand(self):
        """`fdp chat -D d3d` must work, not just `fdp -D d3d chat`.

        chat/query consume a device now, and an unknown one is a hard exit
        (test_chat_exits_on_unknown_device), so the flag is load-bearing
        here. README teaches the post-subcommand spelling for every other
        subcommand; _add_device_arg's SUPPRESS default is what makes both
        positions work."""
        from fdp import cli
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "chat", "--device", "d3d"]))
            setup_mock = stack.enter_context(
                mock.patch.object(cli, "setup_environment"))
            ev = stack.enter_context(
                mock.patch.object(cli.os, "execvpe"))
            with redirect_stdout(io.StringIO()):
                cli.main()
        self.assertEqual(setup_mock.call_args.kwargs.get("device"), "d3d")
        ev.assert_called_once()

    def test_chat_device_before_subcommand_still_works(self):
        """Regression guard: the top-level spelling is the one that worked
        before chat declared its own --device, so it must keep working."""
        from fdp import cli
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "--device", "d3d", "chat"]))
            setup_mock = stack.enter_context(
                mock.patch.object(cli, "setup_environment"))
            ev = stack.enter_context(
                mock.patch.object(cli.os, "execvpe"))
            with redirect_stdout(io.StringIO()):
                cli.main()
        self.assertEqual(setup_mock.call_args.kwargs.get("device"), "d3d")
        ev.assert_called_once()

    def test_query_device_after_subcommand_keeps_the_query(self):
        """`fdp query -D d3d "hi"` resolves the device *and* still delivers
        the query. `query`'s positional is a plain one rather than
        REMAINDER, so a preceding optional cannot swallow it -- assert that
        rather than trust it, since `run` needed a specific ordering."""
        from fdp import cli
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "query", "--device", "d3d", "hi"]))
            setup_mock = stack.enter_context(
                mock.patch.object(cli, "setup_environment"))
            ev = stack.enter_context(
                mock.patch.object(cli.os, "execvpe"))
            with redirect_stdout(io.StringIO()):
                cli.main()
        self.assertEqual(setup_mock.call_args.kwargs.get("device"), "d3d")
        ev.assert_called_once()
        argv = ev.call_args.args[1]
        self.assertIn("hi", argv)
        self.assertNotIn("--device", argv)

    def test_chat_exits_on_device_env_conflict(self):
        """DeviceEnvConflict subclasses ValueError but is a genuine
        multi-device disagreement, not an absent device -- it must stay
        hard for chat too.

        This injects the exception, so it pins the CLI's type dispatch only.
        The end-to-end version -- two really-conflicting devices composed by
        the real setup_environment -- lives in test_composition.py, next to
        the fixtures that produce the conflict (it needs that module's temp
        $HOME, since with two devices resolution does reach
        ~/.fdp/config.toml)."""
        from fdp import cli
        from fdp.environment import DeviceEnvConflict
        buf = io.StringIO()
        with ExitStack() as stack:
            _patch_catalog(stack)
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "chat"]))
            stack.enter_context(mock.patch.object(
                cli, "setup_environment",
                side_effect=DeviceEnvConflict("devices disagree on FOO")))
            ev = stack.enter_context(
                mock.patch.object(cli.os, "execvpe"))
            stack.enter_context(mock.patch.object(sys, "stderr", buf))
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    cli.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("devices disagree on FOO", buf.getvalue())
        self.assertNotIn("Warning", buf.getvalue())
        ev.assert_not_called()


if __name__ == "__main__":
    unittest.main()
