# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Pinned env-var parity test.

This test asserts that the new catalog-driven _tokamak_env() emits exactly
the same dict that the legacy D3D_DEVICE.to_env() produced. The fixture
was captured during planning (see tests/_d3d_to_env_capture.json) before
D3D_DEVICE was deleted.

If you edit toksearch_d3d/data/d3d.yaml and this test breaks, the test is
doing its job. Either:
  - the YAML edit was intentional (update the fixture)
  - the YAML edit broke env-var compatibility (revert the edit)
"""

import unittest


# Captured 2026-06-01 from D3D_DEVICE.to_env(). Do not edit casually.
EXPECTED_D3D_ENV = {
    "default_tree_path": (
        "pelican://osg-htc.org:443/fdp-d3d/archives/mdsplus/codes/~t/~j~i/~h~g/~f~e/~d~c;"
        "pelican://osg-htc.org:443/fdp-d3d/archives/mdsplus/usershots/~t;"
        "pelican://osg-htc.org:443/fdp-d3d/archives/mdsplus/models/~t;"
        "pelican://osg-htc.org:443/fdp-d3d/archives/mdsplus/shots/~t/~f~e/~d~c"
    ),
    "PTDATA_JSON_INDEX_DIR": (
        "pelican://osg-htc.org:443/fdp-d3d/archives/index/json"
    ),
    "PTDATA_JSON_INDEX_PATTERN": "json_indexes_*",
    # Added 2026-09-13 (project B5). A client resolves store versions against
    # this root, which is where IT can read catalog/ -- deliberately not the
    # filesystem path the origin writes into its own tree paths.
    "FDP_STORE_ROOT": "pelican://osg-htc.org:443/fdp-d3d",
    "D3DATA": "yes",
    "SYS_D3_DELIM": ";",
    "CAKE_DB_PATH": (
        "pelican://osg-htc.org:443/fdp-d3d/metadata/iri_logs.db"
    ),
}


@unittest.skipUnless(
    bool(__import__("importlib.metadata", fromlist=["entry_points"])
         .entry_points(group="fdp_schema.catalogs")),
    "Requires the toksearch_d3d entry point installed in the env "
    "(run from toksearch_d3d's pixi env, not fdp's)",
)
class TestEnvParity(unittest.TestCase):
    def test_d3d_env_matches_captured_fixture(self):
        from fdp.environment import _tokamak_env
        from fdp.catalog import catalog
        got = _tokamak_env(catalog["d3d"])
        self.assertEqual(got, EXPECTED_D3D_ENV)


class TestStoreRoot(unittest.TestCase):
    """FDP_STORE_ROOT is where a CLIENT reads the catalog from.

    It is deliberately not the path an origin writes into its own tree paths:
    over fdp:// that is the mdsip sandbox's filesystem, which no client can
    see. Conflating the two makes a client try to list the origin's disk, and
    the failure mimics success -- pinned reads error, unpinned reads fall back
    to archives, and nothing resolves at all.
    """

    def test_a_device_with_a_pelican_root_gets_one(self):
        from fdp.environment import _tokamak_env
        from fdp.catalog import catalog

        env = _tokamak_env(catalog["d3d"])
        self.assertEqual(env["FDP_STORE_ROOT"],
                         "pelican://osg-htc.org:443/fdp-d3d")

    def test_it_is_a_client_readable_url_not_a_filesystem_path(self):
        from fdp.environment import _tokamak_env
        from fdp.catalog import catalog

        root = _tokamak_env(catalog["d3d"])["FDP_STORE_ROOT"]
        self.assertTrue(root.startswith("pelican://"), root)
        self.assertFalse(root.startswith("/"), root)

    def test_the_catalog_sits_directly_beneath_it(self):
        # The resolver appends /catalog and /views, so the root must be the
        # namespace holding both -- not, say, the archives subtree.
        from fdp.environment import _tokamak_env
        from fdp.catalog import catalog

        root = _tokamak_env(catalog["d3d"])["FDP_STORE_ROOT"]
        self.assertFalse(root.rstrip("/").endswith("archives"), root)

    def test_extra_env_can_override_it(self):
        # A deployment whose store lives elsewhere must be able to say so
        # without a new fdp release.
        from fdp.environment import _tokamak_env
        from fdp.catalog import catalog
        from unittest import mock

        handle = catalog["d3d"]
        with mock.patch.object(type(handle), "extra_env",
                               new_callable=mock.PropertyMock) as extra:
            extra.return_value = {"FDP_STORE_ROOT": "pelican://elsewhere/x"}
            self.assertEqual(_tokamak_env(handle)["FDP_STORE_ROOT"],
                             "pelican://elsewhere/x")
