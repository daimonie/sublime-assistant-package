"""Unit tests for assistant.project_rules — AGENTS.md loading."""
from __future__ import annotations

import itertools

from assistant import project_rules

_ids = itertools.count(200000)


def _win_id() -> int:
    return next(_ids)


def test_loads_agents_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Follow PEP 8 strictly.")
    assert project_rules.load(str(tmp_path), _win_id()) == "Follow PEP 8 strictly."


def test_missing_file_returns_empty_string(tmp_path):
    assert project_rules.load(str(tmp_path), _win_id()) == ""


def test_skills_md_is_ignored(tmp_path):
    """SKILLS.md used to be combined in here; it's now handled by assistant.skills
    instead, so project_rules must not read it any more."""
    (tmp_path / "AGENTS.md").write_text("Rule A")
    (tmp_path / "SKILLS.md").write_text("Use the internal http client.")
    combined = project_rules.load(str(tmp_path), _win_id())
    assert combined == "Rule A"
    assert "http client" not in combined


def test_result_is_cached_per_window(tmp_path):
    wid = _win_id()
    (tmp_path / "AGENTS.md").write_text("Original rule")
    first = project_rules.load(str(tmp_path), wid)

    (tmp_path / "AGENTS.md").write_text("Changed rule")
    second = project_rules.load(str(tmp_path), wid)

    assert first == second == "Original rule"


def test_different_windows_are_not_cached_together(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Shared rule")
    first = project_rules.load(str(tmp_path), _win_id())
    second = project_rules.load(str(tmp_path), _win_id())
    assert first == second == "Shared rule"
