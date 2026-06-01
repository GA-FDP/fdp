# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Tests for fdp.resolvers — typed per-backend helpers."""

import unittest


class TestMdsTemplateExpansion(unittest.TestCase):
    """The MDSplus path-token convention used by the search_path templates.

    Tokens are single chars after `~`:
      ~t — full shot as decimal
      ~c, ~d, ~e, ~f, ~g, ~h, ~i, ~j — individual digits of the shot
        zero-padded to 8 digits, where ~c is the units digit (rightmost),
        ~d is tens, ..., ~j is 10^7.

    Examples for shot 165920 (zero-padded: "00165920"):
      ~t   → "165920"
      ~c   → "0"   (units)
      ~d   → "2"   (tens)
      ~e   → "9"   (hundreds)
      ~f   → "5"   (thousands)
      ~g   → "6"   (ten-thousands)
      ~h   → "1"   (hundred-thousands)
      ~i   → "0"   (millions)
      ~j   → "0"   (ten-millions)
    """

    def test_full_shot_token(self):
        from fdp.resolvers.mds_tree import _expand_mds_template
        self.assertEqual(_expand_mds_template("x/~t/y", 165920), "x/165920/y")

    def test_individual_digit_tokens(self):
        from fdp.resolvers.mds_tree import _expand_mds_template
        # Shot 165920 → padded "00165920"; c..j = 0,2,9,5,6,1,0,0
        result = _expand_mds_template("~j~i/~h~g/~f~e/~d~c", 165920)
        self.assertEqual(result, "00/16/59/20")

    def test_low_shot(self):
        from fdp.resolvers.mds_tree import _expand_mds_template
        # Shot 5 → padded "00000005"; only ~c is nonzero
        self.assertEqual(_expand_mds_template("d/~t", 5), "d/5")
        self.assertEqual(_expand_mds_template("d/~c", 5), "d/5")
        self.assertEqual(_expand_mds_template("d/~d", 5), "d/0")

    def test_shot_too_large_raises(self):
        from fdp.resolvers.mds_tree import _expand_mds_template
        with self.assertRaises(ValueError):
            _expand_mds_template("~t", 100_000_000)  # 9-digit shot


class TestMdsTreeResolver(unittest.TestCase):
    def _model(self):
        from fdp_schema import MdsTreeLocator
        return MdsTreeLocator(
            name="main",
            transport="pelican",
            search_path=[
                "pelican://h/codes/~t/~j~i/~h~g/~f~e/~d~c",
                "pelican://h/shots/~t",
            ],
        )

    def test_urls_for_expands_all(self):
        from fdp.resolvers.mds_tree import MdsTreeResolver
        r = MdsTreeResolver(self._model())
        urls = r.urls_for(165920)
        self.assertEqual(urls, [
            "pelican://h/codes/165920/00/16/59/20",
            "pelican://h/shots/165920",
        ])

    def test_joined_path_default_delim(self):
        from fdp.resolvers.mds_tree import MdsTreeResolver
        r = MdsTreeResolver(self._model())
        joined = r.joined_path(165920)
        self.assertIn(";", joined)
        self.assertEqual(joined.count(";"), 1)  # 2 URLs, 1 separator

    def test_joined_path_custom_delim(self):
        from fdp.resolvers.mds_tree import MdsTreeResolver
        r = MdsTreeResolver(self._model())
        joined = r.joined_path(165920, delim="|")
        self.assertIn("|", joined)


class TestPtDataResolver(unittest.TestCase):
    """Tests use mocked _fetch_index to avoid network."""

    def _model(self, with_auth=True):
        from fdp_schema import PtDataIndexedLocator, AuthHint
        kwargs = dict(
            name="main",
            transport="pelican",
            index_dir="pelican://h/idx",
        )
        if with_auth:
            kwargs["auth"] = AuthHint(kind="bearer_token", env="BEARER_TOKEN")
        return PtDataIndexedLocator(**kwargs)

    def _fake_index(self):
        """Realistic JSON index shape for shot 200000."""
        return {
            "shot": 200000,
            "pointname_ext": {"IP": ".PWR", "DENSITY": ".D8B"},
            "ext_location": {
                ".PWR": "pelican://h/data/200000.PWR",
                ".D8B": "pelican://h/data/200000.D8B",
            },
        }

    def test_index_url_construction_shards_by_100(self):
        from fdp.resolvers.ptdata import PtDataResolver
        r = PtDataResolver(self._model())
        # Shot 165920 → shard 1659
        self.assertEqual(
            r._index_url(165920),
            "pelican://h/idx/1659/165920.json",
        )
        # Shot 200000 → shard 2000
        self.assertEqual(
            r._index_url(200000),
            "pelican://h/idx/2000/200000.json",
        )
        # Edge: small shot
        self.assertEqual(r._index_url(50), "pelican://h/idx/0/50.json")

    def test_resolve_returns_url_for_known_pointname(self):
        import os
        from unittest import mock
        from fdp.resolvers.ptdata import PtDataResolver
        r = PtDataResolver(self._model())
        with mock.patch.dict(os.environ, {"BEARER_TOKEN": "x"}):
            with mock.patch.object(r, "_fetch_index", return_value=self._fake_index()):
                url = r.resolve(200000, "ip")
        self.assertEqual(url, "pelican://h/data/200000.PWR")

    def test_resolve_pointname_case_insensitive(self):
        import os
        from unittest import mock
        from fdp.resolvers.ptdata import PtDataResolver
        r = PtDataResolver(self._model())
        idx = self._fake_index()
        with mock.patch.dict(os.environ, {"BEARER_TOKEN": "x"}):
            with mock.patch.object(r, "_fetch_index", return_value=idx):
                self.assertEqual(r.resolve(200000, "ip"), "pelican://h/data/200000.PWR")
                self.assertEqual(r.resolve(200000, "Ip"), "pelican://h/data/200000.PWR")
                self.assertEqual(r.resolve(200000, "IP"), "pelican://h/data/200000.PWR")

    def test_resolve_unknown_pointname_returns_none(self):
        import os
        from unittest import mock
        from fdp.resolvers.ptdata import PtDataResolver
        r = PtDataResolver(self._model())
        with mock.patch.dict(os.environ, {"BEARER_TOKEN": "x"}):
            with mock.patch.object(r, "_fetch_index", return_value=self._fake_index()):
                self.assertIsNone(r.resolve(200000, "nonexistent"))

    def test_index_cached_after_first_fetch(self):
        import os
        from unittest import mock
        from fdp.resolvers.ptdata import PtDataResolver
        r = PtDataResolver(self._model())
        with mock.patch.dict(os.environ, {"BEARER_TOKEN": "x"}):
            with mock.patch.object(
                r, "_fetch_index", return_value=self._fake_index()
            ) as fetch:
                r.resolve(200000, "ip")
                r.resolve(200000, "ip")
                r.resolve(200000, "density")
                fetch.assert_called_once()  # 3 calls, same shot, one fetch

    def test_missing_auth_env_raises(self):
        import os
        from unittest import mock
        from fdp.resolvers.ptdata import PtDataResolver
        r = PtDataResolver(self._model(with_auth=True))
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BEARER_TOKEN"):
                r.resolve(200000, "ip")

    def test_no_auth_allowed_when_locator_has_no_auth(self):
        import os
        from unittest import mock
        from fdp.resolvers.ptdata import PtDataResolver
        r = PtDataResolver(self._model(with_auth=False))
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                r, "_fetch_index", return_value=self._fake_index()
            ):
                self.assertEqual(r.resolve(200000, "ip"), "pelican://h/data/200000.PWR")
