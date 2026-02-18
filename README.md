# SublimeAssistant

**SublimeAssistant** brings Cursor-like AI interaction directly into **Sublime Text 4**.

It connects your editor to a local LLM (via Ollama or Open WebUI), giving you a persistent
chat panel, file referencing, and one-click code application with diff preview — without ever
leaving the keyboard. Lightweight, thread-safe, and runs on Python 3.8.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

---

## Features

- **Context-aware AI** — Automatically sends the active file and any selected text with every query.
- **Persistent chat panel** — A dedicated Markdown split-pane that keeps the full conversation history per window.
- **Multi-file referencing** — Type `@filename.ext` in the input area to include any open or project file.
- **Inline input area** — A dedicated typing strip at the bottom-right; press `Ctrl+Enter` to submit multi-line messages.
- **Apply with diff preview** — Every code block the assistant produces gets an **Apply** button. Clicking it opens a unified diff preview where you can **Accept** or **Reject** the change before anything is written to disk.
- **New file creation** — When the LLM suggests a brand-new file, the Apply workflow lets you review and create it with one click.
- **Asynchronous** — API calls run in a background thread; the UI never freezes.

---

## Prerequisites

A running instance of **Ollama** or any OpenAI-compatible endpoint.

```bash
# Install Ollama, then pull a coding model
ollama pull devstral-small-2:latest
```

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

Restart Sublime Text. Requires **Sublime Text 4 build 4050+**.

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
| Set Mistral API key (User settings) | Command Palette → **Sublime Assistant: Set Mistral API key** |

---

## Configuration

Edit `SublimeAssistant.sublime-settings` (or create a User override in `Packages/User/`).

### Presets

You can switch between **local** (Ollama) and **Mistral API** (e.g. at a client) without editing URLs or keys each time.

- **`active_preset`** — Which preset is used for API calls: `"local"` or `"mistral"` (or any custom preset name).
- **`presets`** — Map of preset names to `api_url`, `api_key`, and `model`. Top-level `api_url` / `api_key` / `model` are used when no preset is active or as defaults when a preset omits a key.

Example:

```json
{
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
            "model": "devstral-small-2505"
        }
    },
    "system_prompt": "You are an expert coding assistant inside Sublime Text. Be concise.\n..."
}
```

**Devstral on Mistral:** The Mistral API offers Devstral as `devstral-small-2505` (same style of coding assistant as the Ollama `devstral-small-2`). Use the **mistral** preset with that model when you want cloud-backed Devstral without running Ollama.

**Switch preset:** Command Palette (`Ctrl+Shift+P`) → **Sublime Assistant: Use preset Local** or **Sublime Assistant: Use preset Mistral**. The status bar shows the active preset.

### Storing the API key

Keep your Mistral API key out of the plugin folder (and out of version control) by storing it in **User settings**. Sublime merges User settings over the package defaults: values in `Packages/User/SublimeAssistant.sublime-settings` override the same keys in the package, so the key is read from your User file and never needs to live in the repo.

**Option A — Command Palette (recommended)**  
1. `Ctrl+Shift+P` → **Sublime Assistant: Set Mistral API key**  
2. Paste your API key in the input bar and press Enter.  
The key is written to your User settings; the package file is left unchanged.

**Option B — Edit User settings by hand**  
Create or open `Packages/User/SublimeAssistant.sublime-settings` and set only the key (other preset options stay from the package):

```json
{
    "presets": {
        "mistral": {
            "api_key": "your-mistral-api-key-here"
        }
    }
}
```

Sublime merges this with the package settings, so `presets.mistral.api_url` and `model` stay as in the package; only `api_key` is overridden.

### Legacy single-endpoint config

If you don’t use presets, the top-level keys still work as before:

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

When a filepath is present the Apply workflow targets that file directly. When absent it
defaults to the active editor view.

---

## Architecture

```
SublimeAssistant/
├── SublimeAssistant.py        # Sublime commands + phantom/apply orchestration
├── Default.sublime-commands   # Command palette: Use preset Local / Mistral
├── Default.sublime-keymap     # Ctrl+L, Ctrl+Enter
├── .python-version            # Python 3.8
└── assistant/
    ├── api.py                 # HTTP client (OpenAI-compatible)
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

A `docker-compose.yaml` is included to spin up Ollama + Open WebUI locally with GPU support:

```bash
docker compose up -d
```

Open WebUI is then available at `http://localhost:3000`.

---

## License

MIT © 2026 Josko de Boer
