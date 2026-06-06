# Copyright 2024 General Atomics
# Licensed under the Apache License, Version 2.0.

"""ZarrStoreResolver — builds per-shot Zarr store URLs from a
ZarrStoreLocator. Transport/auth are handled by the consuming signal
class (e.g. toksearch_mast.MastSignal via fsspec); this resolver only
owns URL construction."""


class ZarrStoreResolver:
    def __init__(self, model):
        self.model = model

    def shot_url(self, shot) -> str:
        """Return the Zarr store URL for `shot`."""
        fname = self.model.file_name_format.format(shot=shot)
        return f"{self.model.base_url}/{fname}"
