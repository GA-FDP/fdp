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

"""`fdp chat` / `fdp query` shims into `toksearch.llm.cli`.

These are `os.execvpe` shims: fdp's main process is replaced by a fresh
`python -m toksearch.llm.cli {chat,query}` process, with os.environ
already containing the resolved FDP env vars. The fresh process picks
up libfdpio/XRootD env at C-library load time.

The active device's `default_llm_preset` (e.g. "amsc" for d3d) is
inserted as the default `--backend` unless the user supplied one
explicitly via `fdp chat --backend foo`.
"""

import os
import sys

from .devices import Device


def _build_llm_cmd(
    subcommand: str,
    passthrough_args: list[str],
    device: Device,
) -> list[str]:
    """Construct argv for the `toksearch.llm.cli` delegate."""
    cmd = [sys.executable, "-m", "toksearch.llm.cli", subcommand]
    if (
        device.default_llm_preset
        and "--backend" not in passthrough_args
    ):
        cmd.extend(["--backend", device.default_llm_preset])
    cmd.extend(passthrough_args)
    return cmd


def _common_passthrough(args) -> list[str]:
    out: list[str] = []
    if getattr(args, "backend", None):
        out.extend(["--backend", args.backend])
    if getattr(args, "model", None):
        out.extend(["--model", args.model])
    if getattr(args, "max_iterations", None) is not None:
        out.extend(["-n", str(args.max_iterations)])
    return out


def do_query(args, device: Device) -> None:
    passthrough = [args.query] + _common_passthrough(args)
    cmd = _build_llm_cmd("query", passthrough, device)
    os.execvpe(cmd[0], cmd, os.environ)


def do_chat(args, device: Device) -> None:
    passthrough = _common_passthrough(args)
    cmd = _build_llm_cmd("chat", passthrough, device)
    os.execvpe(cmd[0], cmd, os.environ)


def do_backends(args) -> None:
    """Exec into ``toksearch.llm.cli backends`` for a clean device-free path.

    Listing is purely metadata, so we don't inject a default --backend
    or otherwise touch the device.
    """
    cmd = [sys.executable, "-m", "toksearch.llm.cli", "backends"]
    os.execvpe(cmd[0], cmd, os.environ)
