"""Unit tests for assistant.diff_view — the smart snippet-merge engine behind
the inline phantom suggestion and Apply/Accept workflow.

This suite exists to lock in the behavior worked out across a long bug-hunt:
accepting a suggestion must touch *only* the part of the file that actually
changed, matching exactly what the phantom preview shows (same merge
function, same result). Each class below corresponds to a distinct failure
mode that was found and fixed; regressing any one of them silently deletes
or duplicates content in a user's file.
"""
from __future__ import annotations

import difflib
from pathlib import Path

import pytest

from assistant import diff_view

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# ── _is_elision_line ─────────────────────────────────────────────────────

class TestIsElisionLine:
    @pytest.mark.parametrize("line", [
        "<!-- ... rest of file unchanged ... -->\n",
        "<!-- rest of file unchanged -->\n",
        "// ... existing code ...\n",
        "# ... rest of file ...\n",
        "/* ... */\n",
        "...\n",
        "# unchanged\n",
        "-- ... rest of code ...\n",
    ])
    def test_recognized_as_elision(self, line):
        assert diff_view._is_elision_line(line) is True

    @pytest.mark.parametrize("line", [
        "def foo(): ...\n",
        "x = ...\n",
        "## Real heading\n",
        "| Improvement | Cost |\n",
        "\n",
    ])
    def test_not_elision(self, line):
        assert diff_view._is_elision_line(line) is False


# ── _split_on_elisions ───────────────────────────────────────────────────

class TestSplitOnElisions:
    def test_single_trailing_elision_yields_one_chunk(self):
        lines = ["real line 1\n", "real line 2\n", "<!-- ... unchanged ... -->\n"]
        chunks = diff_view._split_on_elisions(lines)
        assert chunks == [["real line 1\n", "real line 2\n"]]

    def test_elisions_on_both_sides_yield_one_chunk(self):
        lines = ["<!-- ... -->\n", "real line\n", "<!-- ... -->\n"]
        chunks = diff_view._split_on_elisions(lines)
        assert chunks == [["real line\n"]]

    def test_multiple_real_chunks_separated_by_elision(self):
        lines = ["chunk one\n", "<!-- ... -->\n", "chunk two\n"]
        chunks = diff_view._split_on_elisions(lines)
        assert chunks == [["chunk one\n"], ["chunk two\n"]]

    def test_all_elisions_yields_no_chunks(self):
        assert diff_view._split_on_elisions(["<!-- ... -->\n"]) == []


# ── _is_trivial_line ─────────────────────────────────────────────────────

class TestIsTrivialLine:
    @pytest.mark.parametrize("line", ["\n", "---\n", "|---|---|\n", "===\n", "   \n"])
    def test_trivial(self, line):
        assert diff_view._is_trivial_line(line) is True

    @pytest.mark.parametrize("line", ["## Heading\n", "a\n", "| a | b |\n"])
    def test_not_trivial(self, line):
        assert diff_view._is_trivial_line(line) is False


# ── _merge_snippet / _diff_bounds: the symmetric-boundary merge core ────

