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

    def tearDown(self):
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
        # Fallback device (d3d) contributes default_tree_path
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


if __name__ == "__main__":
    unittest.main()
