# fdp Device Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `fdp` work with no configuration when several devices are installed, by composing their environments instead of forcing a global device choice.

**Architecture:** Two rules replace "pick one device up front." *Composition*: `fdp env`/`fdp run` emit the union of every registered device's env and hard-error only on a mechanically-detected key conflict. *Capability scoping*: `fdp ls` and `fdp login` ask which devices can service that specific operation (`origin_server` present; bearer auth declared) and require a choice only when more than one qualifies. Device *selection* moves out of `environment.py` into a new `fdp/devices.py`, leaving `environment.py` responsible only for assembling and applying env.

**Tech Stack:** Python 3.11, `argparse`, `pydantic` (via `fdp_schema`), `tomllib`, `unittest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-21-fdp-device-resolution-design.md`

---

## Background for the implementer

You are working in `/fusion/projects/dt/sammuli/fdp_dev/repos/fdp`, the `fdp`
client package. It is one repo in the Fusion Data Platform ecosystem.

**Domain terms:**
- *Device* / *tokamak*: a fusion experiment (`d3d` = DIII-D, `mast` = MAST-U).
  Each is described by a YAML catalog file contributed by a separate package
  through the `fdp_schema.catalogs` entry-point group. **Devices are data, not
  code** — adding one requires no Python.
- *Locator*: one way to reach data (`mds_tree`, `ptdata_indexed`, `sql`,
  `zarr_store`, `http_catalog`). A device has several.
- The `fdp` CLI's job is to set environment variables that C libraries
  (XRootD, MDSplus, ptdata) read as process-global configuration.

**The bug:** `fdp-core` installs both `toksearch_d3d` and `toksearch_mast`, so
two devices register. `_resolve_device_handle` auto-selects only when exactly
one is registered, so every command now errors until the user configures a
default. Measurement shows the two devices' env vars are **completely
disjoint** — 0 conflicting keys — so the conflict is imaginary.

**Test conventions:** `unittest`, not pytest. Tests live in `tests/` and are
discovered by `tests/testit.py`. Catalog entry points are patched with
`mock.patch("fdp.catalog.entry_points", ...)` so tests never touch the network.
`Path.home` is patched to a temp dir so the developer's real
`~/.fdp/config.toml` can never leak in. **Always reset the catalog cache**
(`catalog._cache = None`) in both `setUp` and `tearDown` — it is a module-level
singleton and leaks between test classes otherwise.

**Commands:**
- Full suite: `pixi run python -m unittest discover -s tests -t tests -v`
- One test: `pixi run python -m unittest discover -s tests -t tests -k <name> -v`
- Baseline before you start: **152 tests, OK (skipped=6)**. Keep it green.

---

## File Structure

| File | Responsibility |
|---|---|
| `fdp/config.py` **(new)** | Read/write `~/.fdp/config.toml`. Writing is a targeted text edit so the `[llm]` section and user comments survive. |
| `fdp/devices.py` **(new)** | Device *selection*: explicit choice order, active handles, capability candidates. Imports `auth` + `catalog`; imports nothing from `environment`. |
| `fdp/environment.py` (modify) | Env *assembly*: `compose_device_config`, `DeviceEnvConflict`, `setup_environment`. Re-exports moved names for back-compat. |
| `fdp/cli.py` (modify) | Flag placement, `fdp device` subcommand, wiring `ls`/`login`/`logout` to capability scoping. |
| `tests/test_config_file.py` **(new)** | `fdp/config.py` round-trips. |
| `tests/test_composition.py` **(new)** | Env union + conflict detection + real-catalog regression guard. |
| `tests/test_capabilities.py` **(new)** | Capability scoping for `ls` / `login`. |
| `tests/test_device_resolution.py` (modify) | Update assertions for the renamed flag; invert the now-wrong multi-device `run` test. |

Dependency direction: `cli` → `environment` → `devices` → `config` / `auth` /
`catalog`. No cycles. `auth.py` imports nothing from `fdp`, so `devices.py`
importing it is safe.

---

## Task 1: Read/write `~/.fdp/config.toml` without clobbering it

`~/.fdp/config.toml` already carries an `[llm]` section (read by
`fdp/llm_shims.py`). `fdp device use` must set `[device].default` while
preserving every other section, key, and comment — so we do a targeted line
edit rather than a parse-and-reserialize round trip, which would drop comments.

**Files:**
- Create: `fdp/config.py`
- Create: `tests/test_config_file.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_file.py`:

```python
# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Round-trip tests for ~/.fdp/config.toml handling."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestConfigFile(unittest.TestCase):

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._home = Path(self._td.name)
        (self._home / ".fdp").mkdir()
        self._patch = mock.patch.object(Path, "home", return_value=self._home)
        self._patch.start()
        self.path = self._home / ".fdp" / "config.toml"

    def tearDown(self):
        self._patch.stop()
        self._td.cleanup()

    def test_read_returns_none_when_no_file(self):
        from fdp.config import read_default_device
        self.assertIsNone(read_default_device())

    def test_set_creates_file_when_absent(self):
        from fdp.config import read_default_device, set_default_device
        set_default_device("d3d")
        self.assertEqual(read_default_device(), "d3d")
        self.assertIn("[device]", self.path.read_text())

    def test_set_preserves_other_sections_and_comments(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text(
            "# my notes\n[llm]\nbackend = 'amsc'\n")
        set_default_device("d3d")
        text = self.path.read_text()
        self.assertIn("# my notes", text)
        self.assertIn("[llm]", text)
        self.assertIn("backend = 'amsc'", text)
        self.assertEqual(read_default_device(), "d3d")

    def test_set_replaces_existing_default_exactly_once(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text('[device]\ndefault = "mast"\n')
        set_default_device("d3d")
        text = self.path.read_text()
        self.assertEqual(read_default_device(), "d3d")
        self.assertEqual(text.count("default ="), 1)
        self.assertNotIn("mast", text)

    def test_set_adds_key_to_existing_empty_device_section(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text("[device]\n[llm]\nbackend = 'amsc'\n")
        set_default_device("d3d")
        self.assertEqual(read_default_device(), "d3d")
        self.assertIn("[llm]", self.path.read_text())

    def test_clear_removes_key_but_keeps_other_sections(self):
        from fdp.config import read_default_device, set_default_device
        self.path.write_text(
            '[llm]\nbackend = "amsc"\n\n[device]\ndefault = "d3d"\n')
        set_default_device(None)
        self.assertIsNone(read_default_device())
        self.assertIn("backend", self.path.read_text())

    def test_malformed_toml_reads_as_none(self):
        from fdp.config import read_default_device
        self.path.write_text("not = valid = toml")
        self.assertIsNone(read_default_device())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run python -m unittest discover -s tests -t tests -k TestConfigFile -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fdp.config'`

- [ ] **Step 3: Write the implementation**

Create `fdp/config.py`:

