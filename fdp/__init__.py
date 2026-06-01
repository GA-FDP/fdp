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

"""fdp - Fusion Data Platform CLI.

Public surface:

- ``catalog``: Tokamak catalog singleton (fdp_schema.catalogs entry points).
- ``list_devices()`` / ``get_device(name)`` / ``current_device()``:
  legacy device introspection (kept for backward compat; prefer catalog).
- ``setup_environment(device=None, bearer_token=None)``: apply FDP env
  vars to ``os.environ`` for the chosen device/tokamak. (Added in Task 3.)
- ``FdpFileSystem``: XRootD wrapper used by ``fdp ls``. (Added in Task 4.)
"""

import os as _os

from .catalog import catalog
from .devices import (
    list_devices,
    get_device,
    current_device,
    resolve_default_device,
)
from .environment import setup_environment, apply_environment
from .filesystem import FdpFileSystem
from . import _version

__version__ = _version.get_versions()["version"]


def main_logo_path() -> str | None:
    """Return the filesystem path to the FDP main brand logo, or ``None``.

    The PNG ships as package data under ``fdp/logos/``. Consumers
    (notably the toksearch chat GUI) call this to stylize their
    surface with the FDP mark when running inside the platform's
    ecosystem. Returns ``None`` if the asset isn't installed (e.g.,
    a sdist-only build or a future stripped-down deployment).
    """
    candidate = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                "logos", "FDP Main Logo.png")
    return candidate if _os.path.isfile(candidate) else None


__all__ = [
    "catalog",
    "list_devices",
    "get_device",
    "current_device",
    "resolve_default_device",
    "setup_environment",
    "apply_environment",
    "FdpFileSystem",
    "main_logo_path",
    "__version__",
]
