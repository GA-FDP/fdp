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
"""fdp CLI entrypoint."""

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from .catalog import catalog
from .environment import _generic_config, _resolve_device_env, setup_environment
from .filesystem import FdpFileSystem
from .llm_shims import do_backends as _llm_do_backends
from .llm_shims import do_chat as _llm_do_chat
from .llm_shims import do_query as _llm_do_query
from .skills import BACKENDS, _parse_skill_md, discover_skill_dirs


# ----------------------------------------------------------------------
# Subcommand handlers
# ----------------------------------------------------------------------

def do_env(args) -> None:
    config = _generic_config()
    config.update(_resolve_device_env(args.default_device))
    for key, value in config.items():
        if value is None:
            continue
        print(f"export {key}={shlex.quote(str(value))}")
    bearer_token = os.environ.get("BEARER_TOKEN", "")
    if bearer_token:
        print(f"export BEARER_TOKEN={shlex.quote(bearer_token)}")


def do_run(args) -> None:
    passthrough = args.command_args
    if args.debug:
        print(f"Running: {' '.join(passthrough)}")
        print("With env:")
        for k, v in os.environ.items():
            print(f"  {k}={v}")
    result = subprocess.run(passthrough, env=os.environ)
    sys.exit(result.returncode)


def _resolve_origin_server(device_name: str | None) -> str:
    """Return the origin_server for the given tokamak name.

    If device_name is None, auto-selects when exactly one tokamak is
    registered. Raises ``KeyError`` if the name is not found and
    ``ValueError`` if no default can be determined.
    """
    if device_name is not None:
        return catalog[device_name].schema.origin_server
    # Auto-detect
    names = catalog.names()
    if len(names) == 1:
        return catalog[names[0]].schema.origin_server
    if len(names) == 0:
        raise ValueError("No tokamak contributors are installed.")
    raise ValueError(
        f"No default tokamak selected and {len(names)} are registered "
        f"({names}). Pass --default-device."
    )


def do_ls(args) -> None:
    origin = _resolve_origin_server(args.default_device)
    fs = FdpFileSystem(origin)
    listing = fs.ls(args.path, dirs_only=args.dirs_only)
    if listing:
        for entry in listing:
            print(entry)
    else:
        print("No such file or directory")
        sys.exit(1)


def do_catalog(args) -> None:
    if args.subcmd == "list":
        for name in catalog.names():
            tk = catalog[name]
            print(f"{name}\t{tk.description}")
    elif args.subcmd == "show":
        tk = catalog[args.name]
        import yaml
        print(yaml.safe_dump(tk.schema.model_dump(), sort_keys=False))
    else:
        raise ValueError(f"Unknown catalog subcommand: {args.subcmd!r}")


def do_skills(args) -> None:
    skill_dirs = discover_skill_dirs()
    backend_arg = getattr(args, "backend", "claude")
    if backend_arg == "all":
        backends = [b for b in BACKENDS.values() if b.is_detected()]
        if not backends:
            print("No supported coding assistant tool detected.")
            return
    elif backend_arg in BACKENDS:
        backends = [BACKENDS[backend_arg]]
    else:
        print(f"Unknown backend '{backend_arg}'. Choose from: "
              f"{', '.join(BACKENDS)}, all")
        sys.exit(1)

    if args.skills_command == "list":
        for backend in backends:
            print(f"[{backend.name}]")
            for d in skill_dirs:
                status = ("installed" if backend.is_skill_installed(d.name)
                          else "not installed")
                print(f"  {d.name}  [{status}]")
        return

    force = getattr(args, "force", False)
    for backend in backends:
        print(f"\n[{backend.name}] Installing to {backend.dest_root}")
        installed = skipped = 0
        for skill_dir in skill_dirs:
            result = backend.install_skill(skill_dir, force)
            if result == "installed":
                print(f"  install  {skill_dir.name}")
                installed += 1
            else:
                print(f"  skip     {skill_dir.name}  "
                      "(use --force to overwrite)")
                skipped += 1
        print(f"  {installed} installed, {skipped} skipped")


def _resolve_default_handle_or_none(args):
    """Resolve the default tokamak handle, or return ``None`` if no
    contributors are installed. chat / query are pure LLM operations and
    degrade gracefully when run in a bare fdp dev env."""
    try:
        name = args.default_device
        if name is not None:
            return catalog[name]
        names = catalog.names()
        if len(names) == 1:
            return catalog[names[0]]
        return None
    except (KeyError, ValueError):
        return None


