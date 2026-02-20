# SublimeAssistant

**SublimeAssistant** brings Cursor-like AI interaction directly into **Sublime Text 4**.

It connects your editor to a local LLM (via Ollama) or the Mistral API, giving you a persistent
chat panel, file referencing, and one-click code application with diff preview — without ever
leaving the keyboard. Lightweight, thread-safe, and runs on Python 3.8 with no external dependencies.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

---

## Features

- **Context-aware AI** — Automatically sends the active file and any selected text with every query.
- **Persistent chat panel** — A dedicated Markdown split-pane that keeps the full conversation history per window.
- **Multi-file referencing** — Type `@filename.ext` in the input area to include any open or project file.
- **Inline input area** — A dedicated typing strip at the bottom-right; press `Ctrl+Enter` to submit multi-line messages.
- **Apply with diff preview** — Every code block the assistant produces gets an **Apply** button. Clicking it opens a unified diff preview where you can **Accept** or **Reject** the change before anything is written to disk.
- **New file creation** — When the LLM suggests a brand-new file, the Apply workflow lets you review and create it with one click.
- **Fetch URL tool** — When you ask to read or check a web page, the assistant fetches it and includes the content in context automatically. Works with both the local and Mistral presets. Fetched content is truncated to 80 k characters. If you see "truncating input prompt" in Ollama logs, raise Ollama's context limit (e.g. `OLLAMA_NUM_CTX=32768`; Devstral supports up to 384 k).
- **Preset switching** — Switch between a local Ollama endpoint and the Mistral API from the Command Palette without touching config files.
- **Auto-reload on save** — When you edit any file in `SublimeAssistant/assistant/`, the submodule is hot-reloaded automatically. No need to restart Sublime Text during development.
- **Asynchronous** — API calls run in a background thread; the editor never freezes.

---

## Hardware requirements for local hosting

