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
"""

from __future__ import annotations

import os
import sys

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .catalog import TokamakHandle


def _build_llm_cmd(
    subcommand: str,
    passthrough_args: list[str],
    handle: TokamakHandle | None,
) -> list[str]:
    """Construct argv for the `toksearch.llm.cli` delegate.

    If the active tokamak has `default_llm_preset` set in its catalog
    entry and the user did NOT pass `--backend` explicitly, inject
    `--backend <preset>` so D3D users get the expected default
    (e.g. `amsc`) without typing it every time.
    """
    cmd = [sys.executable, "-m", "toksearch.llm.cli", subcommand]
    if (
        handle is not None
        and handle.schema.default_llm_preset
        and "--backend" not in passthrough_args
    ):
        cmd.extend(["--backend", handle.schema.default_llm_preset])
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
    if getattr(args, "gui", False):
        out.append("--gui")
    # Only forward --no-browser if --gui is set; bare --no-browser has
    # no meaning in the underlying CLI.
    if (getattr(args, "gui", False)
            and getattr(args, "open_browser", True) is False):
        out.append("--no-browser")
    return out


def do_query(args, handle: TokamakHandle | None) -> None:
    passthrough = [args.query] + _common_passthrough(args)
    cmd = _build_llm_cmd("query", passthrough, handle)
    os.execvpe(cmd[0], cmd, os.environ)


def do_chat(args, handle: TokamakHandle | None) -> None:
    passthrough = _common_passthrough(args)
    cmd = _build_llm_cmd("chat", passthrough, handle)
    env = os.environ
    if getattr(args, "gui", False):
        # Hand the GUI the FDP brand logo so it can stylize its
        # header. toksearch.llm.gui consults FDP_GUI_LOGO_PATH;
        # absence is fine and falls back to no logo. Only copy
        # os.environ when we actually need to mutate it.
        from . import main_logo_path
        logo = main_logo_path()
        if logo and "FDP_GUI_LOGO_PATH" not in os.environ:
            env = {**os.environ, "FDP_GUI_LOGO_PATH": logo}
    os.execvpe(cmd[0], cmd, env)


def do_backends(args) -> None:
    """Exec into ``toksearch.llm.cli backends`` for a clean device-free path.

    Listing is purely metadata, so we don't inject a default --backend
    or otherwise touch the tokamak.
    """
    cmd = [sys.executable, "-m", "toksearch.llm.cli", "backends"]
    os.execvpe(cmd[0], cmd, os.environ)
