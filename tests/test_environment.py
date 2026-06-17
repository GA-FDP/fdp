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

from fdp.environment import (
    apply_environment,
    setup_environment,
    _generic_config,
)


# Minimal catalog YAML for a fake d3d test tokamak.
_D3D_TEST_YAML = """\
schema_version: 1
name: d3d
description: test d3d
locators:
  - kind: mds_tree
    name: main
    transport: pelican
    search_path: [pelican://test/fdp-d3d/mds/~t]
    auth: { kind: bearer_token, env: BEARER_TOKEN }
  - kind: ptdata_indexed
    name: main
    transport: pelican
    index_dir: pelican://test/fdp-d3d/idx
    auth: { kind: bearer_token, env: BEARER_TOKEN }
  - kind: sql
    name: d3drdb
    driver: mssql
    host: d3drdb.gat.com
    database: d3drdb
    tdsver: "7.0"
"""


def _make_catalog_ep(name: str, yaml_text: str):
    """Create a mock entry-point that returns a Traversable-like source."""
    src = mock.MagicMock()
    src.read_text.return_value = yaml_text
    ep = mock.MagicMock()
    ep.name = name
    ep.load.return_value = src
    return ep


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
        # Patch the catalog entry points to provide a fake d3d tokamak.
        ep = _make_catalog_ep("d3d", _D3D_TEST_YAML)
        self._cat_patch = mock.patch("fdp.catalog.entry_points",
                                      return_value=[ep])
        self._cat_patch.start()
        # Reset catalog cache so the patch takes effect.
        from fdp.catalog import catalog as _cat
        _cat._cache = None

    def tearDown(self):
        self._cat_patch.stop()
        from fdp.catalog import catalog as _cat
        _cat._cache = None
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
        # With no contributors in the catalog, setup_environment should raise.
        self._cat_patch.stop()
        from fdp.catalog import catalog as _cat
        _cat._cache = None
        try:
            with mock.patch("fdp.catalog.entry_points", return_value=[]):
                _cat._cache = None
                with self.assertRaises(ValueError):
                    setup_environment(bearer_token="dummy")
        finally:
            # Re-start the patch so tearDown's stop() matches a start().
            ep = _make_catalog_ep("d3d", _D3D_TEST_YAML)
            self._cat_patch = mock.patch("fdp.catalog.entry_points",
                                          return_value=[ep])
            self._cat_patch.start()
            from fdp.catalog import catalog as _cat
            _cat._cache = None


