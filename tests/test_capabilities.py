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

"""Capability-scoped device selection.

Each subcommand asks which devices can service *that* operation rather than
demanding a single global default. `ls` needs an origin server; `login` needs
bearer auth. With the real catalog (d3d + mast) both are unambiguous.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# d3d: has an origin server AND bearer auth.
_D3D_YAML = """\
schema_version: 1
name: d3d
description: test d3d
pelican_root: pelican://test/fdp-d3d
origin_server: root://d3d-origin.example.org:8443
locators:
  - kind: mds_tree
    name: main
    transport: pelican
    search_path: [pelican://test/fdp-d3d/mds/~t]
    auth: { kind: bearer_token, env: BEARER_TOKEN }
"""

# mast: neither an origin server nor bearer auth. Mirrors the real mast.yaml.
_MAST_YAML = """\
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
"""

# A second Pelican-hosted device, to prove genuine ambiguity still errors.
_DEVB_YAML = """\
schema_version: 1
name: devb
description: test devb
pelican_root: pelican://test/fdp-devb
origin_server: root://devb-origin.example.org:8443
locators:
  - kind: mds_tree
    name: main
    transport: pelican
    search_path: [pelican://test/fdp-devb/mds/~t]
    auth: { kind: bearer_token, env: BEARER_TOKEN }
"""

# A device whose origin_server is present-but-empty. `origin_server` is an
# optional plain string in fdp_schema, so "" is representable; it is just as
# unusable as None and must not be offered as a candidate.
_BLANK_ORIGIN_YAML = """\
schema_version: 1
name: blank
description: test blank origin
origin_server: ""
locators:
  - kind: zarr_store
    name: main
    protocol: s3
    base_url: s3://blank/shots
    auth: { kind: none }
"""


def make_ep(name, yaml_text):
    src = mock.MagicMock()
    src.read_text.return_value = yaml_text
    ep = mock.MagicMock()
    ep.name = name
    ep.load.return_value = src
    return ep


class CatalogFixture(unittest.TestCase):
    """Registers a set of fake devices and an empty temp $HOME."""

    YAMLS = ()

    def setUp(self):
        self._saved = os.environ.pop("FDP_DEFAULT_DEVICE", None)
        eps = [make_ep(n, y) for n, y in self.YAMLS]
        self._cat_patch = mock.patch("fdp.catalog.entry_points",
                                     return_value=eps)
        self._cat_patch.start()
        from fdp.catalog import catalog
        catalog._cache = None
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
        from fdp.catalog import catalog
        catalog._cache = None
        if self._saved is not None:
            os.environ["FDP_DEFAULT_DEVICE"] = self._saved
        else:
            os.environ.pop("FDP_DEFAULT_DEVICE", None)


class TestCapabilityScoping(CatalogFixture):
    """The real-world shape: d3d + mast."""

    YAMLS = (("d3d", _D3D_YAML), ("mast", _MAST_YAML))

    def test_origin_resolves_to_d3d_with_no_config(self):
        from fdp.devices import resolve_for_capability
        self.assertEqual(
            resolve_for_capability("origin").schema.name, "d3d")

    def test_bearer_resolves_to_d3d_with_no_config(self):
        from fdp.devices import resolve_for_capability
        self.assertEqual(
            resolve_for_capability("bearer").schema.name, "d3d")

    def test_candidates_exclude_incapable_devices(self):
        from fdp.devices import candidate_devices
        self.assertEqual(
            [h.schema.name for h in candidate_devices("origin")], ["d3d"])
        self.assertEqual(
            [h.schema.name for h in candidate_devices("bearer")], ["d3d"])

    def test_explicit_incapable_device_errors_clearly(self):
        # Regression: previously returned origin_server=None and crashed
        # inside FdpFileSystem(None).
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin", "mast")
        self.assertIn("mast", str(ctx.exception))
        self.assertIn("origin server", str(ctx.exception))

    def test_explicit_capable_device_is_honored(self):
        from fdp.devices import resolve_for_capability
        self.assertEqual(
            resolve_for_capability("origin", "d3d").schema.name, "d3d")

    def test_active_handles_returns_all_when_nothing_selected(self):
        from fdp.devices import active_handles
        self.assertEqual(
            sorted(h.schema.name for h in active_handles()), ["d3d", "mast"])

    def test_active_handles_narrows_to_explicit_selection(self):
        from fdp.devices import active_handles
        self.assertEqual(
            [h.schema.name for h in active_handles("mast")], ["mast"])

    def test_active_handles_honors_env_var(self):
        from fdp.devices import active_handles
        os.environ["FDP_DEFAULT_DEVICE"] = "mast"
        self.assertEqual(
            [h.schema.name for h in active_handles()], ["mast"])

    def test_capabilities_entries_are_predicate_description_pairs(self):
        # Task 8 iterates CAPABILITIES.items() and unpacks the value.
        from fdp.devices import CAPABILITIES
        for name, entry in CAPABILITIES.items():
            predicate, described = entry
            self.assertTrue(callable(predicate), name)
            self.assertIsInstance(described, str)


class TestDeterministicOrdering(CatalogFixture):
    """Selection output order must be stable so error messages are stable."""

    YAMLS = (("mast", _MAST_YAML), ("devb", _DEVB_YAML), ("d3d", _D3D_YAML))

    def test_active_handles_is_sorted_by_name(self):
        from fdp.devices import active_handles
        self.assertEqual([h.schema.name for h in active_handles()],
                         ["d3d", "devb", "mast"])

    def test_candidate_devices_is_sorted_by_name(self):
        from fdp.devices import candidate_devices
        self.assertEqual([h.schema.name for h in candidate_devices("origin")],
                         ["d3d", "devb"])


class TestGenuineAmbiguity(CatalogFixture):
    """Two Pelican-hosted devices: the ambiguity is real, so it must error."""

    YAMLS = (("d3d", _D3D_YAML), ("devb", _DEVB_YAML))

    def test_two_origin_devices_error_and_list_both(self):
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin")
        msg = str(ctx.exception)
        self.assertIn("d3d", msg)
        self.assertIn("devb", msg)
        self.assertIn("fdp device use", msg)

    def test_explicit_selection_resolves_the_ambiguity(self):
        from fdp.devices import resolve_for_capability
        self.assertEqual(
            resolve_for_capability("origin", "devb").schema.name, "devb")


class TestNoCapableDevice(CatalogFixture):
    """Only mast: nothing has an origin server."""

    YAMLS = (("mast", _MAST_YAML),)

    def test_zero_candidates_errors(self):
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin")
        self.assertIn("origin server", str(ctx.exception))


class TestBlankOriginIsNotACandidate(CatalogFixture):
    """An empty-string origin_server is as unusable as a missing one."""

    YAMLS = (("blank", _BLANK_ORIGIN_YAML),)

    def test_blank_origin_excluded(self):
        from fdp.devices import candidate_devices, resolve_for_capability
        self.assertEqual(candidate_devices("origin"), [])
        with self.assertRaises(ValueError):
            resolve_for_capability("origin")


class TestBadDeviceNames(CatalogFixture):
    """An unusable explicit name must say what is registered."""

    YAMLS = (("d3d", _D3D_YAML), ("mast", _MAST_YAML))

    def test_unknown_name_names_the_registered_devices(self):
        from fdp.devices import _resolve_device_handle
        with self.assertRaises(ValueError) as ctx:
            _resolve_device_handle("nosuch")
        msg = str(ctx.exception)
        self.assertIn("nosuch", msg)
        self.assertIn("d3d", msg)
        self.assertIn("mast", msg)

    def test_unknown_name_via_capability_is_equally_clear(self):
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin", "nosuch")
        self.assertIn("nosuch", str(ctx.exception))

    def test_unknown_name_via_active_handles_is_equally_clear(self):
        from fdp.devices import active_handles
        with self.assertRaises(ValueError) as ctx:
            active_handles("nosuch")
        self.assertIn("nosuch", str(ctx.exception))

    def test_empty_device_name_is_a_clear_error_not_a_bare_keyerror(self):
        # `fdp -D "" env`: "" is falsy but not None, so it reaches lookup.
        from fdp.devices import _resolve_device_handle
        with self.assertRaises(ValueError) as ctx:
            _resolve_device_handle("")
        self.assertIn("d3d", str(ctx.exception))

    def test_empty_env_var_is_treated_as_unset(self):
        from fdp.devices import explicit_device_name
        os.environ["FDP_DEFAULT_DEVICE"] = ""
        self.assertIsNone(explicit_device_name())


class TestNoDevicesInstalled(CatalogFixture):
    """A bare fdp env with no contributor packages."""

    YAMLS = ()

    def test_active_handles_says_install_a_device_package(self):
        from fdp.devices import active_handles
        with self.assertRaises(ValueError) as ctx:
            active_handles()
        self.assertIn("No tokamak contributors", str(ctx.exception))

    def test_explicit_name_still_says_install_a_device_package(self):
        # Not "unknown device 'd3d'" — nothing is installed at all, and that
        # is the actionable fact.
        from fdp.devices import _resolve_device_handle
        with self.assertRaises(ValueError) as ctx:
            _resolve_device_handle("d3d")
        self.assertIn("No tokamak contributors", str(ctx.exception))

    def test_capability_resolution_says_install_a_device_package(self):
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin", "d3d")
        self.assertIn("No tokamak contributors", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
