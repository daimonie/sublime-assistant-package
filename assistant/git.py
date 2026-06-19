"""Git subprocess helpers — never raise, always return str."""
from __future__ import annotations

import subprocess


def run(git_root: str, *args: str) -> str:
    """Run git with args in git_root; return stdout or "" on any error."""
    try:
        r = subprocess.run(
            ["git", "-C", git_root, *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout
    except Exception:
        return ""


def get_staged_diff(git_root: str) -> str:
    return run(git_root, "diff", "--cached")


def get_log(git_root: str, n: int = 10) -> str:
    return run(git_root, "log", f"-{n}", "--oneline")


def get_diff(git_root: str) -> str:
    return run(git_root, "diff", "HEAD")
