"""Unit tests for assistant.skills — Agent Skills discovery and on-demand loading."""
from __future__ import annotations

import itertools

from assistant import skills

_ids = itertools.count(300000)


def _win_id() -> int:
    return next(_ids)


def _write_skill(tmp_path, name: str, description: str | None, body: str, dirname: str | None = None) -> None:
    skill_dir = tmp_path / "skills" / (dirname or name)
    skill_dir.mkdir(parents=True)
    frontmatter_lines = ["---", f"name: {name}"]
    if description is not None:
        frontmatter_lines.append(f"description: {description}")
    frontmatter_lines.append("---")
    (skill_dir / "SKILL.md").write_text("\n".join(frontmatter_lines) + "\n\n" + body)


def test_discover_returns_empty_list_when_no_skills_dir(tmp_path):
    assert skills.discover(str(tmp_path)) == []


def test_discover_parses_name_and_description(tmp_path):
    _write_skill(tmp_path, "verify", "Use when checking if a file is current.", "Step 1. Do the thing.")
    metas = skills.discover(str(tmp_path))
    assert metas == [skills.SkillMeta(name="verify", description="Use when checking if a file is current.")]


def test_discover_skips_dir_missing_skill_md(tmp_path):
    (tmp_path / "skills" / "empty").mkdir(parents=True)
    assert skills.discover(str(tmp_path)) == []


def test_discover_skips_skill_missing_description(tmp_path):
    _write_skill(tmp_path, "nodesc", None, "Some body.")
    assert skills.discover(str(tmp_path)) == []


def test_discover_falls_back_to_dirname_when_name_omitted(tmp_path):
    skill_dir = tmp_path / "skills" / "fallback"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ndescription: does a thing\n---\n\nbody")
    metas = skills.discover(str(tmp_path))
    assert metas == [skills.SkillMeta(name="fallback", description="does a thing")]


def test_build_index_renders_bullets(tmp_path):
    _write_skill(tmp_path, "verify", "Checks freshness.", "body")
    index = skills.build_index(str(tmp_path), _win_id())
    assert "## Available Skills" in index
    assert "load_skill" in index
    assert "- **verify**: Checks freshness." in index


def test_build_index_empty_when_no_skills(tmp_path):
    assert skills.build_index(str(tmp_path), _win_id()) == ""


def test_build_index_is_cached_per_window(tmp_path):
    wid = _win_id()
    _write_skill(tmp_path, "verify", "Checks freshness.", "body")
    first = skills.build_index(str(tmp_path), wid)

    _write_skill(tmp_path, "second", "A second skill.", "body", dirname="second")
    second = skills.build_index(str(tmp_path), wid)

    assert first == second
    assert "second" not in second


def test_build_index_different_windows_not_cached_together(tmp_path):
    _write_skill(tmp_path, "verify", "Checks freshness.", "body")
    first = skills.build_index(str(tmp_path), _win_id())
    second = skills.build_index(str(tmp_path), _win_id())
    assert first == second


def test_load_body_returns_full_instructions(tmp_path):
    _write_skill(tmp_path, "verify", "Checks freshness.", "Step 1.\nStep 2.")
    body = skills.load_body(str(tmp_path), "verify")
    assert body == "Step 1.\nStep 2."


def test_load_body_unknown_skill(tmp_path):
    assert skills.load_body(str(tmp_path), "nonexistent") == "Unknown skill: nonexistent"
