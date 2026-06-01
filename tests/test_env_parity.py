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
        "pelican://osg-htc.org:443/fdp-d3d/archives/index/json/"
        "json_indexes_2026-01-13_12:22:11"
    ),
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
