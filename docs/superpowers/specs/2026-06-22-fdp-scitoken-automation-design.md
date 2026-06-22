# Design: Automated SciToken Acquisition in the `fdp` CLI

**Date:** 2026-06-22
**Status:** Approved (brainstorming complete; pending implementation plan)
**Repo:** `fdp`

## Problem

`fdp` can *read* a bearer token (a SciToken JWT) but cannot *create* one. Today
users obtain tokens out-of-band via the `pelican` GitHub-OAuth flow, drop the
resulting JWT into `~/.fdp/`, and hand-manage a `~/.fdp/token` symlink pointing
at a dated file (e.g. `BSammuli_Jun16_1month`). `resolve_bearer_token()` only
checks the `-t` arg, then `$BEARER_TOKEN`, then `~/.fdp/token`, and warns if none
is found.

We want `fdp` to mint and refresh a SciToken itself — via the same
GitHub-backed OAuth flow — and feed it into the `BEARER_TOKEN` path it already
manages.

## Decisions (from brainstorming)

- **Trigger:** both an explicit `fdp login` subcommand *and* transparent
  auto-acquire when a command needs a token and none is valid, with an opt-out
  for batch/non-interactive use.
- **Mechanism:** shell out to the `pelican` client (already a runtime
  dependency, `pelicanplatform >=7.24.3`), which already implements the
  federation-aware GitHub OAuth flow. Specifically
  `pelican credentials token get <read|write> <pelican-url>`.
  (`pelican token create` is the wrong command — it locally signs with a
  private key, for origins, not end users.)
- **Storage:** a dedicated fdp-managed cache file per device. The existing
  manual `~/.fdp/token` symlink + dated files remain a valid fallback and are
  never clobbered.
- **Code organization:** a new `fdp/auth.py` module owns the whole credential
  lifecycle; `environment.py` and `cli.py` call into it and never touch
  `pelican` or the cache directly.

## Catalog inputs

Everything `auth.py` needs comes from the resolved `TokamakHandle`:

- `handle.schema.pelican_root` → the namespace URL for
  `pelican credentials token get` (e.g.
  `pelican://osg-htc.org:443/fdp-d3d`).
- Each locator's `auth` (`AuthHint`, `kind="bearer_token"`, `env="BEARER_TOKEN"`)
  → which env var the token populates and whether the device uses bearer auth
  at all.

A device with no `bearer_token` locator (e.g. a public device like MAST) needs
no token; login/auto-acquire are no-ops for it.

## Section 1 — `fdp/auth.py` module surface

```python
def get_valid_token(handle, explicit=None) -> str | None:
    """Resolution-order lookup. Returns a usable token or None.
    Never triggers an interactive flow."""

def ensure_token(handle, explicit=None, *, interactive=None) -> str | None:
    """get_valid_token(); if None and auto-login is allowed + possible,
    run login() then re-resolve. Called by setup_environment().
    interactive=None means auto-detect via TTY (Section 4); callers may
    pass True/False to force the gate (e.g. fdp env passes False)."""

def login(handle, *, write=False) -> CachedToken:
    """Always mint fresh via pelican, write the cache, return metadata.
    Called by `fdp login`. CachedToken is a lightweight in-memory value
    (device, scope, exp) used only for the printed summary — it is NOT
    the on-disk format; the cache file holds the bare JWT (Section 3)."""

def logout(handle) -> bool:
    """Delete the managed cache file for this device.
    Returns whether a file was removed."""
```

Keeping pelican-shell-out and cache I/O behind these four functions means
`environment.py` and `cli.py` never touch pelican or the cache directly.

## Section 2 — Token resolution & precedence

`get_valid_token()` returns the first **usable** source:

1. **Explicit** (`-t/--bearer-token`) — trusted verbatim, no expiry check.
   Also the batch-job injection path.
2. **`$BEARER_TOKEN`** (already in env) — trusted verbatim, no expiry check.
3. **Managed cache** `~/.fdp/cache/<device>.token` — used only if it decodes
   and `exp >= now + 5 min`.
4. **Legacy `~/.fdp/token`** — used only if it decodes and is unexpired
   (manual symlink/dated files remain a valid fallback).

If nothing usable is found, returns `None`.

Rationale: user-supplied tokens (1, 2) are trusted as-is — never second-guess
an explicit override, and that is how non-interactive jobs supply credentials.
File-based sources (3, 4) are expiry-checked because an expired file token only
fails later at XRootD; catching it here is what lets auto-login engage. Expiry
is read by base64url-decoding the JWT payload's `exp` claim — no signature
verification, no new dependency.

## Section 3 — Acquisition flow & cache format

`login(handle, write=False)`:

