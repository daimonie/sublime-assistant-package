"""Diff preview and new-file preview views for Apply workflow."""
from __future__ import annotations

import difflib
import os
import re
from difflib import SequenceMatcher
from typing import NamedTuple

import sublime

_DEF_RE = re.compile(r'^(?:async\s+)?(?:def|class)\s+(\w+)', re.MULTILINE)


class _DiffEntry(NamedTuple):
    target_view_id: int | None
    full_proposed: str       # complete merged file content, ready to write
    new_filepath: str | None


_pending: dict[str, _DiffEntry] = {}
_SETTING = "sublime_assistant_diff_id"

_CONTROLS_HTML = """<body id="sa_controls">
<style>
  body { margin: 4px 0; }
  a { padding: 3px 10px; border-radius: 3px; text-decoration: none; font-size: 1em; }
  .ok { background-color: #2d6a2d; color: #fff; }
  .no { background-color: #6a2d2d; color: #fff; }
</style>
<a class="ok" href="accept">&#10003; Accept</a>&nbsp;
<a class="no" href="reject">&#10007; Reject</a>
</body>"""

_CREATE_HTML = """<body id="sa_controls">
<style>
  body { margin: 4px 0; }
  a { padding: 3px 10px; border-radius: 3px; text-decoration: none; font-size: 1em; }
  .ok { background-color: #2d6a2d; color: #fff; }
  .no { background-color: #6a2d2d; color: #fff; }
</style>
<a class="ok" href="accept">&#10003; Create File</a>&nbsp;
<a class="no" href="reject">&#10007; Reject</a>
</body>"""


# ── Snippet merging ────────────────────────────────────────────────────────────

def _merge_snippet(orig_lines: list[str], snippet_lines: list[str]) -> list[str]:
    """Merge a (possibly truncated) snippet into the original lines.

    Uses SequenceMatcher to classify each block:
    - equal / replace / insert  →  take from snippet as normal
    - delete at the END         →  keep from original (LLM truncation)
    - delete in the MIDDLE      →  drop (intentional removal by LLM)
    """
    matcher = SequenceMatcher(None, orig_lines, snippet_lines, autojunk=False)
    opcodes = list(matcher.get_opcodes())

    # Last original index covered by a non-delete opcode
    last_non_delete_orig_end = 0
    for tag, i1, i2, j1, j2 in opcodes:
        if tag != 'delete':
            last_non_delete_orig_end = i2

    result: list[str] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'equal':
            result.extend(orig_lines[i1:i2])
        elif tag in ('replace', 'insert'):
            result.extend(snippet_lines[j1:j2])
        elif tag == 'delete':
            if i1 >= last_non_delete_orig_end:
                # Trailing: snippet was truncated — keep original
                result.extend(orig_lines[i1:i2])
            # else: intentional deletion — drop

    return result


def _find_snippet_region(original: str, snippet: str) -> tuple[int, int] | None:
    """Find (start_line, end_line) of the first def/class named in snippet."""
    m = _DEF_RE.search(snippet)
    if not m:
        return None
    name = m.group(1)

    orig_lines = original.splitlines()
    n = len(orig_lines)
    start = indent = None

    for i, line in enumerate(orig_lines):
        stripped = line.lstrip()
        if re.match(rf'(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b', stripped):
            start = i
            indent = len(line) - len(stripped)
            break

    if start is None:
        return None

    end = n
    for i in range(start + 1, n):
        line = orig_lines[i]
        if not line.strip():
            continue
        line_indent = len(line) - len(line.lstrip())
        if line_indent <= indent and re.match(r'(?:async\s+)?(?:def|class)\s', line.lstrip()):
            end = i
            break

    return (start, end)


def _compute_proposed_file(
    orig_lines: list[str],
    proposed_code: str,
    hint_region: tuple[int, int] | None,
) -> list[str]:
    """Return the full proposed file as a line list, merging the snippet smartly."""
    snippet_lines = proposed_code.splitlines(keepends=True)

    if hint_region:
        start, end = hint_region
        merged = _merge_snippet(orig_lines[start:end], snippet_lines)
        return orig_lines[:start] + merged + orig_lines[end:]

    if len(snippet_lines) < len(orig_lines) * 0.85:
        region = _find_snippet_region(''.join(orig_lines), proposed_code)
        if region:
            start, end = region
            merged = _merge_snippet(orig_lines[start:end], snippet_lines)
            return orig_lines[:start] + merged + orig_lines[end:]

    return snippet_lines   # full-file replacement


