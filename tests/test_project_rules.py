"""Unit tests for assistant.project_rules — AGENTS.md / SKILLS.md loading."""
from __future__ import annotations

import itertools

from assistant import project_rules

_ids = itertools.count(200000)


def _win_id() -> int:
    return next(_ids)


def test_loads_and_combines_both_files(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Follow PEP 8 strictly.")
    (tmp_path / "SKILLS.md").write_text("Use the internal http client.")
    combined = project_rules.load(str(tmp_path), _win_id())
    assert "Follow PEP 8 strictly." in combined
    assert "Use the internal http client." in combined


def test_missing_files_returns_empty_string(tmp_path):
    assert project_rules.load(str(tmp_path), _win_id()) == ""


def test_only_agents_md_present(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Rule A")
    combined = project_rules.load(str(tmp_path), _win_id())
    assert combined == "Rule A"


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
