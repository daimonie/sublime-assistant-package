# SublimeAssistant

[![tests](https://github.com/daimonie/sublime-assistant-package/actions/workflows/tests.yml/badge.svg)](https://github.com/daimonie/sublime-assistant-package/actions/workflows/tests.yml)

**SublimeAssistant** brings AI-powered coding assistance directly into **Sublime Text 4**. Seamlessly connect your editor to a local LLM (via Ollama), the Mistral API, or the Claude (Anthropic) API for a streamlined workflow. Generate code, debug issues, and document projects—all without leaving your keyboard.

Key features include:
- A persistent chat panel for context-aware conversations.
- Inline code suggestions with one-click apply.
- File referencing and streaming responses.
- Lightweight, thread-safe, and dependency-free (Python 3.8).

---

## features

- **context-aware ai** — automatically sends the active file and any selected text with every query.
- **persistent chat panel** — a dedicated markdown split-pane that keeps the full conversation history per window.
- **streaming responses** — token-by-token display as the model generates, so you see the reply as it arrives.
- **inline phantom suggestions** — every code block the assistant produces shows a colored red/green diff phantom directly in your editor at the target location, with **accept** (instant apply), **≋ diff** (open diff preview), and **dismiss** buttons. no need to leave the editor to review a suggestion.
- **smart, localized merging** — accepting a suggestion never overwrites the whole file. the plugin anchors the snippet to the specific lines it actually changes (via your selection, a matched `def`/`class`, or fuzzy text-anchor localization), so edits, insertions, and deletions elsewhere in the snippet only touch what really changed — everything else in the file is left byte-for-byte alone. the same merge logic drives both the phantom preview and the actual Accept, so what you see is exactly what you get.
- **apply with diff preview** — the **≋ diff** path opens a unified diff showing exactly what changes, with **✓ accept** / **✗ reject** before anything is written to disk.
- **slash commands** — type a command alone in the input strip to trigger preset prompts or plugin actions. See the [Slash Commands](#slash-commands) section for details.
- **project rules** — place an `agents.md` and/or `skills.md` file at the git root. their contents are automatically prepended to the system prompt at the start of each window session, letting you set project-specific instructions, conventions, or personas.
- **suggested commands** — when the model recommends a shell command to run (tests, linter, build), it outputs it in a `suggested-command` fenced block. the plugin displays it but **never executes it** — you copy and run it yourself.
- **multi-file referencing** — type `@filename.ext` in the input area to include any open or project file.
- **tool use** — the model can call `read_file`, `fetch_url`, `list_project_files`, and `get_file_summary` tools mid-conversation. tool calls are shown in the chat footer.
- **directory summary (lazy)** — run `/init` or **summarize directory** from the command palette to crawl the git root and build llm-generated per-file descriptions. the summary is cached to `.sublime_assistant_summary.md`. when the model needs project context it calls `list_project_files` / `get_file_summary` rather than having the entire summary injected into every message.
- **new file creation** — when the llm suggests a brand-new file, the apply workflow lets you review and create it with one click.
- **preset switching** — switch between a local ollama endpoint, the mistral api, or the claude api from the command palette without touching config files.
- **auto-reload on save** — when you edit any file in `sublimeassistant/assistant/` *from within Sublime Text*, the submodule is hot-reloaded automatically. editing those files with an external tool (another editor, an AI coding agent) won't trigger this — restart Sublime Text to pick up the change.
- **asynchronous** — api calls run in a background thread; the editor never freezes.

---

## Slash Commands

Slash commands are preset prompts or plugin actions triggered by typing a command alone in the input strip (e.g., `/explain`). You can append extra context after the command (e.g., `/fix there's a race condition in the handler`).

| Command | Description |
|---------|-------------|
| `/explain` | Explain the selected code in detail. |
| `/fix` | Identify and fix bugs in the selected code. |
| `/tests` | Write unit tests for the selected code. |
| `/review` | Perform a code review for correctness, style, and performance. |
| `/debug` | Root-cause analysis and fix proposal for the selected code. |
| `/docs` | Write docstrings and comments for the selected code. |
| `/research` | Research a topic using the `fetch_url` tool and cite sources. |
| `/diff` | Explain and review the current working-tree diff. |
| `/init` | Crawl the project directory and build the file-summary cache. |
| `/compact` | Clear the conversation history for this window. |
| `/clear` | Same as `/compact`. |

Slash commands must be the **entire input** (e.g., type `/explain` alone, then press `Ctrl+Enter`).

---

| Model | VRAM needed | Notes |
|-------|-------------|-------|
| `devstral-small-2:latest` (22 B) | ~14 GB | Requires a high-end consumer GPU (RTX 3090 / 4090, or better) |

If you do not have a suitable GPU, **use the Mistral API preset instead** — it runs the same model in the cloud with no local hardware requirement. See [Configuration](#configuration) below.

---

## Prerequisites

- **Sublime Text 4** build 4050 or later
- **Python 3.8** (bundled with Sublime Text 4 — no extra install needed)
- No external Python packages required (uses stdlib only)
- **For local hosting:** Ollama installed and running, with a compatible model pulled (see above)
- **For Mistral cloud:** A [Mistral API key](https://console.mistral.ai/)
- **For Claude cloud:** An [Anthropic API key](https://console.anthropic.com/) (see [Getting a Claude API key](#getting-a-claude-api-key))

### Local setup (Ollama)

```bash
# Install Ollama, then pull the coding model
ollama pull devstral-small-2:latest
```

Ollama must be running on `http://localhost:11434` (its default). If Sublime Text runs on
Windows and Ollama runs inside WSL, use the WSL hostname instead of `localhost`
(e.g. `http://LanteanHome:11434/v1/chat/completions`).

---

## Installation

Clone into your Sublime Text `Packages` folder:

| OS      | Path |
|---------|------|
| Windows (installed) | `%APPDATA%\Sublime Text\Packages\` |
| Windows (portable)  | `<Sublime Text portable folder>\Data\Packages\` |
| macOS   | `~/Library/Application Support/Sublime Text/Packages/` |
| Linux   | `~/.config/sublime-text/Packages/` |

```bash
cd "YOUR_PACKAGES_FOLDER"
git clone https://github.com/YOUR_USERNAME/SublimeAssistant.git
```

Restart Sublime Text. No further setup is needed for local Ollama use.
For Mistral or Claude API use, set your API key via the Command Palette after restarting (see below).

---

## Usage

| Action | How |
|--------|-----|
| Open chat + input area | `Ctrl+L` |
| Submit a message | Type in the input strip → `Ctrl+Enter` |
| Run a slash command | Type `/command` alone → `Ctrl+Enter` |
| Reference a file | `@filename.ext` anywhere in your message |
| Accept inline suggestion | Click **✓ Accept** in the editor phantom |
| Open diff for inline suggestion | Click **≋ Diff** in the editor phantom |
| Dismiss inline suggestion | Click **✗ Dismiss** in the editor phantom |
| Apply from chat panel | Click **Apply** below a code block in the chat |
| Accept diff | Click **✓ Accept** in the diff preview |
| Reject diff | Click **✗ Reject** in the diff preview |
| Build project file index | Type `/init` → `Ctrl+Enter` |
| Clear conversation history | Type `/compact` → `Ctrl+Enter` |
| Switch to local (Ollama) | Command Palette → **Sublime Assistant: Use preset Local** |
| Switch to Mistral API | Command Palette → **Sublime Assistant: Use preset Mistral** |
| Switch to Claude API | Command Palette → **Sublime Assistant: Use preset Claude** |
| Set Mistral API key | Command Palette → **Sublime Assistant: Set Mistral API key** |
| Set Claude API key | Command Palette → **Sublime Assistant: Set Claude API key** |
| Select model (any preset) | Command Palette → **Sublime Assistant: Select Model** |
| Summarize current directory | Command Palette → **Sublime Assistant: Summarize Directory** |

---

## Project rules (AGENTS.md / SKILLS.md)

Place one or both files at the **git root** of your project:

- **`AGENTS.md`** — Project-level instructions: coding conventions, architecture notes, personas, things the model must always do or avoid.
- **`SKILLS.md`** — Optional secondary rules (e.g. team-specific tooling notes).

Both files are read once per window session and prepended to the system prompt. They are never sent again after the first message, so they don't inflate subsequent requests.

Example `AGENTS.md`:

```markdown
This project uses Python 3.11 and follows PEP 8 strictly.
All public functions must have type hints and a one-line docstring.
Never suggest external dependencies unless explicitly asked.
```

---

## Configuration

Edit `SublimeAssistant.sublime-settings` (or create a User override in `Packages/User/`).

### Settings reference

- **`active_preset`** — Which preset is used: `"local"`, `"mistral"`, `"claude"`, or any custom name.
- **`presets`** — Map of preset names to connection settings. Each preset supports:
  - `api_url` — Full chat completions endpoint (OpenAI-compatible backends).
  - `api_key` — API key for the backend.
  - `model` — Model ID to use.
  - `backend` — `"openai"` (default, covers Ollama/Mistral/LM Studio) or `"claude"` (Anthropic). Required for Claude API usage.
- **`request_timeout`** — Timeout in seconds for an AI request (default 120). Increase when using fetch_url or with slow token generation.
- **`system_prompt`** — Instructions prepended to every conversation. Project rules from `AGENTS.md` / `SKILLS.md` are prepended on top of this.

Top-level `api_url` / `api_key` / `model` are used as fallbacks when no preset is active or when a preset omits a key.

### Example

```json
{
    "request_timeout": 120,
    "active_preset": "local",
    "presets": {
        "local": {
            "api_url": "http://localhost:11434/v1/chat/completions",
            "api_key": "",
            "model": "devstral-small-2:latest"
        },
        "mistral": {
            "api_url": "https://api.mistral.ai/v1/chat/completions",
            "api_key": "YOUR_MISTRAL_API_KEY",
            "model": "devstral-latest"
        },
        "claude": {
            "backend": "claude",
            "api_key": "YOUR_ANTHROPIC_API_KEY",
            "model": "claude-sonnet-4-6"
        }
    },
    "system_prompt": "You are an expert coding assistant inside Sublime Text. Be concise.\n..."
}
```

**Mistral model IDs:** Use `devstral-latest` for the latest Devstral, or `mistral-small-latest` as a lighter fallback.

**Claude model IDs:** Use `claude-sonnet-4-6` (balanced), `claude-opus-4-8` (most capable), or `claude-haiku-4-5-20251001` (fastest/cheapest). Use **Sublime Assistant: Select Model** in the Command Palette to browse all available models.

**Switch preset:** Command Palette (`Ctrl+Shift+P`) → **Sublime Assistant: Use preset Local**, **Use preset Mistral**, or **Use preset Claude**.

### Getting a Claude API key

1. Go to [console.anthropic.com](https://console.anthropic.com) and sign up or log in.
2. In the left sidebar, click **API Keys**.
3. Click **Create Key**, give it a name (e.g. `SublimeAssistant`), and confirm.
4. **Copy the key immediately** — it is only shown once.
5. Paste it into Sublime Text via `Ctrl+Shift+P` → **Sublime Assistant: Set Claude API key**.

Claude API usage is billed per token. See [Anthropic's pricing page](https://www.anthropic.com/pricing) for current rates.

### Storing API keys securely

Keep API keys out of the plugin folder (and out of version control) by storing them in **User settings**.

**Option A — Command Palette (recommended)**

- For Mistral: `Ctrl+Shift+P` → **Sublime Assistant: Set Mistral API key** → paste key → Enter.
- For Claude: `Ctrl+Shift+P` → **Sublime Assistant: Set Claude API key** → paste key → Enter.

The key is written to your User settings file; the package file is not modified.

**Option B — Edit User settings manually**

Create or open `Packages/User/SublimeAssistant.sublime-settings`:

```json
{
    "presets": {
        "mistral": {
            "api_key": "your-mistral-api-key-here"
        },
        "claude": {
            "api_key": "your-anthropic-api-key-here"
        }
    }
}
```

Sublime merges this over the package defaults, so only `api_key` is overridden; all other preset fields stay as defined in the package.

---

## Code block format

The system prompt instructs the model to label code blocks with their target file:

    ```python:src/utils.py
    def my_function():
        ...
    ```

When a filepath is present the Apply / inline phantom workflow targets that file directly. When absent it defaults to the active editor view.

For **partial edits** (one function, one section, one table) the model is instructed to include only the changed portion and mark any skipped context with a placeholder like `# ... rest of file unchanged ...` or `<!-- ... rest unchanged ... -->` (any common comment style, or a bare `...`, is recognized). The plugin strips these placeholders and locates each real chunk of changed content independently — via your selection, a matched `def`/`class`, or fuzzy text-anchor localization against the surrounding file — so the merge only touches the lines that actually changed. Content the snippet omits (leading or trailing context it didn't bother repeating) is preserved; content it genuinely removes (a line sandwiched between two still-present anchors) is deleted. Both the phantom preview and the actual Accept run this same merge, so the diff you see is exactly what gets written.

---

## Troubleshooting

- **405 Method Not Allowed (Ollama):** The plugin sends a `tools` parameter so the model can call `fetch_url`, `read_file`, etc. Point the **local** preset at **Ollama directly** (`http://localhost:11434/v1/chat/completions`), not at Open WebUI (port 3000) — Open WebUI's proxy may reject requests that include `tools`. Ollama 0.15.x+ supports tool calling on that endpoint. If Sublime runs on Windows and Ollama is in WSL, use the WSL hostname or IP instead of `localhost`.
- **Request timed out:** Increase **`request_timeout`** in settings (e.g. 60 or 120). The fetch_url step uses a separate 30-second timeout for fetching the page; the `request_timeout` covers the LLM response after the page content is sent.
- **"truncating input prompt" in Ollama logs:** The fetched page plus your conversation exceeded Ollama's context window. Set `OLLAMA_NUM_CTX=65536` (or higher) in your Ollama environment. Devstral supports up to 384 k tokens.
- **Model not found / 400 from Mistral:** List valid IDs with `GET https://api.mistral.ai/v1/models`. Set `presets.mistral.model` to `mistral-small-latest` in User settings as a safe fallback.
- **`/init` does nothing visible:** The command triggers a background crawl and LLM enrichment pass. Check `View → Show Console` for `[SA]` log lines. The enrichment can take 30–60 seconds depending on project size and model speed.
- **Inline phantom in wrong location:** Localization is tried in order: your selection, a matched `def`/`class` name, then fuzzy text-anchor matching against the rest of the file. If the snippet shares no recognizable content with the file at all (e.g. a wholesale rewrite with very different wording), it falls back to the cursor position or a full-file replacement. Selecting the text you want changed before submitting is the most reliable way to pin the location exactly.

---

## Architecture

```
SublimeAssistant/
├── SublimeAssistant.py        # Sublime commands, phantom orchestration, streaming
├── Default.sublime-commands   # Command palette entries
├── Default.sublime-keymap     # Ctrl+L, Ctrl+Enter
├── .python-version            # Python 3.8
├── assistant/
│   ├── api.py                 # APIClient base; OpenAIClient + ClaudeClient; streaming; tool loop
│   ├── code_extractor.py      # Parse fenced code blocks from replies
│   ├── context.py             # Build LLM context block from file/selection/@refs
│   ├── diff_view.py           # Diff preview + new-file preview + smart snippet merge
│   ├── file_finder.py         # Locate files across open tabs and project folders
│   ├── git.py                 # Git subprocess helpers (diff, log, status)
│   ├── history.py             # Per-window conversation history
│   ├── input_view.py          # Input area view (bottom-right strip)
│   ├── project_rules.py       # Load AGENTS.md / SKILLS.md from git root
│   ├── slash_commands.py      # Slash command parsing and template expansion
│   ├── summarizer.py          # Crawl git root and produce a code-structure summary
│   └── view.py                # Chat panel UI helpers
└── tests/                     # Unit test suite (pytest) — see Testing below
```

## Testing

The `sublime` module only exists inside Sublime Text's embedded Python, so the test suite stubs it (`tests/conftest.py`) to unit-test the plugin's pure logic — snippet merging, code block extraction, slash commands, history, project rules — outside the editor. `assistant/diff_view.py` (the merge/localization engine behind Accept and the phantom preview) has the deepest coverage, including a regression test built from a real bug report so that class of failure can't silently come back.

```bash
pip install pytest
pytest tests/ -v
```

CI (`.github/workflows/tests.yml`) runs the suite on every push and PR to `main`, against Python 3.8 (matching `.python-version`, what Sublime Text actually runs) and 3.12 (forward-compatibility check).

## Local Development Stack

A `docker-compose.yaml` is included to spin up Ollama + Open WebUI locally with GPU support. This is optional — it is not required for basic plugin use. Ollama runs with `OLLAMA_NUM_CTX=65536` so the fetch_url tool and large conversations are unlikely to get truncated.

To start the services, run the following command in the directory containing the `docker-compose.yaml` file:

```bash
docker compose up -d
```

Open WebUI is then available at `http://localhost:3000`. Note: for the plugin, always point the **local** preset at Ollama's port (11434), not Open WebUI's port (3000). Open WebUI is optional and not required for the plugin to function.

---

## License

MIT © 2026 Josko de Boer
