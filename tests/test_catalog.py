# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Tests for fdp.catalog — discovery + lazy load."""

import unittest
from unittest import mock


def _make_mock_ep(name: str, tokamak_yaml: str):
    """Build a mock entry-point object whose .load() returns a Traversable
    whose .read_text() returns `tokamak_yaml`."""
    ep = mock.MagicMock()
    ep.name = name
    ep.value = f"mock:{name}"
    src = mock.MagicMock()
    src.read_text.return_value = tokamak_yaml
    ep.load.return_value = src
    return ep


class TestDiscover(unittest.TestCase):
    def test_discover_single(self):
        from fdp.catalog import _discover
        ep = _make_mock_ep("d3d", "schema_version: 1\nname: d3d\n")
        with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
            result = _discover()
        self.assertEqual(set(result.keys()), {"d3d"})
        self.assertEqual(result["d3d"].name, "d3d")

    def test_duplicate_name_raises(self):
        from fdp.catalog import _discover
        ep1 = _make_mock_ep("d3d_a", "schema_version: 1\nname: d3d\n")
        ep2 = _make_mock_ep("d3d_b", "schema_version: 1\nname: d3d\n")
        with mock.patch("fdp.catalog.entry_points", return_value=[ep1, ep2]):
            with self.assertRaisesRegex(RuntimeError, "Duplicate tokamak name"):
                _discover()


class TestCatalogSingleton(unittest.TestCase):
    def test_lazy_load_then_cached(self):
        from fdp.catalog import _Catalog
        c = _Catalog()
        ep = _make_mock_ep("x", "schema_version: 1\nname: x\n")
        with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
            tk = c._load()
            self.assertIn("x", tk)
        # Second access should not re-call entry_points
        with mock.patch("fdp.catalog.entry_points") as ep_patch:
            tk2 = c._load()
            ep_patch.assert_not_called()
        self.assertIs(tk, tk2)

    def test_contains(self):
        from fdp.catalog import _Catalog
        c = _Catalog()
        ep = _make_mock_ep("d3d", "schema_version: 1\nname: d3d\n")
        with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
            self.assertIn("d3d", c)
            self.assertNotIn("xyz", c)

    def test_names(self):
        from fdp.catalog import _Catalog
        c = _Catalog()
        eps = [
            _make_mock_ep("d3d", "schema_version: 1\nname: d3d\n"),
            _make_mock_ep("kstar", "schema_version: 1\nname: kstar\n"),
        ]
        with mock.patch("fdp.catalog.entry_points", return_value=eps):
            self.assertEqual(c.names(), ["d3d", "kstar"])
