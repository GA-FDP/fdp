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


def _listed(names) -> str:
    """Render device/capability names for a message: ``d3d, devb, mast``.
    Interpolating the list itself would leak Python repr punctuation."""
    return ", ".join(names)


def explicit_device_name(device: str | None = None) -> str | None:
    """The user's explicit choice, or None. Order: argument, then
    ``$FDP_DEFAULT_DEVICE``, then ``~/.fdp/config.toml`` ``[device].default``.

    An empty ``$FDP_DEFAULT_DEVICE`` counts as unset (it is what an
    unconditional ``export FDP_DEFAULT_DEVICE=$SOMETHING`` leaves behind).
    An empty *argument* is not silently ignored: it is a value the user typed,
    so it flows through to lookup and is reported as an unknown device.
    """
    if device is not None:
        return device
    return (os.environ.get("FDP_DEFAULT_DEVICE")
            or read_default_device() or None)


def _registered_names() -> list:
    """All registered device names, sorted. Raises if none are installed.

    Every entry point into this module funnels through here first, so
    "nothing is installed" always beats "I don't recognize that name" --
    the former is the actionable fact.
    """
    names = _catalog.names()
    if not names:
        raise ValueError(_NO_DEVICES)
    return names


def _handle(name: str):
    """Look up one registered device by name.

    ``_catalog[name]`` alone raises a bare ``KeyError(name)``, which the CLI
    renders as the unhelpful ``Error: 'nosuch'``; this names the alternatives.
    """
    names = _registered_names()
    if name not in names:
        raise ValueError(
            f"Unknown device {name!r}. Registered devices: {_listed(names)}."
        )
    return _catalog[name]


def active_handles(device: str | None = None) -> list:
    """Handles whose environments should be composed, sorted by name.

    An explicit selection narrows to that one device. Otherwise *every*
    registered device contributes, and the env-assembly layer decides whether
    they actually conflict -- rather than assuming they do.
    """
    names = _registered_names()
    name = explicit_device_name(device)
    if name is not None:
        return [_handle(name)]
    return [_catalog[n] for n in names]


# capability -> (predicate, human-readable description)
CAPABILITIES = {
    "origin": (lambda h: bool(h.schema.origin_server),
               "an origin server"),
    "bearer": (lambda h: auth.bearer_env(h) is not None,
               "bearer-token authentication"),
}


def _capability(capability: str):
    """The ``(predicate, description)`` pair for *capability*.

    Indexing CAPABILITIES directly raises a bare ``KeyError('orgin')``. These
    are public functions, so a typo deserves to say what the options are --
    programmer error rather than user error, hence the terse message.
    """
    try:
        return CAPABILITIES[capability]
    except KeyError:
        raise ValueError(
            f"Unknown capability {capability!r}. "
            f"Valid capabilities: {_listed(sorted(CAPABILITIES))}."
        ) from None


def candidate_devices(capability: str) -> list:
    """Registered devices that can service *capability*, sorted by name."""
    predicate, _ = _capability(capability)
    handles = [_catalog[n] for n in _registered_names()]
    return [h for h in handles if predicate(h)]


def _no_candidate_error(described: str) -> ValueError:
    return ValueError(
        f"No registered device declares {described}, so this command has "
        f"nothing to act on."
    )


def resolve_for_capability(capability: str,
                           device: str | None = None):
    """Resolve the single device to use for a capability-scoped command."""
    predicate, described = _capability(capability)
    name = explicit_device_name(device)
    if name is not None:
        handle = _handle(name)
        if not predicate(handle):
            # The one message a user reaches by making a specific wrong
            # choice, so it points at the right choice rather than just
            # rejecting theirs.
            capable = [h.schema.name for h in candidate_devices(capability)]
            if not capable:
                raise _no_candidate_error(described)
            raise ValueError(
                f"Device {name!r} does not declare {described}, so it cannot "
                f"service this command. Devices that do: {_listed(capable)}."
            )
        return handle

    candidates = candidate_devices(capability)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise _no_candidate_error(described)
    names = [h.schema.name for h in candidates]
    raise ValueError(
        f"{len(candidates)} registered devices declare {described} "
        f"({_listed(names)}). {_CHOOSE_HINT}"
    )


def _resolve_device_handle(device: str | None = None):
    """Resolve exactly one device: explicit choice, else the sole registered
    device, else error.

    Retained for callers that genuinely need a single device -- the public
    ``fdp.setup_environment(device=...)`` argument and the ``toksearch_d3d`` /
    ``toksearch_mast`` shims that pass an explicit name.
    """
    names = _registered_names()
    name = explicit_device_name(device)
    if name is not None:
        return _handle(name)
    if len(names) == 1:
        return _catalog[names[0]]
    raise ValueError(
        f"No default tokamak selected and {len(names)} are registered "
        f"({_listed(names)}). {_CHOOSE_HINT}"
    )
