# Copyright 2024 General Atomics
#
# Licensed under the Apache License, Version 2.0 (the "License").
"""Tests for fdp.auth."""

import base64
import json
import time
import unittest

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
