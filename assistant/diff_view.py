"""Diff preview and new-file preview views for Apply workflow."""
from __future__ import annotations

import difflib
import os
import re
from difflib import SequenceMatcher
from typing import NamedTuple

import sublime

_DEF_RE = re.compile(r'^(?:async\s+)?(?:def|class)\s+(\w+)', re.MULTILINE)

# Matches placeholder lines an LLM uses to mean "unchanged content goes here",
# e.g. "<!-- ... rest of file unchanged ... -->", "// ... existing code ...",
# "# ... rest of file ...", or a bare "...". These are never real file content
# and must not be diffed/merged as literal text.
_BLOCK_COMMENT_RE = re.compile(r'^(?:<!--|/\*)\s*(.*?)\s*(?:-->|\*/)$', re.DOTALL)
_LINE_COMMENT_RE = re.compile(r'^(?:#|//|;|--)\s*(.*)$')
_ELISION_DOTS_RE = re.compile(r'\.{3,}')
_ELISION_WS_RE = re.compile(r'\s+')
_ELISION_KEYWORDS = frozenset({
    'rest of file unchanged', 'rest of the file unchanged',
    'rest of code unchanged', 'rest of the code unchanged',
    'rest of file', 'rest of the file', 'rest of code', 'rest of the code',
    'existing code unchanged', 'existing code',
    'remains the same', 'remains same', 'unchanged',
    'no changes', 'no changes made',
    'truncated', 'truncated for brevity',
    'code continues unchanged', 'omitted',
})


def _strip_comment_wrapper(stripped: str) -> str:
    m = _BLOCK_COMMENT_RE.match(stripped)
    if m:
        return m.group(1).strip()
    m = _LINE_COMMENT_RE.match(stripped)
    if m:
        return m.group(1).strip()
    return stripped


def _is_elision_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    inner = _strip_comment_wrapper(stripped)
    if not inner:
        return False
    core = _ELISION_DOTS_RE.sub(' ', inner)
    core = _ELISION_WS_RE.sub(' ', core).strip(' ()').lower()
    if core == '':
        return True  # pure "..." (with or without a comment wrapper)
    return core in _ELISION_KEYWORDS


def _split_on_elisions(lines: list[str]) -> list[list[str]]:
    """Split snippet lines into chunks of real content, dropping elision markers."""
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if _is_elision_line(line):
            if current:
                chunks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        chunks.append(current)
    return chunks


class _DiffEntry(NamedTuple):
    target_view_id: int | None
    full_proposed: str       # complete merged file content, ready to write
    new_filepath: str | None


_pending: dict[str, _DiffEntry] = {}
_SETTING = "sublime_assistant_diff_id"

_CONTROLS_HTML = """<body id="sa_controls">
<style>
  body { margin: 4px 0; }
  a { padding: 3px 12px; border-radius: 3px; text-decoration: none; font-size: 1em; font-weight: bold; margin-right: 6px; }
  .ok { background-color: #238636; color: #fff; }
  .no { background-color: #da3633; color: #fff; }
</style>
<a class="ok" href="accept">&#10003; Accept</a>
<a class="no" href="reject">&#10007; Reject</a>
</body>"""

_CREATE_HTML = """<body id="sa_controls">
<style>
  body { margin: 4px 0; }
  a { padding: 3px 12px; border-radius: 3px; text-decoration: none; font-size: 1em; font-weight: bold; margin-right: 6px; }
  .ok { background-color: #238636; color: #fff; }
  .no { background-color: #da3633; color: #fff; }
</style>
<a class="ok" href="accept">&#10003; Create File</a>
<a class="no" href="reject">&#10007; Reject</a>
</body>"""


# ── Snippet merging ────────────────────────────────────────────────────────────

def _diff_bounds(
    orig_lines: list[str], snippet_lines: list[str]
) -> tuple[list[tuple[str, int, int, int, int]], int, int]:
    """Diff orig_lines against snippet_lines and return (opcodes, first_start, last_end).

    first_start / last_end are the original-side bounds of the first and last
    non-delete opcode — i.e. the span of original content the snippet actually
    engages with. Deletes outside that span are content the LLM simply didn't
    echo (omitted context); deletes inside it are genuine removals.
    """
    matcher = SequenceMatcher(None, orig_lines, snippet_lines, autojunk=False)
    opcodes = list(matcher.get_opcodes())

    first_non_delete_start = None
    last_non_delete_end = None
    for tag, i1, i2, j1, j2 in opcodes:
        if tag != 'delete':
            first_non_delete_start = i1 if first_non_delete_start is None else min(first_non_delete_start, i1)
            last_non_delete_end = i2 if last_non_delete_end is None else max(last_non_delete_end, i2)

    if first_non_delete_start is None:
        # The snippet shares NOTHING with this region (e.g. it's empty — a
        # pure deletion, or unrelated replacement text). There's no anchor to
        # treat anything as "omitted context", so the whole region is the
        # edit itself: every delete opcode here must be genuine, not
        # boundary-preserved. Using bounds of (0, len) makes every delete's
        # i2 <= first_start / i1 >= last_end check fail, so _merge_snippet's
        # "intentional deletion — drop" branch applies to all of it.
        first_non_delete_start = 0
        last_non_delete_end = len(orig_lines)

    return opcodes, first_non_delete_start, last_non_delete_end


