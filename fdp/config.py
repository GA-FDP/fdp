# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Read and write ``~/.fdp/config.toml``.

Reading uses ``tomllib``. Writing is a targeted line edit rather than a
parse-and-reserialize round trip: the file also carries an ``[llm]`` section
(see ``fdp/llm_shims.py``) and may carry user comments, both of which a naive
rewrite would silently discard.
"""

import os
import re
import tempfile
import tomllib
from pathlib import Path


def config_path() -> Path:
    """Location of the user's fdp config file."""
    return Path.home() / ".fdp" / "config.toml"


def read_default_device() -> "str | None":
    """``[device].default``, or None if the file, section, or key is absent,
    not a non-empty string, or the file is unreadable/malformed."""
    path = config_path()
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    device = data.get("device", {})
    if not isinstance(device, dict):
        return None
    value = device.get("default")
    return value if isinstance(value, str) and value else None


_DEVICE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_DEVICE_HEADER = re.compile(
    r"""^\s*\[\s*(?:device|"device"|'device')\s*\]\s*(?:\#.*)?$"""
)
_ANY_HEADER = re.compile(r"^\s*\[")
_DEFAULT_KEY = re.compile(r"^\s*default\s*=")


def set_default_device(name: "str | None") -> None:
    """Set ``[device].default`` to *name*, or remove the key when *name* is
    None. All other sections, keys, and comments are preserved verbatim,
    except: a trailing comment on the ``default =`` line itself is dropped
    along with the line, and CRLF line endings / trailing blank lines are
    normalized.

    If the config path is a symlink (e.g. a dotfile manager like
    stow/chezmoi/yadm linking ``~/.fdp/config.toml`` to a tracked file
    elsewhere), the write lands on the link's target and the symlink itself
    is left in place. The file is written with mode 0600
    (readable/writable only by the owner), since it may hold LLM API keys.

    Raises ``ValueError`` if *name* is not a bare identifier-like string
    (letters, digits, ``_``, ``-``, ``.``), or if the existing file is not
    valid TOML — a pre-existing problem, not one this edit caused, so it's
    reported as a user error rather than swallowed like `read_default_device`
    does. Raises ``RuntimeError`` instead of writing if *this edit* would
    itself produce invalid TOML — a bug in this module's line-editing logic
    (e.g. an unrecognized ``[device]`` header spelling, or a ``device`` key
    written in a form, like an inline table, this simple line-editor can't
    safely reason about) — so a config that was valid before the call is
    never corrupted by the call.
    """
    if name is not None and not _DEVICE_NAME.match(name):
        raise ValueError(f"invalid device name: {name!r}")

    path = config_path()
    if name is None and not path.is_file():
        return  # nothing to clear, and nothing to create

    original_text = path.read_text() if path.is_file() else ""
    if path.is_file():
        try:
            tomllib.loads(original_text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(
                f"{path} is not valid TOML ({exc}); fix or remove it"
            ) from exc
    lines = original_text.splitlines()

    out: list[str] = []
    in_device = False
    wrote = False

    for line in lines:
        if _ANY_HEADER.match(line):
            # About to leave [device] without having written the key.
            if in_device and not wrote and name is not None:
                out.append(f'default = "{name}"')
                wrote = True
            in_device = bool(_DEVICE_HEADER.match(line))
            out.append(line)
            continue
        if in_device and _DEFAULT_KEY.match(line):
            # Replace the first occurrence; drop it when clearing, and drop
            # any duplicates.
            if name is not None and not wrote:
                out.append(f'default = "{name}"')
                wrote = True
            continue
        out.append(line)

    if in_device and not wrote and name is not None:
        out.append(f'default = "{name}"')
        wrote = True

    if not wrote and name is not None:
        if out and out[-1].strip():
            out.append("")
        out.append("[device]")
        out.append(f'default = "{name}"')

    new_text = "\n".join(out).rstrip("\n") + "\n"

    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise RuntimeError(
            f"refusing to write {path}: edit would produce invalid TOML "
            f"({exc})"
        ) from exc

    # Resolve symlinks so a symlinked config has its target updated in
    # place (os.replace does not follow symlinks; it would otherwise
    # delete the link and leave the real file untouched).
    target = path.resolve() if path.exists() else path
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=".config.toml.", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w") as fh:
            fh.write(new_text)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
