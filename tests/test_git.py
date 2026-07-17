"""Unit tests for assistant.git — subprocess wrappers that must never raise."""
from __future__ import annotations

import subprocess
from unittest.mock import patch

from assistant import git


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    return repo


def test_run_returns_stdout_on_success(tmp_path):
    repo = _init_repo(tmp_path)
    out = git.run(str(repo), "status", "--short")
    assert out == ""  # clean repo, nothing staged/changed


def test_run_returns_empty_string_on_invalid_git_root():
    out = git.run("/definitely/not/a/real/path/xyz", "status")
    assert out == ""


def test_run_returns_empty_string_when_git_binary_missing():
    with patch("assistant.git.subprocess.run", side_effect=FileNotFoundError()):
        out = git.run("/tmp", "status")
    assert out == ""


def test_run_returns_empty_string_on_timeout():
    with patch("assistant.git.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=10)):
        out = git.run("/tmp", "log")
    assert out == ""


def test_run_passes_args_through_to_subprocess():
    with patch("assistant.git.subprocess.run") as mock_run:
        mock_run.return_value.stdout = "output"
        result = git.run("/some/root", "diff", "--cached")
    mock_run.assert_called_once_with(
        ["git", "-C", "/some/root", "diff", "--cached"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result == "output"


def test_get_staged_diff_shows_staged_changes(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    diff = git.get_staged_diff(str(repo))
    assert "a.txt" in diff
    assert "+hello" in diff


def test_get_staged_diff_empty_when_nothing_staged(tmp_path):
    repo = _init_repo(tmp_path)
    assert git.get_staged_diff(str(repo)) == ""


def test_get_log_shows_oneline_commits(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial commit"], cwd=repo, check=True)
    log = git.get_log(str(repo))
    assert "initial commit" in log
    assert len(log.strip().splitlines()) == 1


def test_get_log_respects_n(tmp_path):
    repo = _init_repo(tmp_path)
    for i in range(3):
        (repo / "a.txt").write_text(f"content {i}\n")
        subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"commit {i}"], cwd=repo, check=True)
    log = git.get_log(str(repo), n=2)
    assert len(log.strip().splitlines()) == 2


def test_get_diff_shows_unstaged_and_staged_changes_against_head(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)

    (repo / "a.txt").write_text("hello world\n")
    diff = git.get_diff(str(repo))
    assert "hello world" in diff


def test_get_diff_empty_on_clean_repo(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("hello\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    assert git.get_diff(str(repo)) == ""
