# fdp device resolution: env composition + capability-scoped selection

**Date:** 2026-07-21
**Status:** Draft — awaiting review
**Scope:** the `fdp` package only. No changes to `toksearch`, `toksearch_d3d`,
`toksearch_mast`, `ptdata`, or the catalog schema.

## Problem

Since `fdp-core` became multi-device (it pins both `toksearch_d3d` and
`toksearch_mast`), every DIII-D user who upgrades hits this on commands that
worked the day before:

```
$ fdp run python my_pipeline.py
Error: No default tokamak selected and 2 are registered (['d3d', 'mast']).
```

`_resolve_device_handle` (`fdp/environment.py:213`) auto-selects a device only
when *exactly one* is registered. `fdp-core` guarantees two, so the fallback
never fires and every user must configure something before `fdp` will act.

`fdp_install.py:_print_device_selection_note` prints instructions once at
install time. That is easy to miss and gone by the next shell.

Four smaller defects compound it:

1. **The flag position is unnatural.** `--default-device` is a top-level
   argparse argument, so the ergonomic form fails:
   `fdp run --default-device d3d echo hi` → `error: unrecognized arguments`.
   Only `fdp --default-device d3d run echo hi` works.
2. **The name misleads.** `--default-device` reads as "set the default," but it
   is a per-invocation selector that persists nothing.
3. **`fdp ls -D mast` crashes.** `_resolve_origin_server` (`fdp/cli.py:105`)
   returns `handle.schema.origin_server`, which is `str | None` and is `None`
   for mast. `FdpFileSystem(None)` follows.
4. **No CLI way to set a default.** The error tells users to hand-edit
   `~/.fdp/config.toml`.

## Root cause

The ambiguity is manufactured. Measured across the two registered devices:

| Measure | Value |
|---|---|
| env keys emitted by d3d | 20 |
| env keys emitted by mast | 11 |
| keys shared by both | 4 — `MKL_NUM_THREADS`, `NUMEXPR_NUM_THREADS`, `OMP_NUM_THREADS`, `PATH` |
| **keys where they disagree** | **0** |

Every device-specific variable is disjoint — `PTDATA_*`, `XRD_*`,
`default_tree_path`, `MDS_PATH`, `D3DATA` versus `MAST_ZARR_*`,
`MAST_CATALOG_*`. The four shared keys are generic and carry identical values.

So `fdp run` refuses to act not because the devices disagree, but because the
code assumed they might. It treats "more than one package is installed" as a
proxy for "these configurations conflict," and those are different questions.

The second assumption is that every subcommand needs *a* device. It does not:
`fdp ls` needs a device that has an origin server; `fdp login` needs a device
that wants a bearer token; `fdp run` needs the union of everything installed.

## Design principle

Replace "pick one device up front" with two rules:

> **Composition.** `fdp env` and `fdp run` emit the *union* of all registered
> devices' environments, and fail only on a mechanically-detected key conflict.
>
> **Capability scoping.** Every other subcommand asks the catalog which devices
> can service *that operation*, and requires a choice only when that set has
> more than one member.

Ambiguity becomes a real, checkable condition rather than a proxy. Both rules
are derived from catalog data, so neither is a heuristic guess.

## Components

### 1. Env composition (`fdp env`, `fdp run`)

New function in `fdp/environment.py`:

```python
def compose_device_config(handles) -> dict:
    """Merge per-device env dicts. Raises DeviceEnvConflict on any key two
    devices assign different values."""
```

- Build `build_device_config(h)` for each handle.
- Merge in catalog-name order (deterministic, not entry-point order).
- On a key present in two dicts with unequal values, raise
  `DeviceEnvConflict` naming the key and both devices.

`PATH` needs care: `_generic_config` builds it by prepending the environment's
`bin/` to the *current* `PATH`. Every device computes an identical string
(it derives from `sys.executable`), so it merges without conflict. The
composer must not prepend once per device; it takes the single merged value,
which `apply_environment` then writes through unconditionally as it does today.

**Conflict error format** (a hard error, per decision D2):

```
Error: devices 'd3d' and 'devB' set PTDATA_JSON_INDEX_DIR to different values:
  d3d  = pelican://osg-htc.org:443/fdp-d3d/archives/index/json
  devB = pelican://osg-htc.org:443/fdp-b/archives/index/json
Select one device with `fdp --device d3d ...`, $FDP_DEFAULT_DEVICE, or
~/.fdp/config.toml [device].default.
```

When a device *is* explicitly selected (flag, env var, or config file),
composition is skipped and only that device's env is emitted. That is both the
escape hatch from a conflict and exact back-compat with today's behavior.

### 2. Capability-scoped selection

```python
def candidate_devices(capability: str) -> list[TokamakHandle]
```

