# SublimeAssistant

[![tests](https://github.com/daimonie/sublime-assistant-package/actions/workflows/tests.yml/badge.svg)](https://github.com/daimonie/sublime-assistant-package/actions/workflows/tests.yml)

**An AI coding agent that lives inside Sublime Text 4.** Not a sidebar chatbot bolted onto your editor — a plugin that reads your project, proposes precise edits, and applies them exactly where they belong, with any LLM backend you choose. Local, cloud, your call.

No subscription. No Electron shell. No sending your whole file just to change one line. Just Sublime Text, talking to a model, editing your code the way you would.

---

## Why SublimeAssistant

- **You own the backend.** Point it at a local Ollama model with zero data leaving your machine, or at the Mistral or Claude API when you want more horsepower. Switch between them from the Command Palette — no config file editing, no restart.
- **Edits land exactly where they should.** SublimeAssistant doesn't paste a wall of text over your file. It localizes each change — via your selection, a matched `def`/`class`, or fuzzy anchor matching — so accepting a suggestion only touches the lines that actually changed. Everything else stays byte-for-byte alone.
- **It's an agent, not a Q&A box.** Give it a goal and let `/loop` iterate across turns — reading files, fetching URLs, crawling your project — until the goal is done. It never writes to disk without your explicit accept, but it can *investigate* on its own.
- **It respects your keyboard.** `Ctrl+L` opens it, `Ctrl+Enter` submits, everything else — accept, diff, dismiss — is a click on an inline phantom right where your cursor already is.
- **Zero dependencies.** Pure Python 3.8 stdlib, the same interpreter Sublime Text already bundles. Nothing to `pip install` to get started.

---

## What it does

- **Context-aware chat** — every query automatically includes the active file and any selection, so you don't have to paste code by hand.
- **Persistent chat panel** — a dedicated markdown split-pane holding the full conversation history per window.
- **Streaming responses** — token-by-token, so you see the reply as it's generated.
- **Inline phantom suggestions** — every code block the assistant produces shows a colored red/green diff directly in your editor at the target location, with **accept** (instant apply), **≋ diff** (preview), and **dismiss** buttons. Review and apply without leaving the file.
- **Smart, localized merging** — accepting a suggestion never overwrites the whole file. The plugin anchors the snippet to the specific lines it actually changes, so edits, insertions, and deletions elsewhere in the snippet only touch what really changed. The same merge logic drives both the phantom preview and the actual Accept, so what you see is exactly what you get.
- **Diff preview before writing** — the **≋ diff** path opens a unified diff with **✓ accept** / **✗ reject** before anything touches disk.
- **New file creation** — when the model proposes a brand-new file, the apply workflow lets you review and create it with one click.
- **Multi-file referencing** — type `@filename.ext` in the input area to pull any open or project file into context.
- **Interrupt anytime** — while a response or `/loop` run is streaming, click **⏹ Stop generating** (or press `Ctrl+C` with the input focused) to cancel.
- **Slash commands** — preset prompts (`/explain`, `/fix`, `/tests`, `/review`, `/debug`, `/docs`, `/diff`) and plugin actions (`/init`, `/compact`) triggered by typing a command alone. See [Slash Commands](#slash-commands).
- **Suggested shell commands, never run for you** — when the model recommends a test/lint/build command, it's shown in a fenced block for you to copy and run — the plugin never executes it.
- **Project rules** — an `AGENTS.md` at the git root is automatically prepended to the system prompt once per window session, so you can set project-specific conventions, constraints, or personas.
- **Agent Skills** — drop a `skills/<name>/SKILL.md` at the git root to teach the model a triggered, on-demand procedure (steps to follow, tools to call) instead of one more always-on instruction. See [Project rules and Agent Skills](#project-rules-agentsmd-and-agent-skills-skills).
- **Directory summary, built lazily** — `/init` crawls the git root and builds LLM-generated per-file descriptions, cached to disk. The model pulls context on demand via tools instead of the whole summary being injected into every message.
- **Asynchronous by design** — API calls run on a background thread; the editor never freezes waiting on a response.

---

## Harness capabilities: it can act, not just answer

SublimeAssistant's chat isn't limited to one prompt in, one reply out. It has a tool-use loop and an iteration engine underneath it, so it can go find the answer instead of guessing at it.

**Tools available mid-conversation:**

| Tool | What it does |
|------|---------------|
| `read_file` | Reads any file in the project |
| `fetch_url` | Fetches and reads a web page |
| `list_project_files` | Lists files under the git root |
| `get_file_summary` | Pulls a cached per-file summary (built by `/init`) |
| `load_skill` | Loads the full instructions for a project-defined skill (see [Agent Skills](#project-rules-agentsmd-and-agent-skills-skills)) |

Tool calls are shown in the chat footer as they happen, so you can see what the model is doing, not just what it concludes.

**`/goal` and `/loop` — a persistent, multi-turn agent:**

1. `/goal <description>` stores a goal for the window.
2. `/loop` (or `/loop <description>` to set-and-run in one step) resumes it. Each iteration is a normal turn — using the same tools above — that ends with a status marker telling the plugin whether to keep going.
3. It stops when the model reports the goal is complete, when it hits `loop_max_iterations` (default 8), or when you interrupt it.
4. **It never writes files on its own.** Every code change it proposes, even mid-loop, still surfaces as a normal Apply / inline-phantom suggestion you accept by hand.

**`/research <topic>`** runs the same engine with a research-flavored goal baked in: consult multiple sources across turns via `fetch_url`, cross-check them, and cite what it used — rather than one single-shot guess.

This makes SublimeAssistant useful for more than autocomplete: point it at a topic, a bug, or a refactor, and let it spend several turns actually looking before it answers — while you stay the only one who can commit a change to disk.

---

## Bring your own LLM

SublimeAssistant doesn't lock you into one model or one vendor. Every backend speaks through the same chat panel, the same tools, the same phantom-diff workflow — you just pick where the tokens come from.

| Backend | Where it runs | Good for |
|---------|---------------|----------|
| **Local (Ollama)** | Your machine | Zero cost per token, nothing leaves your machine, works offline |
| **Mistral API** | Cloud | No GPU required, same open model family as the local default |
| **Claude API** | Cloud | Strongest reasoning for large refactors, research, and multi-step `/loop` runs |
| **Any OpenAI-compatible endpoint** | Your choice | LM Studio, vLLM, a company-hosted gateway — anything speaking the `/v1/chat/completions` shape |

Switching is one command — **Command Palette → Sublime Assistant: Use preset Local / Mistral / Claude** — no restart, no config file editing. Mix and match per project by overriding `active_preset` in a workspace's settings.

### Running fully local

```bash
ollama pull devstral-small-2:latest
```

| Model | VRAM needed | Notes |
|-------|-------------|-------|
| `devstral-small-2:latest` (22B) | ~14 GB | Needs a high-end consumer GPU (RTX 3090/4090 or better) |

No suitable GPU? Switch to the Mistral preset — it runs the same model family in the cloud with no local hardware requirement. Ollama must be reachable at `http://localhost:11434` (its default); if Sublime Text runs on Windows with Ollama inside WSL, use the WSL hostname instead of `localhost`.

### Running in the cloud

- **Mistral** — get an API key at [console.mistral.ai](https://console.mistral.ai/), then set it via Command Palette → **Sublime Assistant: Set Mistral API key**.
- **Claude** — get an API key at [console.anthropic.com](https://console.anthropic.com/) (see [Getting a Claude API key](#getting-a-claude-api-key)), then set it via Command Palette → **Sublime Assistant: Set Claude API key**.

Keys are written to your User settings, never to the package folder or version control.

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

Restart Sublime Text. That's it for local Ollama use — no further setup needed. For Mistral or Claude, set your API key via the Command Palette after restarting (see [Running in the cloud](#running-in-the-cloud) above).

**Prerequisites:**
- Sublime Text 4, build 4050+
- Python 3.8 (bundled with Sublime Text — nothing extra to install)
- No external Python packages, ever
- Ollama (for local hosting), or a Mistral/Claude API key (for cloud)

---

## Usage

| Action | How |
|--------|-----|
| Open chat + input area | `Ctrl+L` |
| Submit a message | Type in the input strip → `Ctrl+Enter` |
| Run a slash command | Type `/command` alone → `Ctrl+Enter` |
| Interrupt a response or `/loop` run | Click **⏹ Stop generating** in the input pane, or `Ctrl+C` while the input area is focused |
| Set a goal | `/goal <description>` → `Ctrl+Enter` |
| Run the goal loop | `/loop` (or `/loop <description>`) → `Ctrl+Enter` |
| Research a topic across iterations | `/research <topic>` → `Ctrl+Enter` |
| Reference a file | `@filename.ext` anywhere in your message |
| Accept inline suggestion | Click **✓ Accept** in the editor phantom |
| Open diff for inline suggestion | Click **≋ Diff** in the editor phantom |
| Dismiss inline suggestion | Click **✗ Dismiss** in the editor phantom |
| Apply from chat panel | Click **Apply** below a code block in the chat |
| Accept / reject diff preview | **✓ Accept** / **✗ Reject** in the diff preview |
| Build project file index | `/init` → `Ctrl+Enter` |
| Clear conversation history | `/compact` → `Ctrl+Enter` |
| Switch backend | Command Palette → **Sublime Assistant: Use preset Local / Mistral / Claude** |
| Set an API key | Command Palette → **Sublime Assistant: Set Mistral / Claude API key** |
| Select model (any preset) | Command Palette → **Sublime Assistant: Select Model** |
| Summarize current directory | Command Palette → **Sublime Assistant: Summarize Directory** |

---

## Slash Commands

Slash commands are preset prompts or plugin actions triggered by typing a command alone in the input strip (e.g., `/explain`). You can append extra context after the command (e.g., `/fix there's a race condition in the handler`). Must be typed at the **start** of the input, then submitted with `Ctrl+Enter`.

| Command | Description |
|---------|-------------|
| `/explain` | Explain the selected code in detail. |
| `/fix` | Identify and fix bugs in the selected code. |
| `/tests` | Write unit tests for the selected code. |
| `/review` | Perform a code review for correctness, style, and performance. |
| `/debug` | Root-cause analysis and fix proposal for the selected code. |
| `/docs` | Write docstrings and comments for the selected code. |
| `/diff` | Explain and review the current working-tree diff. |
| `/init` | Crawl the project directory and build the file-summary cache. |
| `/compact` | Clear the conversation history for this window (also clears any stored `/goal`). |
| `/clear` | Same as `/compact`. |
| `/goal <description>` | Store a persistent goal for this window. `/goal` alone shows the current goal. |
| `/loop [description]` | Iteratively pursue a goal across multiple turns. Uses the stored `/goal` if no description is given. See [Harness capabilities](#harness-capabilities-it-can-act-not-just-answer). |
| `/research <topic>` | Multi-iteration research on a topic, citing sources. Runs on the same engine as `/loop`. |

---

## Project rules (AGENTS.md) and Agent Skills (skills/)

These are two different mechanisms for teaching the model about your project, and they
belong in different places:

- **`AGENTS.md`** (git root) — always-on instructions: coding conventions, architecture
  notes, personas, things the model must always do or avoid. Read once per window
  session and prepended in full to the system prompt, so keep it short — it costs
  context on every single turn.
- **`skills/<name>/SKILL.md`** (git root) — a *triggered* procedure, not an always-on
  rule. Each skill lives in its own subdirectory as a `SKILL.md` file with a small
  frontmatter block plus a body of steps. Only each skill's `name` and `description`
  are read up front and listed in the system prompt (one line each); the full body is
  loaded only when the model decides a skill's description matches the current request,
  via the `load_skill` tool. This keeps the system prompt cheap even with many skills,
  and means a skill's steps only ever show up when they're actually relevant.

Both are read once per window session — edit either and run `/compact` to pick up the
change mid-session.

Example `AGENTS.md`:

```markdown
This project uses Python 3.11 and follows PEP 8 strictly.
All public functions must have type hints and a one-line docstring.
Never suggest external dependencies unless explicitly asked.
```

Example `skills/verify/SKILL.md` (this repo ships one, used to check other docs like
this README against the actual code):

```markdown
---
name: verify
description: Use when the user asks whether a file is up to date, accurate, or
  reflects the current state of the project.
---

1. Call `list_project_files` to see what exists.
2. Call `get_file_summary` on the files relevant to the claim being checked.
3. Compare the target file's claims against what those files actually contain.
4. Report "Up to date" vs "Needs updating", with concrete fixes.
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
- **`loop_max_iterations`** — Maximum number of turns a `/loop` or `/research` run will take before stopping on its own (default 8). Raise it for deeper research/investigation runs; lower it to cap cost/time.
- **`system_prompt`** — Instructions prepended to every conversation. `AGENTS.md` and the Agent Skills index are prepended on top of this — see [Project rules and Agent Skills](#project-rules-agentsmd-and-agent-skills-skills).

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
- **"truncating input prompt" in Ollama logs:** The fetched page plus your conversation exceeded Ollama's context window. Set `OLLAMA_NUM_CTX=65536` (or higher) in your Ollama environment. Devstral supports up to 384k tokens.
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
│   ├── loop_runner.py         # /goal + /loop iteration prompts and status-marker parsing
│   ├── project_rules.py       # Load AGENTS.md from git root
│   ├── skills.py              # Discover skills/<name>/SKILL.md; build the index; load bodies on demand
│   ├── slash_commands.py      # Slash command parsing and template expansion
│   ├── summarizer.py          # Crawl git root and produce a code-structure summary
│   └── view.py                # Chat panel UI helpers
└── tests/                     # Unit test suite (pytest) — see Testing below
```

## Testing

The `sublime` module only exists inside Sublime Text's embedded Python, so the test suite stubs it (`tests/conftest.py`) to unit-test the plugin's pure logic — snippet merging, code block extraction, slash commands, history, project rules, Agent Skills — outside the editor. `assistant/diff_view.py` (the merge/localization engine behind Accept and the phantom preview) has the deepest coverage, including a regression test built from a real bug report so that class of failure can't silently come back.

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
