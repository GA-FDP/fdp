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

"""Tests for fdp.llm_shims."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock


# Minimal YAML for a fake handle.
_FAKE_YAML = """\
schema_version: 1
name: d3d
description: test
locators: []
"""


def _make_handle():
    from fdp.catalog import _Catalog, TokamakHandle
    src = mock.MagicMock()
    src.read_text.return_value = _FAKE_YAML
    ep = mock.MagicMock()
    ep.name = "d3d"
    ep.load.return_value = src
    with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
        cat = _Catalog()
        return cat["d3d"]


class TestBuildLlmCmd(unittest.TestCase):
    def test_basic_cmd_structure(self):
        from fdp.llm_shims import _build_llm_cmd
        handle = _make_handle()
        cmd = _build_llm_cmd("query", ["hello"], handle)
        self.assertEqual(cmd[:4],
                         [sys.executable, "-m", "toksearch.llm.cli",
                          "query"])
        self.assertIn("hello", cmd)

    def test_none_handle_works(self):
        """handle=None occurs when fdp runs without a tokamak contributor."""
        from fdp.llm_shims import _build_llm_cmd
        cmd = _build_llm_cmd("chat", ["--gui"], None)
        self.assertEqual(cmd[:4],
                         [sys.executable, "-m", "toksearch.llm.cli",
                          "chat"])
        self.assertIn("--gui", cmd)

    def test_explicit_backend_forwarded(self):
        from fdp.llm_shims import _build_llm_cmd
        handle = _make_handle()
        cmd = _build_llm_cmd("query",
                              ["--backend", "anthropic", "hi"],
                              handle)
        self.assertEqual(cmd[cmd.index("--backend") + 1], "anthropic")


class TestDoChat(unittest.TestCase):
    def test_execvpe_called(self):
        from fdp.llm_shims import do_chat
        handle = _make_handle()
        args = SimpleNamespace(backend=None, model=None,
                                max_iterations=None)
        with mock.patch("fdp.llm_shims.os.execvpe") as ev:
            do_chat(args, handle)
        ev.assert_called_once()
        _, argv, env = ev.call_args.args
        self.assertEqual(argv[:4],
                         [sys.executable, "-m", "toksearch.llm.cli",
                          "chat"])
        self.assertIs(env, os.environ)

    def test_max_iterations_forwarded(self):
        from fdp.llm_shims import do_chat
        handle = _make_handle()
        args = SimpleNamespace(backend=None, model=None, max_iterations=5)
        with mock.patch("fdp.llm_shims.os.execvpe") as ev:
            do_chat(args, handle)
        argv = ev.call_args.args[1]
        self.assertEqual(argv[argv.index("-n") + 1], "5")


class TestDoQuery(unittest.TestCase):
    def test_query_passed_through(self):
        from fdp.llm_shims import do_query
        handle = _make_handle()
        args = SimpleNamespace(query="my prompt",
                                backend=None, model=None,
                                max_iterations=None)
        with mock.patch("fdp.llm_shims.os.execvpe") as ev:
            do_query(args, handle)
        argv = ev.call_args.args[1]
        self.assertIn("my prompt", argv)
        self.assertEqual(argv[3], "query")


class TestLogoEnvInjection(unittest.TestCase):
    def test_gui_sets_fdp_gui_logo_path_when_logo_available(self):
        from fdp import llm_shims as shims
        handle = _make_handle()
        args = SimpleNamespace(backend=None, model=None,
                                max_iterations=None,
                                gui=True, open_browser=True)
        import fdp as _fdp
        with mock.patch.object(_fdp, "main_logo_path",
                                return_value="/path/to/logo.png"), \
                mock.patch.dict("os.environ",
                                 {k: v for k, v in __import__("os").environ.items()
                                  if k != "FDP_GUI_LOGO_PATH"},
                                 clear=True), \
                mock.patch("fdp.llm_shims.os.execvpe") as ev:
            shims.do_chat(args, handle)
        _, _, env = ev.call_args.args
        self.assertEqual(env.get("FDP_GUI_LOGO_PATH"),
                         "/path/to/logo.png")

    def test_no_gui_keeps_os_environ_unchanged(self):
        # Without --gui, env passes through as os.environ identity.
        from fdp import llm_shims as shims
        import os as _os
        handle = _make_handle()
        args = SimpleNamespace(backend=None, model=None,
                                max_iterations=None,
                                gui=False, open_browser=True)
        with mock.patch("fdp.llm_shims.os.execvpe") as ev:
            shims.do_chat(args, handle)
        _, _, env = ev.call_args.args
        self.assertIs(env, _os.environ)


class TestGuiPassthrough(unittest.TestCase):
    def test_gui_flag_forwarded(self):
        from fdp.llm_shims import _common_passthrough
        args = SimpleNamespace(backend=None, model=None,
                                max_iterations=None,
                                gui=True, open_browser=True)
        out = _common_passthrough(args)
        self.assertIn("--gui", out)
        self.assertNotIn("--no-browser", out)

    def test_no_browser_forwarded_only_with_gui(self):
        from fdp.llm_shims import _common_passthrough
        args = SimpleNamespace(backend=None, model=None,
                                max_iterations=None,
                                gui=True, open_browser=False)
        out = _common_passthrough(args)
        self.assertIn("--gui", out)
        self.assertIn("--no-browser", out)

    def test_no_browser_alone_is_dropped(self):
        from fdp.llm_shims import _common_passthrough
        # --no-browser without --gui has no meaning in the underlying CLI
        args = SimpleNamespace(backend=None, model=None,
                                max_iterations=None,
                                gui=False, open_browser=False)
        out = _common_passthrough(args)
        self.assertNotIn("--gui", out)
        self.assertNotIn("--no-browser", out)


if __name__ == "__main__":
    unittest.main()