class TestMergeSnippetBoundaries:
    def test_mid_list_deletion_is_dropped(self):
        """A line sandwiched between two matched anchors is a genuine,
        intentional deletion and must be removed."""
        orig = ["# List\n", "- one\n", "- two\n", "- three\n", "- four\n", "- five\n"]
        snippet = ["# List\n", "- one\n", "- two\n", "- four\n", "- five\n"]
        result = diff_view._merge_snippet(orig, snippet)
        assert "- three\n" not in result
        assert result == snippet

    def test_leading_omitted_context_is_preserved(self):
        """Content before the first match that the LLM simply didn't echo
        must be kept, not treated as an intentional deletion."""
        orig = ["Header\n", "\n", "Body\n"]
        snippet = ["Body\n"]
        result = diff_view._merge_snippet(orig, snippet)
        assert result == orig

    def test_trailing_omitted_context_is_preserved(self):
        orig = ["Body\n", "\n", "Footer\n"]
        snippet = ["Body\n"]
        result = diff_view._merge_snippet(orig, snippet)
        assert result == orig

    def test_pure_deletion_with_empty_snippet_removes_everything(self):
        """Regression: an empty snippet (pure deletion, nothing to replace
        it with) used to be silently treated as 'preserve everything' since
        there was no non-delete opcode to anchor the boundary logic against."""
        orig = ["line to delete 1\n", "line to delete 2\n"]
        result = diff_view._merge_snippet(orig, [])
        assert result == []

    def test_unrelated_replacement_replaces_everything(self):
        """When the snippet shares nothing with the region at all, the whole
        region genuinely is the edit (a full rewrite), not omitted context."""
        orig = ["old a\n", "old b\n"]
        snippet = ["new x\n", "new y\n", "new z\n"]
        result = diff_view._merge_snippet(orig, snippet)
        assert result == snippet

    def test_mixed_insert_and_delete_in_one_edit(self):
        orig = ["Header\n", "\n", "old 1\n", "old 2\n", "old 3\n", "\n", "Footer\n"]
        snippet = ["Header\n", "\n", "new replacement\n", "\n", "Footer\n"]
        result = diff_view._merge_snippet(orig, snippet)
        assert result == snippet
        assert "old 1\n" not in result


# ── _locate_window: anchor-based localization for the no-hint fallback ──

class TestLocateWindow:
    def test_returns_none_with_no_shared_content(self):
        orig = ["old line 1\n", "old line 2\n", "old line 3\n"]
        content = ["totally new content\n", "another new line\n"]
        assert diff_view._locate_window(orig, content) is None

    def test_single_weak_anchor_extends_to_cover_its_block(self):
        """A section with only one strong anchor (its heading) must still
        pull in the rest of that section via the local walk-outward pass,
        without a flat safety margin dragging in unrelated content."""
        orig = (
            "## Section 9\n\nSome text 9.\n\n---\n\n"
            "## Section 10\n\nSome text 10.\n\n---\n\n"
            "## Section 11\n\nSome text 11.\n\n---\n\n"
        ).splitlines(keepends=True)
        content = "## Section 10\n\nNEW body.\n\n---\n\n".splitlines(keepends=True)
        window = diff_view._locate_window(orig, content)
        assert window is not None
        start, end = window
        windowed_text = "".join(orig[start:end])
        assert "Section 10" in windowed_text
        assert "Section 9" not in windowed_text
        assert "Section 11" not in windowed_text

    def test_does_not_lock_onto_repeated_boilerplate(self):
        """Regression: a naive longest-common-substring search greedily
        commits to the first equally-long run of shared blank/'---' lines
        it finds — with many near-identical sections, that's almost never
        the right one."""
        sections = [f"## Section {i}\n\nBody {i}.\n\n---\n\n" for i in range(20)]
        orig = "".join(sections).splitlines(keepends=True)
        content = "## Section 10\n\nNEW body.\n\n---\n\n".splitlines(keepends=True)
        start, end = diff_view._locate_window(orig, content)
        windowed_text = "".join(orig[start:end])
        assert "Body 10." in windowed_text
        assert "Body 9." not in windowed_text
        assert "Body 11." not in windowed_text

    def test_reworded_neighbor_of_a_strong_anchor_is_included_without_overreach(self):
        """Regression case from a real user report: a table header reworded
        enough to score just under the global anchor threshold must still
        be pulled in via the local walk-outward pass — but the window must
        not overshoot past it into unrelated surrounding content."""
        orig = (
            "## Heading Above\n\n"
            "| Old Header | Other |\n"
            "|---|---|\n"
            "| Row One | 1 |\n"
            "| Row Two | 2 |\n"
            "\n"
            "Text below.\n"
        ).splitlines(keepends=True)
        content = (
            "| New Header | Other |\n"
            "|---|---|\n"
            "| Row One | 1 |\n"
            "| Row Two | 2 |\n"
        ).splitlines(keepends=True)
        start, end = diff_view._locate_window(orig, content)
        windowed_text = "".join(orig[start:end])
        assert "Old Header" in windowed_text
        assert "Heading Above" not in windowed_text
        assert "Text below." not in windowed_text