_HAS_CONTENT_RE = re.compile(r'[A-Za-z0-9]')


def _is_trivial_line(line: str) -> bool:
    """True for lines with no real content: blank lines, '---' / '===' rules,
    table separator rows ('|---|---|'), etc. These recur identically all over
    a file and make worthless anchors."""
    return not _HAS_CONTENT_RE.search(line)


_ANCHOR_RATIO_THRESHOLD = 0.55
_LOCAL_MATCH_THRESHOLD = 0.3


def _locate_window(
    orig_lines: list[str], content_lines: list[str]
) -> tuple[int, int] | None:
    """Find a window in orig_lines around the strongest textual anchor(s)
    shared with content_lines.

    Used whenever there's no explicit hint region, so the diff stays local to
    where the edit actually is instead of aligning against coincidentally
    similar lines elsewhere in a large file.

    Two passes, deliberately not one:

    1. A *global* per-line fuzzy search establishes a small set of
       high-confidence "core" anchors (_ANCHOR_RATIO_THRESHOLD). This can't
       be run at a low threshold: a context-free global search will happily
       match short, generic lines against unrelated content purely by
       chance (e.g. two totally unrelated one-liners can score ~0.5 just
       from shared common words) — there is no fixed cutoff that reliably
       separates that from a genuinely related but reworded line.
    2. From that core, walk outward one content-line at a time, comparing
       each remaining line only against its *immediate neighbor* in
       orig_lines (a local, not global, comparison). A weak match here is
       trustworthy in a way the same score isn't in a global search, because
       we already know roughly where we are — so a much lower threshold
       (_LOCAL_MATCH_THRESHOLD) is safe. This is what lets a reworded table
       header right next to its (strongly anchored) rows still get pulled
       into the window, without also pulling in a same-scoring but unrelated
       line elsewhere in the document.

    Trivial lines (blank, '---' rules) never serve as anchors, but do extend
    the window when they sit adjacent to it — they're cheap padding, not a
    source of false localization.
    """
    if not orig_lines or not content_lines:
        return None

    non_trivial_orig = [
        (idx, line) for idx, line in enumerate(orig_lines) if not _is_trivial_line(line)
    ]
    if not non_trivial_orig:
        return None

    # Pass 1: high-confidence core anchors via global search.
    anchors: list[tuple[int, int, float]] = []
    for content_idx, line in enumerate(content_lines):
        if _is_trivial_line(line):
            continue
        best_ratio = 0.0
        best_idx = None
        for idx, orig_line in non_trivial_orig:
            ratio = SequenceMatcher(None, line, orig_line, autojunk=False).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = idx
                if ratio == 1.0:
                    break
        if best_idx is not None and best_ratio >= _ANCHOR_RATIO_THRESHOLD:
            anchors.append((content_idx, best_idx, best_ratio))

    if not anchors:
        return None

    # Keep only anchors positionally consistent with the single strongest
    # match. A short, generic content line (e.g. "NEW body.") can score
    # above the threshold against a coincidentally similar but unrelated
    # line elsewhere in a large, repetitive document; left in, a single
    # stray anchor like that blows the window open across everything
    # between it and the real edit. A genuine multi-anchor match moves
    # through orig_lines in step with content order, so anything whose
    # orig-position offset from the seed doesn't track its content-index
    # offset is a coincidence, not part of this edit.
    seed_content_idx, seed_orig_idx, _ = max(anchors, key=lambda a: a[2])
    tolerance = max(len(content_lines), 10)
    anchors = [
        a for a in anchors
        if abs((a[1] - seed_orig_idx) - (a[0] - seed_content_idx)) <= tolerance
    ]

    orig_positions = [a[1] for a in anchors]
    start = min(orig_positions)
    end = max(orig_positions) + 1

    # Pass 2: walk outward from the core, one line at a time, using local
    # (not global) comparisons.
    first_anchor_content_idx = anchors[0][0]
    for k in range(first_anchor_content_idx - 1, -1, -1):
        if start <= 0:
            break
        line = content_lines[k]
        if _is_trivial_line(line):
            start -= 1
            continue
        ratio = SequenceMatcher(None, line, orig_lines[start - 1], autojunk=False).ratio()
        if ratio < _LOCAL_MATCH_THRESHOLD:
            break
        start -= 1

    last_anchor_content_idx = anchors[-1][0]
    for k in range(last_anchor_content_idx + 1, len(content_lines)):
        if end >= len(orig_lines):
            break
        line = content_lines[k]
        if _is_trivial_line(line):
            end += 1
            continue
        ratio = SequenceMatcher(None, line, orig_lines[end], autojunk=False).ratio()
        if ratio < _LOCAL_MATCH_THRESHOLD:
            break
        end += 1

    return (start, end)


