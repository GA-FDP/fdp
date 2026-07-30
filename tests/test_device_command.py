# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""The `fdp device` subcommand group."""

import contextlib
import io
import unittest
from unittest import mock

from test_capabilities import CatalogFixture, _D3D_YAML, _MAST_YAML


class TestDeviceCommand(CatalogFixture):
    YAMLS = (("d3d", _D3D_YAML), ("mast", _MAST_YAML))

    def _run(self, argv):
        from fdp import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(argv)
        return out.getvalue()

    def test_list_shows_names_and_capabilities(self):
        text = self._run(["device", "list"])
        self.assertIn("d3d", text)
        self.assertIn("mast", text)
        self.assertIn("origin", text)
        self.assertIn("bearer", text)

    def test_show_prints_yaml(self):
        text = self._run(["device", "show", "d3d"])
        self.assertIn("name: d3d", text)

    def test_use_writes_config_and_takes_effect(self):
        from fdp.config import read_default_device
        from fdp.devices import explicit_device_name
        self._run(["device", "use", "mast"])
        self.assertEqual(read_default_device(), "mast")
        self.assertEqual(explicit_device_name(), "mast")

    def test_use_clear_removes_the_setting(self):
        from fdp.config import read_default_device
        self._run(["device", "use", "mast"])
        self._run(["device", "use", "--clear"])
        self.assertIsNone(read_default_device())

    def test_use_rejects_unknown_device(self):
        from fdp import cli
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stderr(stderr):
            cli.main(["device", "use", "nosuchdevice"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("nosuchdevice", stderr.getvalue())
        self.assertNotIn("['", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
