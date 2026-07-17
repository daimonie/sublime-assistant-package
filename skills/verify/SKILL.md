---
name: verify
description: Use when the user asks whether a file is up to date, accurate, current, or reflects the present state of the project — e.g. "Is README.md up to date?", "Does this file reflect the current code?", "Check if X is accurate", "Make sure Y is current".
---

**Steps (always follow in order):**

1. Call `list_project_files` to get the full project file index and understand what exists.
2. Identify which source files are most relevant to the file being verified (e.g. for README.md:
   all source files; for a module docstring: its imports and callers).
3. Call `get_file_summary` on each relevant file to read its actual contents.
4. Compare the target file's claims, feature lists, API descriptions, or architecture diagrams
   against what you found in step 3.
5. Report your findings in two sections:
   - **Up to date**: what is correct.
   - **Needs updating**: what is missing, wrong, or outdated — with specific line references
     and concrete suggested fixes where possible.

**Important:** Do not answer based only on the file provided. You must call the tools in
steps 1–3 before drawing conclusions. A file that looks complete may still be missing
recently added features.