# ── compute_proposed: end-to-end merge scenarios ─────────────────────────

class TestComputeProposedEndToEnd:
    def test_mid_list_delete_no_hint(self):
        orig = "# List\n- one\n- two\n- three\n- four\n- five\n"
        snippet = "# List\n- one\n- two\n- four\n- five\n"
        proposed = diff_view.compute_proposed(orig, snippet, None)
        assert "- three\n" not in proposed
        for kept in ("- one\n", "- two\n", "- four\n", "- five\n"):
            assert kept in proposed

    def test_mixed_insert_delete_no_hint(self):
        orig = "Header\n\nold 1\nold 2\nold 3\n\nFooter\n"
        snippet = "Header\n\nnew replacement\n\nFooter\n"
        proposed = diff_view.compute_proposed(orig, snippet, None)
        assert "old 1\n" not in proposed
        assert "new replacement\n" in proposed
        assert "Header" in proposed and "Footer" in proposed

    def test_elision_chunk_with_internal_deletion(self):
        orig = (
            "# Doc\n\n## Section A\nline a1\nline a2\nline a3\nline a4\n\n"
            "## Section B\nline b1\nline b2\n"
        )
        snippet = "## Section A\nline a1\nline a4\n\n<!-- ... rest of file unchanged ... -->\n"
        proposed = diff_view.compute_proposed(orig, snippet, None)
        assert "line a2" not in proposed and "line a3" not in proposed
        assert "line a1" in proposed and "line a4" in proposed
        assert "line b1" in proposed and "line b2" in proposed
        assert "rest of file unchanged" not in proposed

    def test_hint_region_scopes_the_edit_precisely(self):
        orig = "Before\n\nold row 1\nold row 2\n\nAfter\n"
        orig_lines = orig.splitlines(keepends=True)
        start = orig_lines.index("old row 1\n")
        end = start + 2
        snippet = "new row 1\nnew row 2\nnew row 3\n"
        proposed = diff_view.compute_proposed(orig, snippet, (start, end))
        assert "old row 1\n" not in proposed
        assert "new row 3\n" in proposed
        assert "Before" in proposed and "After" in proposed

    def test_full_rewrite_with_zero_overlap(self):
        orig = "old line 1\nold line 2\nold line 3\n"
        snippet = "totally new content\nanother new line\n"
        proposed = diff_view.compute_proposed(orig, snippet, None)
        assert proposed == snippet

    def test_pure_deletion_with_hint_region(self):
        orig = "Header\n\nParagraph to delete line 1\nParagraph to delete line 2\n\nFooter\n"
        orig_lines = orig.splitlines(keepends=True)
        start = orig_lines.index("Paragraph to delete line 1\n")
        proposed = diff_view.compute_proposed(orig, "", (start, start + 2))
        assert "Paragraph to delete" not in proposed
        assert "Header" in proposed and "Footer" in proposed

    def test_pure_deletion_without_hint_region(self):
        orig = "A\nB\nC to remove\nD\n"
        proposed = diff_view.compute_proposed(orig, "", None)
        assert proposed == ""

    def test_pure_deletion_single_line(self):
        orig = "keep1\nDELETE ME\nkeep2\n"
        orig_lines = orig.splitlines(keepends=True)
        idx = orig_lines.index("DELETE ME\n")
        proposed = diff_view.compute_proposed(orig, "", (idx, idx + 1))
        assert proposed == "keep1\nkeep2\n"

    def test_repeated_boilerplate_document_stays_localized(self):
        sections = [f"## Section {i}\n\nBody {i}.\n\n---\n\n" for i in range(20)]
        orig = "".join(sections)
        snippet = "## Section 10\n\nNEW body.\n\n---\n\n"
        proposed = diff_view.compute_proposed(orig, snippet, None)
        assert "NEW body." in proposed
        assert "Body 10." not in proposed
        for i in range(20):
            if i != 10:
                assert f"Body {i}." in proposed


