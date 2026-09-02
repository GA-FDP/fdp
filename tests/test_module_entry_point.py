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
"""`python -m fdp` must stay reachable.

The `fdp` console script is not always reachable: graphviz installs its
force-directed layout engine at the same `bin/fdp` path, conda records graphviz
as the owner, and graphviz wins. Any environment with cmflib pulls graphviz
transitively, so `fdp run` there invokes a layout tool. `python -m fdp`
resolves through the package rather than PATH.
"""

import subprocess
import sys
import unittest


class TestModuleEntryPoint(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "fdp", *args],
            capture_output=True, text=True, timeout=60,
        )

    def test_module_invocation_works(self):
        self.assertEqual(self._run("--help").returncode, 0)

    def test_every_subcommand_is_reachable(self):
        out = self._run("--help").stdout
        for command in ("run", "env", "login", "logout", "ls", "catalog",
                        "device", "skills"):
            self.assertIn(command, out)

    def test_usage_shows_something_typeable(self):
        # argparse defaults prog to basename(sys.argv[0]), which is
        # "__main__.py" under -m -- useless in an error message.
        out = self._run("--help").stdout
        self.assertIn("python -m fdp", out)
        self.assertNotIn("__main__.py", out)

    def test_errors_name_the_module_form(self):
        err = self._run("nosuchcommand").stderr
        self.assertIn("python -m fdp", err)

    def test_it_is_the_same_cli(self):
        from fdp.cli import main

        import fdp.__main__ as entry

        self.assertIs(entry.main, main)