def _merge_snippet(orig_lines: list[str], snippet_lines: list[str]) -> list[str]:
    """Merge a (possibly partial) snippet into the original lines.

    Uses SequenceMatcher to classify each block:
    - equal / replace / insert        →  take from snippet as normal
    - delete before the first match,
      or after the last match         →  keep from original (omitted context)
    - delete sandwiched between two
      matched blocks                  →  drop (intentional removal by LLM)
    """
    opcodes, first_start, last_end = _diff_bounds(orig_lines, snippet_lines)

    result: list[str] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == 'equal':
            result.extend(orig_lines[i1:i2])
        elif tag in ('replace', 'insert'):
            result.extend(snippet_lines[j1:j2])
        elif tag == 'delete':
            if i2 <= first_start or i1 >= last_end:
                # Boundary: snippet omitted this context — keep original
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


def _merge_chunks_anchored(orig_lines: list[str], chunks: list[list[str]]) -> list[str]:
    """Merge snippet chunks (as split around elision markers) into original lines.

    Each chunk is first localized to a window around its strongest textual
    anchor (_locate_window), then diffed within that window only
    (_diff_bounds) — so later chunks, and any trailing content after the
    last chunk like a footer, are carried over from the original untouched,
    and the diff can't get pulled toward unrelated look-alike lines
    elsewhere in the remaining file.
    """
    result: list[str] = []
    cursor = 0

    for chunk in chunks:
        if not chunk:
            continue
        remaining = orig_lines[cursor:]
        if not remaining:
            result.extend(chunk)
            continue

        window = _locate_window(remaining, chunk)
        if window is None:
            # No match anywhere in what's left — best effort: insert here
            # without consuming any original lines.
            result.extend(chunk)
            continue

        wstart, wend = window
        result.extend(remaining[:wstart])
        windowed = remaining[wstart:wend]

        opcodes, first_start, last_end = _diff_bounds(windowed, chunk)
        for tag, i1, i2, j1, j2 in opcodes:
            if i2 > last_end:
                continue  # belongs after this chunk's matched span
            if tag == 'equal':
                result.extend(windowed[i1:i2])
            elif tag in ('replace', 'insert'):
                result.extend(chunk[j1:j2])
            elif tag == 'delete' and i2 <= first_start:
                result.extend(windowed[i1:i2])
                # else: intentional deletion within the matched span — drop

        cursor += wstart + last_end

    result.extend(orig_lines[cursor:])
    return result


def _compute_proposed_file(
    orig_lines: list[str],
    proposed_code: str,
    hint_region: tuple[int, int] | None,
) -> list[str]:
    """Return the full proposed file as a line list, merging the snippet smartly."""
    snippet_lines = proposed_code.splitlines(keepends=True)

    # 0. Snippet contains elision markers ("... rest of file unchanged ...") —
    #    strip them and anchor each remaining real chunk independently so
    #    unrelated parts of the file (e.g. a trailing footer) are never
    #    touched or replaced by placeholder text.
    if any(_is_elision_line(line) for line in snippet_lines):
        chunks = _split_on_elisions(snippet_lines)
        if hint_region:
            start, end = hint_region
            merged = _merge_chunks_anchored(orig_lines[start:end], chunks)
            return orig_lines[:start] + merged + orig_lines[end:]
        return _merge_chunks_anchored(orig_lines, chunks)

    # 1. Explicit selection hint — highest priority
    if hint_region:
        start, end = hint_region
        merged = _merge_snippet(orig_lines[start:end], snippet_lines)
        return orig_lines[:start] + merged + orig_lines[end:]

    # 2. Locate by def/class name — works for single-function edits
    region = _find_snippet_region(''.join(orig_lines), proposed_code)
    if region:
        start, end = region
        merged = _merge_snippet(orig_lines[start:end], snippet_lines)
        return orig_lines[:start] + merged + orig_lines[end:]

    # 3. No explicit anchor — localize to a window around the strongest
    #    shared textual anchor, then diff within that window only. This
    #    keeps the diff local instead of aligning against coincidentally
    #    similar lines (blank lines, table separators, etc.) elsewhere in a
    #    large file, which would otherwise scatter it across the document.
    window = _locate_window(orig_lines, snippet_lines)
    if window:
        start, end = window
        merged = _merge_snippet(orig_lines[start:end], snippet_lines)
        return orig_lines[:start] + merged + orig_lines[end:]

    # 4. No shared anchor at all — genuine full-file replacement/rewrite.
    return _merge_snippet(orig_lines, snippet_lines)


def compute_proposed(orig: str, proposed_code: str, hint_region: tuple[int, int] | None) -> str:
    """Return the full merged file content for direct application (no diff view)."""
    orig_lines = orig.splitlines(keepends=True)
    new_lines = _compute_proposed_file(orig_lines, proposed_code, hint_region)
    return "".join(new_lines)


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
