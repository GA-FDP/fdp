# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""PtDataResolver — reads the Pelican-hosted PTData JSON index.

The index files mirror the format consumed by libfdpio's C plugin. Each
shot is at:

    {index_dir}/{shot // 100}/{shot}.json

with content:

    {
      "shot": <int>,
      "pointname_ext": { "POINTNAME": ".EXT", ... },
      "ext_location":  { ".EXT": "pelican://.../shot.EXT", ... }
    }

Lookup is `pointname (uppercased) → ext via pointname_ext → URL via
ext_location`.

Network fetch uses `ptdata._core.read_remote_file`, which wraps the same
libfdpio C++ engine the C plugin uses (handles Pelican URLs + BEARER_TOKEN
natively). `ptdata._core` is imported lazily inside `_fetch_index` so this
module imports cleanly even when ptdata is absent.
"""

import json
import os


class PtDataResolver:
    """Resolver for PtDataIndexedLocator. Caches the per-shot index in
    process memory; first access per shot does network I/O."""

    def __init__(self, model):
        self.model = model
        self._index_cache: dict[int, dict] = {}

    def resolve(self, shot: int, pointname: str) -> "str | None":
        """Return the shotfile URL for `(shot, pointname)`, or None if no
        such pointname is indexed for this shot."""
        self._check_auth()
        if shot not in self._index_cache:
            self._index_cache[shot] = self._fetch_index(shot)
        idx = self._index_cache[shot]
        ext = idx.get("pointname_ext", {}).get(pointname.upper())
        if ext is None:
            return None
        return idx.get("ext_location", {}).get(ext)

    def _index_url(self, shot: int) -> str:
        return f"{self.model.index_dir}/{shot // 100}/{shot}.json"

    def _fetch_index(self, shot: int) -> dict:
        # Lazy import so fdp.resolvers imports without requiring ptdata
        # at module-load time.
        from ptdata import _core
        url = self._index_url(shot)
        raw = _core.read_remote_file(url)
        return json.loads(raw)

    def _check_auth(self):
        auth = self.model.auth
        if auth and auth.env and not os.environ.get(auth.env):
            raise RuntimeError(
                f"PtData auth: env var {auth.env!r} must be set "
                f"(needed for locator {self.model.name!r})"
            )