| Subcommand | Capability | Predicate | Today's result |
|---|---|---|---|
| `ls` | `origin` | `schema.origin_server is not None` | `[d3d]` — unambiguous |
| `login`, `logout` | `bearer` | any locator with `auth.kind == "bearer_token"` | `[d3d]` — unambiguous |
| `env`, `run` | — | composition; no selection | n/a |

Resolution for a capability-scoped subcommand:

1. Explicit selection (flag → `$FDP_DEFAULT_DEVICE` → `config.toml`) wins. If
   the named device lacks the capability, error saying so explicitly — this is
   what fixes `fdp ls -D mast` crashing on `FileSystem(None)`.
2. Otherwise, exactly one candidate → use it.
3. Zero candidates → error naming the capability
   (`no registered device has an origin server`).
4. Two or more → error listing the candidates and the three ways to choose.

Both `fdp ls` and `fdp login` therefore work with no configuration today, and
keep working when a second Pelican-hosted device is added — at which point the
error is genuine rather than incidental.

As a low-cost bonus, if the `ls` path is given as a full `pelican://` URL that
prefix-matches exactly one device's `pelican_root`, that device is selected
before falling through to the capability rule. Typical invocations pass a
path relative to the origin, so this is a convenience, not the mechanism.

### 3. Auto-login under composition

**Open decision (D3) — recommendation below, confirm before implementing.**

`fdp run` sets `auto_login=True`, which triggers the interactive `pelican`
consent flow when no valid token is found. Under composition, "which devices do
we ensure tokens for?" becomes live.

Recommendation: **auto-login only when exactly one registered device declares
bearer auth.** If several do, emit the composed env without tokens and warn
`run 'fdp login --device <name>'`. This preserves today's behavior exactly for
every current user (d3d auto-logs-in; mast needs nothing) and fails toward "no
surprise prompt" rather than "surprise prompt for data you never touch."

Tokens are written to each device's own `auth.bearer_env` var. Two devices
sharing one env var with different tokens is a conflict, caught by the same
composition check.

### 4. CLI ergonomics

- Rename the flag to **`--device` / `-D`**; keep `--default-device` as a
  hidden, still-functional alias (it appears in shipped docs and the installer
  note).
- **Accept the flag after the subcommand too.** Add it to each subparser as
  well as the top-level parser, so `fdp run -D d3d ...` and
  `fdp -D d3d run ...` are equivalent. The subparser value wins when both are
  given. `run` uses `argparse.REMAINDER`, so the flag must be declared on the
  `run` subparser before the REMAINDER argument to bind correctly; a test
  pins that `fdp run -D d3d python -c '...'` does not swallow `-D`.
- Add **`fdp device`** as the home for device operations:
  - `fdp device list` — names, descriptions, and capabilities
  - `fdp device show <name>` — the full catalog YAML
  - `fdp device use <name>` — write `[device].default` to
    `~/.fdp/config.toml`, creating it if absent
  - `fdp device use --clear` — remove the setting
  `fdp catalog list`/`show` remain as deprecated aliases.

`fdp device use` matters because the remaining error paths all end with "set a
default," and hand-editing TOML is the friction that produced this spec.

### 5. Installer note

`_print_device_selection_note` currently tells every user they must choose a
device. After this change they usually need not. Reword to state which devices
are available and mention `fdp device use` for when a choice *is* wanted.

## Data flow

```
fdp run python x.py
  └─ needs_env → setup_environment(device=None)
       ├─ explicit selection? ── yes ──▶ build_device_config(handle)      [old path]
       └─ no ──▶ compose_device_config(all handles)
                   ├─ conflict? ──▶ DeviceEnvConflict → clean error, exit 1
                   └─ ok ──▶ apply_environment(merged, os.environ)
                              └─ auto-login iff exactly one bearer device
```

## Error handling

`DeviceEnvConflict` subclasses `ValueError`, so `cli.main`'s existing
`except (ValueError, KeyError)` continues to render a clean message and
`exit(1)` rather than a traceback. No new handler branch.

## Testing

Extending `tests/test_device_resolution.py`, which already patches the catalog
entry points to register fake devices with no network access.

1. **Conflict-free composition** — two fake devices, disjoint keys; `fdp run`
   succeeds with no selection configured and the merged env contains both
   devices' vars.
2. **Regression guard on the real catalog** — for every pair of
   *actually registered* devices, assert `compose_device_config` raises
   nothing. This is the falsifiability property: a future device that genuinely
   collides fails CI instead of silently corrupting an environment.
3. **Conflict detection** — two fake devices assigning different values to one
   key; assert exit 1 and that stderr names the key and both devices.
4. **Explicit selection bypasses composition** — `-D d3d` emits only d3d's env,
   even when a conflicting device is registered.
5. **Capability scoping** — `fdp ls` with d3d+mast resolves to d3d without
   configuration; `fdp ls -D mast` errors cleanly instead of crashing;
   two origin-bearing devices error and list both.
