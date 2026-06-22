# Copyright 2024 General Atomics
#
# Licensed under the Apache License, Version 2.0 (the "License").
"""Tests for fdp.auth."""

import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fdp import auth


def _make_jwt(exp_offset_sec):
    """Build an unsigned JWT whose exp is now + offset (negative = past)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": int(time.time()) + exp_offset_sec}).encode()
    ).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.sig"


class TestDecodeExp(unittest.TestCase):
    def test_valid_future_token(self):
        tok = _make_jwt(3600)
        exp = auth.decode_exp(tok)
        self.assertIsNotNone(exp)
        self.assertGreater(exp, time.time())

    def test_expired_token_still_decodes(self):
        exp = auth.decode_exp(_make_jwt(-3600))
        self.assertLess(exp, time.time())

    def test_no_exp_claim_returns_none(self):
        header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=")
        payload = base64.urlsafe_b64encode(b'{"sub":"x"}').rstrip(b"=")
        tok = f"{header.decode()}.{payload.decode()}.sig"
        self.assertIsNone(auth.decode_exp(tok))

    def test_malformed_base64_returns_none(self):
        self.assertIsNone(auth.decode_exp("a.!!!notbase64!!!.c"))

    def test_not_a_jwt_returns_none(self):
        self.assertIsNone(auth.decode_exp("file-token"))
        self.assertIsNone(auth.decode_exp(""))

    def test_non_string_returns_none(self):
        self.assertIsNone(auth.decode_exp(12345))
        self.assertIsNone(auth.decode_exp(None))


def _bearer_handle(name="d3d", pelican_root="pelican://test/fdp-d3d"):
    """A fake TokamakHandle exposing .schema.name / .pelican_root /
    .locators[*].auth, which is all auth.py reads."""
    auth_hint = SimpleNamespace(kind="bearer_token", env="BEARER_TOKEN")
    locator = SimpleNamespace(auth=auth_hint)
    schema = SimpleNamespace(name=name, pelican_root=pelican_root,
                             locators=[locator])
    return SimpleNamespace(schema=schema)


def _no_bearer_handle(name="mast"):
    locator = SimpleNamespace(auth=SimpleNamespace(kind="none", env=None))
    schema = SimpleNamespace(name=name, pelican_root=None, locators=[locator])
    return SimpleNamespace(schema=schema)


class TestGetValidToken(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name)
        (self.home / ".fdp").mkdir()
        home_patch = mock.patch.object(Path, "home", return_value=self.home)
        home_patch.start()
        self.addCleanup(home_patch.stop)
        os.environ.pop("BEARER_TOKEN", None)

    def _write_cache(self, name, token):
        cache_dir = self.home / ".fdp" / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{name}.token").write_text(token)

    def _write_legacy(self, token):
        (self.home / ".fdp" / "token").write_text(token)

    def test_no_bearer_auth_returns_none(self):
        self.assertIsNone(auth.get_valid_token(_no_bearer_handle()))

    def test_explicit_wins_verbatim(self):
        self.assertEqual(
            auth.get_valid_token(_bearer_handle(), explicit="xtok"), "xtok")

    def test_env_var_used_verbatim(self):
        os.environ["BEARER_TOKEN"] = "envtok"
        self.addCleanup(os.environ.pop, "BEARER_TOKEN", None)
        self.assertEqual(auth.get_valid_token(_bearer_handle()), "envtok")

    def test_fresh_cache_used(self):
        token = _make_jwt(3600)
        self._write_cache("d3d", token)
        self.assertEqual(auth.get_valid_token(_bearer_handle()), token)

    def test_expired_cache_skipped_falls_to_legacy(self):
        self._write_cache("d3d", _make_jwt(-10))
        legacy = _make_jwt(3600)
        self._write_legacy(legacy)
        self.assertEqual(auth.get_valid_token(_bearer_handle()), legacy)

    def test_expired_legacy_skipped(self):
        self._write_legacy(_make_jwt(-10))
        self.assertIsNone(auth.get_valid_token(_bearer_handle()))

    def test_all_empty_returns_none(self):
        self.assertIsNone(auth.get_valid_token(_bearer_handle()))
