# Automated SciToken Acquisition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `fdp` CLI the ability to mint and refresh a SciToken via the `pelican` GitHub-OAuth flow, feeding it into the existing `BEARER_TOKEN` path — both via an explicit `fdp login`/`fdp logout` and via transparent auto-acquire on `fdp run`.

**Architecture:** A new `fdp/auth.py` module owns the whole credential lifecycle (resolution precedence, JWT-expiry decode, pelican shell-out, managed per-device cache). `environment.py` delegates token resolution to it; `cli.py` adds `login`/`logout` handlers and an `auto_login` flag that distinguishes `fdp run` (may acquire) from `fdp env` (never acquires).

**Tech Stack:** Python 3.11, stdlib only (`subprocess`, `base64`, `json`, `shutil`, `dataclasses`), `unittest` test suite discovered by `tests/testit.py`, run under `pixi`.

**Spec:** `docs/superpowers/specs/2026-06-22-fdp-scitoken-automation-design.md`

---

## File Structure

- **Create `fdp/auth.py`** — credential lifecycle: `decode_exp`, `get_valid_token`, `ensure_token`, `login`, `logout`, plus private helpers (`_bearer_env`, `_cache_path`, `_pelican_get_token`, `_extract_token`), `AuthError`, and `CachedToken`. Sole owner of pelican shell-out and cache I/O.
- **Modify `fdp/environment.py`** — `resolve_bearer_token` becomes a thin wrapper over `auth.get_valid_token`; `setup_environment` gains an `auto_login` keyword and routes through `auth.ensure_token` (when `auto_login=True`) or `auth.get_valid_token`.
- **Modify `fdp/cli.py`** — add `do_login`/`do_logout`, their argparse subparsers (`needs_env=False`), set `auto_login=True` on the `run` subparser, and thread `auto_login` through `main()`'s `setup_environment` call.
- **Create `tests/test_auth.py`** — unit tests for everything in `auth.py` (pelican always mocked).
- **Modify `tests/test_environment.py`** — update `test_bearer_token_from_file` to use a real unexpired JWT (legacy file is now expiry-checked).
- **Modify `tests/test_cli.py`** — add login/logout dispatch tests.

### Test invocation

All commands run from the repo root `/fusion/projects/dt/sammuli/fdp_dev/repos/fdp`:

- Single module: `pixi run -- python -m unittest discover -s tests -p 'test_auth.py' -v`
- Single test by name: `pixi run -- python -m unittest discover -s tests -p 'test_auth.py' -k <substring> -v`
- Full suite: `pixi run test`

`fdp` is already editable-installed in the pixi env, so `from fdp... import` works in tests.

---

## Task 1: Scaffold `fdp/auth.py` with `decode_exp`

**Files:**
- Create: `fdp/auth.py`
- Test: `tests/test_auth.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_auth.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fdp.auth'`.

- [ ] **Step 3: Write minimal implementation**

Create `fdp/auth.py`:

```python
# Copyright 2024 General Atomics
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Bearer-credential (SciToken) lifecycle for FDP.

Owns the entire token story behind four public functions
(get_valid_token / ensure_token / login / logout) so that environment.py
and cli.py never shell out to `pelican` or touch the credential cache
directly. Tokens are minted via the `pelican` client's GitHub-OAuth flow
and cached per-device as bare JWTs under ~/.fdp/cache/.
"""

import base64
import json


def decode_exp(token: str) -> "int | None":
    """Return a JWT's 'exp' claim (unix seconds), or None if the token is
    not a decodable JWT or carries no exp. No signature verification."""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        exp = payload.get("exp")
        return int(exp) if exp is not None else None
    except (IndexError, ValueError, TypeError, json.JSONDecodeError):
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_auth.py' -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add fdp/auth.py tests/test_auth.py
git commit -m "feat(auth): add fdp.auth module with decode_exp"
```

---

## Task 2: Token resolution precedence (`get_valid_token`)

**Files:**
- Modify: `fdp/auth.py`
- Test: `tests/test_auth.py`

The resolution order (spec Section 2): explicit arg → `$BEARER_TOKEN` (both verbatim) → managed cache (expiry-checked) → legacy `~/.fdp/token` (expiry-checked). A device with no `bearer_token` locator returns `None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auth.py`:

