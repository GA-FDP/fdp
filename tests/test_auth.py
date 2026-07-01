# Copyright 2024 General Atomics
#
# Licensed under the Apache License, Version 2.0 (the "License").
"""Tests for fdp.auth."""

import base64
import json
import os
import sys
import tempfile
import time
import unittest
import warnings
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
        with self.assertWarns(UserWarning):
            self.assertIsNone(auth.get_valid_token(_bearer_handle()))

    def test_unusable_legacy_file_warns(self):
        self._write_legacy(_make_jwt(-10))
        with self.assertWarns(UserWarning):
            self.assertIsNone(auth.get_valid_token(_bearer_handle()))

    def test_all_empty_returns_none(self):
        self.assertIsNone(auth.get_valid_token(_bearer_handle()))

    def test_bearer_env_custom_name(self):
        hint = SimpleNamespace(kind="bearer_token", env="FDP_TOKEN_X")
        loc = SimpleNamespace(auth=hint)
        handle = SimpleNamespace(schema=SimpleNamespace(
            name="x", pelican_root="p", locators=[loc]))
        self.assertEqual(auth.bearer_env(handle), "FDP_TOKEN_X")

    def test_custom_env_var_used(self):
        os.environ["FDP_TOKEN_X"] = "ctok"
        self.addCleanup(os.environ.pop, "FDP_TOKEN_X", None)
        hint = SimpleNamespace(kind="bearer_token", env="FDP_TOKEN_X")
        loc = SimpleNamespace(auth=hint)
        handle = SimpleNamespace(schema=SimpleNamespace(
            name="x", pelican_root="p", locators=[loc]))
        self.assertEqual(auth.get_valid_token(handle), "ctok")


class TestLoginLogout(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name)
        (self.home / ".fdp").mkdir()
        p = mock.patch.object(Path, "home", return_value=self.home)
        p.start()
        self.addCleanup(p.stop)

    def test_login_writes_cache_0600(self):
        token = _make_jwt(3600)
        fake = auth._PtyResult(returncode=0,
                               output=json.dumps({"access_token": token}))
        with mock.patch.object(auth.shutil, "which", return_value="/bin/pelican"), \
             mock.patch.object(auth, "_run_in_pty", return_value=fake):
            result = auth.login(_bearer_handle())
        self.assertEqual(result.device, "d3d")
        self.assertEqual(result.scope, "read")
        cache = self.home / ".fdp" / "cache" / "d3d.token"
        self.assertEqual(cache.read_text(), token)
        self.assertEqual(oct(cache.stat().st_mode & 0o777), "0o600")

    def test_login_write_scope_passes_write_arg(self):
        token = _make_jwt(3600)
        fake = auth._PtyResult(returncode=0, output=token)
        with mock.patch.object(auth.shutil, "which", return_value="/bin/pelican"), \
             mock.patch.object(auth, "_run_in_pty", return_value=fake) as run:
            auth.login(_bearer_handle(), write=True)
        argv = run.call_args[0][0]
        self.assertIn("write", argv)
        self.assertNotIn("read", argv)
        self.assertEqual(argv[4], "write")

    def test_login_missing_pelican_raises_autherror(self):
        with mock.patch.object(auth.shutil, "which", return_value=None):
            with self.assertRaises(auth.AuthError):
                auth.login(_bearer_handle())

    def test_login_pelican_nonzero_raises_autherror(self):
        fake = auth._PtyResult(returncode=1, output="")
        with mock.patch.object(auth.shutil, "which", return_value="/bin/pelican"), \
             mock.patch.object(auth, "_run_in_pty", return_value=fake):
            with self.assertRaises(auth.AuthError):
                auth.login(_bearer_handle())

    def test_login_no_pelican_root_raises(self):
        h = _bearer_handle(pelican_root=None)
        with mock.patch.object(auth.shutil, "which", return_value="/bin/pelican"):
            with self.assertRaises(auth.AuthError):
                auth.login(h)

    def test_login_no_bearer_device_returns_none(self):
        self.assertIsNone(auth.login(_no_bearer_handle()))

    def test_logout_removes_cache(self):
        cache_dir = self.home / ".fdp" / "cache"
        cache_dir.mkdir(parents=True)
        (cache_dir / "d3d.token").write_text("x")
        self.assertTrue(auth.logout(_bearer_handle()))
        self.assertFalse((cache_dir / "d3d.token").exists())

    def test_logout_no_cache_returns_false(self):
        self.assertFalse(auth.logout(_bearer_handle()))


