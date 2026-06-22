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
