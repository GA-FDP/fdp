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
"""Tests for fdp.skills."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestParseSkillMd(unittest.TestCase):
    def test_with_frontmatter(self):
        from fdp.skills import _parse_skill_md
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                          delete=False) as f:
            f.write("---\nname: mysk\ndescription: A skill\n---\nBody\n")
            path = Path(f.name)
        try:
            fm, body = _parse_skill_md(path)
            self.assertEqual(fm.get("description"), "A skill")
            self.assertIn("Body", body)
        finally:
            path.unlink()

    def test_without_frontmatter(self):
        from fdp.skills import _parse_skill_md
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md",
                                          delete=False) as f:
            f.write("Just body\n")
            path = Path(f.name)
        try:
            fm, body = _parse_skill_md(path)
            self.assertEqual(fm, {})
            self.assertIn("Just body", body)
        finally:
            path.unlink()


def _fake_ep(name, path):
    ep = mock.MagicMock()
    ep.name = name
    ep.load.return_value = path
    return ep


class TestDiscoverSkillDirs(unittest.TestCase):
    def test_reads_toksearch_llm_skills_group(self):
        from fdp.skills import discover_skill_dirs
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "myskill").mkdir()
            (root / "myskill" / "SKILL.md").write_text(
                "---\ndescription: x\n---\nBody")
            with mock.patch(
                "fdp.skills._entry_points",
                return_value=[_fake_ep("contrib", root)],
            ):
                dirs = discover_skill_dirs()
        self.assertEqual(len(dirs), 1)
        self.assertEqual(dirs[0].name, "myskill")

    def test_callable_value_is_invoked(self):
        from fdp.skills import discover_skill_dirs
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "skill1").mkdir()
            (root / "skill1" / "SKILL.md").write_text("body")
            with mock.patch(
                "fdp.skills._entry_points",
                return_value=[_fake_ep("contrib", lambda: root)],
            ):
                dirs = discover_skill_dirs()
        self.assertEqual(len(dirs), 1)

    def test_no_contributors_returns_empty(self):
        from fdp.skills import discover_skill_dirs
        with mock.patch("fdp.skills._entry_points", return_value=[]):
            dirs = discover_skill_dirs()
        self.assertEqual(dirs, [])


class TestClaudeBackend(unittest.TestCase):
    def test_install_skill_copies_tree(self):
        from fdp.skills import ClaudeBackend
        with tempfile.TemporaryDirectory() as src_td, \
                tempfile.TemporaryDirectory() as home_td:
            src = Path(src_td) / "myskill"
            src.mkdir()
            (src / "SKILL.md").write_text("body")
            with mock.patch.object(Path, "home",
                                    return_value=Path(home_td)):
                backend = ClaudeBackend()
                result = backend.install_skill(src, force=False)
            self.assertEqual(result, "installed")
            dest = Path(home_td) / ".claude" / "skills" / "myskill"
            self.assertTrue((dest / "SKILL.md").exists())


if __name__ == "__main__":
    unittest.main()
