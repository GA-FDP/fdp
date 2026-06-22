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
import time
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