6. **Flag position parity** — `fdp -D d3d run` and `fdp run -D d3d` produce
   identical env; `-D` is not passed through to the child command.
7. **`fdp device use`** — writes `[device].default` to a temp `$HOME`, is
   picked up by the next resolution, and `--clear` removes it.
8. **Env parity preserved** — `test_env_parity.py` must still pass unchanged;
   composition must not perturb single-device output.

## Out of scope

- Any change to `toksearch`, `toksearch_d3d`, `toksearch_mast`, or `ptdata`.
- The `Device` abstraction (see Future direction).
- Per-device shot-range validation.
- Changes to `fdp_schema`. Capability predicates read fields that already
  exist (`origin_server`, `auth.kind`); no new `capabilities` field is added,
  because a declared capability list could drift from the locators that
  actually back it.

## Future direction (Effort 2 — separate spec)

This spec deliberately treats the symptom at the CLI boundary. The deeper issue
is that **device identity is ambient process state instead of a value carried
by the signal**, which is what forces a single global choice.

Two of three backends already support per-signal configuration:

- `MdsSignal` accepts `location` / `MdsTreePath` and sets `${tree}_path`
  *temporarily around the fetch* (`toksearch/signal/mds.py:80`), needing no
  ambient env.
- `ZarrSignal` takes `fs` and `file_name_format` explicitly and reads no env;
  `MastSignal` merely reads env to *fill those parameters in*, and all are
  overridable.
- `PtDataSignal` is the outlier: `ptdata.PtDataReader()` takes no config and
  captures the environment at construction, behind a process-global
  `PtDataReaderRegistry`.

Crucially, this does **not** require changing ptdata's C++ API. A
device-bound PTData signal can wrap its `gather` in scoped `set_env(...)` drawn
from its own locator — exactly the pattern `MdsTreePath` already uses. The only
real obstacle is `PtDataReaderRegistry`'s single-slot cache, which would need
keying by config identity rather than one global slot.

Intended shape:

```python
d3d  = fdp.device("d3d")
pipe = d3d.pipeline(shots)              # == Pipeline(shots, device=d3d)
pipe.fetch("ip", d3d.ptdata("ip"))
pipe.fetch("ipmhd", d3d.mds(r"\ipmhd", "efit01"))
```

Decisions already reached:

- **Devices are data, not types.** No `D3DPipeline` subclass. A device is
  contributed today as a YAML file through the `fdp_schema.catalogs` entry
  point with no Python; encoding device identity in a class would break that
  and force a subclass per device.
- **`device=` is the mechanism; `device.pipeline(...)` is sugar over it.**
- **`toksearch` accepts `device` as a duck-typed protocol**, never importing
  `fdp`. The current dependency direction (`toksearch` is device-neutral) is
  preserved.
- **One device per Pipeline, enforced at `.fetch()`** — shot numbering is
  device-specific. Cross-device analysis is a join of two pipelines' *outputs*,
  a downstream pandas/xarray concern, explicitly outside Pipeline's scope.

What the device on a Pipeline earns:

1. Binds unbound signals at `.fetch()`, so existing scripts keep working.
2. Rejects cross-device signals immediately rather than after a long compute.
3. Gives **worker-side setup** a principled home. Under `compute_ray` /
   `compute_spark`, workers do not reliably inherit the parent's env; today
   correctness depends on `fdp run` plus fork inheritance. This is an existing
   latent fragility, not a hypothetical.
4. Makes shot-list provenance explicit (`from_sql` against d3drdb is inherently
   a d3d operation).

Effort 1 is a bridge to this, not a dead end: composed env is exactly the
back-compat layer needed while signals migrate, and `fdp run` can shed
device-specific vars incrementally as each backend becomes device-bound.

Effort 2 cannot replace Effort 1. `fdp ls` and `fdp login` are not signal
fetches — no signal object exists to carry the device — so capability scoping is
needed regardless.

## Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Compose env rather than select a device for `run`/`env` | The devices do not actually conflict; measured 0 collisions |
| D2 | Hard error on a genuine key conflict | Never silently wrong; failure lands at the cause |
| D3 | Auto-login only when exactly one device declares bearer auth | **Open — confirm.** Preserves current behavior; no surprise prompts |
| D4 | Capability scoping instead of a global default | `ls` and `login` need different device sets; a global default answers a question they did not ask |
| D5 | `--device` accepted before *and* after the subcommand | The natural form currently errors |
| D6 | No `capabilities` field in `fdp_schema` | A declared list could drift from the locators backing it |

## Release

`fdp` 0.4.2 → **0.5.0** (new subcommand, changed resolution behavior). Then
`fdp-core/members.yaml` is bumped and regenerated via `pixi run gen`. No other
package changes version.
