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


class TestTokamakHandle(unittest.TestCase):
    YAML = """
schema_version: 1
name: d3d
description: DIII-D
locators:
  - kind: mds_tree
    name: main
    transport: pelican
    search_path: [u1, u2]
  - kind: mds_tree
    name: backup
    transport: pelican
    search_path: [b1]
  - kind: ptdata_indexed
    name: main
    transport: pelican
    index_dir: idx
extra_env: { D3DATA: "yes" }
"""

    def _catalog(self):
        from fdp.catalog import _Catalog
        c = _Catalog()
        ep = _make_mock_ep("d3d", self.YAML)
        return c, ep

    def test_getitem_returns_handle(self):
        from fdp.catalog import TokamakHandle
        c, ep = self._catalog()
        with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
            handle = c["d3d"]
        self.assertIsInstance(handle, TokamakHandle)
        self.assertEqual(handle.name, "d3d")
        self.assertEqual(handle.description, "DIII-D")
        self.assertEqual(handle.extra_env, {"D3DATA": "yes"})

    def test_handle_schema_is_raw_model(self):
        from fdp_schema import Tokamak
        c, ep = self._catalog()
        with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
            handle = c["d3d"]
        self.assertIsInstance(handle.schema, Tokamak)

    def test_locator_default_name_main(self):
        c, ep = self._catalog()
        with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
            handle = c["d3d"]
            loc = handle.locator("mds_tree")  # default name="main"
        self.assertEqual(loc.model.name, "main")

    def test_locator_explicit_name(self):
        c, ep = self._catalog()
        with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
            handle = c["d3d"]
            loc = handle.locator("mds_tree", name="backup")
        self.assertEqual(loc.model.name, "backup")
        self.assertEqual(loc.model.search_path, ["b1"])

    def test_locator_not_found_raises(self):
        c, ep = self._catalog()
        with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
            handle = c["d3d"]
            with self.assertRaises(KeyError):
                handle.locator("sql")  # no sql locator in this fixture

    def test_unknown_tokamak_raises(self):
        c, ep = self._catalog()
        with mock.patch("fdp.catalog.entry_points", return_value=[ep]):
            with self.assertRaises(KeyError):
                c["nonexistent"]


def _d3d_ep_available() -> bool:
    """Return True iff the toksearch_d3d d3d entry point is registered."""
    from importlib.metadata import entry_points
    return any(ep.name == "d3d" for ep in entry_points(group="fdp_schema.catalogs"))


_SKIP_INTEGRATION = not _d3d_ep_available()
_SKIP_REASON = "toksearch_d3d fdp_schema.catalogs entry point not installed (run from toksearch_d3d env)"


@unittest.skipIf(_SKIP_INTEGRATION, _SKIP_REASON)
class TestCatalogIntegration(unittest.TestCase):
    """End-to-end with the real toksearch_d3d entry point installed."""

    def test_d3d_handle_exposes_real_locators(self):
        from fdp.catalog import catalog
        tk = catalog["d3d"]
        self.assertEqual(tk.name, "d3d")
        self.assertIn("D3DATA", tk.extra_env)

    def test_d3d_mds_tree_resolver_works(self):
        from fdp.catalog import catalog
        from fdp.resolvers.mds_tree import MdsTreeResolver
        loc = catalog["d3d"].locator("mds_tree")
        self.assertIsInstance(loc, MdsTreeResolver)
        urls = loc.urls_for(165920)
        self.assertEqual(len(urls), 4)  # four D3D search-path entries
        # Tokens should be expanded — no ~ in concrete URLs
        for u in urls:
            self.assertNotIn("~", u)

    def test_d3d_sql_locator_metadata_present(self):
        from fdp.catalog import catalog
        from fdp.resolvers.sql import SqlResolver
        loc = catalog["d3d"].locator("sql", name="d3drdb")
        self.assertIsInstance(loc, SqlResolver)
        self.assertEqual(loc.model.host, "d3drdb.gat.com")
        self.assertEqual(loc.model.port, 8001)
        self.assertEqual(loc.model.database, "d3drdb")
        self.assertEqual(loc.model.tdsver, "7.0")