1. Derive the namespace URL from `handle.schema.pelican_root`.
2. Shell out:
   `pelican credentials token get <read|write> <pelican_root> --json`
   (`read` by default; `write` when `--write` is passed). This runs the
   GitHub-backed OAuth flow — registering an OAuth client on first use,
   reusing the cached registration thereafter.
3. Parse the raw JWT from pelican's output.
4. Write it to the managed cache.

**Open implementation item (first task):** confirm whether
`pelican credentials token get` emits the raw token to stdout (via `--json` or
plain) or only writes it into pelican's own credential store. The design
assumes the raw token string can be captured; verifying the exact extraction is
the first implementation step.

**Cache file:** `~/.fdp/cache/<device>.token`, mode `0600`, in a `~/.fdp/cache/`
directory created `0700`. The file contains the **bare JWT and nothing else** —
identical format to the legacy `~/.fdp/token`. Resolution reads the file →
`decode_exp()` → compares to `now + 5 min`. The same decode helper serves both
file sources, so there is one read/decode code path, no second format, no
token/`exp` drift risk, and `logout` is a plain `unlink`. `<device>` is the
catalog name, so devices cache independently and never collide.

## Section 4 — CLI surface & auto-acquire control

New subcommands:

- **`fdp login [--write]`** — mint + cache for the resolved device. Prints a
  summary: device, scope, human-readable expiry (e.g. "valid until
  2026-07-22 14:00 UTC, 30 days"). Honors global `-D/--default-device`.
- **`fdp logout`** — delete the managed cache file for the device; report
  whether one was removed.

**Auto-acquire** is wired into the existing `setup_environment()` path that
`fdp run` already calls. `ensure_token()` fires the login flow only when **all**
hold:

- no usable token was found (Section 2), **and**
- not opted out via `FDP_NO_AUTO_LOGIN=1`, **and**
- the session is interactive — both stdin **and** stderr are TTYs.

Otherwise it falls back to today's behavior: warn ("no valid BEARER_TOKEN; run
`fdp login`") and proceed token-less. The TTY gate keeps batch jobs from hanging
on a browser prompt.

`fdp env` is a deliberate exception: its output is normally `eval`'d, so it must
**never** auto-launch a flow. It warns and emits whatever token it has. Thus
auto-acquire effectively applies to `fdp run` and `setup_environment()` library
callers, not `fdp env`.

## Section 5 — Error handling & edge cases

- **`pelican` binary missing** — `login()` raises a clear error; auto-acquire
  catches it and degrades to warn-and-continue rather than crashing `fdp run`.
- **OAuth flow fails / user aborts** — surface pelican's stderr, leave any
  existing cache untouched; exit non-zero for explicit `fdp login`,
  warn-and-continue for auto-acquire.
- **Device declares no `bearer_token` auth** — `login()`/`ensure_token()` are
  no-ops that say so, mirroring `resolve_bearer_token()` returning `None`.
- **`pelican_root` is `None`** — cannot build the namespace URL; `login()`
  errors with that explanation.
- **Corrupt/undecodable cache or legacy file** — treated as "not usable" (like
  expired), not a crash; resolution falls through.
- **Cache writes** — `mkdir(exist_ok=True)`; write via temp file + atomic
  `os.replace` so a concurrent `fdp run` never reads a half-written token.
- **Clock skew** — the 5-minute validity margin absorbs minor skew.

## Section 6 — Testing

Under `fdp/tests/`, all pelican calls mocked — no real OAuth or network:

- **`decode_exp()`** — valid, expired, no-`exp`, malformed base64, not-a-JWT.
- **Resolution precedence** — fake `handle` + `tmp_path` HOME: explicit >
  `$BEARER_TOKEN` > fresh cache > valid legacy; expired cache/legacy skipped;
  all-empty → `None`.
- **Acquisition** — `login()` with `subprocess.run` monkeypatched to return a
  canned JWT; assert cache written `0600` with the bare JWT; missing-pelican
  degrades correctly.
- **Auto-acquire gating** — `ensure_token()` does *not* invoke the flow under
  `FDP_NO_AUTO_LOGIN=1`, non-TTY stdin/stderr (monkeypatched `isatty`), or when
  a valid token exists; *does* when all gates pass.
- **CLI smoke** — `fdp login` / `fdp logout` argparse wiring → right handlers,
  right device.
- **Parity guard** — `test_env_parity.py` still passes (token plumbing must not
  perturb the D3D env-var fixture).

## Out of scope

- Controlling token lifetime/scope beyond read/write — the OAuth-issued token's
  lifetime is set by the issuer, not by fdp.
- Replacing or migrating the user's existing manual `~/.fdp/` token files.
- A native (non-pelican) OAuth implementation.
