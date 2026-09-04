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
from datetime import datetime, timezone
from pathlib import Path

from . import auth, config
from .catalog import catalog
from .devices import (
    CAPABILITIES, NoDevicesError, active_handles, resolve_for_capability,
)
from .environment import (
    compose_device_config, resolve_bearer_token, setup_environment,
)
from .filesystem import FdpFileSystem
from .llm_shims import do_backends as _llm_do_backends
from .llm_shims import do_chat as _llm_do_chat
from .llm_shims import do_query as _llm_do_query
from .skills import BACKENDS, _parse_skill_md, discover_skill_dirs

# The one non-boolean `needs_env` state: attempt setup, but warn and continue
# when no device contributor is installed. Named so a typo is a NameError
# rather than a silent fall-back to strict behaviour (`if needs_env:` treats
# any truthy value as strict).
BEST_EFFORT = "best-effort"   # needs_env: True | False | BEST_EFFORT


# ----------------------------------------------------------------------
# Subcommand handlers
# ----------------------------------------------------------------------

def do_env(args) -> None:
    handles = active_handles(args.device)
    for key, value in compose_device_config(handles).items():
        if value is None:
            continue
        print(f"export {key}={shlex.quote(str(value))}")
    for handle in handles:
        env_var = auth.bearer_env(handle)
        if env_var is None:
            continue
        token = resolve_bearer_token(handle)
        if token:
            print(f"export {env_var}={shlex.quote(token)}")


