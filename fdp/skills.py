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
"""AI-assistant skill installation.

`fdp skills install` copies SKILL.md files from registered skill
directories into the local AI assistant's config dir (Claude Code,
Cursor, Codex). Skill directories are discovered via the
`toksearch.llm.skills` entry-point group, which is the canonical group
name across the FDP stack (set by toksearch, written to by anyone who
ships skills).
"""

import re
import shutil
from importlib.metadata import entry_points
from pathlib import Path


# ----------------------------------------------------------------------
# SKILL.md parsing + discovery
# ----------------------------------------------------------------------

def _parse_skill_md(path: Path) -> tuple[dict, str]:
    """Return (frontmatter_dict, body_text) from a SKILL.md file."""
    text = path.read_text()
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        fm_dict = {}
        for line in fm.strip().splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                fm_dict[k.strip()] = v.strip()
        return fm_dict, body.lstrip("\n")
    return {}, text


def _entry_points(group: str = "toksearch.llm.skills"):
    """Indirection for monkeypatching in tests."""
    return entry_points(group=group)


def discover_skill_dirs() -> list[Path]:
    """Return all SKILL.md-containing subdirs across registered skill paths.

    Reads the `toksearch.llm.skills` entry-point group. Each entry's
    value is loaded; if it's a Path (or string/callable returning one),
    its subdirectories are scanned for SKILL.md files. Returns the list
    of subdirectories in sorted order.
    """
    skill_dirs: list[Path] = []
    for ep in _entry_points():
        try:
            value = ep.load()
        except Exception:
            continue
        if callable(value):
            value = value()
        if isinstance(value, str):
            value = Path(value)
        if not isinstance(value, Path):
            continue
        if not value.exists():
            continue
        for sub in sorted(value.iterdir()):
            if sub.is_dir() and (sub / "SKILL.md").exists():
                skill_dirs.append(sub)
    return skill_dirs


# ----------------------------------------------------------------------
# Backend classes (one per AI assistant)
# ----------------------------------------------------------------------

class ClaudeBackend:
    """Claude Code - ~/.claude/skills/<name>/SKILL.md"""
    name = "claude"

    @property
    def dest_root(self) -> Path:
        return Path.home() / ".claude" / "skills"

    def is_detected(self) -> bool:
        return (Path.home() / ".claude").exists()

    def install_skill(self, skill_dir: Path, force: bool) -> str:
        dest = self.dest_root / skill_dir.name
        if dest.exists() and not force:
            return "skipped"
        if dest.exists():
            shutil.rmtree(dest)
        self.dest_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(skill_dir, dest)
        return "installed"

    def is_skill_installed(self, skill_name: str) -> bool:
        return (self.dest_root / skill_name).exists()


class CursorBackend:
    """Cursor IDE - ~/.cursor/rules/fdp-<name>.mdc"""
    name = "cursor"

    @property
    def dest_root(self) -> Path:
        return Path.home() / ".cursor" / "rules"

    def is_detected(self) -> bool:
        return (Path.home() / ".cursor").exists()

    def install_skill(self, skill_dir: Path, force: bool) -> str:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return "skipped"
        fm, body = _parse_skill_md(skill_md)
        description = fm.get("description", skill_dir.name)
        dest_file = self.dest_root / f"fdp-{skill_dir.name}.mdc"
        if dest_file.exists() and not force:
            return "skipped"
        self.dest_root.mkdir(parents=True, exist_ok=True)
        content = (
            f"---\ndescription: {description}\nglobs: \n"
            f"alwaysApply: false\n---\n\n{body}"
        )
        dest_file.write_text(content)
        return "installed"

    def is_skill_installed(self, skill_name: str) -> bool:
        return (self.dest_root / f"fdp-{skill_name}.mdc").exists()


class CodexBackend:
    """OpenAI Codex CLI - ~/.codex/instructions.md (section-per-skill)"""
    name = "codex"

    @property
    def dest_root(self) -> Path:
        return Path.home() / ".codex"

    @property
    def _instructions_file(self) -> Path:
        return self.dest_root / "instructions.md"

    def is_detected(self) -> bool:
        return (Path.home() / ".codex").exists()

    def _markers(self, skill_name: str) -> tuple[str, str]:
        return (
            f"<!-- fdp-skill:{skill_name} -->",
            f"<!-- /fdp-skill:{skill_name} -->",
        )

    def is_skill_installed(self, skill_name: str) -> bool:
        if not self._instructions_file.exists():
            return False
        start, _ = self._markers(skill_name)
        return start in self._instructions_file.read_text()

    def install_skill(self, skill_dir: Path, force: bool) -> str:
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            return "skipped"
        _, body = _parse_skill_md(skill_md)
        start, end = self._markers(skill_dir.name)
        section = f"{start}\n{body.rstrip()}\n{end}"
        current = (
            self._instructions_file.read_text()
            if self._instructions_file.exists() else ""
        )
        if start in current:
            if not force:
                return "skipped"
            pattern = re.escape(start) + r".*?" + re.escape(end)
            current = re.sub(pattern, section, current, flags=re.DOTALL)
        else:
            current = (
                current.rstrip("\n")
                + ("\n\n" if current else "")
                + section + "\n"
            )
        self.dest_root.mkdir(parents=True, exist_ok=True)
        self._instructions_file.write_text(current)
        return "installed"


BACKENDS = {
    "claude": ClaudeBackend(),
    "cursor": CursorBackend(),
    "codex": CodexBackend(),
}
