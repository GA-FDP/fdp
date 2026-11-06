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
        # Opaque (non-JWT) env tokens are trusted verbatim — we cannot inspect
        # their expiry, so we must not second-guess them.
        os.environ["BEARER_TOKEN"] = "envtok"
        self.addCleanup(os.environ.pop, "BEARER_TOKEN", None)
        self.assertEqual(auth.get_valid_token(_bearer_handle()), "envtok")

    def test_unexpired_jwt_env_var_used(self):
        tok = _make_jwt(3600)
        os.environ["BEARER_TOKEN"] = tok
        self.addCleanup(os.environ.pop, "BEARER_TOKEN", None)
        self.assertEqual(auth.get_valid_token(_bearer_handle()), tok)

    def test_expired_jwt_env_var_ignored_falls_to_cache(self):
        # A stale exported BEARER_TOKEN must not shadow a freshly cached token
        # (the trap that made `fdp login` appear to do nothing).
        os.environ["BEARER_TOKEN"] = _make_jwt(-10)
        self.addCleanup(os.environ.pop, "BEARER_TOKEN", None)
        fresh = _make_jwt(3600)
        self._write_cache("d3d", fresh)
        with self.assertWarns(UserWarning):
            self.assertEqual(auth.get_valid_token(_bearer_handle()), fresh)

    def test_expired_jwt_env_var_ignored_returns_none_without_fallback(self):
        os.environ["BEARER_TOKEN"] = _make_jwt(-10)
        self.addCleanup(os.environ.pop, "BEARER_TOKEN", None)
        with self.assertWarns(UserWarning):
            self.assertIsNone(auth.get_valid_token(_bearer_handle()))

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


# Verbatim from pelican 7.24.3 against an issuer that no longer knows the
# client id stored in ~/.config/pelican/credentials (2026-10-01, after the
# d3d origin's issuer was replaced).
_UNKNOWN_CLIENT = (
    "Failed to get a token: Failed to perform device code flow with URL "
    "https://origin:8000/api/v1.0/issuer/ns/fdp-d3d/device_authorization: "
    "oauth2: cannot fetch token: 401 Unauthorized\r\n"
    'Response: {"error":"invalid_client","error_description":"Unknown client"}'
    "\r\n")


class _FakePelican:
    """Stands in for _run_in_pty: answers `token get` from a script of
    results, and records every argv it is given."""

    def __init__(self, token_get_results, delete_returncode=0):
        self._token_get = list(token_get_results)
        self._delete_returncode = delete_returncode
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[1:4] == ["credentials", "prefix", "delete"]:
            return auth._PtyResult(returncode=self._delete_returncode,
                                   output="")
        return self._token_get.pop(0)

    def deletes(self):
        return [c for c in self.calls
                if c[1:4] == ["credentials", "prefix", "delete"]]

    def token_gets(self):
        return [c for c in self.calls
                if c[1:4] == ["credentials", "token", "get"]]


class TestStaleClientRegistration(unittest.TestCase):
    """The issuer forgets a client (its state was reset or replaced) while
    pelican still holds the old registration. pelican does not recover by
    itself, so login must drop that one registration and try again."""

    def _login(self, fake):
        with mock.patch.object(auth.shutil, "which",
                               return_value="/bin/pelican"), \
             mock.patch.object(auth, "_run_in_pty", side_effect=fake):
            return auth._pelican_get_token("pelican://host:443/fdp-d3d")

    def test_unknown_client_is_dropped_and_login_retried(self):
        token = _make_jwt(3600)
        fake = _FakePelican([
            auth._PtyResult(returncode=1, output=_UNKNOWN_CLIENT),
            auth._PtyResult(returncode=0, output=token),
        ])
        self.assertEqual(self._login(fake), token)
        self.assertEqual(
            fake.deletes(),
            [["/bin/pelican", "credentials", "prefix", "delete", "/fdp-d3d"]])
        self.assertEqual(len(fake.token_gets()), 2)

    def test_only_the_devices_own_prefix_is_dropped(self):
        fake = _FakePelican([
            auth._PtyResult(returncode=1, output=_UNKNOWN_CLIENT),
            auth._PtyResult(returncode=0, output=_make_jwt(3600)),
        ])
        with mock.patch.object(auth.shutil, "which",
                               return_value="/bin/pelican"), \
             mock.patch.object(auth, "_run_in_pty", side_effect=fake):
            auth._pelican_get_token("pelican://host:443/fdp-cmod/")
        self.assertEqual(fake.deletes()[0][4], "/fdp-cmod")

    def test_retries_once_then_gives_up(self):
        fake = _FakePelican([
            auth._PtyResult(returncode=1, output=_UNKNOWN_CLIENT),
            auth._PtyResult(returncode=1, output=_UNKNOWN_CLIENT),
        ])
        with self.assertRaises(auth.AuthError):
            self._login(fake)
        self.assertEqual(len(fake.deletes()), 1)
        self.assertEqual(len(fake.token_gets()), 2)

    def test_other_failures_leave_credentials_alone(self):
        fake = _FakePelican([
            auth._PtyResult(returncode=1,
                            output="Failed to get a token: connection refused"),
        ])
        with self.assertRaises(auth.AuthError):
            self._login(fake)
        self.assertEqual(fake.deletes(), [])
        self.assertEqual(len(fake.token_gets()), 1)

    def test_failed_drop_names_the_manual_command(self):
        fake = _FakePelican(
            [auth._PtyResult(returncode=1, output=_UNKNOWN_CLIENT)],
            delete_returncode=1)
        with self.assertRaises(auth.AuthError) as ctx:
            self._login(fake)
        self.assertIn("pelican credentials prefix delete /fdp-d3d",
                      str(ctx.exception))
        self.assertEqual(len(fake.token_gets()), 1)


