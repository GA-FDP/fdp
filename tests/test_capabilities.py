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
    """Registers a set of fake devices and an empty temp $HOME.

    Subclasses override ``YAMLS``; ``self._home`` is the temp home for tests
    that need to write a config.toml. Every teardown step is registered with
    ``addCleanup`` the instant its resource exists, so a failure part-way
    through ``setUp`` (or in a subclass that extends it) can never leak the
    ``entry_points`` / ``Path.home`` patches into unrelated test classes.
    ``addCleanup`` also unwinds LIFO, which is the order we want.
    """

    YAMLS = ()

    def setUp(self):
        saved = os.environ.pop("FDP_DEFAULT_DEVICE", None)
        self.addCleanup(self._restore_env, saved)

        self.addCleanup(self._reset_catalog_cache)
        eps = [make_ep(n, y) for n, y in self.YAMLS]
        self._cat_patch = mock.patch("fdp.catalog.entry_points",
                                     return_value=eps)
        self._cat_patch.start()
        self.addCleanup(self._cat_patch.stop)
        self._reset_catalog_cache()

        self._home_td = tempfile.TemporaryDirectory()
        self.addCleanup(self._home_td.cleanup)
        self._home = Path(self._home_td.name)
        (self._home / ".fdp").mkdir()
        self._home_patch = mock.patch.object(
            Path, "home", return_value=self._home)
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    @staticmethod
    def _reset_catalog_cache():
        from fdp.catalog import catalog
        catalog._cache = None

    @staticmethod
    def _restore_env(saved):
        if saved is not None:
            os.environ["FDP_DEFAULT_DEVICE"] = saved
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

    def test_explicit_incapable_device_names_a_capable_one(self):
        # Rejecting the user's choice without naming a working one leaves
        # them to guess.
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin", "mast")
        self.assertIn("Devices that do: d3d", str(ctx.exception))

    def test_messages_do_not_leak_python_list_repr(self):
        from fdp.devices import _resolve_device_handle
        with self.assertRaises(ValueError) as ctx:
            _resolve_device_handle(None)
        msg = str(ctx.exception)
        self.assertIn("d3d, mast", msg)
        self.assertNotIn("['", msg)

    def test_unknown_capability_names_the_valid_ones(self):
        from fdp.devices import candidate_devices, resolve_for_capability
        for call in (lambda: candidate_devices("orgin"),
                     lambda: resolve_for_capability("orgin")):
            with self.assertRaises(ValueError) as ctx:
                call()
            msg = str(ctx.exception)
            self.assertIn("orgin", msg)
            self.assertIn("origin", msg)
            self.assertIn("bearer", msg)

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

    def test_ls_origin_resolves_without_config(self):
        from fdp.cli import _resolve_origin_server
        self.assertEqual(_resolve_origin_server(None),
                         "root://d3d-origin.example.org:8443")

    def test_ls_origin_rejects_device_without_origin(self):
        from fdp.cli import _resolve_origin_server
        with self.assertRaises(ValueError):
            _resolve_origin_server("mast")

    def test_ls_pelican_url_selects_matching_device(self):
        from fdp.cli import _device_for_ls
        handle = _device_for_ls("pelican://test/fdp-d3d/archives", None)
        self.assertEqual(handle.schema.name, "d3d")

    def test_explicit_device_overrides_pelican_url_inference(self):
        # An explicit -D wins over URL inference: even a d3d URL must not
        # rescue an explicitly-named origin-less device. Guards the
        # `device_name is None` gate against a future parser regression.
        from fdp.cli import _device_for_ls
        with self.assertRaises(ValueError):
            _device_for_ls("pelican://test/fdp-d3d/archives", "mast")

    def test_login_resolves_to_the_only_bearer_device(self):
        import argparse
        import contextlib
        import io
        from fdp import cli
        # argparse.Namespace (not mock.Mock) is a strict double: it raises
        # AttributeError if do_login reads an unexpected arg, rather than
        # silently auto-vivifying a child mock.
        args = argparse.Namespace(device=None, write=False)
        with mock.patch("fdp.auth.login") as m, \
                contextlib.redirect_stdout(io.StringIO()):
            m.return_value = None
            cli.do_login(args)
        self.assertEqual(m.call_args[0][0].schema.name, "d3d")

    def test_login_rejects_device_without_bearer_auth(self):
        import argparse
        import contextlib
        import io
        from fdp import cli
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stderr(stderr):
            cli.do_login(argparse.Namespace(device="mast", write=False))
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("bearer", stderr.getvalue())

    def test_logout_rejects_device_without_bearer_auth(self):
        # Symmetric with the login rejection: logout is scoped to bearer
        # devices too, so an explicit non-bearer device must clean-error
        # rather than silently resolve.
        import argparse
        import contextlib
        import io
        from fdp import cli
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stderr(stderr):
            cli.do_logout(argparse.Namespace(device="mast"))
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("bearer", stderr.getvalue())


