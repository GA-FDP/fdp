# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Resolvers for fdp.catalog locator subtypes. Each resolver wraps a
schema Locator and exposes typed methods appropriate to its backend."""

from .mds_tree import MdsTreeResolver
from .ptdata import PtDataResolver
# from .sql import SqlResolver         # Task 16


# Temporary placeholder so `from fdp.resolvers import ...` works during
# the staged build. Replaced in Task 16.
class SqlResolver:
    def __init__(self, model): raise NotImplementedError("Task 16")
