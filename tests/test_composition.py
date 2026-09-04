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

"""Environment composition across devices.

The premise: registered devices' env vars are disjoint in practice, so the
union is well-defined and no device choice is needed. A genuine collision is
a hard error naming the key.
"""

import unittest

from test_capabilities import CatalogFixture, _D3D_YAML, _MAST_YAML


def _ptdata_yaml(name: str, index_dir: str) -> str:
    """A minimal Pelican/PTData device. Two of these with different
    ``index_dir`` collide on PTDATA_JSON_INDEX_DIR; every other var they
    emit is derived from the interpreter, so it is identical between them."""
    return f"""\
schema_version: 1
name: {name}
description: test {name} with ptdata
origin_server: root://{name}-origin.example.org:8443
locators:
  - kind: ptdata_indexed
    name: main
    transport: pelican
    index_dir: {index_dir}
    auth: {{ kind: bearer_token, env: BEARER_TOKEN }}
"""


_D3D_INDEX = "pelican://test/fdp-d3d/index/json"
_DEVB_INDEX = "pelican://test/fdp-devb/index/json"

_D3D_PTDATA_YAML = _ptdata_yaml("d3d", _D3D_INDEX)
# Deliberately collides with d3d on PTDATA_JSON_INDEX_DIR.
_CONFLICT_YAML = _ptdata_yaml("devb", _DEVB_INDEX)


class TestCompositionSucceeds(CatalogFixture):
    YAMLS = (("d3d", _D3D_YAML), ("mast", _MAST_YAML))

    def test_union_contains_both_devices_vars(self):
        from fdp.devices import active_handles
        from fdp.environment import compose_device_config
        env = compose_device_config(active_handles())
        # d3d contributes an MDSplus tree path...
        self.assertIn("default_tree_path", env)
        # ...and mast contributes its zarr store.
        self.assertEqual(env["MAST_ZARR_BASE_URL"], "s3://mast/level2/shots")

    def test_path_is_not_prepended_once_per_device(self):
        # Every device computes an identical PATH (it derives from
        # sys.executable), so composing N devices must yield exactly the
        # single-device value -- not N stacked prepends.
        from fdp.devices import active_handles
        from fdp.environment import build_device_config, compose_device_config
        composed = compose_device_config(active_handles())
        single = build_device_config(active_handles("d3d")[0])
        self.assertEqual(composed["PATH"], single["PATH"])

    def test_single_device_selection_skips_composition(self):
        from fdp.devices import active_handles
        from fdp.environment import compose_device_config
        env = compose_device_config(active_handles("mast"))
        self.assertNotIn("default_tree_path", env)
        self.assertIn("MAST_ZARR_BASE_URL", env)

    def test_every_value_is_a_string(self):
        # The result is fed to apply_environment / printed as shell exports,
        # so a stray non-string would only surface downstream.
        from fdp.devices import active_handles
        from fdp.environment import compose_device_config
        for key, value in compose_device_config(active_handles()).items():
            self.assertIsInstance(value, str, key)


