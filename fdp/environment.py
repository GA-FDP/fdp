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

"""FDP environment configuration.

Two halves: a *generic* config (XRootD plugin path, thread-affinity vars,
TDSVER, etc.) that applies regardless of tokamak; and a *tokamak* config
(MDSplus tree paths, PTData index, etc.) from the catalog. Merged into
``os.environ`` by ``setup_environment``.
"""

import os
import sys
import warnings
from pathlib import Path

from . import auth
from .catalog import catalog as _catalog


def _get_default_xrd_pluginconfdir() -> str | None:
    """Find XRootD client plugin config dir based on the active env.

    Order:
      1. CONDA_PREFIX (set by activated conda envs)
      2. PREFIX (set by rattler-build inside a recipe test)
      3. existing XRD_PLUGINCONFDIR env var
    """
    conda_prefix = os.getenv("CONDA_PREFIX", None)
    prefix = os.getenv("PREFIX", None)

    def _plugin_conf_path(base_dir: str) -> str:
        return os.path.join(base_dir, "etc", "xrootd", "client.plugins.d")

    if conda_prefix is not None:
        return _plugin_conf_path(conda_prefix)
    elif prefix is not None:
        return _plugin_conf_path(prefix)
    else:
        val = os.getenv("XRD_PLUGINCONFDIR", None)
        if val is None:
            warnings.warn(
                "XRD_PLUGINCONFDIR is not set. "
                "This may cause problems with FDP access."
            )
        return val


def _locator_kinds(handle) -> set:
    if handle is None:
        return set()
    return {l.kind for l in handle.schema.locators}


def _has_xrootd_transport(handle) -> bool:
    if handle is None:
        return False
    return any(
        getattr(l, "transport", None) in ("pelican", "xrootd")
        for l in handle.schema.locators
    )


def _has_bearer_auth(handle) -> bool:
    if handle is None:
        return False
    return any(
        getattr(l, "auth", None) is not None
        and l.auth.kind == "bearer_token"
        for l in handle.schema.locators
    )


def _generic_config(handle=None) -> dict:
    """FDP env vars. Universal vars are always emitted; transport/auth
    vars only when the device declares a locator that needs them. With
    handle=None only the universal vars are returned."""
    env_dir = Path(sys.executable).parent.parent
    lib_dir = env_dir / "lib"
    bin_dir = env_dir / "bin"

    config = {
        # Thread-affinity vars (keep NumPy / MKL single-threaded)
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        # Prepend env's bin/ to PATH
        "PATH": f"{bin_dir}:{os.getenv('PATH', '')}",
    }

    kinds = _locator_kinds(handle)

    # XRootD / Pelican transport — only for pelican/xrootd locators.
    if _has_xrootd_transport(handle):
        config.update({
            "XRDCP_ALLOW_HTTP": "true",
            "XRD_PELICANUSEAUTHHEADERS": "true",
            "XRD_CURLDISABLEPREFETCH": "1",
            "XRD_PLUGINCONFDIR": _get_default_xrd_pluginconfdir() or "",
            "X509_CERT_FILE": str(env_dir / "ssl" / "cacert.pem"),
        })

    # MDSplus TDI search path — only with an mds_tree locator.
    if "mds_tree" in kinds:
        config["MDS_PATH"] = str(env_dir / "tdi")

    # PTData library hookup — only with a ptdata_indexed locator.
    if "ptdata_indexed" in kinds:
        config.update({
            "PTDATA_LOC": os.getenv("PTDATA_LOC", "1"),
            "PTDATA_LIBRARY": str(lib_dir / "libd3.so"),
            "PTDATA_PLUGIN_LIB": str(lib_dir / "libjson_index_plugin.so"),
        })

    # pymssql TLS requirement — only with a sql locator.
    if "sql" in kinds:
        config["TDSVER"] = "7.0"

    return config