_MAST_TEST_YAML = """\
schema_version: 1
name: mast
description: test mast
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


class TestGenericConfigParity(unittest.TestCase):
    """Pin the exact set of generic env keys emitted for a full d3d-like
    device (mds_tree + ptdata_indexed + sql, all over pelican transport).
    Guards against a generic var being accidentally dropped or added."""

    _EXPECTED_GENERIC_KEYS = {
        "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "OMP_NUM_THREADS", "PATH",
        "XRDCP_ALLOW_HTTP", "XRD_PELICANUSEAUTHHEADERS",
        "XRD_CURLDISABLEPREFETCH", "XRD_PLUGINCONFDIR", "X509_CERT_FILE",
        "MDS_PATH", "PTDATA_LOC", "PTDATA_LIBRARY", "PTDATA_PLUGIN_LIB",
        "TDSVER",
    }

    def setUp(self):
        ep = _make_catalog_ep("d3d", _D3D_TEST_YAML)
        self._cat_patch = mock.patch("fdp.catalog.entry_points",
                                     return_value=[ep])
        self._cat_patch.start()
        from fdp.catalog import catalog as _cat
        _cat._cache = None

    def tearDown(self):
        self._cat_patch.stop()
        from fdp.catalog import catalog as _cat
        _cat._cache = None

    def test_generic_keys_exact(self):
        from fdp.catalog import catalog
        handle = catalog["d3d"]
        self.assertEqual(
            set(_generic_config(handle).keys()),
            self._EXPECTED_GENERIC_KEYS,
        )


class TestMastCleanEnv(unittest.TestCase):
    """A public, no-XRootD/no-token device gets a clean env."""

    _XROOTD_PTDATA_KEYS = (
        "XRDCP_ALLOW_HTTP", "XRD_PELICANUSEAUTHHEADERS",
        "XRD_CURLDISABLEPREFETCH", "XRD_PLUGINCONFDIR", "X509_CERT_FILE",
        "PTDATA_LOC", "PTDATA_LIBRARY", "PTDATA_PLUGIN_LIB",
        "MDS_PATH", "TDSVER", "default_tree_path", "PTDATA_JSON_INDEX_DIR",
    )

    def setUp(self):
        self._saved = {}
        for k in self._XROOTD_PTDATA_KEYS + (
            "BEARER_TOKEN", "FDP_DEFAULT_DEVICE",
            "MKL_NUM_THREADS", "OMP_NUM_THREADS", "NUMEXPR_NUM_THREADS",
            "MAST_ZARR_BASE_URL", "MAST_ZARR_PROTOCOL", "MAST_ZARR_ENDPOINT",
            "MAST_ZARR_FILE_NAME_FORMAT", "MAST_CATALOG_URL",
            "MAST_CATALOG_SHOTS_PATH", "MAST_CATALOG_SIGNALS_PATH",
        ):
            self._saved[k] = os.environ.pop(k, None)
        ep = _make_catalog_ep("mast", _MAST_TEST_YAML)
        self._cat_patch = mock.patch("fdp.catalog.entry_points",
                                     return_value=[ep])
        self._cat_patch.start()
        from fdp.catalog import catalog as _cat
        _cat._cache = None

    def tearDown(self):
        self._cat_patch.stop()
        from fdp.catalog import catalog as _cat
        _cat._cache = None
        for k, v in self._saved.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_no_xrootd_ptdata_mds_sql_vars(self):
        setup_environment()
        for k in self._XROOTD_PTDATA_KEYS:
            self.assertNotIn(k, os.environ, f"{k} should not be set for mast")

    def test_universal_vars_present(self):
        setup_environment()
        self.assertEqual(os.environ.get("MKL_NUM_THREADS"), "1")
        self.assertIn("PATH", os.environ)

    def test_mast_vars_emitted(self):
        setup_environment()
        self.assertEqual(os.environ["MAST_ZARR_BASE_URL"],
                         "s3://mast/level2/shots")
        self.assertEqual(os.environ["MAST_ZARR_PROTOCOL"], "s3")
        self.assertEqual(os.environ["MAST_ZARR_ENDPOINT"],
                         "https://s3.echo.stfc.ac.uk")
        self.assertEqual(os.environ["MAST_CATALOG_URL"],
                         "https://mastapp.site")
        self.assertEqual(os.environ["MAST_CATALOG_SHOTS_PATH"],
                         "parquet/level2/shots")

    def test_no_bearer_token_and_no_warning(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning -> test failure
            setup_environment()
        self.assertNotIn("BEARER_TOKEN", os.environ)


_PTDATA_PATTERN_YAML = """\
schema_version: 1
name: t
description: test ptdata pattern
locators:
  - kind: ptdata_indexed
    name: main
    transport: pelican
    index_dir: pelican://h/ns/idx
    index_pattern: "json_indexes_*"
    auth: { kind: bearer_token, env: BEARER_TOKEN }
"""


class TestPtDataIndexPatternEnv(unittest.TestCase):
    """_tokamak_env emits PTDATA_JSON_INDEX_PATTERN iff the locator has one."""

    def _env_for(self, yaml_text):
        from fdp.environment import _tokamak_env
        from fdp.catalog import catalog as _cat
        ep = _make_catalog_ep("t", yaml_text)
        with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
            _cat._cache = None
            try:
                return _tokamak_env(_cat["t"])
            finally:
                _cat._cache = None

    def test_emits_pattern_when_set(self):
        env = self._env_for(_PTDATA_PATTERN_YAML)
        self.assertEqual(env["PTDATA_JSON_INDEX_DIR"], "pelican://h/ns/idx")
        self.assertEqual(env["PTDATA_JSON_INDEX_PATTERN"], "json_indexes_*")

    def test_omits_pattern_when_unset(self):
        yaml_no_pattern = _PTDATA_PATTERN_YAML.replace(
            '    index_pattern: "json_indexes_*"\n', "")
        env = self._env_for(yaml_no_pattern)
        self.assertTrue(env["PTDATA_JSON_INDEX_DIR"].endswith("idx"))
        self.assertNotIn("PTDATA_JSON_INDEX_PATTERN", env)


if __name__ == "__main__":
    unittest.main()