class TestCompositionConflict(CatalogFixture):
    YAMLS = (("d3d", _D3D_PTDATA_YAML), ("devb", _CONFLICT_YAML))

    def test_conflict_raises_naming_key_and_devices(self):
        from fdp.devices import active_handles
        from fdp.environment import DeviceEnvConflict, compose_device_config
        with self.assertRaises(DeviceEnvConflict) as ctx:
            compose_device_config(active_handles())
        msg = str(ctx.exception)
        self.assertIn("PTDATA_JSON_INDEX_DIR", msg)
        self.assertIn("d3d", msg)
        self.assertIn("devb", msg)

    def test_conflict_shows_both_offending_values(self):
        # Naming only the key leaves the user unable to tell which device is
        # wrong; the values are the diagnosis.
        from fdp.devices import active_handles
        from fdp.environment import DeviceEnvConflict, compose_device_config
        with self.assertRaises(DeviceEnvConflict) as ctx:
            compose_device_config(active_handles())
        msg = str(ctx.exception)
        self.assertIn(_D3D_INDEX, msg)
        self.assertIn(_DEVB_INDEX, msg)

    def test_conflict_hint_leads_with_the_per_invocation_remedy(self):
        # Spec D7, same standard as devices._CHOOSE_HINT: `--device` first,
        # and the persistent remedies labeled as global.
        from fdp.devices import active_handles
        from fdp.environment import DeviceEnvConflict, compose_device_config
        with self.assertRaises(DeviceEnvConflict) as ctx:
            compose_device_config(active_handles())
        msg = str(ctx.exception)
        self.assertLess(msg.index("--device"), msg.index("FDP_DEFAULT_DEVICE"))
        self.assertLess(msg.index("--device"), msg.index("fdp device use"))
        self.assertIn("ALL commands", msg)
        self.assertIn("~/.fdp/config.toml", msg)

    def test_conflict_message_is_independent_of_argument_order(self):
        # Callers may hand over handles in any order (active_handles sorts,
        # but compose_device_config is public); the reported culprits must
        # not depend on that, or the same broken install produces two
        # different messages.
        from fdp.devices import active_handles
        from fdp.environment import DeviceEnvConflict, compose_device_config
        handles = active_handles()
        messages = set()
        for order in (handles, list(reversed(handles))):
            with self.assertRaises(DeviceEnvConflict) as ctx:
                compose_device_config(order)
            messages.add(str(ctx.exception))
        self.assertEqual(len(messages), 1, messages)

    def test_conflict_is_a_valueerror_so_cli_renders_it_cleanly(self):
        # cli.main catches (ValueError, KeyError) to print a clean message
        # instead of a traceback; keep DeviceEnvConflict inside that net.
        from fdp.environment import DeviceEnvConflict
        self.assertTrue(issubclass(DeviceEnvConflict, ValueError))

    def test_explicit_selection_escapes_the_conflict(self):
        from fdp.devices import active_handles
        from fdp.environment import compose_device_config
        env = compose_device_config(active_handles("d3d"))
        self.assertEqual(env["PTDATA_JSON_INDEX_DIR"], _D3D_INDEX)

    def test_cli_renders_conflict_without_traceback(self):
        import contextlib
        import io
        from fdp import cli
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stderr(stderr):
            cli.main(["env"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("PTDATA_JSON_INDEX_DIR", stderr.getvalue())

    def test_chat_exits_on_conflict_rather_than_warning(self):
        """`fdp chat` sets up the environment best-effort: it warns and
        continues when no device is installed at all. A real conflict between
        two installed devices is not that case -- it is a choice the user has
        to make -- so chat must still exit 1. Driven through the real
        setup_environment rather than an injected exception, so the whole
        path from two conflicting catalogs to the CLI's decision is covered.
        """
        import contextlib
        import io
        from unittest import mock
        from fdp import cli
        stderr = io.StringIO()
        with mock.patch.object(cli.os, "execvpe") as ev:
            with self.assertRaises(SystemExit) as ctx, \
                    contextlib.redirect_stderr(stderr):
                cli.main(["chat"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("PTDATA_JSON_INDEX_DIR", stderr.getvalue())
        self.assertNotIn("Warning", stderr.getvalue())
        ev.assert_not_called()


class TestConflictAttribution(CatalogFixture):
    """Three devices, two of which agree: the message must blame the device
    that actually set the surviving value, not whichever one came last."""

    YAMLS = (
        ("aaa", _ptdata_yaml("aaa", _D3D_INDEX)),
        ("bbb", _ptdata_yaml("bbb", _D3D_INDEX)),
        ("ccc", _ptdata_yaml("ccc", _DEVB_INDEX)),
    )

    def test_agreeing_devices_do_not_steal_the_blame(self):
        from fdp.devices import active_handles
        from fdp.environment import DeviceEnvConflict, compose_device_config
        with self.assertRaises(DeviceEnvConflict) as ctx:
            compose_device_config(active_handles())
        msg = str(ctx.exception)
        self.assertIn("'aaa' and 'ccc'", msg)
        # bbb agrees with aaa, so it is not part of the disagreement.
        self.assertNotIn("bbb", msg)


class TestRealCatalogHasNoConflicts(unittest.TestCase):
    """Regression guard against the *actually installed* devices.

    This is what makes composition falsifiable rather than an assumption: a
    future device that genuinely collides fails CI instead of silently
    corrupting a user's environment.

    Runs only where a device contributor is installed. In CI that means the
    `pixi run test-mock` job (fdp's dev env pulls in toksearch_d3d +
    toksearch_mast); it does NOT run in the recipe-based build test, whose env
    is provisioned from fdp's run deps and by design contains zero device
    contributors (device packages depend on fdp, not the reverse). There the
    guard skips -- so the enforcement lives in the pixi test job, not the
    package build test.
    """

    def test_installed_devices_compose_without_conflict(self):
        from fdp.catalog import catalog
        from fdp.environment import compose_device_config
        handles = [catalog[n] for n in catalog.names()]
        if len(handles) < 2:
            self.skipTest(
                f"only {len(handles)} device(s) installed; nothing to compose")
        compose_device_config(handles)  # must not raise


if __name__ == "__main__":
    unittest.main()