# ── Real-world regression: the exact document/response pair that exposed
#    the "unrelated content silently deleted" bug in production ─────────

class TestRealWorldRegression:
    @pytest.fixture
    def improvements_doc(self) -> str:
        return (FIXTURES_DIR / "improvements.md").read_text()

    NEW_TABLE = (
        "| Improvement       | Approach                                      | Cost (€)       | Effort      |\n"
        "|-------------------|-----------------------------------------------|----------------|-------------|\n"
        "| Plug-in battery   | Anker SOLIX Solarbank 3 (2.7 kWh) or MAX AC (7 kWh) | 800–2,500 (DIY) | Very low    |\n"
        "| Air conditioning  | Triple multi-split (Daikin/Mitsubishi/LG), 3 units | 3,200–5,200    | Medium      |\n"
        "| Skylight removal  | Remove + seal with insulated kunststof panel, route AC pipes | 800–2,200      | Medium      |\n"
        "| Rolluiken         | Buitenvoorzetrolluiken on front windows, electric preferred | 800–2,000      | Low–medium  |\n"
        "<!-- ... rest of file unchanged ... -->\n"
    )

    def test_condensed_table_headers_no_hint(self, improvements_doc):
        proposed = diff_view.compute_proposed(improvements_doc, self.NEW_TABLE, None)

        # The table itself changed.
        assert "Est. cost (incl. install)" not in proposed
        assert "Cost (€)" in proposed

        # Everything else in the document — the heading right above the
        # table, the blockquote right below it, every section, and the
        # trailing footer — must survive untouched. This is exactly what
        # silently vanished when the locate-window padding overshot into
        # unrelated content.
        assert "## Summary Overview" in proposed
        assert "Coordination tip" in proposed
        assert "Research date: July 2026" in proposed
        for heading in (
            "## 1. Plug-in Battery",
            "## 2. Air Conditioning",
            "## 3. Skylight",
            "## 4. Window Darkening",
        ):
            assert heading in proposed

    def test_condensed_table_headers_produces_a_localized_diff(self, improvements_doc):
        """The unified diff (what the phantom preview renders as red/green)
        must show exactly the 6 changed table rows — no more, no less."""
        proposed = diff_view.compute_proposed(improvements_doc, self.NEW_TABLE, None)
        diff_lines = list(difflib.unified_diff(
            improvements_doc.splitlines(keepends=True),
            proposed.splitlines(keepends=True),
            n=2,
        ))
        reds = [l for l in diff_lines if l.startswith("-") and not l.startswith("---")]
        greens = [l for l in diff_lines if l.startswith("+") and not l.startswith("+++")]
        assert len(reds) == 6
        assert len(greens) == 6

    def test_condensed_table_headers_with_explicit_hint(self, improvements_doc):
        """Same edit, but with a selection hint (the common real path) —
        must produce the same clean, localized result."""
        orig_lines = improvements_doc.splitlines(keepends=True)
        start = next(
            i for i, l in enumerate(orig_lines)
            if l.startswith("| Improvement | Recommended")
        )
        end = start + 6
        table_only = "\n".join(self.NEW_TABLE.splitlines()[:-1]) + "\n"  # drop elision line
        proposed = diff_view.compute_proposed(improvements_doc, table_only, (start, end))
        assert "Cost (€)" in proposed
        assert "Est. cost (incl. install)" not in proposed
        assert "## Summary Overview" in proposed
        assert "Coordination tip" in proposed
