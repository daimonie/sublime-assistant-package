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

---

## Configuration

Edit `SublimeAssistant.sublime-settings` (or create a User override):

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