class TestLsPelicanShortcutRequiresOrigin(CatalogFixture):
    """The pelican:// URL shortcut in `_device_for_ls` must not bypass the
    origin capability check. `pelican_root` and `origin_server` are
    independent optional fields in fdp_schema -- a device can declare the
    former without the latter (mast's real catalog entry has neither, but
    nothing stops a future device from having only `pelican_root`). If the
    shortcut trusted a pelican_root match alone, `fdp ls` on such a device's
    URL would resolve to a handle with `origin_server is None` and crash
    inside `FdpFileSystem(None)` -- the exact bug this task removes for the
    no-URL case.
    """

    _NO_ORIGIN_PELICAN_YAML = """\
schema_version: 1
name: noorigin
description: test device with pelican_root but no origin_server
pelican_root: pelican://test/fdp-noorigin
locators:
  - kind: zarr_store
    name: main
    protocol: s3
    base_url: s3://noorigin/shots
    auth: { kind: none }
"""

    YAMLS = (("noorigin", _NO_ORIGIN_PELICAN_YAML),)

    def test_pelican_shortcut_does_not_bypass_origin_check(self):
        from fdp.cli import _device_for_ls
        with self.assertRaises(ValueError):
            _device_for_ls("pelican://test/fdp-noorigin/archives", None)


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

    def test_hint_leads_with_the_per_invocation_remedy(self):
        # Spec D7: capability ambiguity is per-command, but FDP_DEFAULT_DEVICE
        # and `fdp device use` are global -- following them to unstick
        # `fdp ls` would silently stop `fdp env`/`fdp run` from composing
        # every installed device. The hint must lead with `--device` and label
        # the persistent remedies, so it does not set that trap.
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin")
        msg = str(ctx.exception)
        self.assertLess(msg.index("--device"), msg.index("FDP_DEFAULT_DEVICE"))
        self.assertLess(msg.index("--device"), msg.index("fdp device use"))
        self.assertIn("ALL commands", msg)
        self.assertIn("`fdp env`/`fdp run`", msg)
        # `fdp device use` alone doesn't say which file it writes.
        self.assertIn("~/.fdp/config.toml", msg)

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

    def test_explicit_incapable_device_falls_back_to_the_same_guidance(self):
        # There is no capable device to point at, so "Devices that do: "
        # would dangle; give the general message instead.
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin", "mast")
        msg = str(ctx.exception)
        self.assertIn("No registered device declares", msg)
        self.assertNotIn("Devices that do", msg)


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
    """A bare fdp env with no contributor packages.

    These assert the *type*, not just the message: `fdp chat`/`fdp query`
    degrade to a warning only for NoDevicesError, so a plain ValueError here
    would silently turn that degradation back into a hard exit. The message
    check alone cannot see the difference.
    """

    YAMLS = ()

    def test_active_handles_says_install_a_device_package(self):
        from fdp.devices import NoDevicesError, active_handles
        with self.assertRaises(NoDevicesError) as ctx:
            active_handles()
        self.assertIn("No tokamak contributors", str(ctx.exception))

    def test_explicit_name_still_says_install_a_device_package(self):
        # Not "unknown device 'd3d'" — nothing is installed at all, and that
        # is the actionable fact.
        from fdp.devices import NoDevicesError, _resolve_device_handle
        with self.assertRaises(NoDevicesError) as ctx:
            _resolve_device_handle("d3d")
        self.assertIn("No tokamak contributors", str(ctx.exception))

    def test_capability_resolution_says_install_a_device_package(self):
        from fdp.devices import NoDevicesError, resolve_for_capability
        with self.assertRaises(NoDevicesError) as ctx:
            resolve_for_capability("origin", "d3d")
        self.assertIn("No tokamak contributors", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