```python
from pathlib import Path
from unittest import mock
from types import SimpleNamespace


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
        import os
        import tempfile
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
        import os
        os.environ["BEARER_TOKEN"] = "envtok"
        self.assertEqual(auth.get_valid_token(_bearer_handle()), "envtok")

    def test_fresh_cache_used(self):
        self._write_cache("d3d", _make_jwt(3600))
        self.assertIsNotNone(auth.get_valid_token(_bearer_handle()))

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_auth.py' -k GetValidToken -v`
Expected: FAIL with `AttributeError: module 'fdp.auth' has no attribute 'get_valid_token'`.

- [ ] **Step 3: Write minimal implementation**

Append to `fdp/auth.py` (add `import os`, `import time` to the import block):

```python
import os
import time
from pathlib import Path

CACHE_MARGIN_SEC = 300


def _bearer_env(handle) -> "str | None":
    """The env-var name for this device's bearer auth, or None if the
    device declares no bearer_token locator."""
    for loc in handle.schema.locators:
        a = getattr(loc, "auth", None)
        if a is not None and getattr(a, "kind", None) == "bearer_token":
            return a.env or "BEARER_TOKEN"
    return None


def _is_unexpired(token: str, margin: int = CACHE_MARGIN_SEC) -> bool:
    exp = decode_exp(token)
    return exp is not None and exp >= time.time() + margin


def _cache_path(handle) -> Path:
    return Path.home() / ".fdp" / "cache" / f"{handle.schema.name}.token"


def _read_token_file(path) -> "str | None":
    try:
        tok = Path(path).read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None
    return tok or None


def get_valid_token(handle, explicit=None) -> "str | None":
    """First usable token by precedence, or None. Never interactive.

    explicit and $BEARER_TOKEN are trusted verbatim; file sources are
    only used when they decode to an unexpired JWT.
    """
    env_var = _bearer_env(handle)
    if env_var is None:
        return None
    if explicit:
        return explicit
    env_tok = os.environ.get(env_var)
    if env_tok:
        return env_tok
    cached = _read_token_file(_cache_path(handle))
    if cached and _is_unexpired(cached):
        return cached
    legacy = _read_token_file(Path.home() / ".fdp" / "token")
    if legacy and _is_unexpired(legacy):
        return legacy
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_auth.py' -v`
Expected: PASS (all `TestDecodeExp` + `TestGetValidToken`).

- [ ] **Step 5: Commit**

```bash
git add fdp/auth.py tests/test_auth.py
git commit -m "feat(auth): get_valid_token resolution precedence"
```

---

## Task 3: Pelican shell-out, `login`, `logout`

**Files:**
- Modify: `fdp/auth.py`
- Test: `tests/test_auth.py`

