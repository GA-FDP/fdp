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

"""Tests for fdp.devices: Device dataclass, discovery, resolution."""

import os
import unittest
from contextlib import contextmanager
from unittest import mock

from fdp.devices import (
    Device,
    discover_devices,
    get_device,
    list_devices,
    resolve_default_device,
    clear_device_cache,
    _FALLBACK_DEVICE,
)


def _fake_ep(name, value):
    ep = mock.MagicMock()
    ep.name = name
    ep.load.return_value = value
    return ep


class _Base(unittest.TestCase):
    def setUp(self):
        clear_device_cache()

    def tearDown(self):
        clear_device_cache()


class TestDevice(_Base):
    def test_to_env_includes_known_fields(self):
        d = Device(
            name="testdev",
            pelican_root="pelican://x/testdev",
            origin_server="root://x:8443",
            mds_default_tree_path="pelican://x/testdev/mds/~t",
            ptdata_index_dir="pelican://x/testdev/idx",
            extra_env={"FOO": "bar"},
        )
        env = d.to_env()
        self.assertEqual(env["default_tree_path"],
                         "pelican://x/testdev/mds/~t")
        self.assertEqual(env["PTDATA_JSON_INDEX_DIR"],
                         "pelican://x/testdev/idx")
        self.assertEqual(env["FOO"], "bar")

    def test_to_env_omits_none_optional_fields(self):
        d = Device(name="x", pelican_root="r", origin_server="o")
        env = d.to_env()
        self.assertNotIn("default_tree_path", env)
        self.assertNotIn("PTDATA_JSON_INDEX_DIR", env)

    def test_apply_sets_env_vars(self):
        saved = os.environ.pop("FDP_TEST_VAR", None)
        try:
            d = Device(name="x", pelican_root="r", origin_server="o",
                       extra_env={"FDP_TEST_VAR": "set-by-apply"})
            d.apply()
            self.assertEqual(os.environ.get("FDP_TEST_VAR"), "set-by-apply")
        finally:
            if saved is not None:
                os.environ["FDP_TEST_VAR"] = saved
            else:
                os.environ.pop("FDP_TEST_VAR", None)

    def test_activate_swaps_and_restores_runtime_keys(self):
        os.environ["default_tree_path"] = "original"
        try:
            d = Device(name="x", pelican_root="r", origin_server="o",
                       mds_default_tree_path="device-path")
            with d.activate():
                self.assertEqual(os.environ["default_tree_path"],
                                 "device-path")
            self.assertEqual(os.environ["default_tree_path"], "original")
        finally:
            os.environ.pop("default_tree_path", None)


class TestDiscovery(_Base):
    def test_no_entry_points_yields_only_fallback(self):
        with mock.patch("fdp.devices._entry_points", return_value=[]):
            devices = discover_devices()
        # The fallback is always present.
        self.assertIn("d3d", devices)
        self.assertIs(devices["d3d"], _FALLBACK_DEVICE)

    def test_entry_point_contributor_takes_precedence_over_fallback(self):
        contributor = Device(
            name="d3d",
            pelican_root="pelican://contrib/fdp-d3d",
            origin_server="root://contrib:8443",
            description="contributor-supplied D3D",
        )
        with mock.patch(
            "fdp.devices._entry_points",
            return_value=[_fake_ep("d3d", contributor)],
        ):
            devices = discover_devices()
        self.assertIs(devices["d3d"], contributor)

    def test_multiple_contributors_listed(self):
        a = Device(name="d3d", pelican_root="r1", origin_server="o1")
        b = Device(name="mast", pelican_root="r2", origin_server="o2")
        with mock.patch(
            "fdp.devices._entry_points",
            return_value=[_fake_ep("d3d", a), _fake_ep("mast", b)],
        ):
            devices = discover_devices()
        self.assertEqual(set(devices), {"d3d", "mast"})


class TestResolution(_Base):
    def test_single_device_auto_selected(self):
        with mock.patch("fdp.devices._entry_points", return_value=[]):
            d = resolve_default_device()
        self.assertEqual(d.name, "d3d")  # the fallback

    def test_explicit_pick(self):
        d = resolve_default_device(explicit="d3d")
        self.assertEqual(d.name, "d3d")

    def test_env_var(self):
        with mock.patch.dict(os.environ, {"FDP_DEFAULT_DEVICE": "d3d"}):
            d = resolve_default_device()
        self.assertEqual(d.name, "d3d")

    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            resolve_default_device(explicit="nonexistent")

    def test_multiple_no_pick_raises(self):
        # Two contributors; no env var, no flag, no config -> error.
        a = Device(name="d3d", pelican_root="r1", origin_server="o1")
        b = Device(name="mast", pelican_root="r2", origin_server="o2")
        env = {k: v for k, v in os.environ.items()
               if k != "FDP_DEFAULT_DEVICE"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch("fdp.devices._entry_points",
                           return_value=[_fake_ep("d3d", a),
                                         _fake_ep("mast", b)]):
            with self.assertRaises(ValueError):
                resolve_default_device()


if __name__ == "__main__":
    unittest.main()
