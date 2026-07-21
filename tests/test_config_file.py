# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Round-trip tests for ~/.fdp/config.toml handling."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestConfigFile(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._home = Path(self._td.name)
        (self._home / ".fdp").mkdir()
        self._patch = mock.patch.object(Path, "home", return_value=self._home)
        self._patch.start()
        self.path = self._home / ".fdp" / "config.toml"

    def tearDown(self):
        self._patch.stop()
        self._td.cleanup()

    def test_read_returns_none_when_no_file(self):
        from fdp.config import read_default_device
        self.assertIsNone(read_default_device())

    def test_set_creates_file_when_absent(self):
        from fdp.config import read_default_device, set_default_device
        set_default_device("d3d")
        self.assertEqual(read_default_device(), "d3d")
        self.assertIn("[device]", self.path.read_text())

    def test_set_preserves_other_sections_and_comments(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text(
            "# my notes\n[llm]\nbackend = 'amsc'\n")
        set_default_device("d3d")
        text = self.path.read_text()
        self.assertIn("# my notes", text)
        self.assertIn("[llm]", text)
        self.assertIn("backend = 'amsc'", text)
        self.assertEqual(read_default_device(), "d3d")

    def test_set_replaces_existing_default_exactly_once(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text('[device]\ndefault = "mast"\n')
        set_default_device("d3d")
        text = self.path.read_text()
        self.assertEqual(read_default_device(), "d3d")
        self.assertEqual(text.count("default ="), 1)
        self.assertNotIn("mast", text)

    def test_set_adds_key_to_existing_empty_device_section(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text("[device]\n[llm]\nbackend = 'amsc'\n")
        set_default_device("d3d")
        self.assertEqual(read_default_device(), "d3d")
        self.assertIn("[llm]", self.path.read_text())

    def test_clear_removes_key_but_keeps_other_sections(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text(
            '[llm]\nbackend = "amsc"\n\n[device]\ndefault = "d3d"\n')
        set_default_device(None)
        self.assertIsNone(read_default_device())
        self.assertIn("backend", self.path.read_text())

    def test_malformed_toml_reads_as_none(self):
        from fdp.config import read_default_device
        self.path.write_text("not = valid = toml")
        self.assertIsNone(read_default_device())


if __name__ == "__main__":
    unittest.main()
