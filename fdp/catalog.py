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

    def __getitem__(self, name: str) -> "TokamakHandle":
        loaded = self._load()
        if name not in loaded:
            raise KeyError(name)
        return TokamakHandle(loaded[name])


from fdp_schema import (
    MdsTreeLocator, PtDataIndexedLocator, SqlLocator,
    ZarrStoreLocator, HttpCatalogLocator,
)
from fdp.resolvers import (
    MdsTreeResolver, PtDataResolver, SqlResolver,
    ZarrStoreResolver, HttpCatalogResolver,
)


def _wrap(loc):
    """Dispatch a Locator subtype to its Resolver."""
    return {
        MdsTreeLocator:        MdsTreeResolver,
        PtDataIndexedLocator:  PtDataResolver,
        SqlLocator:            SqlResolver,
        ZarrStoreLocator:      ZarrStoreResolver,
        HttpCatalogLocator:    HttpCatalogResolver,
    }[type(loc)](loc)


class TokamakHandle:
    """fdp-side wrapper around a fdp_schema.Tokamak. Adds typed resolver
    methods; the underlying schema model is exposed via `.schema` as an
    escape hatch."""

    def __init__(self, model):
        self._model = model

    @property
    def name(self) -> str:        return self._model.name

    @property
    def description(self) -> str: return self._model.description

    @property
    def extra_env(self) -> dict:  return self._model.extra_env

    @property
    def schema(self):             return self._model

    def locator(self, kind: str, name: str = "main"):
        matches = [l for l in self._model.locators
                   if l.kind == kind and l.name == name]
        if not matches:
            raise KeyError(
                f"No locator with kind={kind!r} name={name!r} on {self.name!r}"
            )
        if len(matches) > 1:
            raise KeyError(
                f"Multiple locators with kind={kind!r} name={name!r} on {self.name!r}"
            )
        return _wrap(matches[0])


catalog = _Catalog()
