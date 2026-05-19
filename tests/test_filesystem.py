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

"""Tests for fdp.filesystem.FdpFileSystem."""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class TestFdpFileSystem(unittest.TestCase):
    def test_ls_returns_path_objects(self):
        fake_listing = [
            SimpleNamespace(name="dir1",
                            statinfo=SimpleNamespace(flags=0x1)),
            SimpleNamespace(name="file1",
                            statinfo=SimpleNamespace(flags=0x0)),
        ]
        fake_client = mock.MagicMock()
        fake_client.dirlist.return_value = (None, fake_listing)
        with mock.patch("fdp.filesystem.client.FileSystem",
                        return_value=fake_client):
            from fdp.filesystem import FdpFileSystem
            fs = FdpFileSystem("root://example:8443")
            results = fs.ls("/some/path")
        self.assertEqual(results, [Path("dir1"), Path("file1")])

    def test_ls_dirs_only_filters_by_stat_flag(self):
        from XRootD.client.flags import StatInfoFlags
        fake_listing = [
            SimpleNamespace(name="dir1",
                            statinfo=SimpleNamespace(flags=int(StatInfoFlags.IS_DIR))),
            SimpleNamespace(name="file1",
                            statinfo=SimpleNamespace(flags=0)),
        ]
        fake_client = mock.MagicMock()
        fake_client.dirlist.return_value = (None, fake_listing)
        with mock.patch("fdp.filesystem.client.FileSystem",
                        return_value=fake_client):
            from fdp.filesystem import FdpFileSystem
            fs = FdpFileSystem("root://example:8443")
            results = fs.ls("/some/path", dirs_only=True)
        self.assertEqual(results, [Path("dir1")])

    def test_ls_empty_listing(self):
        fake_client = mock.MagicMock()
        fake_client.dirlist.return_value = (None, None)
        with mock.patch("fdp.filesystem.client.FileSystem",
                        return_value=fake_client):
            from fdp.filesystem import FdpFileSystem
            fs = FdpFileSystem("root://example:8443")
            self.assertEqual(fs.ls("/empty"), [])


if __name__ == "__main__":
    unittest.main()
