# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Read and write ``~/.fdp/config.toml``.

Reading uses ``tomllib``. Writing is a targeted line edit rather than a
parse-and-reserialize round trip: the file also carries an ``[llm]`` section
(see ``fdp/llm_shims.py``) and may carry user comments, both of which a naive
rewrite would silently discard.
"""

import re
import tomllib
from pathlib import Path


def config_path() -> Path:
    """Location of the user's fdp config file."""
    return Path.home() / ".fdp" / "config.toml"


def read_default_device() -> "str | None":
    """``[device].default``, or None if the file, section, or key is absent
    or the file is unreadable/malformed."""
    path = config_path()
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data.get("device", {}).get("default") or None


_DEVICE_HEADER = re.compile(r"^\s*\[device\]\s*$")
_ANY_HEADER = re.compile(r"^\s*\[")
_DEFAULT_KEY = re.compile(r"^\s*default\s*=")


def set_default_device(name: "str | None") -> None:
    """Set ``[device].default`` to *name*, or remove the key when *name* is
    None. All other sections, keys, and comments are preserved verbatim."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.is_file() else []

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

    path.write_text("\n".join(out).rstrip("\n") + "\n")
