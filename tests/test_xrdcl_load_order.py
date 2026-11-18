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

    def _setup(self, started: bool):
        with mock.patch("fdp.environment._xrdcl_started",
                        return_value=started):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                setup_environment(device="d3d", bearer_token="tok")
        return [w for w in caught if "fdp run" in str(w.message)]

    def test_warns_when_token_and_plugin_dir_arrive_late(self):
        hits = self._setup(started=True)
        self.assertEqual(len(hits), 1)
        msg = str(hits[0].message)
        self.assertIn("BEARER_TOKEN", msg)
        self.assertIn("XRD_PLUGINCONFDIR", msg)
        self.assertNotIn("tok", msg.replace("token", ""))

    def test_no_warning_before_xrdcl_starts(self):
        self.assertEqual(self._setup(started=False), [])

    def test_no_warning_when_values_were_already_in_place(self):
        os.environ["BEARER_TOKEN"] = "tok"
        os.environ["XRD_PLUGINCONFDIR"] = "/env/etc/xrootd/client.plugins.d"
        self.assertEqual(self._setup(started=True), [])


class TestXrdClStartedDetection(unittest.TestCase):
    def test_loaded_libxrdcl_counts_as_started(self):
        code = ("import ctypes, ctypes.util, sys; from fdp.environment import "
                "_xrdcl_started as s; a = s(); "
                "ctypes.CDLL(ctypes.util.find_library('XrdCl')); print(a, s())")
        self.assertEqual(_run(code), "False True")


if __name__ == "__main__":
    unittest.main()
