# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Tokamak catalog discovery for fdp.

Reads tokamak YAMLs contributed via the `fdp_schema.catalogs` entry-point
group, validates them against fdp_schema, and exposes them through a
lazy-loaded singleton `catalog`.
"""

from importlib.metadata import entry_points
from fdp_schema import Tokamak, load_tokamak


def _discover() -> dict[str, Tokamak]:
    """Load all contributed tokamak YAMLs from the entry-point group."""
    out: dict[str, Tokamak] = {}
    for ep in entry_points(group="fdp_schema.catalogs"):
        source = ep.load()
        tk = load_tokamak(source)
        if tk.name in out:
            raise RuntimeError(
                f"Duplicate tokamak name {tk.name!r}: a previous entry point "
                f"already contributed it; {ep.value} conflicts."
            )
        out[tk.name] = tk
    return out


class _Catalog:
    """Lazy-loaded registry of tokamaks. Discovery runs on first access."""

    def __init__(self):
        self._cache: dict[str, Tokamak] | None = None

    def _load(self) -> dict[str, Tokamak]:
        if self._cache is None:
            self._cache = _discover()
        return self._cache

    def __contains__(self, name: str) -> bool:
        return name in self._load()

    def __iter__(self):
        return iter(self._load())

    def names(self) -> list[str]:
        return sorted(self._load())


catalog = _Catalog()