class TestExtractToken(unittest.TestCase):
    def test_json_access_token(self):
        self.assertEqual(
            auth._extract_token(json.dumps({"access_token": "a.b.c"})), "a.b.c")

    def test_json_token_key(self):
        self.assertEqual(
            auth._extract_token(json.dumps({"token": "a.b.c"})), "a.b.c")

    def test_bare_jwt_line(self):
        self.assertEqual(auth._extract_token("\na.b.c\n"), "a.b.c")

    def test_empty_returns_none(self):
        self.assertIsNone(auth._extract_token("   "))

    def test_access_token_beats_token(self):
        out = auth._extract_token(json.dumps(
            {"access_token": "a.b.c", "token": "x.y.z"}))
        self.assertEqual(out, "a.b.c")

    def test_interleaved_pty_stream_bare_jwt(self):
        # What a PTY run really looks like: warnings + device URL, then the
        # bare JWT printed last. The URL has >2 dots and must not be mistaken
        # for the token.
        stream = (
            "WARNING Failed to renew an expired token\r\n"
            "To approve, navigate to the following URL:\r\n"
            "https://origin.example.org:8000/api/v1.0/issuer/device?"
            "user_code=ABC\r\n"
            "eyJhbGc.eyJleHA.sig\r\n"
        )
        self.assertEqual(auth._extract_token(stream), "eyJhbGc.eyJleHA.sig")

    def test_interleaved_pty_stream_json_line(self):
        stream = (
            "WARNING something\r\n"
            '{"access_token":"a.b.c","token_type":"Bearer"}\r\n'
        )
        self.assertEqual(auth._extract_token(stream), "a.b.c")


class TestRunInPty(unittest.TestCase):
    """The regression guard for the login bug: pelican gates interactive
    token acquisition on stdout being a TTY, so _run_in_pty must give the
    child a real terminal on stdout (not a pipe)."""

    def test_child_sees_tty_on_stdout_and_output_captured(self):
        # Mimic pelican's gate: fail unless stdout is a TTY, else emit a JWT.
        script = (
            "import sys;"
            "sys.exit('must be run in a terminal') "
            "if not sys.stdout.isatty() else print('h.p.s')"
        )
        result = auth._run_in_pty([sys.executable, "-c", script])
        self.assertEqual(result.returncode, 0)
        self.assertIn("h.p.s", result.output)
        self.assertEqual(auth._extract_token(result.output), "h.p.s")

    def test_nonzero_returncode_propagated(self):
        result = auth._run_in_pty(
            [sys.executable, "-c", "import sys; sys.exit(3)"])
        self.assertEqual(result.returncode, 3)

    def test_missing_binary_raises_oserror(self):
        with self.assertRaises(OSError):
            auth._run_in_pty(["/nonexistent/pelican-xyz"])


class TestEnsureToken(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name)
        (self.home / ".fdp").mkdir()
        p = mock.patch.object(Path, "home", return_value=self.home)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("BEARER_TOKEN", None)
        os.environ.pop("FDP_NO_AUTO_LOGIN", None)
        self.addCleanup(os.environ.pop, "FDP_NO_AUTO_LOGIN", None)
        self.addCleanup(os.environ.pop, "BEARER_TOKEN", None)

    def test_returns_existing_valid_token_without_login(self):
        with mock.patch.object(auth, "login") as login_mock:
            out = auth.ensure_token(_bearer_handle(), explicit="tok")
        self.assertEqual(out, "tok")
        login_mock.assert_not_called()

    def test_no_bearer_device_is_noop(self):
        with mock.patch.object(auth, "login") as login_mock:
            self.assertIsNone(auth.ensure_token(_no_bearer_handle()))
        login_mock.assert_not_called()

    def test_opt_out_env_blocks_login(self):
        os.environ["FDP_NO_AUTO_LOGIN"] = "1"
        with mock.patch.object(auth, "login") as login_mock:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                self.assertIsNone(
                    auth.ensure_token(_bearer_handle(), interactive=True))
        login_mock.assert_not_called()
        self.assertEqual(
            [w for w in caught if issubclass(w.category, UserWarning)], [])

    def test_non_interactive_blocks_login(self):
        with mock.patch.object(auth, "login") as login_mock:
            with self.assertWarns(UserWarning):
                self.assertIsNone(
                    auth.ensure_token(_bearer_handle(), interactive=False))
        login_mock.assert_not_called()

    def test_interactive_acquires_and_reresolves(self):
        token = _make_jwt(3600)

        def fake_login(handle, write=False):
            (self.home / ".fdp" / "cache").mkdir(parents=True, exist_ok=True)
            (self.home / ".fdp" / "cache" / "d3d.token").write_text(token)
            return auth.CachedToken("d3d", "read", auth.decode_exp(token))

        with mock.patch.object(auth, "login", side_effect=fake_login):
            out = auth.ensure_token(_bearer_handle(), interactive=True)
        self.assertEqual(out, token)

    def test_login_failure_degrades_to_none(self):
        with mock.patch.object(auth, "login",
                               side_effect=auth.AuthError("nope")):
            with self.assertWarns(UserWarning):
                out = auth.ensure_token(_bearer_handle(), interactive=True)
        self.assertIsNone(out)
