"""Discover and load Agent Skills from skills/<name>/SKILL.md at the git root.

Each skill is a directory containing a SKILL.md file with a small YAML-like
frontmatter block (`name`, `description`) followed by a free-form instructions
body. Only the frontmatter is loaded up front (see build_index) so the model
can decide which skill applies without paying for every skill's full body;
the body itself is only read when the model calls load_skill (see
assistant/api.py's LOAD_SKILL_TOOL).
"""
from __future__ import annotations

import os
from typing import NamedTuple

# win_id -> rendered skills index (empty string means "checked and found nothing")
_cache: dict[int, str] = {}


class SkillMeta(NamedTuple):
    name: str
    description: str


def _skills_dir(git_root: str) -> str:
    return os.path.join(git_root, "skills")


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a SKILL.md file into (frontmatter dict, body).

    Frontmatter is a `---\\nkey: value\\n---` block at the top of the file;
    everything after the closing `---` is the body. Files without a leading
    `---` line have no frontmatter and their whole content is the body.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    meta: dict[str, str] = {}
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        line = lines[i]
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
        i += 1
    body = "\n".join(lines[i + 1:]).strip()
    return meta, body


def discover(git_root: str) -> list[SkillMeta]:
    """Return metadata for every skill under skills/*/SKILL.md, sorted by directory name.

    A skill directory missing SKILL.md, or a SKILL.md missing a description, is skipped.
    """
    root = _skills_dir(git_root)
    if not os.path.isdir(root):
        return []
    metas: list[SkillMeta] = []
    for entry in sorted(os.listdir(root)):
        skill_file = os.path.join(root, entry, "SKILL.md")
        if not os.path.isfile(skill_file):
            continue
        try:
            text = open(skill_file, encoding="utf-8").read()
        except Exception:
            continue
        meta, _ = _parse_frontmatter(text)
        description = meta.get("description", "")
        if not description:
            continue
        metas.append(SkillMeta(name=meta.get("name") or entry, description=description))
    return metas


def build_index(git_root: str, win_id: int) -> str:
    """Return the always-on system-prompt block listing available skills.

    Results are cached per window so skills/ is only scanned once per session,
    mirroring assistant.project_rules.load.
    """
    if win_id in _cache:
        return _cache[win_id]
    metas = discover(git_root)
    if not metas:
        _cache[win_id] = ""
        return ""
    lines = [
        "## Available Skills",
        "",
        "Call the `load_skill` tool with a skill's name to load its full instructions "
        "before following it, whenever the user's request matches a skill's description "
        "below. Do not act on a skill based on its description alone.",
        "",
    ]
    lines.extend(f"- **{m.name}**: {m.description}" for m in metas)
    index = "\n".join(lines)
    _cache[win_id] = index
    return index


def load_body(git_root: str, name: str) -> str:
    """Return the full instructions body for skill `name`, read fresh from disk."""
    skill_file = os.path.join(_skills_dir(git_root), name, "SKILL.md")
    if not os.path.isfile(skill_file):
        return f"Unknown skill: {name}"
    try:
        text = open(skill_file, encoding="utf-8").read()
    except Exception as e:
        return f"Error reading skill {name}: {e}"
    _, body = _parse_frontmatter(text)
    return body or f"Skill {name} has no instructions body."
