# SublimeAssistant

**SublimeAssistant** brings the power of Cursor-like AI interaction directly into **Sublime Text 3**. 

It connects your editor to a local LLM (via Ollama) or Open WebUI, allowing you to chat with your code, generate functions, and refactor files without ever leaving the keyboard. It is designed to be lightweight, thread-safe, and fully compatible with Sublime's Python 3.3 environment.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

## Features

-   **🤖 Context-Aware AI:** Automatically pulls your active file and selected text into the prompt.
-   **💬 Sidebar Chat:** Opens a dedicated Markdown-formatted split pane for the AI conversation.
-   **📂 File Referencing:** Type `@filename.py` in your prompt to search your project and include that file's context.
-   **⚡ Asynchronous:** Runs in the background so your UI never freezes while the AI thinks.
-   **🎨 Theme Aware:** Optimizes chat output for dark themes (like Midnight) using Markdown syntax.

## Prerequisites

You need a running instance of **Ollama** or **Open WebUI**.

1.  Install [Ollama](https://ollama.com/).
2.  Pull a coding model (Devstral, Mistral, etc.):
    ```bash
    ollama pull devstral-small-2:latest
    ```
    *(Note: You can use any model you like, just update the settings).*

## Installation

Since this is a manual plugin, you need to clone it into your Sublime Text `Packages` folder.

1.  **Locate your Packages folder:**
    *   **Windows:** `%APPDATA%\Sublime Text 3\Packages\`
    *   **Mac:** `~/Library/Application Support/Sublime Text 3/Packages/`
    *   **Linux:** `~/.config/sublime-text-3/Packages/`

2.  **Clone the repo:**
    ```bash
    cd "YOUR_PACKAGES_FOLDER"
    git clone https://github.com/YOUR_USERNAME/SublimeAssistant.git
    ```

3.  **Restart Sublime Text.**

## Configuration

By default, the plugin connects to `localhost:11434` and uses `devstral-small-2:latest`.

To change this, create a file named `SublimeAssistant.sublime-settings` in the User directory, or edit the one in the package folder:

```json
{
    "api_url": "http://localhost:11434/v1/chat/completions",
    "api_key": "", 
    "model": "devstral-small-2:latest",
    "system_prompt": "You are SublimeAssistant. You are an expert developer. Be concise."
}
