# Copyright 2026 General Atomics
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
"""`fdp snapshot` — saving, showing and checking a citable shot list.

A *catalog* is the origin's published state, rebuilt by ingest. A **saved
snapshot** is a file you keep: the shots you chose, the exact version of
each, and the `dir_hash` that lets a third party verify the bytes without
asking the origin to vouch for itself.

Everything heavy is imported lazily. `fdp` is device-neutral — a public
device like MAST gets a clean environment and needs none of this — so a
missing dependency has to name the fix rather than surface as an ImportError
from an unrelated command.
"""

import json
import os
import sys


def _ptdata():
    try:
        import ptdata
    except ImportError as exc:
        sys.exit("`fdp snapshot` needs ptdata >= 2.10.0 ({}). Install it, or "
                 "use a device that has a versioned store.".format(exc))
    for name in ("build_snapshot", "snapshot_token", "verify_snapshot"):
        if not hasattr(ptdata, name):
            sys.exit("this ptdata has no {}; `fdp snapshot` needs "
                     ">= 2.10.0.".format(name))
    return ptdata


def _shard_key(treename, shot):
    """toksearch's tree→shard mapping, imported rather than reimplemented.

    A second copy of this is how the `ical` table drifted and silently
    returned the wrong calibration. `fdp` accepts `--shard` too, so a caller
    without toksearch is not stuck.
    """
    try:
        from toksearch.signal.store_path import shard_key
    except ImportError as exc:
        sys.exit("--tree needs toksearch >= 2.14.0 to map a tree to a shard "
                 "({}). Pass --shard NAME instead if you know it.".format(exc))
    return shard_key(treename, shot)


def _token(doc):
    return _ptdata().snapshot_token(doc)


def parse_shots(value):
    """A shot list from `1,2,3`, `1-5`, or `@file`.

    `@file` because 650 shots do not go on a command line; blank lines and
    `#` comments in it are ignored, so a list can be annotated.
    """
    if value.startswith("@"):
        path = value[1:]
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError as exc:
            sys.exit("cannot read shot list {}: {}".format(path, exc))
        value = ",".join(line.split("#", 1)[0] for line in text.splitlines())

    shots = []
    for piece in value.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece.lstrip("-"):
            lo, _, hi = piece.partition("-")
            try:
                shots.extend(range(int(lo), int(hi) + 1))
            except ValueError:
                sys.exit("not a shot range: {!r}".format(piece))
            continue
        try:
            shots.append(int(piece))
        except ValueError:
            sys.exit("not a shot number: {!r}".format(piece))

    if not shots:
        sys.exit("no shots given")
    return sorted(set(shots))


def parse_names(values):
    """Tree or shard names from repeated flags, commas, or both.

    `--shot` takes `1,2,3`, so a user types `--tree bci,efit01` next. Without
    this that was one tree named "bci,efit01", and the failure named a shard
    called `bci,efit01-0` -- which reads as a gap in the store rather than a
    comma that was not split.
    """
    out = []
    for value in values or ():
        for name in str(value).split(","):
            name = name.strip()
            if name and name not in out:
                out.append(name)
    return out


def shards_for(trees, shots):
    """One shard per (tree, million-shot span), sorted.

    A run crossing a boundary occupies TWO shards for one tree; recording
    only the first would leave the snapshot leaning on its catalog for half
    its model trees, silently, and only for runs that happen to span one.
    """
    if not trees or not shots:
        return []
    return sorted({_shard_key(t, s) for t in trees for s in shots})


def load(path):
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except OSError as exc:
        sys.exit("cannot read snapshot {}: {}".format(path, exc))
    except ValueError as exc:
        sys.exit("{} is not valid JSON: {}".format(path, exc))
    if doc.get("schema") != "fdp-snapshot/1":
        sys.exit("{} declares schema {!r}, not 'fdp-snapshot/1'".format(
            path, doc.get("schema")))
    return doc


def extract(inputs_path):
    """Lift the snapshot out of a CMF-recorded run's `inputs.json`."""
    try:
        with open(inputs_path) as fh:
            payload = json.load(fh)
    except OSError as exc:
        sys.exit("cannot read {}: {}".format(inputs_path, exc))
    except ValueError as exc:
        sys.exit("{} is not valid JSON: {}".format(inputs_path, exc))

    doc = payload.get("archive_version")
    if not isinstance(doc, dict):
        sys.exit("{} records archive_version={!r}: that run did not read a "
                 "versioned store, so there is no snapshot to extract.".format(
                     inputs_path, doc))
    return doc


def show(doc):
    """Report what a snapshot names. Does NOT check the bytes."""
    print("token    {}".format(_token(doc)))
    print("catalog  {}".format(doc.get("catalog", "-")))
    print("store    {}".format(doc.get("store_root", "-")))
    print("created  {}".format(doc.get("created_at", "-")))
    print("names    {} shots, {} shard{}".format(
        len(doc.get("shots", [])), len(doc.get("shared", [])),
        "" if len(doc.get("shared", [])) == 1 else "s"))
    if not doc.get("shared"):
        print("         (no shards named: model trees will resolve through "
              "the catalog, so this citation needs that catalog to survive)")
    print()
    print("This proves the list is intact, not that the bytes match it.")
    print("Run `fdp snapshot verify` for that.")
