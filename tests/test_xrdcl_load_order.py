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

"""In-process setup_environment() vs. XRootD client start-up (GA-FDP/fdp#15).

Importing XRootD.client starts XrdCl: it reads XRD_PLUGINCONFDIR, loads the
Pelican plugin, and the plugin copies BEARER_TOKEN. Values assigned to
os.environ afterwards never reach it, so `import fdp` must not import XRootD,
and setup_environment() must say so when it is already too late.
"""

import os
import subprocess
import sys
import unittest
import warnings
from unittest import mock

from test_environment import _D3D_TEST_YAML, _make_catalog_ep

from fdp.environment import setup_environment


def _run(code: str) -> str:
    out = subprocess.run([sys.executable, "-c", code], check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


class TestImportDoesNotStartXrdCl(unittest.TestCase):
    def test_import_fdp_leaves_xrootd_unloaded(self):
        self.assertEqual(
            _run("import fdp, sys; print('pyxrootd.client' in sys.modules)"),
            "False")

    def test_fdpfilesystem_still_exported(self):
        self.assertEqual(
            _run("import fdp; print(fdp.FdpFileSystem.__name__)"),
            "FdpFileSystem")

    def test_from_import_fdpfilesystem(self):
        self.assertEqual(
            _run("from fdp import FdpFileSystem; print(FdpFileSystem.__name__)"),
            "FdpFileSystem")


class TestWarnsWhenXrdClAlreadyStarted(unittest.TestCase):
    _KEYS = ("BEARER_TOKEN", "XRD_PLUGINCONFDIR", "FDP_DEFAULT_DEVICE")

    def setUp(self):
        self._saved = {k: os.environ.pop(k, None) for k in self._KEYS}
        self._env_patch = mock.patch.dict(os.environ)
        self._env_patch.start()
        self._cat_patch = mock.patch(
            "fdp.catalog.entry_points",
            return_value=[_make_catalog_ep("d3d", _D3D_TEST_YAML)])
        self._cat_patch.start()
        from fdp.catalog import catalog as _cat
        _cat._cache = None
        self._pluginconfdir = mock.patch(
            "fdp.environment._get_default_xrd_pluginconfdir",
            return_value="/env/etc/xrootd/client.plugins.d")
        self._pluginconfdir.start()

    def tearDown(self):
        self._pluginconfdir.stop()
        self._cat_patch.stop()
        from fdp.catalog import catalog as _cat
        _cat._cache = None
        self._env_patch.stop()
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v

    def _setup(self, started):
        # started: bool, or [at entry, after the catalog loads]
        seq = started if isinstance(started, list) else [started, started]
        with mock.patch("fdp.environment._xrdcl_started", side_effect=seq):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                setup_environment(device="d3d", bearer_token="s3cr3t-tok")
        return [w for w in caught if "fdp run" in str(w.message)]

    def test_warns_when_token_and_plugin_dir_arrive_late(self):
        hits = self._setup(started=True)
        self.assertEqual(len(hits), 1)
        msg = str(hits[0].message)
        self.assertIn("BEARER_TOKEN", msg)
        self.assertIn("XRD_PLUGINCONFDIR", msg)
        self.assertNotIn("s3cr3t", msg)

    def test_warns_when_catalog_load_sets_the_token(self):
        # A contributor's import (toksearch_d3d) may export the token while
        # the catalog loads; that is still after XrdCl started.
        from fdp import devices
        real = devices.active_handles

        def loading_sets_token(device=None):
            os.environ["BEARER_TOKEN"] = "s3cr3t-tok"
            return real(device)

        with mock.patch("fdp.environment.active_handles", loading_sets_token):
            hits = self._setup(started=True)
        self.assertEqual(len(hits), 1)
        self.assertIn("BEARER_TOKEN", str(hits[0].message))

    def test_no_warning_when_catalog_load_sets_token_then_starts_xrdcl(self):
        from fdp import devices
        real = devices.active_handles

        def loading_sets_env(device=None):
            os.environ["BEARER_TOKEN"] = "s3cr3t-tok"
            os.environ["XRD_PLUGINCONFDIR"] = "/env/etc/xrootd/client.plugins.d"
            return real(device)

        with mock.patch("fdp.environment.active_handles", loading_sets_env):
            self.assertEqual(self._setup(started=[False, True]), [])

    def test_warns_when_catalog_load_starts_xrdcl_without_token(self):
        hits = self._setup(started=[False, True])
        self.assertEqual(len(hits), 1)
        self.assertIn("BEARER_TOKEN", str(hits[0].message))

    def test_no_warning_before_xrdcl_starts(self):
        self.assertEqual(self._setup(started=False), [])

    def test_no_warning_when_values_were_already_in_place(self):
        os.environ["BEARER_TOKEN"] = "s3cr3t-tok"
        os.environ["XRD_PLUGINCONFDIR"] = "/env/etc/xrootd/client.plugins.d"
        self.assertEqual(self._setup(started=True), [])


class TestCliSilencesWarning(unittest.TestCase):
    """The CLI hands the env to a child (fdp run) or re-execs, so XrdCl in
    the CLI process itself starting early is not the user's problem."""

    def test_fdp_env_does_not_surface_the_warning(self):
        import io
        from contextlib import redirect_stdout
        from fdp import cli
        from fdp.environment import XrdClStartedWarning

        def noisy_setup(**kwargs):
            warnings.warn("late", XrdClStartedWarning)

        with mock.patch("fdp.catalog.entry_points",
                        return_value=[_make_catalog_ep("d3d", _D3D_TEST_YAML)]), \
             mock.patch.object(cli, "setup_environment", noisy_setup), \
             mock.patch.object(sys, "argv", ["fdp", "env"]), \
             redirect_stdout(io.StringIO()), \
             warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            from fdp.catalog import catalog as _cat
            _cat._cache = None
            try:
                cli.main()
            except SystemExit:
                pass
            _cat._cache = None
        self.assertEqual(
            [w for w in caught if w.category is XrdClStartedWarning], [])


class TestXrdClStartedDetection(unittest.TestCase):
    def test_loaded_libxrdcl_counts_as_started(self):
        code = ("import ctypes, ctypes.util, sys; from fdp.environment import "
                "_xrdcl_started as s; a = s(); "
                "ctypes.CDLL(ctypes.util.find_library('XrdCl')); print(a, s())")
        self.assertEqual(_run(code), "False True")


if __name__ == "__main__":
    unittest.main()