# ── Public API ─────────────────────────────────────────────────────────────────

def open_diff(
    window: sublime.Window,
    diff_id: str,
    target_view: sublime.View,
    proposed_code: str,
    hint_region: tuple[int, int] | None = None,
) -> None:
    """Open a unified diff preview for modifying an existing file."""
    original = target_view.substr(sublime.Region(0, target_view.size()))
    filename = os.path.basename(target_view.file_name() or target_view.name() or "file")

    orig_lines = original.splitlines(keepends=True)
    new_lines = _compute_proposed_file(orig_lines, proposed_code, hint_region)
    full_proposed = ''.join(new_lines)

    diff_lines = list(difflib.unified_diff(
        orig_lines, new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=3,
    ))
    diff_text = ''.join(diff_lines) if diff_lines else '(No differences)\n'

    _pending[diff_id] = _DiffEntry(target_view.id(), full_proposed, None)
    _open_preview_view(window, diff_id, f"Diff: {filename}", diff_text,
                       "Packages/Diff/Diff.sublime-syntax", _CONTROLS_HTML)


def open_new_file_preview(
    window: sublime.Window,
    diff_id: str,
    filepath: str,
    content: str,
) -> None:
    """Open a review view for creating a brand-new file."""
    _pending[diff_id] = _DiffEntry(None, content, filepath)
    ext = os.path.splitext(filepath)[1]
    _open_preview_view(window, diff_id, f"New: {os.path.basename(filepath)}",
                       content, _syntax_for_ext(ext), _CREATE_HTML)


# ── Internal ───────────────────────────────────────────────────────────────────

def _open_preview_view(
    window: sublime.Window,
    diff_id: str,
    name: str,
    text: str,
    syntax: str,
    controls_html: str,
) -> None:
    window.focus_group(0)
    view = window.new_file()
    view.set_name(name)
    view.set_scratch(True)
    view.settings().set(_SETTING, diff_id)
    view.set_read_only(False)
    view.run_command("append", {"characters": text})
    view.set_read_only(True)
    try:
        view.assign_syntax(syntax)
    except Exception:
        pass
    view.add_phantom(
        "diff_controls",
        sublime.Region(0, 0),
        controls_html,
        sublime.LAYOUT_BLOCK,
        on_navigate=lambda href: _on_navigate(href, diff_id, view),
    )


def _on_navigate(href: str, diff_id: str, diff_view: sublime.View) -> None:
    if href == "accept":
        _apply(diff_id, diff_view)
    elif href == "reject":
        _pending.pop(diff_id, None)
        diff_view.close()


def _apply(diff_id: str, diff_view: sublime.View) -> None:
    entry = _pending.pop(diff_id, None)
    if not entry:
        return

    window = diff_view.window()
    diff_view.close()
    if not window:
        return

    if entry.new_filepath:
        window.run_command("sublime_assistant_create_file", {
            "filepath": entry.new_filepath,
            "code": entry.full_proposed,
        })
        return

    target = next((v for v in window.views() if v.id() == entry.target_view_id), None)
    if target:
        window.focus_view(target)
        target.run_command("sublime_assistant_apply_code", {"code": entry.full_proposed})


def _syntax_for_ext(ext: str) -> str:
    return {
        ".py":   "Packages/Python/Python.sublime-syntax",
        ".js":   "Packages/JavaScript/JavaScript.sublime-syntax",
        ".ts":   "Packages/JavaScript/TypeScript.sublime-syntax",
        ".json": "Packages/JSON/JSON.sublime-syntax",
        ".sql":  "Packages/SQL/SQL.sublime-syntax",
        ".md":   "Packages/Markdown/Markdown.sublime-syntax",
        ".sh":   "Packages/ShellScript/Shell-Unix-Generic.sublime-syntax",
    }.get(ext.lower(), "Packages/Text/Plain text.tmLanguage")