```python
# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Read and write ``~/.fdp/config.toml``.

Reading uses ``tomllib``. Writing is a targeted line edit rather than a
parse-and-reserialize round trip: the file also carries an ``[llm]`` section
(see ``fdp/llm_shims.py``) and may carry user comments, both of which a naive
rewrite would silently discard.
"""

import re
import tomllib
from pathlib import Path


def config_path() -> Path:
    """Location of the user's fdp config file."""
    return Path.home() / ".fdp" / "config.toml"


def read_default_device() -> "str | None":
    """``[device].default``, or None if the file, section, or key is absent
    or the file is unreadable/malformed."""
    path = config_path()
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data.get("device", {}).get("default") or None


_DEVICE_HEADER = re.compile(r"^\s*\[device\]\s*$")
_ANY_HEADER = re.compile(r"^\s*\[")
_DEFAULT_KEY = re.compile(r"^\s*default\s*=")


def set_default_device(name: "str | None") -> None:
    """Set ``[device].default`` to *name*, or remove the key when *name* is
    None. All other sections, keys, and comments are preserved verbatim."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.is_file() else []

    out: list[str] = []
    in_device = False
    wrote = False

    for line in lines:
        if _ANY_HEADER.match(line):
            # About to leave [device] without having written the key.
            if in_device and not wrote and name is not None:
                out.append(f'default = "{name}"')
                wrote = True
            in_device = bool(_DEVICE_HEADER.match(line))
            out.append(line)
            continue
        if in_device and _DEFAULT_KEY.match(line):
            # Replace the first occurrence; drop it when clearing, and drop
            # any duplicates.
            if name is not None and not wrote:
                out.append(f'default = "{name}"')
                wrote = True
            continue
        out.append(line)

    if in_device and not wrote and name is not None:
        out.append(f'default = "{name}"')
        wrote = True

    if not wrote and name is not None:
        if out and out[-1].strip():
            out.append("")
        out.append("[device]")
        out.append(f'default = "{name}"')

    path.write_text("\n".join(out).rstrip("\n") + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run python -m unittest discover -s tests -t tests -k TestConfigFile -v`
Expected: PASS, 7 tests

- [ ] **Step 5: Commit**

```bash
git add fdp/config.py tests/test_config_file.py
git commit -m "feat(config): read/write ~/.fdp/config.toml [device].default

Targeted line edit rather than reserialize, so the [llm] section and user
comments survive a device-default change."
```

---

## Task 2: Extract device selection into `fdp/devices.py`

Pure move plus the new capability logic. `environment.py` keeps working
because it re-exports the moved names.

**Files:**
- Create: `fdp/devices.py`
- Create: `tests/test_capabilities.py`
- Modify: `fdp/environment.py:197-238` (delete `_config_default_device` and
  `_resolve_device_handle`, add re-export imports)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_capabilities.py`:

```python
# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Capability-scoped device selection.

Each subcommand asks which devices can service *that* operation rather than
demanding a single global default. `ls` needs an origin server; `login` needs
bearer auth. With the real catalog (d3d + mast) both are unambiguous.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# d3d: has an origin server AND bearer auth.
_D3D_YAML = """\
schema_version: 1
name: d3d
description: test d3d
pelican_root: pelican://test/fdp-d3d
origin_server: root://d3d-origin.example.org:8443
locators:
  - kind: mds_tree
    name: main
    transport: pelican
    search_path: [pelican://test/fdp-d3d/mds/~t]
    auth: { kind: bearer_token, env: BEARER_TOKEN }
"""

# mast: neither an origin server nor bearer auth. Mirrors the real mast.yaml.
_MAST_YAML = """\
schema_version: 1
name: mast
description: test mast
locators:
  - kind: zarr_store
    name: main
    protocol: s3
    base_url: s3://mast/level2/shots
    endpoint: https://s3.echo.stfc.ac.uk
    auth: { kind: none }
"""

# A second Pelican-hosted device, to prove genuine ambiguity still errors.
_DEVB_YAML = """\
schema_version: 1
name: devb
description: test devb
pelican_root: pelican://test/fdp-devb
origin_server: root://devb-origin.example.org:8443
locators:
  - kind: mds_tree
    name: main
    transport: pelican
    search_path: [pelican://test/fdp-devb/mds/~t]
    auth: { kind: bearer_token, env: BEARER_TOKEN }
"""


def make_ep(name, yaml_text):
    src = mock.MagicMock()
    src.read_text.return_value = yaml_text
    ep = mock.MagicMock()
    ep.name = name
    ep.load.return_value = src
    return ep


class CatalogFixture(unittest.TestCase):
    """Registers a set of fake devices and an empty temp $HOME."""

    YAMLS = ()

    def setUp(self):
        self._saved = os.environ.pop("FDP_DEFAULT_DEVICE", None)
        eps = [make_ep(n, y) for n, y in self.YAMLS]
        self._cat_patch = mock.patch("fdp.catalog.entry_points",
                                     return_value=eps)
        self._cat_patch.start()
        from fdp.catalog import catalog
        catalog._cache = None
        self._home_td = tempfile.TemporaryDirectory()
        self._home = Path(self._home_td.name)
        (self._home / ".fdp").mkdir()
        self._home_patch = mock.patch.object(
            Path, "home", return_value=self._home)
        self._home_patch.start()

    def tearDown(self):
        self._home_patch.stop()
        self._home_td.cleanup()
        self._cat_patch.stop()
        from fdp.catalog import catalog
        catalog._cache = None
        if self._saved is not None:
            os.environ["FDP_DEFAULT_DEVICE"] = self._saved
        else:
            os.environ.pop("FDP_DEFAULT_DEVICE", None)


class TestCapabilityScoping(CatalogFixture):
    """The real-world shape: d3d + mast."""

    YAMLS = (("d3d", _D3D_YAML), ("mast", _MAST_YAML))

    def test_origin_resolves_to_d3d_with_no_config(self):
        from fdp.devices import resolve_for_capability
        self.assertEqual(
            resolve_for_capability("origin").schema.name, "d3d")

    def test_bearer_resolves_to_d3d_with_no_config(self):
        from fdp.devices import resolve_for_capability
        self.assertEqual(
            resolve_for_capability("bearer").schema.name, "d3d")

    def test_candidates_exclude_incapable_devices(self):
        from fdp.devices import candidate_devices
        self.assertEqual(
            [h.schema.name for h in candidate_devices("origin")], ["d3d"])
        self.assertEqual(
            [h.schema.name for h in candidate_devices("bearer")], ["d3d"])

    def test_explicit_incapable_device_errors_clearly(self):
        # Regression: previously returned origin_server=None and crashed
        # inside FdpFileSystem(None).
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin", "mast")
        self.assertIn("mast", str(ctx.exception))
        self.assertIn("origin server", str(ctx.exception))

    def test_explicit_capable_device_is_honored(self):
        from fdp.devices import resolve_for_capability
        self.assertEqual(
            resolve_for_capability("origin", "d3d").schema.name, "d3d")

    def test_active_handles_returns_all_when_nothing_selected(self):
        from fdp.devices import active_handles
        self.assertEqual(
            sorted(h.schema.name for h in active_handles()), ["d3d", "mast"])

    def test_active_handles_narrows_to_explicit_selection(self):
        from fdp.devices import active_handles
        self.assertEqual(
            [h.schema.name for h in active_handles("mast")], ["mast"])

    def test_active_handles_honors_env_var(self):
        from fdp.devices import active_handles
        os.environ["FDP_DEFAULT_DEVICE"] = "mast"
        self.assertEqual(
            [h.schema.name for h in active_handles()], ["mast"])