class TestFreshLogin(unittest.TestCase):
    """pelican hands back its stored token until that token expires, so the
    only way to a full-lifetime token before then is to drop the stored
    login and consent again."""

    def _get(self, fake, **kwargs):
        with mock.patch.object(auth.shutil, "which",
                               return_value="/bin/pelican"), \
             mock.patch.object(auth, "_run_in_pty", side_effect=fake):
            return auth._pelican_get_token("pelican://host:443/fdp-d3d",
                                           **kwargs)

    def test_fresh_drops_the_stored_login_before_requesting(self):
        token = _make_jwt(86400)
        fake = _FakePelican([auth._PtyResult(returncode=0, output=token)])
        self.assertEqual(self._get(fake, fresh=True), token)
        self.assertEqual(
            [c[1:4] for c in fake.calls],
            [["credentials", "prefix", "delete"],
             ["credentials", "token", "get"]])
        self.assertEqual(fake.deletes()[0][4], "/fdp-d3d")

    def test_fresh_requests_nothing_when_the_drop_fails(self):
        fake = _FakePelican([], delete_returncode=1)
        with self.assertRaises(auth.AuthError):
            self._get(fake, fresh=True)
        self.assertEqual(fake.token_gets(), [])

    def test_plain_login_keeps_the_stored_login(self):
        fake = _FakePelican(
            [auth._PtyResult(returncode=0, output=_make_jwt(3600))])
        self._get(fake)
        self.assertEqual(fake.deletes(), [])

    def test_login_passes_fresh_through(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        fake = _FakePelican(
            [auth._PtyResult(returncode=0, output=_make_jwt(86400))])
        with mock.patch.object(Path, "home", return_value=Path(td.name)), \
             mock.patch.object(auth.shutil, "which",
                               return_value="/bin/pelican"), \
             mock.patch.object(auth, "_run_in_pty", side_effect=fake):
            auth.login(_bearer_handle(pelican_root="pelican://h:443/fdp-d3d"),
                       fresh=True)
        self.assertEqual(len(fake.deletes()), 1)


class TestFormatRemaining(unittest.TestCase):
    def test_hours_and_minutes(self):
        self.assertEqual(auth.format_remaining(2 * 3600 + 38 * 60 + 12),
                         "2 h 38 min")

    def test_under_an_hour(self):
        self.assertEqual(auth.format_remaining(38 * 60 + 59), "38 min")

    def test_days(self):
        self.assertEqual(auth.format_remaining(3 * 86400 + 4 * 3600 + 5),
                         "3 d 4 h")

    def test_under_a_minute(self):
        self.assertEqual(auth.format_remaining(42), "less than a minute")

    def test_expired(self):
        self.assertEqual(auth.format_remaining(-5), "expired")


class TestWarnIfExpiring(unittest.TestCase):
    """A job holds the token it started with, and nothing renews it mid-run.
    Starting work on a token with little time left is a failure the user
    only meets hours later, as a 401."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name)
        (self.home / ".fdp" / "cache").mkdir(parents=True)
        p = mock.patch.object(Path, "home", return_value=self.home)
        p.start()
        self.addCleanup(p.stop)
        self._saved = os.environ.pop("FDP_MIN_TOKEN_HOURS", None)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop("FDP_MIN_TOKEN_HOURS", None)
        if self._saved is not None:
            os.environ["FDP_MIN_TOKEN_HOURS"] = self._saved

    def _warnings(self, token):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            auth.warn_if_expiring(_bearer_handle(), token)
        return [str(w.message) for w in caught]

    def _cache(self, token):
        (self.home / ".fdp" / "cache" / "d3d.token").write_text(token)
        return token

    def test_short_login_token_warns_and_names_the_remedy(self):
        msgs = self._warnings(self._cache(_make_jwt(3600 + 30)))
        self.assertEqual(len(msgs), 1)
        self.assertIn("1 h 0 min", msgs[0])
        self.assertIn("fdp login --fresh --device d3d", msgs[0])

    def test_long_token_is_silent(self):
        self.assertEqual(self._warnings(self._cache(_make_jwt(10 * 3600))), [])

    def test_opaque_token_is_silent(self):
        self.assertEqual(self._warnings("not-a-jwt"), [])

    def test_threshold_is_configurable(self):
        os.environ["FDP_MIN_TOKEN_HOURS"] = "12"
        self.assertEqual(
            len(self._warnings(self._cache(_make_jwt(10 * 3600)))), 1)

    def test_zero_threshold_disables_it(self):
        os.environ["FDP_MIN_TOKEN_HOURS"] = "0"
        self.assertEqual(self._warnings(self._cache(_make_jwt(60))), [])

    def test_token_from_elsewhere_is_not_told_to_log_in(self):
        # $BEARER_TOKEN and --bearer-token win over the login cache, so
        # `fdp login --fresh` would not replace them.
        msgs = self._warnings(_make_jwt(3600 + 30))
        self.assertEqual(len(msgs), 1)
        self.assertIn("1 h 0 min", msgs[0])
        self.assertNotIn("fdp login", msgs[0])


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


JWT = "eyJhbGciOiJFUzI1NiJ9.eyJleHAiOjE3MDAwMDAwMDB9.c2lnbmF0dXJl"


class TestTokenFilter(unittest.TestCase):
    """pelican's output is shown to the user, but the token it ends with
    must never be."""

    def run_filter(self, *chunks):
        f = auth._TokenFilter()
        return b"".join(f.feed(c) for c in chunks) + f.finish()

    def test_json_token_line_dropped(self):
        out = self.run_filter(
            b"Visit https://example/device\r\n",
            json.dumps({"access_token": JWT, "expires_in": 3600}).encode()
            + b"\r\n")
        self.assertIn(b"https://example/device", out)
        self.assertNotIn(JWT.encode(), out)
        self.assertNotIn(b"access_token", out)

    def test_approval_url_with_two_dots_shown(self):
        # The real d3d approval URL has exactly two dots and no spaces, which
        # the old bare-token heuristic mistook for a JWT and hid.
        url = (b"https://fdp-d3d-origin.nationalresearchplatform.org:8000"
               b"/view/issuer/device?namespace=%2Ffdp-d3d&user_code=AB12-CD34")
        out = self.run_filter(
            b"please navigate to the following URL and approve the request:"
            b"\r\n\r\n", url + b"\r\n", JWT.encode() + b"\r\n")
        self.assertIn(url, out)
        self.assertNotIn(JWT.encode(), out)
        self.assertIsNone(auth._token_in_line(url.decode()))

    def test_bare_token_line_dropped(self):
        self.assertEqual(self.run_filter(JWT.encode() + b"\r\n"), b"")

    def test_token_split_across_reads_dropped(self):
        line = json.dumps({"access_token": JWT}).encode() + b"\n"
        out = self.run_filter(line[:10], line[10:30], line[30:])
        self.assertEqual(out, b"")

    def test_token_inside_other_text_replaced(self):
        out = self.run_filter(f"got token {JWT} ok\n".encode())
        self.assertEqual(out, b"got token [token hidden] ok\n")

    def test_prompt_without_newline_shown_when_idle(self):
        f = auth._TokenFilter()
        self.assertEqual(f.feed(b"Password: "), b"")
        self.assertEqual(f.idle(), b"Password: ")
        self.assertEqual(f.idle(), b"")

    def test_unfinished_token_held_when_idle(self):
        f = auth._TokenFilter()
        f.feed(b'{"access_token": "' + JWT[:20].encode())
        self.assertEqual(f.idle(), b"")
        f2 = auth._TokenFilter()
        f2.feed(JWT[:20].encode())
        self.assertEqual(f2.idle(), b"")


class TestRunInPtyHidesToken(unittest.TestCase):
    def test_token_captured_but_not_shown(self):
        script = (
            "import json;"
            "print('Enter the password: ', end='', flush=True);"
            "import time; time.sleep(0.5); print();"
            f"print(json.dumps({{'access_token': '{JWT}'}}))"
        )
        with tempfile.TemporaryFile() as err:
            with mock.patch.object(sys, "stderr", err):
                result = auth._run_in_pty([sys.executable, "-c", script])
            err.seek(0)
            shown = err.read()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(auth._extract_token(result.output), JWT)
        self.assertIn(b"Enter the password: ", shown)
        self.assertNotIn(JWT.encode(), shown)


class TestQuietTerminal(unittest.TestCase):
    """While pelican runs, our terminal must not echo what the user types
    (pelican's own terminal decides that) but Ctrl-C must still work."""

    def test_echo_off_signals_on_then_restored(self):
        import pty
        import termios
        master, slave = pty.openpty()
        try:
            before = termios.tcgetattr(slave)
            saved = auth._quiet_terminal(slave)
            during = termios.tcgetattr(slave)
            self.assertFalse(during[3] & termios.ECHO)
            self.assertFalse(during[3] & termios.ICANON)
            self.assertTrue(during[3] & termios.ISIG)
            auth._restore_terminal(slave, saved)
            self.assertEqual(termios.tcgetattr(slave), before)
        finally:
            os.close(master)
            os.close(slave)

    def test_not_a_terminal_returns_none(self):
        with tempfile.TemporaryFile() as f:
            self.assertIsNone(auth._quiet_terminal(f.fileno()))


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