> **VERIFY FIRST (the spec's flagged open item).** Before writing code, confirm how `pelican credentials token get` surfaces the raw token. In an environment with a registered pelican client, run once **interactively**:
> `pelican credentials token get read pelican://osg-htc.org:443/fdp-d3d --json`
> Observe: (a) does the JWT appear on **stdout** (so it can be captured) while the OAuth device-code prompt goes to **stderr**? (b) what key holds it under `--json` (`access_token` vs `token`)? Adjust `_extract_token`'s key list and the `subprocess` wiring below to match. The implementation below assumes stdout carries the token and stderr carries the human prompt; if pelican only writes the token into its own store, capture it via `--json` parsing of `pelican credentials print` instead and note the deviation in a code comment.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auth.py`:

```python
import subprocess


class TestLoginLogout(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name)
        (self.home / ".fdp").mkdir()
        p = mock.patch.object(Path, "home", return_value=self.home)
        p.start()
        self.addCleanup(p.stop)

    def test_login_writes_cache_0600(self):
        token = _make_jwt(3600)
        fake = SimpleNamespace(returncode=0, stdout=json.dumps(
            {"access_token": token}), stderr="")
        with mock.patch.object(auth.shutil, "which", return_value="/bin/pelican"), \
             mock.patch.object(auth.subprocess, "run", return_value=fake):
            result = auth.login(_bearer_handle())
        self.assertEqual(result.device, "d3d")
        self.assertEqual(result.scope, "read")
        cache = self.home / ".fdp" / "cache" / "d3d.token"
        self.assertEqual(cache.read_text(), token)
        self.assertEqual(oct(cache.stat().st_mode & 0o777), "0o600")

    def test_login_write_scope_passes_write_arg(self):
        token = _make_jwt(3600)
        fake = SimpleNamespace(returncode=0, stdout=token, stderr="")
        with mock.patch.object(auth.shutil, "which", return_value="/bin/pelican"), \
             mock.patch.object(auth.subprocess, "run", return_value=fake) as run:
            auth.login(_bearer_handle(), write=True)
        argv = run.call_args[0][0]
        self.assertIn("write", argv)
        self.assertNotIn("read", argv)

    def test_login_missing_pelican_raises_autherror(self):
        with mock.patch.object(auth.shutil, "which", return_value=None):
            with self.assertRaises(auth.AuthError):
                auth.login(_bearer_handle())

    def test_login_pelican_nonzero_raises_autherror(self):
        fake = SimpleNamespace(returncode=1, stdout="", stderr="boom")
        with mock.patch.object(auth.shutil, "which", return_value="/bin/pelican"), \
             mock.patch.object(auth.subprocess, "run", return_value=fake):
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_auth.py' -k 'Login or Extract' -v`
Expected: FAIL with `AttributeError` for `auth.AuthError` / `auth.login`.

- [ ] **Step 3: Write minimal implementation**

Add `import shutil`, `import subprocess` to the imports, and `from dataclasses import dataclass` near the top. Append to `fdp/auth.py`:

```python
import shutil
import subprocess
from dataclasses import dataclass


class AuthError(Exception):
    """Token acquisition failed (pelican missing, flow aborted, etc.)."""


@dataclass
class CachedToken:
    """In-memory result of login() for the CLI summary. NOT the on-disk
    format — the cache file holds the bare JWT."""
    device: str
    scope: str
    exp: "int | None"


def _extract_token(stdout: str) -> "str | None":
    """Pull the raw JWT out of pelican's output. Handles --json dict,
    a bare JSON string, or a bare JWT printed on its own line."""
    text = stdout.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("access_token", "token"):
                v = data.get(key)
                if isinstance(v, str) and v:
                    return v
        elif isinstance(data, str) and data:
            return data
    except (ValueError, TypeError):
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.count(".") == 2 and " " not in line:
            return line
    return None


def _pelican_get_token(pelican_root: str, *, write: bool = False) -> str:
    """Run the pelican OAuth flow and return the raw JWT. stderr is
    inherited so the user sees the device-code/browser prompt; stdout is
    captured for the token."""
    pelican = shutil.which("pelican")
    if pelican is None:
        raise AuthError("pelican client not found in environment")
    scope = "write" if write else "read"
    cmd = [pelican, "credentials", "token", "get", scope, pelican_root,
           "--json"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    except OSError as exc:
        raise AuthError(f"failed to invoke pelican: {exc}")
    if proc.returncode != 0:
        raise AuthError("pelican token request failed "
                        f"(exit {proc.returncode})")
    token = _extract_token(proc.stdout)
    if not token:
        raise AuthError("could not parse a token from pelican output")
    return token


def _write_cache(handle, token: str) -> None:
    path = _cache_path(handle)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".token.tmp")
    tmp.write_text(token)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def login(handle, *, write: bool = False) -> "CachedToken | None":
    """Mint a fresh token via pelican and cache it. Returns None when the
    device declares no bearer auth (a no-op the CLI reports friendlily)."""
    env_var = _bearer_env(handle)
    if env_var is None:
        return None
    pelican_root = handle.schema.pelican_root
    if not pelican_root:
        raise AuthError(
            f"device '{handle.schema.name}' has no pelican_root; "
            "cannot mint a token")
    token = _pelican_get_token(pelican_root, write=write)
    _write_cache(handle, token)
    return CachedToken(device=handle.schema.name,
                       scope="write" if write else "read",
                       exp=decode_exp(token))


def logout(handle) -> bool:
    """Delete the device's managed cache file. Returns whether one was
    removed."""
    path = _cache_path(handle)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_auth.py' -v`
Expected: PASS (all auth tests so far).

- [ ] **Step 5: Commit**

```bash
git add fdp/auth.py tests/test_auth.py
git commit -m "feat(auth): pelican shell-out, login, logout"
```

---

## Task 4: `ensure_token` + auto-login gating

**Files:**
- Modify: `fdp/auth.py`
- Test: `tests/test_auth.py`

Gating (spec Section 4): auto-acquire fires only when no usable token exists **and** `FDP_NO_AUTO_LOGIN` is unset **and** the session is interactive (stdin **and** stderr are TTYs, unless `interactive` is forced).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_auth.py`:

```python
class TestEnsureToken(unittest.TestCase):
    def setUp(self):
        import tempfile, os
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.home = Path(self._td.name)
        (self.home / ".fdp").mkdir()
        p = mock.patch.object(Path, "home", return_value=self.home)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("BEARER_TOKEN", None)
        os.environ.pop("FDP_NO_AUTO_LOGIN", None)

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
        import os
        os.environ["FDP_NO_AUTO_LOGIN"] = "1"
        with mock.patch.object(auth, "login") as login_mock:
            self.assertIsNone(
                auth.ensure_token(_bearer_handle(), interactive=True))
        login_mock.assert_not_called()

    def test_non_interactive_blocks_login(self):
        with mock.patch.object(auth, "login") as login_mock:
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_auth.py' -k EnsureToken -v`
Expected: FAIL with `AttributeError: module 'fdp.auth' has no attribute 'ensure_token'`.

- [ ] **Step 3: Write minimal implementation**

Add `import sys`, `import warnings` to imports. Append to `fdp/auth.py`:

```python
import sys
import warnings


def _auto_login_allowed(interactive) -> bool:
    if os.environ.get("FDP_NO_AUTO_LOGIN"):
        return False
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stderr.isatty()
    return bool(interactive)


def ensure_token(handle, explicit=None, *, interactive=None) -> "str | None":
    """get_valid_token(); if nothing usable and auto-login is allowed +
    possible, run login() and re-resolve. Warns and returns None when it
    cannot acquire."""
    tok = get_valid_token(handle, explicit)
    if tok is not None:
        return tok
    if _bearer_env(handle) is None:
        return None
    if not _auto_login_allowed(interactive):
        warnings.warn("No valid BEARER_TOKEN found; run `fdp login`.")
        return None
    try:
        login(handle)
    except AuthError as exc:
        warnings.warn(f"Automatic login failed: {exc}. Run `fdp login`.")
        return None
    return get_valid_token(handle, explicit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_auth.py' -v`
Expected: PASS (entire `test_auth.py`).

- [ ] **Step 5: Commit**

```bash
git add fdp/auth.py tests/test_auth.py
git commit -m "feat(auth): ensure_token with auto-login gating"
```

---

## Task 5: Integrate into `environment.py`

**Files:**
- Modify: `fdp/environment.py` (`resolve_bearer_token`, `setup_environment`)
- Modify: `tests/test_environment.py` (`test_bearer_token_from_file`)
- Test: `tests/test_environment.py`

Behavior change: the legacy `~/.fdp/token` is now expiry-checked, so the existing test's non-JWT `"file-token"` would no longer be accepted. Update it to a real unexpired JWT.

- [ ] **Step 1: Update the failing test**

In `tests/test_environment.py`, add this helper near the top (after imports):

```python
import base64 as _b64, json as _json, time as _time

def _unexpired_jwt():
    h = _b64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    p = _b64.urlsafe_b64encode(
        _json.dumps({"exp": int(_time.time()) + 3600}).encode()
    ).rstrip(b"=").decode()
    return f"{h}.{p}.sig"
```

Replace the body of `test_bearer_token_from_file` with:

```python
    def test_bearer_token_from_file(self):
        token = _unexpired_jwt()
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            (home / ".fdp").mkdir()
            (home / ".fdp" / "token").write_text(token + "\n")
            with mock.patch.object(Path, "home", return_value=home):
                os.environ.pop("BEARER_TOKEN", None)
                setup_environment()
            self.assertEqual(os.environ["BEARER_TOKEN"], token)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_environment.py' -k bearer_token_from_file -v`
Expected: FAIL — current `setup_environment` reads the file verbatim but the new resolution path isn't wired yet (or passes for the wrong reason). Proceed to wire the implementation.

- [ ] **Step 3: Write the implementation**

In `fdp/environment.py`, add the import near the top:

```python
from . import auth
```

Replace the entire `resolve_bearer_token` function with a thin compat wrapper:

```python
def resolve_bearer_token(handle, bearer_token=None) -> "str | None":
    """Resolve a usable bearer token for a device, or None when the device
    declares no bearer auth or nothing usable is found. Delegates to
    fdp.auth; never triggers an interactive flow."""
    return auth.get_valid_token(handle, explicit=bearer_token)
```

Replace the tail of `setup_environment` (the `token = resolve_bearer_token(...)` block) and its signature:

```python
def setup_environment(
    device: str | None = None,
    bearer_token: str | None = None,
    *,
    auto_login: bool = False,
    **overrides,
) -> None:
    """Populate os.environ with FDP variables for the active tokamak.

    Env emission is locator-driven. When auto_login is True (set by
    `fdp run`), a missing/expired token triggers the interactive
    `fdp login` flow subject to TTY / FDP_NO_AUTO_LOGIN gating. Mutates
    os.environ in place. Safe to call repeatedly.
    """
    handle = _resolve_device_handle(device)
    apply_environment(build_device_config(handle), os.environ)

    for key, value in overrides.items():
        os.environ[key] = str(value)

    if auto_login:
        token = auth.ensure_token(handle, explicit=bearer_token)
    else:
        token = auth.get_valid_token(handle, explicit=bearer_token)

    if token is not None:
        env_var = auth._bearer_env(handle) or "BEARER_TOKEN"
        os.environ[env_var] = token
    elif auth._bearer_env(handle) is not None and not auto_login:
        warnings.warn("No valid BEARER_TOKEN found; run `fdp login`.")
```

(The old `resolve_bearer_token(handle, bearer_token)` call and its `if token is not None` block are fully replaced by the above. `ensure_token` already warns on the auto_login path, so the `elif` only warns on the non-auto path to avoid double warnings.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_environment.py' -v`
Expected: PASS, including the updated `test_bearer_token_from_file` and the existing `TestMastCleanEnv::test_no_bearer_token_and_no_warning` (MAST has no bearer auth → no warning).

- [ ] **Step 5: Commit**

```bash
git add fdp/environment.py tests/test_environment.py
git commit -m "feat(env): route bearer-token resolution through fdp.auth"
```

---

## Task 6: CLI wiring — `fdp login` / `fdp logout` + `auto_login`

**Files:**
- Modify: `fdp/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
class TestCliLoginLogout(unittest.TestCase):
    def test_login_dispatches_to_auth_login(self):
        from fdp import cli, auth
        ep = _make_catalog_ep("d3d", _D3D_TEST_YAML)
        ct = auth.CachedToken(device="d3d", scope="read", exp=None)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "login"]))
            stack.enter_context(mock.patch(
                "fdp.catalog.entry_points", return_value=[ep]))
            from fdp.catalog import catalog as _cat
            _cat._cache = None
            login_mock = stack.enter_context(
                mock.patch.object(cli.auth, "login", return_value=ct))
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main()
            login_mock.assert_called_once()
            self.assertEqual(login_mock.call_args.kwargs.get("write"), False)

    def test_login_write_flag(self):
        from fdp import cli, auth
        ep = _make_catalog_ep("d3d", _D3D_TEST_YAML)
        ct = auth.CachedToken(device="d3d", scope="write", exp=None)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "login", "--write"]))
            stack.enter_context(mock.patch(
                "fdp.catalog.entry_points", return_value=[ep]))
            from fdp.catalog import catalog as _cat
            _cat._cache = None
            login_mock = stack.enter_context(
                mock.patch.object(cli.auth, "login", return_value=ct))
            with redirect_stdout(io.StringIO()):
                cli.main()
            self.assertEqual(login_mock.call_args.kwargs.get("write"), True)

    def test_logout_dispatches(self):
        from fdp import cli
        ep = _make_catalog_ep("d3d", _D3D_TEST_YAML)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "logout"]))
            stack.enter_context(mock.patch(
                "fdp.catalog.entry_points", return_value=[ep]))
            from fdp.catalog import catalog as _cat
            _cat._cache = None
            logout_mock = stack.enter_context(
                mock.patch.object(cli.auth, "logout", return_value=True))
            with redirect_stdout(io.StringIO()):
                cli.main()
            logout_mock.assert_called_once()

    def test_run_sets_auto_login_true(self):
        from fdp import cli
        ep = _make_catalog_ep("d3d", _D3D_TEST_YAML)
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                sys, "argv", ["fdp", "run", "true"]))
            stack.enter_context(mock.patch(
                "fdp.catalog.entry_points", return_value=[ep]))
            from fdp.catalog import catalog as _cat
            _cat._cache = None
            setup_mock = stack.enter_context(
                mock.patch.object(cli, "setup_environment"))
            stack.enter_context(mock.patch.object(
                cli.subprocess, "run",
                return_value=SimpleNamespace(returncode=0)))
            with self.assertRaises(SystemExit):
                cli.main()
            self.assertEqual(
                setup_mock.call_args.kwargs.get("auto_login"), True)
```

Add `from types import SimpleNamespace` to the imports of `tests/test_cli.py` if not present.

- [ ] **Step 2: Run test to verify it fails**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_cli.py' -k LoginLogout -v`
Expected: FAIL — `cli.auth` does not exist / no `login` subcommand (argparse error).

- [ ] **Step 3: Write the implementation**

In `fdp/cli.py`:

Add to the imports block:

```python
from datetime import datetime, timezone

from . import auth
```

Add the two handlers (near `do_env`):

```python
def do_login(args) -> None:
    handle = _resolve_device_handle(args.default_device)
    try:
        result = auth.login(handle, write=args.write)
    except auth.AuthError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        sys.exit(1)
    if result is None:
        print(f"Device '{handle.schema.name}' needs no bearer token.")
        return
    if result.exp:
        when = datetime.fromtimestamp(
            result.exp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    else:
        when = "unknown"
    print(f"Logged in to {result.device} ({result.scope}); "
          f"token valid until {when}.")


def do_logout(args) -> None:
    handle = _resolve_device_handle(args.default_device)
    removed = auth.logout(handle)
    print("Removed cached token."
          if removed else "No cached token to remove.")
```

In `build_parser`, register the subcommands (after the `env` parser) and mark the `run` parser as auto-login:

```python
    p_login = sub.add_parser("login",
                             help="Acquire/refresh a bearer token via pelican")
    p_login.add_argument("--write", action="store_true",
                         help="Request a write-scoped token (default: read).")
    p_login.set_defaults(func=do_login, needs_env=False)

    p_logout = sub.add_parser("logout",
                              help="Delete the cached bearer token")
    p_logout.set_defaults(func=do_logout, needs_env=False)
```

Change the `run` parser's defaults from `p_run.set_defaults(func=do_run)` to:

```python
    p_run.set_defaults(func=do_run, auto_login=True)
```

In `main()`, thread `auto_login` into the `setup_environment` call:

```python
    if getattr(args, "needs_env", True):
        setup_environment(
            device=args.default_device,
            bearer_token=args.bearer_token or None,
            auto_login=getattr(args, "auto_login", False),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_cli.py' -v`
Expected: PASS (existing CLI tests + the four new ones).

- [ ] **Step 5: Commit**

```bash
git add fdp/cli.py tests/test_cli.py
git commit -m "feat(cli): fdp login/logout and auto-login on fdp run"
```

---

## Task 7: Parity guard, full suite, and docs

**Files:**
- Modify: `fdp/README.md` (or repo `CLAUDE.md`) — document `fdp login`/`fdp logout` and `FDP_NO_AUTO_LOGIN`.

- [ ] **Step 1: Run the env-parity guard**

Run: `pixi run -- python -m unittest discover -s tests -p 'test_env_parity.py' -v`
Expected: PASS — the D3D env-var fixture is unchanged (token plumbing must not perturb it).

- [ ] **Step 2: Run the full suite**

Run: `pixi run test`
Expected: PASS — all modules green.

- [ ] **Step 3: Document the feature**

Append a short "Authentication" section to `fdp/README.md`:

```markdown
## Authentication

`fdp login` mints a SciToken via the pelican GitHub-OAuth flow and caches it
under `~/.fdp/cache/<device>.token`. `fdp run ...` auto-acquires a token when
none is valid and the session is interactive; set `FDP_NO_AUTO_LOGIN=1` to
disable that (e.g. in batch jobs). `fdp logout` deletes the cached token.
`fdp env` never launches the flow. Resolution order: `-t/--bearer-token`,
then `$BEARER_TOKEN`, then the managed cache, then the legacy `~/.fdp/token`.
```

- [ ] **Step 4: Commit**

```bash
git add fdp/README.md
git commit -m "docs: document fdp login/logout and auto-login"
```

---

## Self-Review notes

- **Spec coverage:** Section 1 surface → Tasks 1–4; Section 2 precedence → Task 2; Section 3 acquisition + bare-JWT cache → Task 3; Section 4 CLI + gating + `fdp env` exception → Tasks 4 & 6; Section 5 error handling → Tasks 3–4 (missing pelican, non-zero exit, no `pelican_root`, corrupt file via `decode_exp` returning None, atomic write); Section 6 testing → Tasks 1–7.
- **Flagged open item** (pelican output format) is the explicit VERIFY block in Task 3, with a robust `_extract_token` covering JSON-dict, JSON-string, and bare-JWT cases.
- **Behavior change** (legacy file now expiry-checked) is handled in Task 5 by updating `test_bearer_token_from_file`.
- **Naming consistency:** `auth._bearer_env`, `auth.get_valid_token`, `auth.ensure_token`, `auth.login`, `auth.logout`, `auth.CachedToken`, `auth.AuthError` are referenced identically across environment.py, cli.py, and tests.
