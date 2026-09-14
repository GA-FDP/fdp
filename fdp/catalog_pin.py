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
"""`--catalog`: pinning a command to one published catalog.

The versioned store keeps every version of a shot, and a published
catalog records which version was latest at one moment. "Latest" is therefore a
lookup that moves, so a long run can read its early shots from one snapshot
and its later ones from the next. Naming one makes a run reproducible, and
`fdp run --catalog` is the form that needs nothing of the script it wraps.

The value reaches the command as `FDP_STORE_CATALOG` — an environment
variable because it has to survive `fork`, `spawn` and a worker on another
host, which no argument does.
"""

import sys

VAR = "FDP_STORE_CATALOG"
LATEST = "latest"


def _newest(store_root: str) -> str:
    """The newest catalog snapshot under `store_root`, or "".

    ptdata is imported lazily and is deliberately not a dependency of `fdp`:
    env composition is locator-driven, and a public device like MAST gets a
    clean environment with no store at all. The cost is that a mismatched
    pair is only discovered here, so the failure has to name the fix.
    """
    try:
        from ptdata import StoreIndex
    except ImportError as exc:
        sys.exit(
            "--catalog latest needs ptdata >= 2.8.0 to resolve one ({}). "
            "Either install it, or pass a snapshot by name.".format(exc)
        )
    return StoreIndex(store_root).current_snapshot


def is_latest(value) -> bool:
    """Whether `value` is the word rather than a catalog.

    Compared case-insensitively after stripping, because it arrives from a
    command line and `Latest` reaching a store as a literal snapshot name
    would be a confusing way to learn about case.
    """
    return bool(value) and value.strip().lower() == LATEST


def resolve_flag(value, store_root: str = "") -> str:
    """Turn a `--catalog` value into a concrete stamp.

    A name passes through **unverified**. Verifying it would duplicate a
    catalog read the first resolution performs anyway, and an unsatisfiable
    pin already fails loudly and by name at the first fetch — so checking
    here would buy an earlier error, not a safer one.

    The word `latest` is resolved now. It is never what reaches the
    environment: a pin meaning "whatever is newest when you read this" is
    not a pin, and would reintroduce one layer up exactly the drift this
    exists to remove.
    """
    if not is_latest(value):
        return value

    if not store_root:
        sys.exit(
            "--catalog latest needs a store to resolve against, and this "
            "device declares none (no FDP_STORE_ROOT). Name a snapshot "
            "explicitly, or drop --snapshot."
        )

    resolved = _newest(store_root)
    if not resolved:
        sys.exit(
            "--catalog latest found no catalog snapshot under {}. Check the "
            "store root, or name a snapshot explicitly.".format(store_root)
        )
    return resolved


def apply_flag(env: dict, value, store_root: str = "") -> dict:
    """Put the resolved pin into `env`, in place. Returns it.

    A `--catalog` the user did not pass leaves the environment alone,
    including any `FDP_STORE_CATALOG` they exported themselves.
    """
    if value:
        env[VAR] = resolve_flag(value, store_root or env.get("FDP_STORE_ROOT", ""))
    return env


def catalog_path(store_root: str) -> str:
    """The namespace path of the catalog directory under `store_root`.

    `fdp ls` addresses the origin by namespace path, not by the federation
    URL, so the scheme and host come off: a store at
    `pelican://osg-htc.org:443/fdp-d3d` has its catalog at `/fdp-d3d/catalog`.
    A bare local path is already a namespace path and passes through.
    """
    rest = store_root.split("://", 1)[-1] if "://" in store_root else store_root
    if "://" in store_root:
        # Drop host[:port]; what remains is the namespace.
        rest = "/" + rest.split("/", 1)[1] if "/" in rest else ""
    return rest.rstrip("/") + "/catalog"


def order_catalogs(names) -> list:
    """Catalog names from the catalog directory, newest first.

    Names are `catalog_<ISO-ish UTC stamp>`, for which lexical order IS
    chronological order -- the same property the resolver relies on to pick
    the newest, so this agrees with it by construction rather than by
    parsing dates a second way.

    Anything else in the directory is dropped rather than shown: a listing
    is not a menu of things that can be pinned unless everything in it can
    be.
    """
    return sorted((str(n) for n in names
                   if str(n).startswith("catalog_")), reverse=True)


def describe(env: dict) -> str:
    """One line saying which catalog a command would read from."""
    pinned = env.get(VAR, "")
    if pinned:
        return "{} (pinned by {})".format(pinned, VAR)

    root = env.get("FDP_STORE_ROOT", "")
    if not root:
        return "no versioned store configured for this device"

    resolved = _newest(root)
    if not resolved:
        return "no catalog found under {}".format(root)
    # Named as what it WOULD be, not as what it is: nothing is pinned, so a
    # run starting a moment later could legitimately resolve a newer one.
    return "{} (newest; a run would resolve and hold this)".format(resolved)
