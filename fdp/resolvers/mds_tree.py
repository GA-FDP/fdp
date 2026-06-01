# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""MdsTreeResolver — typed wrapper for MdsTreeLocator.

Expands MDSplus tree-path tokens (`~t`, `~c`..`~j`) for a given shot. The
expansion is purely lexical — MDSplus's own libraries do the same expansion
at file-open time when consuming `default_tree_path`, but this Python
implementation lets callers compute concrete URLs without going through the
C stack.
"""


def _expand_mds_template(template: str, shot: int) -> str:
    """Expand MDSplus tree-path tokens in `template` for `shot`.

    Tokens (single char after `~`):
      ~t — full shot as decimal string
      ~c..~j — individual digits, zero-padded to 8; ~c=units, ~j=10^7.

    Raises:
      ValueError: if shot exceeds 8 decimal digits (overflows ~j).
    """
    s = str(shot)
    if len(s) > 8:
        raise ValueError(
            f"shot {shot} has more than 8 digits; ~c..~j tokens overflow"
        )
    digits = s.zfill(8)
    # Order doesn't matter (no token is a prefix of another); replace ~t
    # first because it's the most likely to appear and lexically distinct.
    return (
        template
        .replace("~t", s)
        .replace("~c", digits[-1])
        .replace("~d", digits[-2])
        .replace("~e", digits[-3])
        .replace("~f", digits[-4])
        .replace("~g", digits[-5])
        .replace("~h", digits[-6])
        .replace("~i", digits[-7])
        .replace("~j", digits[-8])
    )


class MdsTreeResolver:
    """Resolver for MdsTreeLocator. All methods are pure: no network."""

    def __init__(self, model):
        self.model = model

    def urls_for(self, shot: int) -> list[str]:
        """Return concrete URLs for `shot` (templates expanded in order)."""
        return [_expand_mds_template(t, shot) for t in self.model.search_path]

    def joined_path(self, shot: int, delim: str = ";") -> str:
        """Return the URLs joined by `delim` — the form MDSplus's
        `default_tree_path` env var expects."""
        return delim.join(self.urls_for(shot))