def do_chat(args) -> None:
    handle = _resolve_default_handle_or_none(args)
    _llm_do_chat(args, handle)


def do_query(args) -> None:
    handle = _resolve_default_handle_or_none(args)
    _llm_do_query(args, handle)


def do_backends(args) -> None:
    """List available LLM backend presets (delegates to toksearch.llm.cli)."""
    _llm_do_backends(args)


# ----------------------------------------------------------------------
# argparse wiring
# ----------------------------------------------------------------------

def _add_llm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--backend", default=None,
        help="Backend / preset name (defaults to active device's "
             "default_llm_preset).")
    p.add_argument(
        "--model", default=None,
        help="Override the preset's default model.")
    p.add_argument(
        "-n", "--max-iterations", type=int, default=None,
        help="Cap on tool-call rounds per turn.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI interface for the Fusion Data Platform"
    )
    parser.add_argument("--default-device", "-D", default=None,
                         help="Default device for non-agent subcommands; "
                              "agent (chat/query) uses this as the "
                              "starting device.")
    parser.add_argument("--bearer-token", "-t", default="",
                         help="Override BEARER_TOKEN for this invocation.")
    parser.add_argument("--debug", action="store_true",
                         help="Print debug info.")

    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run",
                            help="Run a command with FDP env applied")
    p_run.add_argument("command_args", nargs=argparse.REMAINDER,
                        help="Command and args to pass through")
    p_run.set_defaults(func=do_run)

    p_env = sub.add_parser("env",
                            help="Print env vars for shell eval")
    p_env.set_defaults(func=do_env)

    p_ls = sub.add_parser("ls", help="List files on the FDP")
    p_ls.add_argument("--dirs-only", "-d", action="store_true",
                       help="Only show subdirectories")
    p_ls.add_argument("path", type=str,
                       help="The path whose contents will be listed")
    p_ls.set_defaults(func=do_ls)

    p_cat = sub.add_parser("catalog", help="Inspect the tokamak catalog")
    cat_sub = p_cat.add_subparsers(dest="subcmd", required=True)
    cat_sub.add_parser("list", help="List tokamak names and descriptions")
    show = cat_sub.add_parser("show", help="Print a tokamak's full catalog YAML")
    show.add_argument("name")
    p_cat.set_defaults(func=do_catalog, needs_env=False)

    p_sk = sub.add_parser("skills",
                           help="Manage AI assistant skills")
    sk_sub = p_sk.add_subparsers(dest="skills_command", required=True)
    sk_list = sk_sub.add_parser("list",
                                  help="List skills + install status")
    sk_list.add_argument("--backend", default="claude",
                          help="claude, cursor, codex, or all")
    sk_install = sk_sub.add_parser("install",
                                      help="Install skills")
    sk_install.add_argument("--backend", default="claude",
                              help="claude, cursor, codex, or all")
    sk_install.add_argument("--force", "-f", action="store_true",
                              help="Overwrite already-installed skills")
    p_sk.set_defaults(func=do_skills, needs_env=False)

    p_chat = sub.add_parser("chat",
                              help="Interactive conversational query")
    _add_llm_args(p_chat)
    p_chat.add_argument("--gui", action="store_true",
                          help="Launch the local Gradio chat GUI "
                               "instead of the terminal REPL.")
    p_chat.add_argument("--no-browser", dest="open_browser",
                          action="store_false", default=True,
                          help="When --gui is set, do not open a "
                               "browser tab.")
    # chat / query just execvpe into toksearch.llm.cli; no FDP env
    # setup needed, and they tolerate no device contributor being
    # installed (useful for working inside the fdp dev env).
    p_chat.set_defaults(func=do_chat, needs_env=False)

    p_query = sub.add_parser("query", help="One-shot query")
    p_query.add_argument("query", type=str,
                           help="Natural-language query (quote it)")
    _add_llm_args(p_query)
    p_query.set_defaults(func=do_query, needs_env=False)

    p_be = sub.add_parser(
        "backends",
        help="List available LLM backend presets (built-in, discovered, "
             "and user-defined).")
    p_be.set_defaults(func=do_backends, needs_env=False)

    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Pure-metadata subcommands (devices, skills, backends) don't touch
    # the FDP env and shouldn't require a device contributor to be
    # installed, so they opt out via `needs_env=False`.
    if getattr(args, "needs_env", True):
        setup_environment(
            device=args.default_device,
            bearer_token=args.bearer_token or None,
        )

    args.func(args)


if __name__ == "__main__":
    main()
