# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""HttpCatalogResolver — builds metadata-catalog URLs from an
HttpCatalogLocator (e.g. FAIR MAST parquet endpoints)."""


class HttpCatalogResolver:
    def __init__(self, model):
        self.model = model

    def shots_url(self) -> str:
        return f"{self.model.base_url}/{self.model.shots_path}"

    def signals_url(self) -> str:
        if self.model.signals_path is None:
            raise ValueError(
                f"http_catalog locator {self.model.name!r} has no "
                f"signals_path configured"
            )
        return f"{self.model.base_url}/{self.model.signals_path}"
