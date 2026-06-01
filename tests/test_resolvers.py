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