Running a coding model locally via Ollama requires a **dedicated GPU with enough VRAM**:

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
- **For cloud:** A [Mistral API key](https://console.mistral.ai/)

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
| Windows | `%APPDATA%\Sublime Text\Packages\` |
| macOS   | `~/Library/Application Support/Sublime Text/Packages/` |
| Linux   | `~/.config/sublime-text/Packages/` |

```bash
cd "YOUR_PACKAGES_FOLDER"
git clone https://github.com/YOUR_USERNAME/SublimeAssistant.git
```

Restart Sublime Text. No further setup is needed for local Ollama use.
For Mistral API use, set your API key via the Command Palette after restarting (see below).

---

## Usage

| Action | How |
|--------|-----|
| Open chat + input area | `Ctrl+L` |
| Submit a message | Type in the input strip → `Ctrl+Enter` |
| Reference a file | `@filename.ext` anywhere in your message |
| Apply a code suggestion | Click the **Apply** button below any code block |
| Accept diff | Click **✓ Accept** in the diff preview |
| Reject diff | Click **✗ Reject** in the diff preview |
| Switch to local (Ollama) | Command Palette → **Sublime Assistant: Use preset Local** |
| Switch to Mistral API | Command Palette → **Sublime Assistant: Use preset Mistral** |
| Set Mistral API key | Command Palette → **Sublime Assistant: Set Mistral API key** |

---

## Configuration

Edit `SublimeAssistant.sublime-settings` (or create a User override in `Packages/User/`).

### Settings reference

- **`active_preset`** — Which preset is used: `"local"` or `"mistral"` (or any custom name).
- **`presets`** — Map of preset names to `api_url`, `api_key`, and `model`.
- **`request_timeout`** — Timeout in seconds for an AI request (default 30). Increase to 60–120 when using fetch_url or with slow token generation.
- **`system_prompt`** — Instructions prepended to every conversation.

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
        }
    },
    "system_prompt": "You are an expert coding assistant inside Sublime Text. Be concise.\n..."
}
```

**Mistral model IDs:** Use `devstral-latest` for the latest Devstral, or `mistral-small-latest` as a lighter fallback. You can list all valid IDs with `GET https://api.mistral.ai/v1/models` using your API key.

**Switch preset:** Command Palette (`Ctrl+Shift+P`) → **Sublime Assistant: Use preset Local** or **Sublime Assistant: Use preset Mistral**.

### Storing the API key securely

Keep your Mistral API key out of the plugin folder (and out of version control) by storing it in **User settings**.

**Option A — Command Palette (recommended)**
1. `Ctrl+Shift+P` → **Sublime Assistant: Set Mistral API key**
2. Paste your key and press Enter.

The key is written to your User settings file; the package file is not modified.

**Option B — Edit User settings manually**

Create or open `Packages/User/SublimeAssistant.sublime-settings`:

```json
{
    "presets": {
        "mistral": {
            "api_key": "your-mistral-api-key-here"
        }
    }
}
```

Sublime merges this over the package defaults, so only `api_key` is overridden; `api_url` and `model` stay as defined in the package.

### Legacy single-endpoint config

If you don't use presets, the top-level keys still work:

```json
{
    "api_url": "http://localhost:11434/v1/chat/completions",
    "api_key": "",
    "model": "devstral-small-2:latest",
    "system_prompt": "You are an expert coding assistant inside Sublime Text. Be concise.\nWhen suggesting code changes:\n1. Label every code block with its target file: ```language:path/to/file.ext\n2. Reference exact file names and line numbers.\n3. Flag breaking changes or new dependencies.\n4. For new files, use the full intended relative path."
}
```

### Code block format

The system prompt instructs the LLM to label code blocks with their target file:

    ```python:src/utils.py
    def my_function():
        ...
    ```

When a filepath is present the Apply workflow targets that file directly. When absent it defaults to the active editor view.

---

## Troubleshooting

- **405 Method Not Allowed (Ollama):** The plugin sends a `tools` parameter so the assistant can use fetch_url. Point the **local** preset at **Ollama directly** (`http://localhost:11434/v1/chat/completions`), not at Open WebUI (port 3000) — Open WebUI's proxy may reject requests that include `tools`. Ollama 0.15.x+ supports tool calling on that endpoint. If Sublime runs on Windows and Ollama is in WSL, use the WSL hostname or IP instead of `localhost`.
- **Request timed out:** Increase **`request_timeout`** in settings (e.g. 60 or 120). The fetch_url step uses a separate 30-second timeout for fetching the page; the `request_timeout` covers the LLM response after the page content is sent.
- **"truncating input prompt" in Ollama logs:** The fetched page plus your conversation exceeded Ollama's context window. Set `OLLAMA_NUM_CTX=65536` (or higher) in your Ollama environment. Devstral supports up to 384 k tokens.
- **Model not found / 400 from Mistral:** List valid IDs with `GET https://api.mistral.ai/v1/models`. Set `presets.mistral.model` to `mistral-small-latest` in User settings as a safe fallback.
- **Request format:** We send `POST` to your `api_url` with body `{ "model": "...", "messages": [...], "stream": false }`, matching the [Mistral Chat Completion API](https://docs.mistral.ai/api).

---

## Architecture

```
SublimeAssistant/
├── SublimeAssistant.py        # Sublime commands + phantom/apply orchestration
├── Default.sublime-commands   # Command palette: Use preset Local / Mistral
├── Default.sublime-keymap     # Ctrl+L, Ctrl+Enter
├── .python-version            # Python 3.8
└── assistant/
    ├── api.py                 # HTTP client (OpenAI-compatible), tool call loop
    ├── code_extractor.py      # Parse fenced code blocks from replies
    ├── context.py             # Build LLM context block from file/selection/@refs
    ├── diff_view.py           # Diff preview + new-file preview management
    ├── file_finder.py         # Locate files across open tabs and project folders
    ├── history.py             # Per-window conversation history
    ├── input_view.py          # Input area view (bottom-right strip)
    └── view.py                # Chat panel UI helpers
```

---

## Local Development Stack

A `docker-compose.yaml` is included to spin up Ollama + Open WebUI locally with GPU support. Ollama runs with `OLLAMA_NUM_CTX=65536` so the fetch_url tool and large conversations don't get truncated.

```bash
docker compose up -d
```

Open WebUI is then available at `http://localhost:3000`. Note: for the plugin, always point the **local** preset at Ollama's port (11434), not Open WebUI's port (3000).

---

## License

MIT © 2026 Josko de Boer
