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
import os
import select
import shutil
import subprocess
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path


def decode_exp(token: str) -> "int | None":
    """Return a JWT's 'exp' claim (unix seconds), or None if the token is
    not a decodable JWT or carries no exp. No signature verification."""
    try:
        payload_b64 = token.split(".")[1]
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        exp = payload.get("exp")
        return int(exp) if exp is not None else None
    except (IndexError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return None


CACHE_MARGIN_SEC = 300


def bearer_env(handle) -> "str | None":
    """The env-var name for this device's bearer auth, or None if the
    device declares no bearer_token locator."""
    for loc in handle.schema.locators:
        a = getattr(loc, "auth", None)
        if a is not None and getattr(a, "kind", None) == "bearer_token":
            return a.env or "BEARER_TOKEN"
    return None


def _is_unexpired(token: str, margin: int = CACHE_MARGIN_SEC) -> bool:
    """True if the token decodes to a JWT whose exp is at least `margin` seconds in the future."""
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

    An empty or None `explicit` is treated as 'no override'.
    """
    env_var = bearer_env(handle)
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
    legacy_path = Path.home() / ".fdp" / "token"
    legacy = _read_token_file(legacy_path)
    if legacy:
        if _is_unexpired(legacy):
            return legacy
        warnings.warn(
            f"{legacy_path} exists but is expired or not a valid JWT; "
            "run `fdp login` to refresh.")
    return None


class AuthError(Exception):
    """Token acquisition failed (pelican missing, flow aborted, etc.)."""


@dataclass
class CachedToken:
    """In-memory result of login() for the CLI summary. NOT the on-disk
    format -- the cache file holds the bare JWT."""
    device: str
    scope: str
    exp: "int | None"


def _token_from_json(text: str) -> "str | None":
    """If *text* is a JSON dict carrying access_token/token, or a bare JSON
    string, return that token; otherwise None."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        for key in ("access_token", "token"):
            v = data.get(key)
            if isinstance(v, str) and v:
                return v
    elif isinstance(data, str) and data:
        return data
    return None


def _extract_token(stdout: str) -> "str | None":
    """Pull the raw JWT out of pelican's output.

    Handles pure --json output (the whole stream is one JSON dict), and also
    the interleaved stream produced when pelican runs under a PTY: WARNING
    lines and the device-approval URL precede the token, which is printed
    last. We therefore scan bottom-up, accepting either a per-line JSON dict
    or a bare JWT (three dot-separated, space-free segments)."""
    text = stdout.strip()
    if not text:
        return None
    tok = _token_from_json(text)
    if tok:
        return tok
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        tok = _token_from_json(line)
        if tok:
            return tok
        if (line.count(".") == 2 and " " not in line
                and "{" not in line and '"' not in line):
            return line
    return None


@dataclass
class _PtyResult:
    """Exit status and combined terminal output of a PTY-hosted subprocess."""
    returncode: int
    output: str


def _run_in_pty(cmd) -> "_PtyResult":
    """Run *cmd* with stdin/stdout/stderr attached to a pseudo-terminal.

    pelican gates *interactive* token acquisition on its **stdout** being a
    TTY: once the cached refresh token expires it must re-consent via the
    device-code/browser flow, and it refuses to start that flow when stdout
    is a pipe. Capturing stdout with an ordinary pipe (the obvious approach)
    therefore makes re-consent impossible. So we give pelican a real terminal
    on all three fds. The child's combined output is streamed to our stderr
    (the user sees the approval URL and any prompt live) while also being
    captured, so the JWT can be parsed once the flow completes. When our own
    stdin is a TTY it is forwarded to the child so the user can answer a
    prompt. Silent renewal (valid refresh token) simply prints the token and
    returns the same way -- no terminal interaction needed.
    """
    import pty  # POSIX-only; the whole FDP stack is Linux.

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            cmd, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
    except OSError:
        os.close(master)
        os.close(slave)
        raise
    os.close(slave)

    try:
        err_fd = sys.stderr.fileno()
    except (OSError, ValueError, AttributeError):
        err_fd = None
    try:
        stdin_fd = sys.stdin.fileno()
        watch_stdin = sys.stdin.isatty()
    except (OSError, ValueError, AttributeError):
        stdin_fd, watch_stdin = None, False

    chunks = []
    try:
        while True:
            watch = [master] + ([stdin_fd] if watch_stdin else [])
            try:
                ready, _, _ = select.select(watch, [], [], 0.2)
            except (OSError, ValueError):
                break
            if master in ready:
                try:
                    data = os.read(master, 4096)
                except OSError:  # slave closed -> EIO on Linux
                    data = b""
                if not data:
                    break
                chunks.append(data)
                if err_fd is not None:
                    try:
                        os.write(err_fd, data)
                    except OSError:
                        pass
            if watch_stdin and stdin_fd in ready:
                try:
                    fwd = os.read(stdin_fd, 4096)
                except OSError:
                    fwd = b""
                if fwd:
                    try:
                        os.write(master, fwd)
                    except OSError:
                        pass
    finally:
        os.close(master)
    proc.wait()
    return _PtyResult(
        returncode=proc.returncode,
        output=b"".join(chunks).decode("utf-8", errors="replace"))


def _pelican_get_token(pelican_root: str, *, write: bool = False) -> str:
    """Run the pelican OAuth flow and return the raw JWT.

    pelican is run under a pseudo-terminal (see _run_in_pty) so its
    stdout-is-a-TTY gate is satisfied and a fresh interactive consent can
    proceed when the cached refresh token has expired -- the case where
    piping stdout would make `fdp login` fail with "must be run in a
    terminal".
    """
    pelican = shutil.which("pelican")
    if pelican is None:
        raise AuthError("pelican client not found in environment")
    scope = "write" if write else "read"
    cmd = [pelican, "credentials", "token", "get", scope, pelican_root,
           "--json"]
    try:
        result = _run_in_pty(cmd)
    except OSError as exc:
        raise AuthError(f"failed to invoke pelican: {exc}")
    if result.returncode != 0:
        raise AuthError(
            f"pelican token request failed (exit {result.returncode})")
    token = _extract_token(result.output)
    if not token:
        raise AuthError("could not parse a token from pelican output")
    return token


def _write_cache(handle, token: str) -> None:
    path = _cache_path(handle)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    tmp = path.with_suffix(".token.tmp")
    # Create with 0o600 from the start so the token is never briefly
    # world-readable under a permissive umask. O_TRUNC overwrites any
    # stale tmp from a crashed run; the explicit chmod fixes the mode of
    # such a pre-existing file (O_CREAT won't change an existing file's mode).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def login(handle, *, write: bool = False) -> "CachedToken | None":
    """Mint a fresh token via pelican and cache it. Returns None when the
    device declares no bearer auth (a no-op the CLI reports friendlily)."""
    env_var = bearer_env(handle)
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


def _auto_login_allowed(interactive) -> bool:
    if os.environ.get("FDP_NO_AUTO_LOGIN"):
        return False
    if interactive is None:
        interactive = sys.stdin.isatty() and sys.stderr.isatty()
    return bool(interactive)


def ensure_token(handle, explicit=None, *, interactive=None) -> "str | None":
    """get_valid_token(); if nothing usable and auto-login is allowed +
    possible, run login() and re-resolve. Warns and returns None when it
    cannot acquire. interactive=None auto-detects via TTY; callers may
    force True/False (e.g. `fdp env` passes False)."""
    tok = get_valid_token(handle, explicit)
    if tok is not None:
        return tok
    if bearer_env(handle) is None:
        return None
    if not _auto_login_allowed(interactive):
        if not os.environ.get("FDP_NO_AUTO_LOGIN"):
            warnings.warn("No valid BEARER_TOKEN found; run `fdp login`.")
        return None
    try:
        login(handle)
    except AuthError as exc:
        warnings.warn(f"Automatic login failed: {exc}. Run `fdp login`.")
        return None
    return get_valid_token(handle, explicit)
