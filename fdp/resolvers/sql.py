# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""SqlResolver — opens a SQL connection from a SqlLocator.

v1 implements driver="mssql" only, via pymssql. Other drivers raise
NotImplementedError. Credential resolution supports:
  - auth.kind="password_file": two-line file (username, password)
  - explicit username= / password= kwargs to connect()
"""

import os
from pathlib import Path


class SqlResolver:
    def __init__(self, model):
        self.model = model

    def connect(self, username: str | None = None, password: str | None = None):
        if self.model.driver != "mssql":
            raise NotImplementedError(
                f"SqlResolver.connect: driver={self.model.driver!r} "
                f"not supported in v1; only 'mssql' is implemented."
            )
        if self.model.tdsver:
            # setdefault preserves a user-provided TDSVER if already set
            # (e.g., by fdp's _generic_config).
            os.environ.setdefault("TDSVER", self.model.tdsver)
        if password is None:
            username, password = self._read_credential()
        import pymssql
        return pymssql.connect(
            self.model.host, username, password, self.model.database,
            port=str(self.model.port) if self.model.port else None,
        )

    def _read_credential(self) -> tuple[str, str]:
        auth = self.model.auth
        if auth and auth.kind == "password_file" and auth.path:
            text = Path(os.path.expanduser(auth.path)).read_text()
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return lines[0], lines[1]
        raise RuntimeError(
            f"SqlResolver: no credential source for locator {self.model.name!r} "
            f"(auth must be kind=password_file with a path, or pass "
            f"username/password explicitly to connect())"
        )