def do_login(args) -> None:
    try:
        handle = resolve_for_capability("bearer", args.device)
        result = auth.login(handle, write=args.write)
    except (ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except auth.AuthError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        sys.exit(1)
    if result is None:
        print(f"Device '{handle.schema.name}' needs no bearer token.")
        return
    if result.exp:
        when = datetime.fromtimestamp(
            result.exp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    else:
        when = "unknown"
    print(f"Logged in to {result.device} ({result.scope}); "
          f"token valid until {when}.")


def do_logout(args) -> None:
    try:
        handle = resolve_for_capability("bearer", args.device)
    except (ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    removed = auth.logout(handle)
    print("Removed cached token."
          if removed else "No cached token to remove.")


def do_run(args) -> None:
    passthrough = args.command_args
    if args.debug:
        print(f"Running: {' '.join(passthrough)}")
        print("With env:")
        for k, v in os.environ.items():
            print(f"  {k}={v}")
    result = subprocess.run(passthrough, env=os.environ)
    sys.exit(result.returncode)


def _device_for_ls(path: str, device_name: str | None):
    """Resolve the device whose origin server should serve `fdp ls`.

    `fdp ls` normally takes a path relative to the origin, so the capability
    rule ("which devices even have an origin server?") does the real work. A
    full pelican:// URL that matches exactly one device's pelican_root is
    honored first as a convenience -- but only when that device also
    declares an origin server. `pelican_root` and `origin_server` are
    independent optional fields in fdp_schema, so a device could in
    principle declare the former without the latter; falling through to the
    capability check in that case gives a clean error instead of resolving
    to a device with no origin and crashing inside FdpFileSystem(None).
    """
    if device_name is None and str(path).startswith("pelican://"):
        matches = [
            catalog[n] for n in catalog.names()
            if catalog[n].schema.pelican_root
            and str(path).startswith(catalog[n].schema.pelican_root)
            and catalog[n].schema.origin_server
        ]
        if len(matches) == 1:
            return matches[0]
    return resolve_for_capability("origin", device_name)


def _resolve_origin_server(device_name: str | None) -> str:
    """Origin server for the resolved tokamak. Kept as a named function
    because the test suite imports it directly; `do_ls` calls
    `_device_for_ls` instead (it also needs the path for URL matching)."""
    return _device_for_ls("", device_name).schema.origin_server


def do_ls(args) -> None:
    origin = _device_for_ls(args.path, args.device).schema.origin_server
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


def do_device(args) -> None:
    if args.device_command == "list":
        for name in catalog.names():
            handle = catalog[name]
            caps = [cap for cap, (predicate, _) in CAPABILITIES.items()
                    if predicate(handle)]
            print(f"{name}\t{handle.description}\t"
                  f"[{', '.join(caps) if caps else 'none'}]")
    elif args.device_command == "show":
        import yaml
        print(yaml.safe_dump(catalog[args.name].schema.model_dump(),
                              sort_keys=False))
    elif args.device_command == "use":
        if args.clear:
            config.set_default_device(None)
            print("Cleared the default device.")
            return
        if args.name is None:
            print("Error: `fdp device use` needs a device name "
                  "(or --clear).", file=sys.stderr)
            sys.exit(1)
        if args.name not in catalog:
            print(f"Error: unknown device {args.name!r}. Registered: "
                  f"{', '.join(catalog.names())}", file=sys.stderr)
            sys.exit(1)
        config.set_default_device(args.name)
        print(f"Default device set to '{args.name}' in "
              f"{config.config_path()}.")
    else:
        raise ValueError(
            f"Unknown device subcommand: {args.device_command!r}")


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
        name = args.device
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
        help="Backend / preset name. Defaults to $FDP_LLM_BACKEND, then "
             "~/.fdp/config.toml [llm].backend, then the built-in default.")
    p.add_argument(
        "--model", default=None,
        help="Override the preset's default model.")
    p.add_argument(
        "-n", "--max-iterations", type=int, default=None,
        help="Cap on tool-call rounds per turn.")


def _add_device_arg(parser, top_level: bool = False) -> None:
    """Declare --device/-D.

    Subparsers use SUPPRESS as the default so that omitting the flag leaves
    the top-level value untouched; supplying it on the subparser overwrites
    the top-level value. That is what makes both positions work.
    """
    default = None if top_level else argparse.SUPPRESS
    # The "compose all" note only makes sense at the top level; on a subparser
    # like `ls`/`login` the flag scopes to a single device, so appending it
    # there (via the shared helper) would mislead. Keep it top-level only.
    help_text = ("Device (tokamak) to use for this command. Defaults to "
                 "$FDP_DEFAULT_DEVICE, then ~/.fdp/config.toml [device].default.")
    if top_level:
        help_text += (" Commands that do not need a single device compose "
                      "all of them.")
    parser.add_argument(
        "--device", "-D", dest="device", default=default, help=help_text)
    parser.add_argument(
        "--default-device", dest="device", default=default,
        help=argparse.SUPPRESS)  # deprecated alias


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CLI interface for the Fusion Data Platform"
    )
    _add_device_arg(parser, top_level=True)
    parser.add_argument("--bearer-token", "-t", default="",
                         help="Override BEARER_TOKEN for this invocation.")
    parser.add_argument("--debug", action="store_true",
                         help="Print debug info.")

    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run",
                            help="Run a command with FDP env applied")
    # Must precede the REMAINDER positional: argparse's interaction between
    # REMAINDER and a preceding optional is version-sensitive, and this order
    # is what lets `fdp run -D d3d echo hi` bind -D to run (not the child).
    _add_device_arg(p_run)
    p_run.add_argument("command_args", nargs=argparse.REMAINDER,
                        help="Command and args to pass through")
    p_run.set_defaults(func=do_run, auto_login=True)

    p_env = sub.add_parser("env",
                            help="Print env vars for shell eval")
    _add_device_arg(p_env)
    p_env.set_defaults(func=do_env)

    p_login = sub.add_parser("login",
                             help="Acquire/refresh a bearer token via pelican")
    _add_device_arg(p_login)
    p_login.add_argument("--write", action="store_true",
                         help="Request a write-scoped token (default: read).")
    p_login.set_defaults(func=do_login, needs_env=False)

    p_logout = sub.add_parser("logout",
                              help="Delete the cached bearer token")
    _add_device_arg(p_logout)
    p_logout.set_defaults(func=do_logout, needs_env=False)

    p_ls = sub.add_parser("ls", help="List files on the FDP")
    _add_device_arg(p_ls)
    p_ls.add_argument("--dirs-only", "-d", action="store_true",
                       help="Only show subdirectories")
    p_ls.add_argument("path", type=str,
                       help="The path whose contents will be listed")
    p_ls.set_defaults(func=do_ls)

    p_cat = sub.add_parser("catalog",
                            help="(deprecated) alias for `fdp device`")
    cat_sub = p_cat.add_subparsers(dest="subcmd", required=True)
    cat_sub.add_parser("list", help="List tokamak names and descriptions")
    show = cat_sub.add_parser("show", help="Print a tokamak's full catalog YAML")
    show.add_argument("name")
    p_cat.set_defaults(func=do_catalog, needs_env=False)

    p_dev = sub.add_parser("device", help="Inspect and select devices")
    dev_sub = p_dev.add_subparsers(dest="device_command", required=True)
    dev_sub.add_parser("list", help="List devices and their capabilities")
    dev_show = dev_sub.add_parser("show", help="Print a device's catalog YAML")
    dev_show.add_argument("name")
    dev_use = dev_sub.add_parser(
        "use", help="Record a default device in ~/.fdp/config.toml")
    dev_use.add_argument("name", nargs="?", default=None)
    dev_use.add_argument("--clear", action="store_true",
                          help="Remove the recorded default device.")
    p_dev.set_defaults(func=do_device, needs_env=False)

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
    _add_device_arg(p_chat)
    _add_llm_args(p_chat)
    p_chat.add_argument("--gui", action="store_true",
                          help="Launch the local Gradio chat GUI "
                               "instead of the terminal REPL.")
    p_chat.add_argument("--no-browser", dest="open_browser",
                          action="store_false", default=True,
                          help="When --gui is set, do not open a "
                               "browser tab.")
    # chat / query want the FDP environment -- the agent fetches shot data --
    # but must still run where no device contributor is installed (fdp's own
    # dev env). "best-effort" is that middle state: try, warn, continue.
    # Setting it up here is safe precisely because these subcommands execvpe
    # into a fresh process, so libfdpio and XRootD read the vars at load time.
    p_chat.set_defaults(func=do_chat, needs_env=BEST_EFFORT,
                        auto_login=True)

    p_query = sub.add_parser("query", help="One-shot query")
    # Unlike `run`, the positional here is a plain one, not REMAINDER, so it
    # does not swallow following optionals and the order is free; keep it
    # matching `run` anyway.
    _add_device_arg(p_query)
    p_query.add_argument("query", type=str,
                           help="Natural-language query (quote it)")
    _add_llm_args(p_query)
    p_query.set_defaults(func=do_query, needs_env=BEST_EFFORT,
                         auto_login=True)

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
    # installed, so they opt out via `needs_env=False`. chat/query use
    # `needs_env="best-effort"`: they want the env when it is available
    # but must not die when it isn't.
    needs_env = getattr(args, "needs_env", True)
    if needs_env:
        best_effort = needs_env == BEST_EFFORT
        # Device resolution can fail (e.g. no default chosen among several
        # registered tokamaks); present it as a clean message, not a traceback.
        try:
            setup_environment(
                device=args.device,
                bearer_token=args.bearer_token or None,
                auto_login=getattr(args, "auto_login", False),
            )
        except (ValueError, KeyError) as exc:
            # Only "nothing is installed here" is worth continuing past: it
            # is the fdp-dev-env case, and the user asked for a chat, not for
            # data. A mistyped --device or a real DeviceEnvConflict (also a
            # ValueError) still exits -- degrading there would hand the user
            # an agent that silently cannot fetch anything.
            no_device_here = best_effort and isinstance(exc, NoDevicesError)
            if not no_device_here:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
            else:
                print("Warning: continuing without the FDP environment. "
                      f"Data access will not work in this session. ({exc})",
                      file=sys.stderr)
        except auth.AuthError as exc:
            if not best_effort:
                print(f"Login failed: {exc}", file=sys.stderr)
                sys.exit(1)
            else:
                print("Warning: continuing without a bearer token. Data "
                      "access will not work in this session; run "
                      f"`fdp login` to fix it. ({exc})", file=sys.stderr)

    try:
        args.func(args)
    except (ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
