# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""Resolvers for fdp.catalog locator subtypes. Each resolver wraps a
schema Locator and exposes typed methods appropriate to its backend."""

from .mds_tree import MdsTreeResolver
# from .ptdata import PtDataResolver   # Task 15
# from .sql import SqlResolver         # Task 16


# Temporary placeholders so `from fdp.resolvers import ...` works during
# the staged build. These are replaced in Tasks 15 and 16.
class PtDataResolver:
    def __init__(self, model): raise NotImplementedError("Task 15")


class SqlResolver:
    def __init__(self, model): raise NotImplementedError("Task 16")
