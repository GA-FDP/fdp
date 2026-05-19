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

"""Tokamak device contributors for fdp.

Devices are discovered via the `fdp.devices` Python entry-point group.
Each contributor registers a `Device` instance under a short name.
With no contributors installed, `discover_devices()` returns an empty
dict; install a device package (e.g. `toksearch_d3d`) to get one.
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Iterator


# Runtime-adjustable env vars: these can be swapped mid-process by
# `Device.activate()`. Load-time-locked vars (XRD_PLUGINCONFDIR,
# PTDATA_JSON_INDEX_DIR for libfdpio, BEARER_TOKEN) are NOT in this set.
_RUNTIME_ADJUSTABLE_KEYS = ("default_tree_path",)


@dataclass(frozen=True)
class Device:
    """A tokamak data platform configuration.

    Each contributor package registers an instance via the `fdp.devices`
    entry point. fdp's CLI and `setup_environment` consume these.
    """

    name: str
    pelican_root: str
    origin_server: str
    ptdata_index_dir: str | None = None
    mds_default_tree_path: str | None = None
    description: str = ""
    default_llm_preset: str | None = None
    extra_env: dict = field(default_factory=dict)

    def to_env(self) -> dict:
        """Return the env-var dict this device contributes."""
        out = {}
        if self.mds_default_tree_path is not None:
            out["default_tree_path"] = self.mds_default_tree_path
        if self.ptdata_index_dir is not None:
            out["PTDATA_JSON_INDEX_DIR"] = self.ptdata_index_dir
        out.update(self.extra_env)
        return out

    def apply(self) -> None:
        """Set this device's env vars in os.environ. No restore."""
        for key, value in self.to_env().items():
            os.environ[key] = value

    @contextmanager
    def activate(self) -> Iterator[None]:
        """Context manager: swap runtime-adjustable env vars on enter,
        restore on exit. Load-time-locked vars are NOT swapped.
        """
        prior = {}
        env = self.to_env()
        for key in _RUNTIME_ADJUSTABLE_KEYS:
            if key in env:
                prior[key] = os.environ.get(key)
                os.environ[key] = env[key]
        try:
            yield
        finally:
            for key, val in prior.items():
                if val is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = val


# ----------------------------------------------------------------------
# Discovery
# ----------------------------------------------------------------------

_DEVICE_CACHE: dict[str, Device] | None = None


def _entry_points(group: str = "fdp.devices"):
    """Indirection for monkeypatching in tests."""
    return entry_points(group=group)


def clear_device_cache() -> None:
    global _DEVICE_CACHE
    _DEVICE_CACHE = None


def discover_devices() -> dict[str, Device]:
    """Return {name: Device} of every discovered device contributor.

    Reads the `fdp.devices` entry-point group. Returns an empty dict if
    no contributor is installed.
    """
    global _DEVICE_CACHE
    if _DEVICE_CACHE is not None:
        return _DEVICE_CACHE
    out: dict[str, Device] = {}
    for ep in _entry_points("fdp.devices"):
        try:
            value = ep.load()
        except Exception:
            continue
        if isinstance(value, Device):
            out[ep.name] = value
    _DEVICE_CACHE = out
    return out


def list_devices() -> dict[str, Device]:
    """Public alias for discover_devices()."""
    return discover_devices()


def get_device(name: str) -> Device:
    """Return a Device by name. Raises ValueError if not found."""
    devices = discover_devices()
    if name not in devices:
        raise ValueError(
            f"Unknown device: {name!r}. Available: {sorted(devices)}.")
    return devices[name]


# ----------------------------------------------------------------------
# Default-device resolution
# ----------------------------------------------------------------------

def _load_config_default() -> str | None:
    """Read ``default_device`` from ~/.fdp/config.toml if present."""
    path = Path.home() / ".fdp" / "config.toml"
    if not path.exists():
        return None
    try:
        import tomllib
        with path.open("rb") as f:
            data = tomllib.load(f)
        val = data.get("default_device")
        return val if isinstance(val, str) else None
    except Exception:
        return None


def resolve_default_device(explicit: str | None = None) -> Device:
    """Resolve the default-device pick.

    Precedence: explicit > FDP_DEFAULT_DEVICE > config.toml > auto-detect.
    """
    if explicit is not None:
        return get_device(explicit)
    env_pick = os.environ.get("FDP_DEFAULT_DEVICE")
    if env_pick:
        return get_device(env_pick)
    cfg_pick = _load_config_default()
    if cfg_pick:
        return get_device(cfg_pick)
    devices = discover_devices()
    if len(devices) == 1:
        return next(iter(devices.values()))
    raise ValueError(
        f"No default device selected and {len(devices)} are installed "
        f"({sorted(devices)}). Pass --default-device, set "
        "FDP_DEFAULT_DEVICE, or add `default_device = \"NAME\"` to "
        "~/.fdp/config.toml.")


# Convenience for downstream code that wants "what's currently active"
# without re-resolving.
def current_device() -> Device | None:
    """Return the active device based on resolution rules, or None."""
    try:
        return resolve_default_device()
    except ValueError:
        return None
