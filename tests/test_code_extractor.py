"""Unit tests for assistant.code_extractor — fenced code block parsing."""
from __future__ import annotations

from assistant import code_extractor


def test_extracts_plain_fenced_block():
    text = "Here you go:\n\n```python\nprint('hi')\n```\n"
    blocks = code_extractor.extract(text)
    assert len(blocks) == 1
    assert blocks[0].language == "python"
    assert blocks[0].filepath is None
    assert blocks[0].content == "print('hi')\n"


def test_extracts_filepath_tag():
    text = "```python:src/utils.py\nx = 1\n```\n"
    blocks = code_extractor.extract(text)
    assert blocks[0].filepath == "src/utils.py"
    assert blocks[0].language == "python"


def test_bare_fence_with_no_language_is_never_an_opener():
    """The extractor deliberately requires a language tag on the opening
    fence (```lang or ```lang:path) — a bare ``` never opens a block, since
    otherwise a closing fence for a real block could be ambiguous with it."""
    text = "```\nplain text block\n```\n"
    blocks = code_extractor.extract(text)
    assert blocks == []


def test_multiple_blocks_in_one_reply():
    text = "```python\na = 1\n```\nsome text\n```python\nb = 2\n```\n"
    blocks = code_extractor.extract(text)
    assert len(blocks) == 2
    assert blocks[0].content == "a = 1\n"
    assert blocks[1].content == "b = 2\n"


def test_nested_fence_is_kept_as_content_not_a_closer():
    text = (
        "```markdown:README.md\n"
        "Example:\n"
        "```bash\n"
        "echo hi\n"
        "```\n"
        "```\n"
    )
    blocks = code_extractor.extract(text)
    assert len(blocks) == 1
    assert blocks[0].language == "markdown"
    assert blocks[0].filepath == "README.md"
    assert "echo hi" in blocks[0].content
    assert "```bash" in blocks[0].content


def test_unclosed_fence_is_skipped():
    text = "```python\nno closing fence here\n"
    blocks = code_extractor.extract(text)
    assert blocks == []


def test_bare_triple_backtick_line_is_not_an_opener():
    text = "some text\n```\nnot a real fence since no language/path and stray\n"
    blocks = code_extractor.extract(text)
    assert blocks == []
