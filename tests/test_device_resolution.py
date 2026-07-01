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

"""Unified multi-device resolution tests.

Covers the shared resolver used by `fdp env`/`fdp run` (via
``_resolve_device_handle``) and `fdp ls` (via ``_resolve_origin_server``).
Resolution order: explicit flag > FDP_DEFAULT_DEVICE > ~/.fdp/config.toml
[device].default > single-device auto-select > error.

All tests are network-free: they register fake d3d + mast devices by
patching the catalog entry points.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_D3D_TEST_YAML = """\
schema_version: 1
name: d3d
description: test d3d
origin_server: root://d3d-origin.example.org:8443
locators:
  - kind: mds_tree
    name: main
    transport: pelican
    search_path: [pelican://test/fdp-d3d/mds/~t]
    auth: { kind: bearer_token, env: BEARER_TOKEN }
"""

_MAST_TEST_YAML = """\
schema_version: 1
name: mast
description: test mast
origin_server: root://mast-origin.example.org:8443
locators:
  - kind: zarr_store
    name: main
    protocol: s3
    base_url: s3://mast/level2/shots
    endpoint: https://s3.echo.stfc.ac.uk
    auth: { kind: none }
"""


def _make_catalog_ep(name: str, yaml_text: str):
    """Create a mock entry-point that returns a Traversable-like source."""
    src = mock.MagicMock()
    src.read_text.return_value = yaml_text
    ep = mock.MagicMock()
    ep.name = name
    ep.load.return_value = src
    return ep


class TestUnifiedDeviceResolution(unittest.TestCase):
    """Two devices registered; nothing else installed. Exercises the shared
    resolver and the `fdp ls` origin resolver identically."""

    def setUp(self):
        # Isolate env vars that influence resolution.
        self._saved = {}
        for k in ("FDP_DEFAULT_DEVICE",):
            self._saved[k] = os.environ.pop(k, None)

        # Register BOTH d3d and mast contributors.
        eps = [
            _make_catalog_ep("d3d", _D3D_TEST_YAML),
            _make_catalog_ep("mast", _MAST_TEST_YAML),
        ]
        self._cat_patch = mock.patch("fdp.catalog.entry_points",
                                     return_value=eps)
        self._cat_patch.start()
        from fdp.catalog import catalog as _cat
        _cat._cache = None

        # Point ~/.fdp at an empty tmp dir by default so the real user's
        # config.toml can never leak into these tests. Individual tests
        # that want a config file write into this dir.
        self._home_td = tempfile.TemporaryDirectory()
        self._home = Path(self._home_td.name)
        (self._home / ".fdp").mkdir()
        self._home_patch = mock.patch.object(
            Path, "home", return_value=self._home)
        self._home_patch.start()

    def tearDown(self):
        self._home_patch.stop()
        self._home_td.cleanup()
        self._cat_patch.stop()
        from fdp.catalog import catalog as _cat
        _cat._cache = None
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def _write_config(self, device_name):
        (self._home / ".fdp" / "config.toml").write_text(
            f'[device]\ndefault = "{device_name}"\n')

    # ------------------------------------------------------------------
    # _resolve_device_handle (fdp env / fdp run)
    # ------------------------------------------------------------------

    def test_two_devices_nothing_set_raises_with_all_three_hints(self):
        from fdp.environment import _resolve_device_handle
        with self.assertRaises(ValueError) as ctx:
            _resolve_device_handle(None)
        msg = str(ctx.exception)
        self.assertIn("--default-device", msg)
        self.assertIn("FDP_DEFAULT_DEVICE", msg)
        self.assertIn("~/.fdp/config.toml", msg)

    def test_fdp_run_multidevice_exits_cleanly_not_traceback(self):
        # `fdp run` resolves the device in main() before dispatching; a
        # multi-device ambiguity must surface as a clean Error + exit(1),
        # not an uncaught traceback.
        import io
        import contextlib
        from fdp import cli
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stderr(stderr):
            cli.main(["run", "true"])
        self.assertEqual(ctx.exception.code, 1)
        err = stderr.getvalue()
        self.assertIn("Error:", err)
        self.assertIn("--default-device", err)

    def test_env_var_selects_device(self):
        from fdp.environment import _resolve_device_handle
        os.environ["FDP_DEFAULT_DEVICE"] = "mast"
        handle = _resolve_device_handle(None)
        self.assertEqual(handle.schema.name, "mast")

    def test_config_file_selects_device(self):
        from fdp.environment import _resolve_device_handle
        self._write_config("mast")
        os.environ.pop("FDP_DEFAULT_DEVICE", None)
        handle = _resolve_device_handle(None)
        self.assertEqual(handle.schema.name, "mast")

    def test_explicit_arg_beats_env(self):
        from fdp.environment import _resolve_device_handle
        os.environ["FDP_DEFAULT_DEVICE"] = "mast"
        handle = _resolve_device_handle("d3d")
        self.assertEqual(handle.schema.name, "d3d")

    def test_env_beats_config(self):
        from fdp.environment import _resolve_device_handle
        self._write_config("d3d")
        os.environ["FDP_DEFAULT_DEVICE"] = "mast"
        handle = _resolve_device_handle(None)
        self.assertEqual(handle.schema.name, "mast")

    def test_arg_beats_config(self):
        from fdp.environment import _resolve_device_handle
        self._write_config("mast")
        os.environ.pop("FDP_DEFAULT_DEVICE", None)
        handle = _resolve_device_handle("d3d")
        self.assertEqual(handle.schema.name, "d3d")

    def test_malformed_config_is_ignored(self):
        # A broken config.toml must not crash resolution; it degrades to the
        # multi-device error.
        from fdp.environment import _resolve_device_handle
        (self._home / ".fdp" / "config.toml").write_text("not = valid = toml")
        os.environ.pop("FDP_DEFAULT_DEVICE", None)
        with self.assertRaises(ValueError):
            _resolve_device_handle(None)

    # ------------------------------------------------------------------
    # _resolve_origin_server (fdp ls) — must honor the same order
    # ------------------------------------------------------------------

    def test_ls_origin_honors_env_var(self):
        from fdp.cli import _resolve_origin_server
        os.environ["FDP_DEFAULT_DEVICE"] = "mast"
        origin = _resolve_origin_server(None)
        self.assertEqual(origin, "root://mast-origin.example.org:8443")

    def test_ls_origin_honors_config_file(self):
        from fdp.cli import _resolve_origin_server
        self._write_config("mast")
        os.environ.pop("FDP_DEFAULT_DEVICE", None)
        origin = _resolve_origin_server(None)
        self.assertEqual(origin, "root://mast-origin.example.org:8443")

    def test_ls_origin_explicit_arg(self):
        from fdp.cli import _resolve_origin_server
        os.environ["FDP_DEFAULT_DEVICE"] = "mast"
        origin = _resolve_origin_server("d3d")
        self.assertEqual(origin, "root://d3d-origin.example.org:8443")

    def test_ls_origin_two_devices_nothing_set_raises(self):
        from fdp.cli import _resolve_origin_server
        with self.assertRaises(ValueError) as ctx:
            _resolve_origin_server(None)
        msg = str(ctx.exception)
        self.assertIn("--default-device", msg)
        self.assertIn("FDP_DEFAULT_DEVICE", msg)
        self.assertIn("~/.fdp/config.toml", msg)


class TestSingleDeviceAutoSelect(unittest.TestCase):
    """With exactly one device registered, resolution still auto-selects."""

    def setUp(self):
        self._saved_env = os.environ.pop("FDP_DEFAULT_DEVICE", None)
        ep = _make_catalog_ep("d3d", _D3D_TEST_YAML)
        self._cat_patch = mock.patch("fdp.catalog.entry_points",
                                     return_value=[ep])
        self._cat_patch.start()
        from fdp.catalog import catalog as _cat
        _cat._cache = None
        # Empty ~/.fdp so no config.toml interferes.
        self._home_td = tempfile.TemporaryDirectory()
        home = Path(self._home_td.name)
        (home / ".fdp").mkdir()
        self._home_patch = mock.patch.object(Path, "home", return_value=home)
        self._home_patch.start()

    def tearDown(self):
        self._home_patch.stop()
        self._home_td.cleanup()
        self._cat_patch.stop()
        from fdp.catalog import catalog as _cat
        _cat._cache = None
        if self._saved_env is not None:
            os.environ["FDP_DEFAULT_DEVICE"] = self._saved_env

    def test_handle_auto_selects_single(self):
        from fdp.environment import _resolve_device_handle
        self.assertEqual(_resolve_device_handle(None).schema.name, "d3d")

    def test_origin_auto_selects_single(self):
        from fdp.cli import _resolve_origin_server
        self.assertEqual(_resolve_origin_server(None),
                         "root://d3d-origin.example.org:8443")


if __name__ == "__main__":
    unittest.main()
