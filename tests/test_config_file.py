# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Round-trip tests for ~/.fdp/config.toml handling."""

import tempfile
import tomllib
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

    def test_scalar_device_key_reads_as_none(self):
        from fdp.config import read_default_device
        self.path.write_text('device = "foo"\n')
        self.assertIsNone(read_default_device())

    def test_device_header_with_trailing_comment(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text('[device] # notes\ndefault = "mast"\n')
        set_default_device("d3d")
        text = self.path.read_text()
        tomllib.loads(text)  # must still be valid TOML
        self.assertEqual(text.count("[device]"), 1)
        self.assertEqual(read_default_device(), "d3d")

    def test_device_header_with_inner_spaces(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text('[ device ]\ndefault = "mast"\n')
        set_default_device("d3d")
        text = self.path.read_text()
        tomllib.loads(text)  # must still be valid TOML
        self.assertEqual(read_default_device(), "d3d")

    def test_device_header_quoted(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text('["device"]\ndefault = "mast"\n')
        set_default_device("d3d")
        text = self.path.read_text()
        tomllib.loads(text)  # must still be valid TOML
        self.assertEqual(read_default_device(), "d3d")

    def test_comments_inside_device_section_survive(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text(
            '[device]\n# pinned by ops\ndefault = "mast"\n')
        set_default_device("d3d")
        text = self.path.read_text()
        self.assertIn("# pinned by ops", text)
        self.assertEqual(read_default_device(), "d3d")

    def test_duplicate_default_key_is_invalid_toml_and_rejected(self):
        # A `default =` key repeated under one [device] table is not
        # actually legal TOML (a spec-compliant parser refuses to
        # redeclare a key) -- confirmed by tomllib raising "Cannot
        # overwrite a value" on this exact text. So this is a
        # pre-existing-broken-file case (see item 2), not a case the
        # in-loop duplicate-line guard needs to collapse: that guard
        # exists as defense in depth, but a real duplicate key is now
        # caught earlier, before any line-editing is attempted.
        from fdp.config import set_default_device
        text = '[device]\ndefault = "mast"\ndefault = "old"\n'
        self.path.write_text(text)
        with self.assertRaises(ValueError):
            set_default_device("d3d")
        self.assertEqual(self.path.read_text(), text)

    def test_idempotent_repeat_calls(self):
        from fdp.config import read_default_device, set_default_device
        set_default_device("d3d")
        first = self.path.read_text()
        set_default_device("d3d")
        second = self.path.read_text()
        self.assertEqual(first, second)
        self.assertEqual(read_default_device(), "d3d")

    def test_non_string_default_value_reads_as_none(self):
        from fdp.config import read_default_device
        self.path.write_text("[device]\ndefault = 3\n")
        self.assertIsNone(read_default_device())

    def test_invalid_device_name_raises(self):
        from fdp.config import set_default_device
        with self.assertRaises(ValueError):
            set_default_device('ev"il')

    def test_clear_with_no_config_creates_no_file(self):
        from fdp.config import set_default_device
        self.assertFalse(self.path.exists())
        set_default_device(None)
        self.assertFalse(self.path.exists())

    def test_symlinked_config_write_follows_link(self):
        from fdp.config import read_default_device, set_default_device
        real = self._home / "real.toml"
        real.write_text('[llm]\nbackend = "amsc"\n')
        self.path.symlink_to(real)
        set_default_device("d3d")
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(self.path.resolve(), real.resolve())
        self.assertIn('backend = "amsc"', real.read_text())
        self.assertEqual(read_default_device(), "d3d")

    def test_preexisting_invalid_toml_raises_value_error(self):
        from fdp.config import set_default_device
        self.path.write_text("not = valid = toml")
        with self.assertRaises(ValueError):
            set_default_device("d3d")

    def test_refused_write_leaves_file_byte_identical(self):
        from fdp.config import set_default_device
        self.path.write_text('device = { default = "mast" }\n')
        before = self.path.read_text()
        with self.assertRaises(RuntimeError):
            set_default_device("d3d")
        self.assertEqual(self.path.read_text(), before)

    def test_refused_write_leaves_no_stray_temp_files(self):
        from fdp.config import set_default_device
        self.path.write_text('device = { default = "mast" }\n')
        with self.assertRaises(RuntimeError):
            set_default_device("d3d")
        entries = list(self.path.parent.iterdir())
        self.assertEqual(entries, [self.path])


if __name__ == "__main__":
    unittest.main()
