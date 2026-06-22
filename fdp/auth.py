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
import shutil
import subprocess
import time
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


def _bearer_env(handle) -> "str | None":
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


class AuthError(Exception):
    """Token acquisition failed (pelican missing, flow aborted, etc.)."""


@dataclass
class CachedToken:
    """In-memory result of login() for the CLI summary. NOT the on-disk
    format -- the cache file holds the bare JWT."""
    device: str
    scope: str
    exp: "int | None"


def _extract_token(stdout: str) -> "str | None":
    """Pull the raw JWT out of pelican's output. Handles a bare JWT printed
    on its own line (the observed real behavior), and also a --json dict or
    bare JSON string for robustness across pelican versions."""
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
    """Run the pelican OAuth flow and return the raw JWT.

    pelican requires a TTY: stdin and stderr are inherited so the user sees
    the device-code/browser prompt and can answer any password prompt; only
    stdout (which carries the bare token) is captured.
    """
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
        raise AuthError(
            f"pelican token request failed (exit {proc.returncode})")
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
