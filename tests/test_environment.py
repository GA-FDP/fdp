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

"""Tests for fdp.environment.setup_environment."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fdp.devices import Device, clear_device_cache
from fdp.environment import (
    apply_environment,
    setup_environment,
    _generic_config,
)


def _fake_ep(name, value):
    ep = mock.MagicMock()
    ep.name = name
    ep.load.return_value = value
    return ep


_D3D_TEST_DEVICE = Device(
    name="d3d",
    pelican_root="pelican://test/fdp-d3d",
    origin_server="root://test:8443",
    mds_default_tree_path="pelican://test/fdp-d3d/mds/~t",
    ptdata_index_dir="pelican://test/fdp-d3d/idx",
    description="test d3d (no fallback in fdp anymore)",
)


class TestApplyEnvironment(unittest.TestCase):
    def test_setdefault_semantics_for_most_keys(self):
        env = {"FOO": "preserved"}
        apply_environment({"FOO": "new", "BAR": "added", "PATH": "p"}, env)
        self.assertEqual(env["FOO"], "preserved")
        self.assertEqual(env["BAR"], "added")

    def test_path_is_overwritten(self):
        env = {"PATH": "old"}
        apply_environment({"PATH": "new"}, env)
        self.assertEqual(env["PATH"], "new")


class TestSetupEnvironment(unittest.TestCase):
    def setUp(self):
        # Save / clear FDP-related env vars so tests are isolated.
        self._saved = {}
        for k in ("BEARER_TOKEN", "XRDCP_ALLOW_HTTP", "default_tree_path",
                  "PTDATA_JSON_INDEX_DIR", "FDP_DEFAULT_DEVICE",
                  "TDSVER", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
                  "OMP_NUM_THREADS", "XRD_PELICANUSEAUTHHEADERS",
                  "XRD_CURLDISABLEPREFETCH", "XRD_PLUGINCONFDIR",
                  "X509_CERT_FILE", "MDS_PATH", "PTDATA_LOC",
                  "PTDATA_LIBRARY", "PTDATA_PLUGIN_LIB"):
            self._saved[k] = os.environ.pop(k, None)
        # Provide a fake 'd3d' contributor since fdp no longer ships a
        # fallback. resolve_default_device() will pick it as the sole
        # registered device.
        clear_device_cache()
        self._ep_patch = mock.patch(
            "fdp.devices._entry_points",
            return_value=[_fake_ep("d3d", _D3D_TEST_DEVICE)],
        )
        self._ep_patch.start()

    def tearDown(self):
        self._ep_patch.stop()
        clear_device_cache()
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_generic_keys_applied(self):
        setup_environment(bearer_token="dummy")
        self.assertEqual(os.environ.get("XRDCP_ALLOW_HTTP"), "true")
        self.assertEqual(os.environ.get("MKL_NUM_THREADS"), "1")
        self.assertEqual(os.environ.get("TDSVER"), "7.0")

    def test_device_keys_applied(self):
        setup_environment(bearer_token="dummy")
        self.assertIn("default_tree_path", os.environ)
        self.assertIn("fdp-d3d", os.environ["default_tree_path"])

    def test_explicit_overrides_win(self):
        setup_environment(
            bearer_token="dummy",
            TDSVER="9.9",
        )
        self.assertEqual(os.environ["TDSVER"], "9.9")

    def test_bearer_token_arg_wins(self):
        setup_environment(bearer_token="explicit-token")
        self.assertEqual(os.environ["BEARER_TOKEN"], "explicit-token")

    def test_bearer_token_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".fdp").mkdir()
            (home / ".fdp" / "token").write_text("file-token\n")
            with mock.patch.object(Path, "home", return_value=home):
                setup_environment()
            self.assertEqual(os.environ["BEARER_TOKEN"], "file-token")

    def test_device_by_name(self):
        setup_environment(device="d3d", bearer_token="dummy")
        self.assertIn("fdp-d3d", os.environ["default_tree_path"])

    def test_no_devices_installed_raises(self):
        # With no contributors in either the legacy device system or the
        # catalog, setup_environment can't pick a default and should raise.
        self._ep_patch.stop()
        clear_device_cache()
        try:
            # Patch both the legacy device entry points and the catalog
            # entry points so neither has any contributors.
            with mock.patch("fdp.devices._entry_points", return_value=[]), \
                 mock.patch("fdp.catalog.entry_points", return_value=[]):
                # Reset catalog cache so the empty patch takes effect.
                from fdp.catalog import catalog as _cat
                _cat._cache = None
                with self.assertRaises(ValueError):
                    setup_environment(bearer_token="dummy")
        finally:
            # Re-start the patch so tearDown's stop() matches a start().
            self._ep_patch = mock.patch(
                "fdp.devices._entry_points",
                return_value=[_fake_ep("d3d", _D3D_TEST_DEVICE)],
            )
            self._ep_patch.start()
            # Reset the catalog cache so subsequent tests see real data.
            from fdp.catalog import catalog as _cat
            _cat._cache = None


if __name__ == "__main__":
    unittest.main()
