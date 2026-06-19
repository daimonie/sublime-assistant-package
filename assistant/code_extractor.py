"""Extract fenced code blocks from assistant reply text."""
from __future__ import annotations

import re
from typing import NamedTuple

# Opening fence: ```lang  or  ```lang:path  (never bare ```)
_OPEN_RE = re.compile(r'^```([\w.-]*)(?::([^\n`]+))?[ \t]*$')
# Bare closing fence (nothing after the backticks)
_CLOSE_RE = re.compile(r'^```[ \t]*$')


class CodeBlock(NamedTuple):
    language: str        # e.g. "python", "sql", ""
    filepath: str | None # e.g. "src/utils.py", or None if not specified
    content: str         # the code inside the fences
    end_pos: int         # char offset of the char after the closing ``` in the reply


def extract(text: str) -> list[CodeBlock]:
    """Return all fenced code blocks found in text.

    Handles nested fences (e.g. a ```bash block inside a ```markdown:README.md block)
    by tracking depth: only a bare ``` at depth 0 closes the outer block.
    """
    blocks: list[CodeBlock] = []
    lines = text.splitlines(keepends=True)
    n = len(lines)
    pos = 0
    i = 0

    while i < n:
        line = lines[i]
        stripped = line.rstrip('\n\r')
        m = _OPEN_RE.match(stripped)

        if m and stripped != '```':
            # Opening fence with a language tag and/or filepath
            lang = m.group(1) or ""
            filepath = m.group(2).strip() if m.group(2) else None
            i += 1
            pos += len(line)

            content_parts: list[str] = []
            depth = 0

            while i < n:
                inner = lines[i]
                inner_stripped = inner.rstrip('\n\r')

                if _CLOSE_RE.match(inner_stripped):
                    if depth == 0:
                        # Closes the outer block
                        pos += len(inner)
                        i += 1
                        blocks.append(CodeBlock(lang, filepath, ''.join(content_parts), pos))
                        break
                    else:
                        # Closes an inner nested block
                        depth -= 1
                        content_parts.append(inner)
                        pos += len(inner)
                        i += 1
                elif _OPEN_RE.match(inner_stripped) and inner_stripped != '```':
                    # Opening an inner nested block
                    depth += 1
                    content_parts.append(inner)
                    pos += len(inner)
                    i += 1
                else:
                    content_parts.append(inner)
                    pos += len(inner)
                    i += 1
            # If loop exhausted without closing, block is unclosed — skip it
        else:
            pos += len(line)
            i += 1

    return blocks
