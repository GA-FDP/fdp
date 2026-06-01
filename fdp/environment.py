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


def _generic_config() -> dict:
    """FDP env vars that apply regardless of device."""
    env_dir = Path(sys.executable).parent.parent
    lib_dir = env_dir / "lib"
    bin_dir = env_dir / "bin"
    return {
        # XRootD / Pelican
        "XRDCP_ALLOW_HTTP": "true",
        "XRD_PELICANUSEAUTHHEADERS": "true",
        "XRD_CURLDISABLEPREFETCH": "1",
        "XRD_PLUGINCONFDIR": _get_default_xrd_pluginconfdir() or "",
        # Thread-affinity vars (keep NumPy / MKL single-threaded)
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        # pymssql TLS requirement
        "TDSVER": "7.0",
        # SSL trust store
        "X509_CERT_FILE": str(env_dir / "ssl" / "cacert.pem"),
        # Prepend env's bin/ to PATH
        "PATH": f"{bin_dir}:{os.getenv('PATH', '')}",
        # MDSplus TDI search path
        "MDS_PATH": str(env_dir / "tdi"),
        # PTData library hookup (the libs themselves are device-agnostic;
        # the *index* directory is device-specific and comes from the Device)
        "PTDATA_LOC": os.getenv("PTDATA_LOC", "1"),
        "PTDATA_LIBRARY": str(lib_dir / "libd3.so"),
        "PTDATA_PLUGIN_LIB": str(lib_dir / "libjson_index_plugin.so"),
    }


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
    ptd = [l for l in handle.schema.locators if l.kind == "ptdata_indexed"]
    if ptd:
        out["PTDATA_JSON_INDEX_DIR"] = ptd[-1].index_dir

    # extra_env passes through verbatim.
    out.update(handle.extra_env)
    return out


def apply_environment(config: dict, env: dict) -> None:
    """Apply config to env, preserving existing values except PATH.

    PATH is overwritten unconditionally because config["PATH"] is built
    by prepending env's bin/ to the existing PATH at config-build time;
    we must always write it through to honor that prepending.
    """
    if "PATH" in config:
        env["PATH"] = config["PATH"]
    for k, v in config.items():
        if k == "PATH" or v is None:
            continue
        env.setdefault(k, str(v))


def _resolve_device_env(device: str | None) -> dict:
    """Return tokamak-specific env vars from the catalog.

    Resolution order (first match wins):
      1. ``device`` argument if supplied.
      2. ``$FDP_DEFAULT_DEVICE`` environment variable.
      3. Auto-select if exactly one tokamak is registered.

    Raises ``KeyError`` if the named tokamak isn't in the catalog and
    ``ValueError`` if no default can be determined (0 or 2+ registered).
    """
    if device is None:
        device = os.environ.get("FDP_DEFAULT_DEVICE") or None
    if device is not None:
        return _tokamak_env(_catalog[device])
    # Auto-detect: if exactly one tokamak is registered, use it.
    names = _catalog.names()
    if len(names) == 1:
        return _tokamak_env(_catalog[names[0]])
    if len(names) == 0:
        raise ValueError(
            "No tokamak contributors are installed. "
            "Install a device package (e.g. toksearch_d3d) to provide one."
        )
    raise ValueError(
        f"No default tokamak selected and {len(names)} are registered "
        f"({names}). Pass --default-device or set FDP_DEFAULT_DEVICE."
    )


def setup_environment(
    device: str | None = None,
    bearer_token: str | None = None,
    **overrides,
) -> None:
    """Populate os.environ with FDP variables and resolve BEARER_TOKEN.

    Resolves the active tokamak from the catalog, merges its env
    contribution with the generic FDP config, and applies the result to
    ``os.environ``.

    Args:
        device: Optional tokamak name (str) to override default resolution.
            If None, resolves via ``$FDP_DEFAULT_DEVICE`` or auto-detection.
        bearer_token: Optional explicit token. Falls back to
            ``$BEARER_TOKEN`` then ``~/.fdp/token``.
        **overrides: Force-set env vars (wins over both default config
            and existing os.environ).

    Mutates os.environ in place. Safe to call multiple times.
    """
    config = _generic_config()
    config.update(_resolve_device_env(device))
    apply_environment(config, os.environ)

    for key, value in overrides.items():
        os.environ[key] = str(value)

    if not bearer_token:
        bearer_token = os.environ.get("BEARER_TOKEN", "")
    if not bearer_token:
        token_file = Path.home() / ".fdp" / "token"
        try:
            bearer_token = token_file.read_text().strip()
        except (OSError, UnicodeDecodeError):
            warnings.warn(
                "No BEARER_TOKEN specified. "
                "This will cause problems with FDP access."
            )
    os.environ["BEARER_TOKEN"] = bearer_token