class TestGenuineAmbiguity(CatalogFixture):
    """Two Pelican-hosted devices: the ambiguity is real, so it must error."""

    YAMLS = (("d3d", _D3D_YAML), ("devb", _DEVB_YAML))

    def test_two_origin_devices_error_and_list_both(self):
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin")
        msg = str(ctx.exception)
        self.assertIn("d3d", msg)
        self.assertIn("devb", msg)
        self.assertIn("fdp device use", msg)

    def test_explicit_selection_resolves_the_ambiguity(self):
        from fdp.devices import resolve_for_capability
        self.assertEqual(
            resolve_for_capability("origin", "devb").schema.name, "devb")


class TestNoCapableDevice(CatalogFixture):
    """Only mast: nothing has an origin server."""

    YAMLS = (("mast", _MAST_YAML),)

    def test_zero_candidates_errors(self):
        from fdp.devices import resolve_for_capability
        with self.assertRaises(ValueError) as ctx:
            resolve_for_capability("origin")
        self.assertIn("origin server", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run python -m unittest discover -s tests -t tests -k TestCapabilityScoping -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fdp.devices'`

- [ ] **Step 3: Write the implementation**

Create `fdp/devices.py`:

```python
# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Device selection for the fdp CLI.

Two distinct questions, deliberately separated:

* Which device did the user explicitly choose? (flag > env var > config file)
* Which devices can service a given operation? (*capability scoping*)

Historically the CLI answered only the first, and demanded an answer even for
commands that did not need one -- which is why installing a second device
package broke every command. Env *assembly* lives in ``fdp/environment.py``;
this module only picks handles.
"""

import os

from . import auth
from .catalog import catalog as _catalog
from .config import read_default_device

_CHOOSE_HINT = (
    "Choose one with `fdp --device <name> ...`, set the FDP_DEFAULT_DEVICE "
    "environment variable, or run `fdp device use <name>` to record it in "
    "~/.fdp/config.toml."
)

_NO_DEVICES = (
    "No tokamak contributors are installed. "
    "Install a device package (e.g. toksearch_d3d) to provide one."
)


def explicit_device_name(device: "str | None" = None) -> "str | None":
    """The user's explicit choice, or None. Order: argument, then
    ``$FDP_DEFAULT_DEVICE``, then ``~/.fdp/config.toml`` ``[device].default``."""
    if device is not None:
        return device
    return (os.environ.get("FDP_DEFAULT_DEVICE")
            or read_default_device() or None)


def _registered_names() -> list:
    names = _catalog.names()
    if not names:
        raise ValueError(_NO_DEVICES)
    return names


def active_handles(device: "str | None" = None) -> list:
    """Handles whose environments should be composed.

    An explicit selection narrows to that one device. Otherwise *every*
    registered device contributes, and ``compose_device_config`` decides
    whether they actually conflict -- rather than assuming they do.
    """
    names = _registered_names()
    name = explicit_device_name(device)
    if name is not None:
        return [_catalog[name]]
    return [_catalog[n] for n in names]


# capability -> (predicate, human-readable description)
CAPABILITIES = {
    "origin": (lambda h: h.schema.origin_server is not None,
               "an origin server"),
    "bearer": (lambda h: auth.bearer_env(h) is not None,
               "bearer-token authentication"),
}


def candidate_devices(capability: str) -> list:
    """Registered devices that can service *capability*."""
    predicate, _ = CAPABILITIES[capability]
    return [_catalog[n] for n in _registered_names()
            if predicate(_catalog[n])]


def resolve_for_capability(capability: str,
                           device: "str | None" = None):
    """Resolve the single device to use for a capability-scoped command."""
    predicate, described = CAPABILITIES[capability]
    name = explicit_device_name(device)
    if name is not None:
        handle = _catalog[name]
        if not predicate(handle):
            raise ValueError(
                f"Device {name!r} does not declare {described}, so it cannot "
                f"service this command."
            )
        return handle

    candidates = candidate_devices(capability)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(
            f"No registered device declares {described}, so this command has "
            f"nothing to act on."
        )
    names = [h.schema.name for h in candidates]
    raise ValueError(
        f"{len(candidates)} registered devices declare {described} "
        f"({names}). {_CHOOSE_HINT}"
    )


def _resolve_device_handle(device: "str | None" = None):
    """Resolve exactly one device: explicit choice, else the sole registered
    device, else error.

    Retained for callers that genuinely need a single device -- the public
    ``fdp.setup_environment(device=...)`` argument and the ``toksearch_d3d`` /
    ``toksearch_mast`` shims that pass an explicit name.
    """
    names = _registered_names()
    name = explicit_device_name(device)
    if name is not None:
        return _catalog[name]
    if len(names) == 1:
        return _catalog[names[0]]
    raise ValueError(
        f"No default tokamak selected and {len(names)} are registered "
        f"({names}). {_CHOOSE_HINT}"
    )
```

- [ ] **Step 4: Remove the moved code from `environment.py`**

Delete `_config_default_device` (lines 197-210) and `_resolve_device_handle`
(lines 213-238) from `fdp/environment.py`. Those were the only users of
`_catalog` in that module, so also delete the now-unused
`from .catalog import catalog as _catalog` import (line 29). Replace it with
these re-exports:

```python
# Selection moved to fdp/devices.py; re-exported so existing imports of
# `fdp.environment._resolve_device_handle` (cli.py, tests, downstream shims)
# keep working.
from .config import read_default_device as _config_default_device  # noqa: F401
from .devices import (  # noqa: F401
    active_handles, candidate_devices, resolve_for_capability,
    _resolve_device_handle,
)
```

- [ ] **Step 5: Run the capability tests and the full suite**

Run: `pixi run python -m unittest discover -s tests -t tests -k TestCapability -v`
Expected: PASS

Run: `pixi run python -m unittest discover -s tests -t tests -v 2>&1 | tail -5`
Expected: some `test_device_resolution` failures asserting the string
`--default-device`, which the new hint replaces with `--device`. Fix them now:
in `tests/test_device_resolution.py`, change the three
`self.assertIn("--default-device", msg)` assertions (in
`test_two_devices_nothing_set_raises_with_all_three_hints`,
`test_fdp_run_multidevice_exits_cleanly_not_traceback`, and
`test_ls_origin_two_devices_nothing_set_raises`) to:

```python
        self.assertIn("--device", msg)
```

and the matching `self.assertIn("--default-device", err)` to
`self.assertIn("--device", err)`.

Re-run the full suite. Expected: **OK**, same count as baseline plus the new
tests. (`test_fdp_run_multidevice_exits_cleanly_not_traceback` still passes at
this point — composition is not wired in until Task 4.)

- [ ] **Step 6: Commit**

```bash
git add fdp/devices.py fdp/environment.py tests/test_capabilities.py tests/test_device_resolution.py
git commit -m "refactor(devices): split device selection out of environment.py

Adds capability scoping: each subcommand asks which devices can service it
rather than demanding one global default. environment.py re-exports the
moved names for back-compat."
```

---

## Task 3: Compose device environments, detecting real conflicts

**Files:**
- Modify: `fdp/environment.py` (add `DeviceEnvConflict` + `compose_device_config`)
- Create: `tests/test_composition.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_composition.py`:

```python
# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Environment composition across devices.

The premise: registered devices' env vars are disjoint in practice, so the
union is well-defined and no device choice is needed. A genuine collision is
a hard error naming the key.
"""

import unittest

from test_capabilities import CatalogFixture, _D3D_YAML, _MAST_YAML

# Deliberately collides with d3d on PTDATA_JSON_INDEX_DIR.
_CONFLICT_YAML = """\
schema_version: 1
name: devb
description: conflicting device
origin_server: root://devb-origin.example.org:8443
locators:
  - kind: ptdata_indexed
    name: main
    transport: pelican
    index_dir: pelican://test/fdp-devb/index/json
    auth: { kind: bearer_token, env: BEARER_TOKEN }
"""

_D3D_PTDATA_YAML = """\
schema_version: 1
name: d3d
description: test d3d with ptdata
origin_server: root://d3d-origin.example.org:8443
locators:
  - kind: ptdata_indexed
    name: main
    transport: pelican
    index_dir: pelican://test/fdp-d3d/index/json
    auth: { kind: bearer_token, env: BEARER_TOKEN }
"""


class TestCompositionSucceeds(CatalogFixture):
    YAMLS = (("d3d", _D3D_YAML), ("mast", _MAST_YAML))

    def test_union_contains_both_devices_vars(self):
        from fdp.devices import active_handles
        from fdp.environment import compose_device_config
        env = compose_device_config(active_handles())
        # d3d contributes an MDSplus tree path...
        self.assertIn("default_tree_path", env)
        # ...and mast contributes its zarr store.
        self.assertEqual(env["MAST_ZARR_BASE_URL"], "s3://mast/level2/shots")

    def test_path_is_not_prepended_once_per_device(self):
        # Every device computes an identical PATH (it derives from
        # sys.executable), so composing N devices must yield exactly the
        # single-device value -- not N stacked prepends.
        from fdp.devices import active_handles
        from fdp.environment import build_device_config, compose_device_config
        composed = compose_device_config(active_handles())
        single = build_device_config(active_handles("d3d")[0])
        self.assertEqual(composed["PATH"], single["PATH"])

    def test_single_device_selection_skips_composition(self):
        from fdp.devices import active_handles
        from fdp.environment import compose_device_config
        env = compose_device_config(active_handles("mast"))
        self.assertNotIn("default_tree_path", env)
        self.assertIn("MAST_ZARR_BASE_URL", env)


class TestCompositionConflict(CatalogFixture):
    YAMLS = (("d3d", _D3D_PTDATA_YAML), ("devb", _CONFLICT_YAML))

    def test_conflict_raises_naming_key_and_devices(self):
        from fdp.devices import active_handles
        from fdp.environment import DeviceEnvConflict, compose_device_config
        with self.assertRaises(DeviceEnvConflict) as ctx:
            compose_device_config(active_handles())
        msg = str(ctx.exception)
        self.assertIn("PTDATA_JSON_INDEX_DIR", msg)
        self.assertIn("d3d", msg)
        self.assertIn("devb", msg)

    def test_conflict_is_a_valueerror_so_cli_renders_it_cleanly(self):
        # cli.main catches (ValueError, KeyError) to print a clean message
        # instead of a traceback; keep DeviceEnvConflict inside that net.
        from fdp.environment import DeviceEnvConflict
        self.assertTrue(issubclass(DeviceEnvConflict, ValueError))

    def test_explicit_selection_escapes_the_conflict(self):
        from fdp.devices import active_handles
        from fdp.environment import compose_device_config
        env = compose_device_config(active_handles("d3d"))
        self.assertEqual(env["PTDATA_JSON_INDEX_DIR"],
                         "pelican://test/fdp-d3d/index/json")


class TestRealCatalogHasNoConflicts(unittest.TestCase):
    """Regression guard against the *actually installed* devices.

    This is what makes composition falsifiable rather than an assumption: a
    future device that genuinely collides fails CI instead of silently
    corrupting a user's environment.
    """

    def test_installed_devices_compose_without_conflict(self):
        from fdp.catalog import catalog
        from fdp.environment import compose_device_config
        handles = [catalog[n] for n in catalog.names()]
        if len(handles) < 2:
            self.skipTest(
                f"only {len(handles)} device(s) installed; nothing to compose")
        compose_device_config(handles)  # must not raise


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pixi run python -m unittest discover -s tests -t tests -k TestComposition -v`
Expected: FAIL — `ImportError: cannot import name 'DeviceEnvConflict'`

- [ ] **Step 3: Write the implementation**

Add to `fdp/environment.py`, immediately after `build_device_config`:

```python
class DeviceEnvConflict(ValueError):
    """Two registered devices assign different values to the same env var.

    Subclasses ValueError so cli.main's existing handler renders it as a
    clean message rather than a traceback.
    """


def compose_device_config(handles) -> dict:
    """Merge several devices' env dicts into one.

    Devices are merged in catalog-name order so the error message is
    deterministic regardless of entry-point discovery order. Keys that
    several devices set to the *same* value (the generic thread-affinity vars
    and PATH) merge silently; only a genuine disagreement is an error.
    """
    merged: dict = {}
    source: dict = {}
    for handle in sorted(handles, key=lambda h: h.schema.name):
        name = handle.schema.name
        for key, value in build_device_config(handle).items():
            if value is None:
                continue
            value = str(value)
            if key in merged and merged[key] != value:
                raise DeviceEnvConflict(
                    f"devices {source[key]!r} and {name!r} set {key} to "
                    f"different values:\n"
                    f"  {source[key]} = {merged[key]}\n"
                    f"  {name} = {value}\n"
                    "Select one device with `fdp --device <name> ...`, "
                    "$FDP_DEFAULT_DEVICE, or `fdp device use <name>`."
                )
            merged[key] = value
            source[key] = name
    return merged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pixi run python -m unittest discover -s tests -t tests -k TestComposition -v`
Expected: PASS

Run: `pixi run python -m unittest discover -s tests -t tests -k TestRealCatalog -v`
Expected: PASS (or `skipped` if only one device is installed in this env —
that is correct behavior, not a failure)

- [ ] **Step 5: Commit**

```bash
git add fdp/environment.py tests/test_composition.py
git commit -m "feat(environment): compose device envs, hard-error on real conflicts

Registered devices' env vars are disjoint in practice, so the union is
well-defined. Includes a regression guard asserting the actually-installed
catalog composes cleanly."
```

---

## Task 4: Wire composition into `setup_environment` and `fdp env`

This is the task that fixes the user-visible bug.

**Files:**
- Modify: `fdp/environment.py:258-289` (`setup_environment`)
- Modify: `fdp/cli.py:41-51` (`do_env`)
- Modify: `tests/test_device_resolution.py` (invert the now-wrong test)

- [ ] **Step 1: Invert the stale test**

`test_fdp_run_multidevice_exits_cleanly_not_traceback` asserts that `fdp run`
*fails* with two devices registered. That is exactly the behavior we are
removing. Replace the whole method in `tests/test_device_resolution.py` with:

```python
    def test_fdp_run_multidevice_succeeds_via_composition(self):
        # Two devices registered and nothing selected: `fdp run` must now
        # compose both environments and succeed, because d3d and mast set
        # disjoint variables. Previously this exited 1.
        #
        # auth.ensure_token is patched because the fake d3d declares bearer
        # auth, and `fdp run` sets auto_login=True: unpatched, this test would
        # reach the real interactive `pelican` consent flow. It previously
        # errored out before ever getting near auth.
        import contextlib
        import io
        from fdp import cli
        stderr = io.StringIO()
        with mock.patch("fdp.auth.ensure_token", return_value=None), \
                self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stderr(stderr):
            cli.main(["run", "true"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertNotIn("No default tokamak", stderr.getvalue())
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pixi run python -m unittest discover -s tests -t tests -k test_fdp_run_multidevice -v`
Expected: FAIL — exit code 1, stderr contains `No default tokamak selected`

- [ ] **Step 3: Rewrite `setup_environment`**

Replace `setup_environment` in `fdp/environment.py` (currently lines 258-289)
with:

```python
def _apply_tokens(handles, bearer_token, auto_login) -> None:
    """Resolve and export a bearer token for each device that wants one.

    Auto-login fires only when exactly one registered device declares bearer
    auth. With several, we cannot justify choosing whose consent flow to
    launch, so we emit the env without tokens and say what to run. This keeps
    today's behavior identical (d3d needs a token, mast does not) and fails
    toward "no surprise prompt".
    """
    bearers = [h for h in handles if auth.bearer_env(h) is not None]
    single = len(bearers) == 1
    for handle in bearers:
        if auto_login and single:
            token = auth.ensure_token(handle, explicit=bearer_token)
        else:
            token = auth.get_valid_token(handle, explicit=bearer_token)
        if token is not None:
            os.environ[auth.bearer_env(handle)] = token
        elif not os.environ.get("FDP_NO_AUTO_LOGIN"):
            suffix = "" if single else f" --device {handle.schema.name}"
            warnings.warn(
                f"No valid bearer token for device "
                f"'{handle.schema.name}'; run `fdp login{suffix}`."
            )


def setup_environment(
    device: str | None = None,
    bearer_token: str | None = None,
    *,
    auto_login: bool = False,
    **overrides,
) -> None:
    """Populate os.environ with FDP variables.

    With no device selected, every registered device's environment is
    composed; a genuine key conflict raises DeviceEnvConflict. Selecting a
    device (argument, $FDP_DEFAULT_DEVICE, or ~/.fdp/config.toml) narrows to
    that one. Env emission is locator-driven. Mutates os.environ in place and
    is safe to call repeatedly.
    """
    handles = active_handles(device)
    apply_environment(compose_device_config(handles), os.environ)

    for key, value in overrides.items():
        os.environ[key] = str(value)

    _apply_tokens(handles, bearer_token, auto_login)
```

- [ ] **Step 4: Rewrite `do_env`**

Replace `do_env` in `fdp/cli.py` (lines 41-51) with:

```python
def do_env(args) -> None:
    handles = active_handles(args.device)
    for key, value in compose_device_config(handles).items():
        if value is None:
            continue
        print(f"export {key}={shlex.quote(str(value))}")
    for handle in handles:
        env_var = auth.bearer_env(handle)
        if env_var is None:
            continue
        token = resolve_bearer_token(handle)
        if token:
            print(f"export {env_var}={shlex.quote(token)}")
```

Update the import block at `fdp/cli.py:26-29` to:

```python
from .devices import (
    active_handles, resolve_for_capability, _resolve_device_handle,
)
from .environment import (
    build_device_config, compose_device_config, resolve_bearer_token,
    setup_environment,
)
```

`args.device` does not exist yet — the argparse dest is still
`default_device`. Rename it now with this one-line change in `build_parser`,
so every handler can use `args.device` from here on. Task 6 builds on it by
adding the flag to the subparsers:

```python
    parser.add_argument("--device", "-D", "--default-device", dest="device",
                         default=None,
                         help="Device (tokamak) to use for this command.")
```

Then rename the remaining `args.default_device` references in `do_login`,
`do_logout`, `do_ls`, and `_resolve_default_handle_or_none` to `args.device`.

- [ ] **Step 5: Run tests**

Run: `pixi run python -m unittest discover -s tests -t tests -k test_fdp_run_multidevice -v`
Expected: PASS

Run: `pixi run python -m unittest discover -s tests -t tests -v 2>&1 | tail -5`
Expected: OK. In particular `test_env_parity.py` must still pass unchanged —
composition must not perturb single-device output.

- [ ] **Step 6: Verify by hand against the real catalog**

Run: `pixi run fdp env | head -20`
Expected: the same d3d exports as before this change (only d3d is installed in
this dev env, so composition is a no-op here).

- [ ] **Step 7: Commit**

```bash
git add fdp/environment.py fdp/cli.py tests/test_device_resolution.py
git commit -m "feat(environment): compose all device envs when none is selected

Fixes 'No default tokamak selected and 2 are registered' on every command
after installing fdp-core. Auto-login fires only when exactly one device
declares bearer auth, so no surprise consent prompts."
```

---

## Task 5: Point `fdp ls` at capability scoping

**Files:**
- Modify: `fdp/cli.py:98-117` (`_resolve_origin_server`, `do_ls`)
- Modify: `tests/test_capabilities.py` (add `ls` cases)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_capabilities.py`, inside `class TestCapabilityScoping`:

```python
    def test_ls_origin_resolves_without_config(self):
        from fdp.cli import _resolve_origin_server
        self.assertEqual(_resolve_origin_server(None),
                         "root://d3d-origin.example.org:8443")

    def test_ls_origin_rejects_device_without_origin(self):
        from fdp.cli import _resolve_origin_server
        with self.assertRaises(ValueError):
            _resolve_origin_server("mast")

    def test_ls_pelican_url_selects_matching_device(self):
        from fdp.cli import _device_for_ls
        handle = _device_for_ls("pelican://test/fdp-d3d/archives", None)
        self.assertEqual(handle.schema.name, "d3d")
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run python -m unittest discover -s tests -t tests -k test_ls_ -v`
Expected: FAIL — `ImportError: cannot import name '_device_for_ls'`

- [ ] **Step 3: Implement**

Replace `_resolve_origin_server` and `do_ls` in `fdp/cli.py` with:

```python
def _device_for_ls(path, device_name):
    """Resolve the device whose origin server should serve `fdp ls`.

    `fdp ls` normally takes a path relative to the origin, so the capability
    rule ("which devices even have an origin server?") does the real work. A
    full pelican:// URL that matches exactly one device's pelican_root is
    honored first as a convenience.
    """
    if device_name is None and str(path).startswith("pelican://"):
        matches = [
            catalog[n] for n in catalog.names()
            if catalog[n].schema.pelican_root
            and str(path).startswith(catalog[n].schema.pelican_root)
        ]
        if len(matches) == 1:
            return matches[0]
    return resolve_for_capability("origin", device_name)


def _resolve_origin_server(device_name: str | None) -> str:
    """Origin server for the resolved tokamak. Kept as a named function
    because tests and downstream code import it."""
    return _device_for_ls("", device_name).schema.origin_server


def do_ls(args) -> None:
    origin = _device_for_ls(args.path, args.device).schema.origin_server
    fs = FdpFileSystem(origin)
    listing = fs.ls(args.path, dirs_only=args.dirs_only)
    if listing:
        for entry in listing:
            print(entry)
    else:
        print("No such file or directory")
        sys.exit(1)
```

- [ ] **Step 4: Run tests**

Run: `pixi run python -m unittest discover -s tests -t tests -k test_ls_ -v`
Expected: PASS

Run: `pixi run python -m unittest discover -s tests -t tests -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add fdp/cli.py tests/test_capabilities.py
git commit -m "fix(ls): scope device selection to devices that have an origin

Also fixes 'fdp ls -D mast' constructing FdpFileSystem(None) and crashing;
it now reports that mast declares no origin server."
```

---

## Task 6: Accept `--device` before *and* after the subcommand

Today `fdp run --default-device d3d echo hi` fails with `unrecognized
arguments` — only the pre-subcommand position works.

**Files:**
- Modify: `fdp/cli.py:221-312` (`build_parser`) and every `args.default_device` reference
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
class TestDeviceFlagPlacement(unittest.TestCase):
    """-D must work on either side of the subcommand, and must not leak into
    the command `fdp run` executes."""

    def test_flag_before_subcommand(self):
        from fdp.cli import build_parser
        args = build_parser().parse_args(["-D", "d3d", "env"])
        self.assertEqual(args.device, "d3d")

    def test_flag_after_subcommand(self):
        from fdp.cli import build_parser
        args = build_parser().parse_args(["env", "-D", "d3d"])
        self.assertEqual(args.device, "d3d")

    def test_subcommand_flag_wins_over_toplevel(self):
        from fdp.cli import build_parser
        args = build_parser().parse_args(["-D", "mast", "env", "-D", "d3d"])
        self.assertEqual(args.device, "d3d")

    def test_absent_flag_is_none(self):
        from fdp.cli import build_parser
        self.assertIsNone(build_parser().parse_args(["env"]).device)

    def test_run_flag_after_subcommand_not_passed_to_child(self):
        from fdp.cli import build_parser
        args = build_parser().parse_args(
            ["run", "-D", "d3d", "echo", "hi"])
        self.assertEqual(args.device, "d3d")
        self.assertEqual(args.command_args, ["echo", "hi"])

    def test_run_child_args_keep_their_own_flags(self):
        # A -D belonging to the child command must survive untouched.
        from fdp.cli import build_parser
        args = build_parser().parse_args(
            ["-D", "d3d", "run", "mytool", "-D", "childvalue"])
        self.assertEqual(args.device, "d3d")
        self.assertEqual(args.command_args, ["mytool", "-D", "childvalue"])

    def test_deprecated_alias_still_works(self):
        from fdp.cli import build_parser
        args = build_parser().parse_args(["--default-device", "d3d", "env"])
        self.assertEqual(args.device, "d3d")
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run python -m unittest discover -s tests -t tests -k TestDeviceFlagPlacement -v`
Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'device'`

- [ ] **Step 3: Implement**

In `fdp/cli.py`, add this helper just above `build_parser`:

```python
def _add_device_arg(parser, top_level: bool = False) -> None:
    """Declare --device/-D.

    Subparsers use SUPPRESS as the default so that omitting the flag leaves
    the top-level value untouched; supplying it on the subparser overwrites
    the top-level value. That is what makes both positions work.
    """
    default = None if top_level else argparse.SUPPRESS
    parser.add_argument(
        "--device", "-D", dest="device", default=default,
        help="Device (tokamak) to use for this command. Defaults to "
             "$FDP_DEFAULT_DEVICE, then ~/.fdp/config.toml [device].default. "
             "Commands that do not need a single device compose all of them.")
    parser.add_argument(
        "--default-device", dest="device", default=default,
        help=argparse.SUPPRESS)  # deprecated alias
```

Replace the top-level flag declaration (currently `parser.add_argument(
"--default-device", "-D", ...)` at line 225) with:

```python
    _add_device_arg(parser, top_level=True)
```

Then add `_add_device_arg(<subparser>)` to each subparser. For `p_run` it
**must come before** the `command_args` REMAINDER argument:

```python
    p_run = sub.add_parser("run",
                            help="Run a command with FDP env applied")
    _add_device_arg(p_run)
    p_run.add_argument("command_args", nargs=argparse.REMAINDER,
                        help="Command and args to pass through")
    p_run.set_defaults(func=do_run, auto_login=True)
```

Add `_add_device_arg(p_env)`, `_add_device_arg(p_login)`,
`_add_device_arg(p_logout)`, and `_add_device_arg(p_ls)` similarly.

Finally, rename every remaining `args.default_device` to `args.device` in
`fdp/cli.py` — in `do_login`, `do_logout`, and
`_resolve_default_handle_or_none`. (`do_env` and `do_ls` already use
`args.device` from Tasks 4 and 5.)

- [ ] **Step 4: Run tests**

Run: `pixi run python -m unittest discover -s tests -t tests -k TestDeviceFlagPlacement -v`
Expected: PASS, 7 tests

If `test_run_flag_after_subcommand_not_passed_to_child` fails, argparse's
REMAINDER is greedily swallowing `-D`. Fall back to declaring the flag only on
the top-level parser for `run` specifically, and update the test to assert the
documented behavior. Do **not** silently drop the assertion — REMAINDER
interactions are the one genuinely uncertain part of this task.

- [ ] **Step 5: Verify by hand**

```bash
pixi run fdp run -D d3d echo hi        # expect: hi
pixi run fdp -D d3d run echo hi        # expect: hi
```

- [ ] **Step 6: Run the full suite and commit**

Run: `pixi run python -m unittest discover -s tests -t tests -v 2>&1 | tail -5`
Expected: OK

```bash
git add fdp/cli.py tests/test_cli.py
git commit -m "feat(cli): accept --device/-D before or after the subcommand

Renames --default-device (kept as a hidden alias): it selects a device for
one invocation rather than setting a default."
```

---

## Task 7: Scope `fdp login` / `fdp logout` to bearer devices

**Files:**
- Modify: `fdp/cli.py:54-84` (`do_login`, `do_logout`)
- Modify: `tests/test_capabilities.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_capabilities.py`, inside `class TestCapabilityScoping`:

```python
    def test_login_resolves_to_the_only_bearer_device(self):
        from fdp import cli
        with mock.patch("fdp.auth.login") as m:
            m.return_value = None
            cli.do_login(mock.Mock(device=None, write=False))
        self.assertEqual(m.call_args[0][0].schema.name, "d3d")

    def test_login_rejects_device_without_bearer_auth(self):
        import contextlib
        import io
        from fdp import cli
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stderr(stderr):
            cli.do_login(mock.Mock(device="mast", write=False))
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("bearer", stderr.getvalue())
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run python -m unittest discover -s tests -t tests -k test_login_ -v`
Expected: FAIL — `do_login` resolves via `_resolve_device_handle`, which raises
on two devices rather than scoping to bearer-capable ones.

- [ ] **Step 3: Implement**

In `fdp/cli.py`, change the first line of `do_login`'s `try` block from
`handle = _resolve_device_handle(args.default_device)` to:

```python
        handle = resolve_for_capability("bearer", args.device)
```

and make the same change in `do_logout`.

`_resolve_device_handle` is now unused in `fdp/cli.py` (it remains public API
in `fdp/devices.py` for `setup_environment` and the downstream shims), so drop
it from cli's import:

```python
from .devices import active_handles, resolve_for_capability
```

- [ ] **Step 4: Run tests**

Run: `pixi run python -m unittest discover -s tests -t tests -k test_login_ -v`
Expected: PASS

Run: `pixi run python -m unittest discover -s tests -t tests -v 2>&1 | tail -5`
Expected: OK

- [ ] **Step 5: Commit**

```bash
git add fdp/cli.py tests/test_capabilities.py
git commit -m "feat(login): scope login/logout to devices declaring bearer auth

d3d resolves unambiguously with mast installed, because mast declares
auth: none."
```

---

## Task 8: `fdp device list / show / use`

Every remaining error path ends in "set a default," and hand-editing TOML is
the friction that produced this work.

**Files:**
- Modify: `fdp/cli.py` (add `do_device`, register the subparser)
- Create: `tests/test_device_command.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_device_command.py`:

```python
# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""The `fdp device` subcommand group."""

import contextlib
import io
import unittest
from unittest import mock

from test_capabilities import CatalogFixture, _D3D_YAML, _MAST_YAML


class TestDeviceCommand(CatalogFixture):
    YAMLS = (("d3d", _D3D_YAML), ("mast", _MAST_YAML))

    def _run(self, argv):
        from fdp import cli
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(argv)
        return out.getvalue()

    def test_list_shows_names_and_capabilities(self):
        text = self._run(["device", "list"])
        self.assertIn("d3d", text)
        self.assertIn("mast", text)
        self.assertIn("origin", text)
        self.assertIn("bearer", text)

    def test_show_prints_yaml(self):
        text = self._run(["device", "show", "d3d"])
        self.assertIn("name: d3d", text)

    def test_use_writes_config_and_takes_effect(self):
        from fdp.config import read_default_device
        from fdp.devices import explicit_device_name
        self._run(["device", "use", "mast"])
        self.assertEqual(read_default_device(), "mast")
        self.assertEqual(explicit_device_name(), "mast")

    def test_use_clear_removes_the_setting(self):
        from fdp.config import read_default_device
        self._run(["device", "use", "mast"])
        self._run(["device", "use", "--clear"])
        self.assertIsNone(read_default_device())

    def test_use_rejects_unknown_device(self):
        from fdp import cli
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, \
                contextlib.redirect_stderr(stderr):
            cli.main(["device", "use", "nosuchdevice"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("nosuchdevice", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `pixi run python -m unittest discover -s tests -t tests -k TestDeviceCommand -v`
Expected: FAIL — `argument command: invalid choice: 'device'`

- [ ] **Step 3: Implement**

Add to `fdp/cli.py`, after `do_catalog`:

```python
def do_device(args) -> None:
    if args.device_command == "list":
        for name in catalog.names():
            handle = catalog[name]
            caps = [cap for cap, (predicate, _) in CAPABILITIES.items()
                    if predicate(handle)]
            print(f"{name}\t{handle.description}\t"
                  f"[{', '.join(caps) if caps else 'none'}]")
    elif args.device_command == "show":
        import yaml
        print(yaml.safe_dump(catalog[args.name].schema.model_dump(),
                             sort_keys=False))
    elif args.device_command == "use":
        if args.clear:
            config.set_default_device(None)
            print("Cleared the default device.")
            return
        if args.name is None:
            print("Error: `fdp device use` needs a device name "
                  "(or --clear).", file=sys.stderr)
            sys.exit(1)
        if args.name not in catalog:
            print(f"Error: unknown device {args.name!r}. Registered: "
                  f"{catalog.names()}", file=sys.stderr)
            sys.exit(1)
        config.set_default_device(args.name)
        print(f"Default device set to '{args.name}' in "
              f"{config.config_path()}.")
    else:
        raise ValueError(
            f"Unknown device subcommand: {args.device_command!r}")
```

Add these imports to `fdp/cli.py`:

```python
from . import config
from .devices import CAPABILITIES
```

Register the subparser inside `build_parser`, after the `catalog` block:

```python
    p_dev = sub.add_parser("device", help="Inspect and select devices")
    dev_sub = p_dev.add_subparsers(dest="device_command", required=True)
    dev_sub.add_parser("list", help="List devices and their capabilities")
    dev_show = dev_sub.add_parser("show", help="Print a device's catalog YAML")
    dev_show.add_argument("name")
    dev_use = dev_sub.add_parser(
        "use", help="Record a default device in ~/.fdp/config.toml")
    dev_use.add_argument("name", nargs="?", default=None)
    dev_use.add_argument("--clear", action="store_true",
                         help="Remove the recorded default device.")
    p_dev.set_defaults(func=do_device, needs_env=False)
```

Also update the `catalog` subparser's help to mark it deprecated:

```python
    p_cat = sub.add_parser("catalog",
                            help="(deprecated) alias for `fdp device`")
```

- [ ] **Step 4: Run tests**

Run: `pixi run python -m unittest discover -s tests -t tests -k TestDeviceCommand -v`
Expected: PASS, 5 tests

- [ ] **Step 5: Verify by hand**

```bash
pixi run fdp device list
```
Expected: `d3d	DIII-D fusion experiment, via Pelican	[origin, bearer]`

- [ ] **Step 6: Run the full suite and commit**

Run: `pixi run python -m unittest discover -s tests -t tests -v 2>&1 | tail -5`
Expected: OK

```bash
git add fdp/cli.py tests/test_device_command.py
git commit -m "feat(cli): add fdp device list/show/use

Removes the need to hand-edit ~/.fdp/config.toml when a default is wanted.
`fdp catalog` remains as a deprecated alias."
```

---

## Task 9: Update docs and the version-bump note

**Files:**
- Modify: `fdp/README.md`
- Modify: `/fusion/projects/dt/sammuli/fdp_dev/repos/CLAUDE.md` (the `fdp` row of the version table)

- [ ] **Step 1: Document the new behavior in `fdp/README.md`**

Add a section (place it after the existing CLI usage material):

```markdown
## Devices

`fdp` discovers devices (tokamaks) from installed packages: `toksearch_d3d`
contributes `d3d`, `toksearch_mast` contributes `mast`.

Most commands need no device selection:

- `fdp env` / `fdp run` compose the environments of **all** installed devices.
  Their variables are disjoint, so the union is well-defined. If two devices
  ever set the same variable to different values, `fdp` reports the conflicting
  variable and asks you to choose.
- `fdp ls` uses the device that has an origin server.
- `fdp login` / `fdp logout` use the device that requires a bearer token.

To choose explicitly, in increasing order of persistence:

```bash
fdp --device d3d ls /archives       # one invocation (also: fdp ls -D d3d)
export FDP_DEFAULT_DEVICE=d3d       # one shell
fdp device use d3d                  # recorded in ~/.fdp/config.toml
fdp device use --clear              # undo
```

`fdp device list` shows each installed device and its capabilities.
```

- [ ] **Step 2: Update the ecosystem version table**

In `/fusion/projects/dt/sammuli/fdp_dev/repos/CLAUDE.md`, replace the `fdp` row
with:

```markdown
| `fdp` | 0.5.0 | env composition across devices + capability-scoped resolution (`ls`→origin, `login`→bearer); `--device/-D` on either side of the subcommand; `fdp device list/show/use` |
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: describe device composition and capability scoping"
```

(The `CLAUDE.md` edit is in the parent repo, not the `fdp` repo — commit it
separately from that directory.)

---

## Task 10: Reword the installer's device note

`fdp_installer` currently tells every user they *must* choose a device. After
this change they usually need not. **This is a different repository** with its
own release cycle — do it as a separate commit there, and note that it only
takes effect once `fdp >= 0.5.0` is in the pinned environment.

**Files:**
- Modify: `/fusion/projects/dt/sammuli/fdp_dev/repos/fdp_installer/fdp_installer/bin/fdp_install.py:120-133`

- [ ] **Step 1: Replace `_print_device_selection_note`**

```python
def _print_device_selection_note():
    """Report which devices are available.

    fdp >= 0.5.0 composes all installed devices' environments and scopes
    per-command resolution by capability, so no selection is normally needed.
    """
    print(
        "\nThis environment includes multiple tokamaks (DIII-D 'd3d', "
        "MAST 'mast').\n"
        "No selection is required: `fdp env`/`fdp run` combine both, and\n"
        "`fdp ls`/`fdp login` pick the device that can service them.\n"
        "To pin one anyway:\n"
        "  - per command:  fdp --device d3d <subcommand> ...\n"
        "  - environment:  export FDP_DEFAULT_DEVICE=d3d\n"
        "  - persistent:   fdp device use d3d\n"
        "Run `fdp device list` to see what is installed."
    )
```

- [ ] **Step 2: Run the installer's own tests**

Run: `cd /fusion/projects/dt/sammuli/fdp_dev/repos/fdp_installer && pixi run python -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: OK (or unchanged from that repo's baseline — capture the baseline
first if it is not already green, and do not treat pre-existing failures as
yours)

- [ ] **Step 3: Commit in the fdp_installer repo**

```bash
cd /fusion/projects/dt/sammuli/fdp_dev/repos/fdp_installer
git add fdp_installer/bin/fdp_install.py
git commit -m "docs(install): device selection is no longer required

fdp >= 0.5.0 composes all installed devices' environments."
```

---

## Task 11: Full verification

- [ ] **Step 1: Full suite, twice**

```bash
cd /fusion/projects/dt/sammuli/fdp_dev/repos/fdp
pixi run python -m unittest discover -s tests -t tests -v 2>&1 | tail -5
pixi run python -m unittest discover -s tests -t tests -v 2>&1 | tail -5
```
Expected: OK both times, test count ≥ baseline 152 plus the ~30 added here.
Running twice catches inter-test pollution from the module-level catalog cache.

- [ ] **Step 2: Confirm env parity is untouched**

```bash
pixi run python -m unittest discover -s tests -t tests -k EnvParity -v
```
Expected: PASS. This test pins d3d's env output byte-for-byte against the
legacy `Device.to_env()`; composition must not perturb it.

- [ ] **Step 3: Exercise the real CLI end to end**

```bash
pixi run fdp device list
pixi run fdp env | head -5
pixi run fdp run echo hi          # expect: hi
pixi run fdp run -D d3d echo hi   # expect: hi
```

- [ ] **Step 4: Confirm the original bug is gone**

The dev env has only `d3d` installed, so composition cannot be proven here.
Simulate two devices against the real code:

```bash
pixi run python -c "
from unittest import mock
import fdp.catalog as c
from pathlib import Path
def ep(name, path):
    src = mock.MagicMock()
    src.read_text.return_value = Path(path).read_text()
    e = mock.MagicMock(); e.name = name; e.load.return_value = src
    return e
eps = [ep('d3d', '../toksearch_d3d/toksearch_d3d/data/d3d.yaml'),
       ep('mast', '../toksearch_mast/toksearch_mast/data/mast.yaml')]
with mock.patch('fdp.catalog.entry_points', return_value=eps):
    c.catalog._cache = None
    from fdp.devices import active_handles
    from fdp.environment import compose_device_config
    env = compose_device_config(active_handles())
    print('composed', len(env), 'vars across d3d+mast')
    assert 'PTDATA_JSON_INDEX_DIR' in env and 'MAST_ZARR_BASE_URL' in env
    print('OK: both devices contributed, no conflict')
"
```
Expected: `OK: both devices contributed, no conflict`

- [ ] **Step 5: Report results honestly**

State the actual test counts and any failures. Do not claim success without
the command output in hand.

---

## Spec coverage

| Spec section | Task |
|---|---|
| Env composition (`compose_device_config`, PATH handling) | 3 |
| Conflict error format, `DeviceEnvConflict` is a `ValueError` | 3 |
| Explicit selection bypasses composition | 3, 4 |
| Capability scoping (`candidate_devices`, `resolve_for_capability`) | 2 |
| `ls` → origin capability; `-D mast` crash fix; `pelican://` bonus | 5 |
| `login`/`logout` → bearer capability | 7 |
| D3: auto-login only when exactly one bearer device | 4 |
| `--device`/`-D` on both sides; `--default-device` alias | 6 |
| `fdp device list/show/use [--clear]`; `catalog` deprecated | 8 |
| Installer note reworded | 10 |
| Test #2: real-catalog regression guard | 3 |
| Test: env parity preserved | 11 |
| Release: `fdp` 0.5.0 | 9 |

Effort 2 (the `Device` abstraction in `toksearch`) is explicitly out of scope
for this plan; see the spec's "Future direction" section.