def _tokamak_env(handle) -> dict[str, str]:
    """Derive tokamak-specific env vars from a TokamakHandle's locators.

    Output mirrors the legacy Device.to_env() for D3D byte-for-byte —
    pinned by test_env_parity.py.
    """
    out: dict[str, str] = {}
    delim = handle.extra_env.get("SYS_D3_DELIM", ";")

    # default_tree_path = delim-joined search_path entries from all
    # mds_tree locators (v1: one per tokamak, but the schema permits more).
    mds = [l for l in handle.schema.locators if l.kind == "mds_tree"]
    if mds:
        out["default_tree_path"] = delim.join(
            p for m in mds for p in m.search_path
        )

    # PTDATA_JSON_INDEX_DIR — last ptdata_indexed wins if multiple.
    # When that locator carries an index_pattern, index_dir is the PARENT
    # and PTDATA_JSON_INDEX_PATTERN tells the libfdpio plugin to select the
    # latest matching subdir at read time.
    ptd = [l for l in handle.schema.locators if l.kind == "ptdata_indexed"]
    if ptd:
        out["PTDATA_JSON_INDEX_DIR"] = ptd[-1].index_dir
        if ptd[-1].index_pattern:
            out["PTDATA_JSON_INDEX_PATTERN"] = ptd[-1].index_pattern

    # Zarr-store locators → env consumed by zarr-based signal packages
    # (e.g. toksearch_mast). v1 emits the first zarr_store locator.
    zarr = [l for l in handle.schema.locators if l.kind == "zarr_store"]
    if zarr:
        z = zarr[0]
        out["MAST_ZARR_BASE_URL"] = z.base_url
        out["MAST_ZARR_PROTOCOL"] = z.protocol
        out["MAST_ZARR_FILE_NAME_FORMAT"] = z.file_name_format
        if z.endpoint:
            out["MAST_ZARR_ENDPOINT"] = z.endpoint

    # HTTP metadata catalog → env for parquet/REST shot-list helpers.
    cat = [l for l in handle.schema.locators if l.kind == "http_catalog"]
    if cat:
        c = cat[0]
        out["MAST_CATALOG_URL"] = c.base_url
        out["MAST_CATALOG_SHOTS_PATH"] = c.shots_path
        if c.signals_path:
            out["MAST_CATALOG_SIGNALS_PATH"] = c.signals_path

    # extra_env passes through verbatim.
    out.update(handle.extra_env)
    return out


def apply_environment(config: dict, env: dict) -> None:
    """Apply config to env, preserving existing values except PATH.

    PATH is overwritten unconditionally because config["PATH"] is built
    by prepending env's bin/ to the existing PATH at config-build time;
    we must always write it through to honor that prepending.

    Note: ``setdefault`` semantics mean this never *clears* a key already in
    ``env``. FDP assumes one device per process (one device package per pixi
    env); switching devices within a single process would leave the prior
    device's vars (e.g. ``MAST_*`` vs ``XRD_*``) stale. That is not a
    supported workflow.
    """
    if "PATH" in config:
        env["PATH"] = config["PATH"]
    for k, v in config.items():
        if k == "PATH" or v is None:
            continue
        env.setdefault(k, str(v))


def _resolve_device_handle(device):
    """Return the TokamakHandle for the active device.

    Resolution order: explicit ``device`` arg, then ``$FDP_DEFAULT_DEVICE``,
    then auto-select if exactly one tokamak is registered.
    """
    if device is None:
        device = os.environ.get("FDP_DEFAULT_DEVICE") or None
    if device is not None:
        return _catalog[device]
    names = _catalog.names()
    if len(names) == 1:
        return _catalog[names[0]]
    if len(names) == 0:
        raise ValueError(
            "No tokamak contributors are installed. "
            "Install a device package (e.g. toksearch_d3d) to provide one."
        )
    raise ValueError(
        f"No default tokamak selected and {len(names)} are registered "
        f"({names}). Pass --default-device or set FDP_DEFAULT_DEVICE."
    )


def build_device_config(handle) -> dict:
    """Assemble the full env-var dict for a resolved device handle:
    generic (locator-gated) config merged with the tokamak's catalog env.
    Shared by setup_environment() and the `fdp env` CLI so the printed env
    and the in-process env never drift."""
    config = _generic_config(handle)
    config.update(_tokamak_env(handle))
    return config


def resolve_bearer_token(handle, bearer_token=None) -> "str | None":
    """Resolve a usable bearer token for a device, or None when the device
    declares no bearer auth or nothing usable is found. Delegates to
    fdp.auth; never triggers an interactive flow."""
    return auth.get_valid_token(handle, explicit=bearer_token)


def setup_environment(
    device: str | None = None,
    bearer_token: str | None = None,
    *,
    auto_login: bool = False,
    **overrides,
) -> None:
    """Populate os.environ with FDP variables for the active tokamak.

    Env emission is locator-driven. When auto_login is True (set by
    `fdp run`), a missing/expired token triggers the interactive
    `fdp login` flow subject to TTY / FDP_NO_AUTO_LOGIN gating. Mutates
    os.environ in place. Safe to call repeatedly.
    """
    handle = _resolve_device_handle(device)
    apply_environment(build_device_config(handle), os.environ)

    for key, value in overrides.items():
        os.environ[key] = str(value)

    if auto_login:
        token = auth.ensure_token(handle, explicit=bearer_token)
    else:
        token = auth.get_valid_token(handle, explicit=bearer_token)

    if token is not None:
        env_var = auth._bearer_env(handle) or "BEARER_TOKEN"
        os.environ[env_var] = token
    elif auth._bearer_env(handle) is not None and not auto_login:
        warnings.warn("No valid BEARER_TOKEN found; run `fdp login`.")
