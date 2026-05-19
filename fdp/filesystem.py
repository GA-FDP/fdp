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

"""XRootD filesystem wrapper used by `fdp ls`."""

from pathlib import Path

from XRootD import client
from XRootD.client.flags import DirListFlags, StatInfoFlags


class FdpFileSystem:
    """Thin wrapper over `XRootD.client.FileSystem` for directory listings."""

    def __init__(self, server: str):
        self.server = server
        self.xrd_fs = client.FileSystem(server)

    def ls(self, path: str | Path, dirs_only: bool = False) -> list[Path]:
        """List the directory at `path`. Returns a list of Path objects."""
        _, listings = self.xrd_fs.dirlist(str(path), DirListFlags.STAT)
        if not listings:
            return []
        if dirs_only:
            listings = [
                l for l in listings
                if l.statinfo.flags & StatInfoFlags.IS_DIR
            ]
        return [Path(l.name) for l in listings]
