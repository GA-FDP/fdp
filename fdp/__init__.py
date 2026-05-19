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

- ``Device``: tokamak data-platform config contributed via the
  ``fdp.devices`` entry point.
- ``list_devices()`` / ``get_device(name)`` / ``current_device()``:
  introspection.
- ``setup_environment(device=None, bearer_token=None)``: apply FDP env
  vars to ``os.environ`` for the chosen device. (Added in Task 3.)
- ``FdpFileSystem``: XRootD wrapper used by ``fdp ls``. (Added in Task 4.)
"""

from .devices import (
    Device,
    list_devices,
    get_device,
    current_device,
    resolve_default_device,
)
from .environment import setup_environment, apply_environment
from . import _version

__version__ = _version.get_versions()["version"]

__all__ = [
    "Device",
    "list_devices",
    "get_device",
    "current_device",
    "resolve_default_device",
    "setup_environment",
    "apply_environment",
    "__version__",
]
