"""Extract fenced code blocks from assistant reply text."""
from __future__ import annotations

import re
from typing import NamedTuple

# Matches ```lang``` or ```lang:filepath``` fences (including multiline content)
_FENCE = re.compile(r'```([\w.-]*?)(?::([^\n]+))?\n(.*?)```', re.DOTALL)


class CodeBlock(NamedTuple):
    language: str        # e.g. "python", "sql", ""
    filepath: str | None # e.g. "src/utils.py", or None if not specified
    content: str         # the code inside the fences
    end_pos: int         # char offset of the char after the closing ``` in the reply


def extract(text: str) -> list[CodeBlock]:
    """Return all fenced code blocks found in text."""
    blocks = []
    for m in _FENCE.finditer(text):
        blocks.append(CodeBlock(
            language=m.group(1) or "",
            filepath=m.group(2).strip() if m.group(2) else None,
            content=m.group(3),
            end_pos=m.end(),
        ))
    return blocks
