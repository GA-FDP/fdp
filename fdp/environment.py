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
TDSVER, etc.) that applies regardless of device; and a *device* config
(MDSplus tree paths, PTData index, etc.) supplied by a `Device`. Merged
into ``os.environ`` by ``setup_environment``.
"""

import os
import sys
import warnings
from pathlib import Path

from .devices import Device, resolve_default_device


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


def setup_environment(
    device: str | Device | None = None,
    bearer_token: str | None = None,
    **overrides,
) -> None:
    """Populate os.environ with FDP variables and resolve BEARER_TOKEN.

    Resolves the active device (via `resolve_default_device`), merges
    its env contribution with the generic FDP config, and applies the
    result to ``os.environ``.

    Args:
        device: Optional device name (str) or Device instance to override
            default-device resolution.
        bearer_token: Optional explicit token. Falls back to
            ``$BEARER_TOKEN`` then ``~/.fdp/token``.
        **overrides: Force-set env vars (wins over both default config
            and existing os.environ).

    Mutates os.environ in place. Safe to call multiple times.
    """
    if isinstance(device, Device):
        active = device
    else:
        active = resolve_default_device(explicit=device)

    config = _generic_config()
    config.update(active.to_env())
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
