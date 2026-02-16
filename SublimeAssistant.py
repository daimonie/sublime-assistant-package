import sublime
import sublime_plugin
import json
import urllib.request
import threading
import os
import re

# Global conversation history storage per window_id
CHAT_HISTORY = {}

class SublimeAssistantAskCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        window = self.view.window()
        
        # 1. Grab Context (Active file + Selection)
        file_context = self.view.substr(sublime.Region(0, self.view.size()))
        current_file_name = self.view.file_name() or "Untitled"
        
        selections = self.view.sel()
        selected_list = [self.view.substr(r) for r in selections if not r.empty()]
        selected_text = "\n".join(selected_list)
        
        # 2. Open Input Panel (Bottom Bar)
        window.show_input_panel(
            "Ask Assistant (@filename to include):", 
            "", 
            lambda user_query: self.on_done(user_query, file_context, current_file_name, selected_text), 
            None, 
            None
        )

    def on_done(self, user_query, file_context, current_file_name, selected_text):
        if not user_query.strip():
            return

        window = self.view.window()

        # 3. Prepare the Chat View (Side Panel)
        chat_view = self.get_or_create_chat_view(window)
        
        # 4. Update UI IMMEDIATELY
        # We print the User message AND the Assistant header + Placeholder right now.
        # This gives instant feedback.
        user_block = "\n___\n\n## User\n{}\n\n".format(user_query)
        bot_block = "## Assistant \n> _Thinking..._"
        
        self.append_to_view(chat_view, user_block + bot_block)

        # 5. Run API in background thread
        thread = threading.Thread(
            target=self.process_and_call_api, 
            args=(window, chat_view, user_query, file_context, current_file_name, selected_text)
        )
        thread.start()

    def get_or_create_chat_view(self, window):
        for view in window.views():
            if view.name() == "SublimeAssistant Chat":
                window.focus_view(view)
                return view
        
        if window.num_groups() < 2:
            window.set_layout({
                "cols": [0.0, 0.7, 1.0], 
                "rows": [0.0, 1.0], 
                "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]
            })
        
        window.focus_group(1)
        chat_view = window.new_file()
        chat_view.set_name("SublimeAssistant Chat")
        chat_view.set_scratch(True)
        chat_view.settings().set("word_wrap", True)
        chat_view.settings().set("line_numbers", False)
        chat_view.settings().set("gutter", False)
        try:
            chat_view.assign_syntax("Packages/Markdown/Markdown.sublime-syntax")
        except:
            pass
        
        return chat_view

    def append_to_view(self, view, text):
        view.run_command("sublime_assistant_append", {"text": text})

    def find_file_content(self, window, filename_query):
        filename_query = os.path.basename(filename_query)  # Get just the filename part
        filename_query = filename_query.strip()

        # Check open tabs first
        for view in window.views():
            path = view.file_name()
            if path and os.path.basename(path) == filename_query:  # Compare just filenames
                return view.substr(sublime.Region(0, view.size()))
            elif view.name() == filename_query:
                 return view.substr(sublime.Region(0, view.size()))

        # Check folders
        folders = window.folders()
        ignore_dirs = {".git", "node_modules", "__pycache__", "dist", "build", "vendor"}

        for folder in folders:
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if d not in ignore_dirs]
                if filename_query in files:  # Compare just filenames
                    full_path = os.path.join(root, filename_query)
                    try:
                        with open(full_path, "r", encoding="utf-8") as f:
                            return f.read()
                    except:
                        return "Error reading file: " + filename_query
        return None

    def process_and_call_api(self, window, chat_view, user_query, active_file_context, active_filename, selected_text):
        # Parse @filename
        referenced_files_content = ""
        file_matches = re.findall(r'@([a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]+)', user_query)
        
        for fname in file_matches:
            content = self.find_file_content(window, fname)
            if content:
                referenced_files_content += "\n\n--- REFERENCED FILE: {} ---\n{}\n".format(fname, content)
            else:
                referenced_files_content += "\n\n--- REFERENCED FILE: {} (NOT FOUND) ---\n".format(fname)

        self.call_api(window.id(), chat_view, user_query, active_file_context, active_filename, selected_text, referenced_files_content)

    def call_api(self, win_id, chat_view, user_query, active_file_context, active_filename, selected_text, extra_file_context):
        settings = sublime.load_settings("SublimeAssistant.sublime-settings")
        api_url = settings.get("api_url", "http://localhost:11434/v1/chat/completions")
        model = settings.get("model", "devstral-small-2:latest")
        system_prompt = settings.get("system_prompt", "You are a helpful coding assistant.")
        api_key = settings.get("api_key", "")
        
        if win_id not in CHAT_HISTORY:
            CHAT_HISTORY[win_id] = [{"role": "system", "content": system_prompt}]

        context_block = ""
        if active_file_context:
            context_block += "--- ACTIVE FILE ({}) ---\n{}\n\n".format(active_filename, active_file_context)
        if extra_file_context:
            context_block += extra_file_context + "\n\n"
        if selected_text:
            context_block += "--- SELECTED CODE ---\n{}\n\n".format(selected_text)
        
        full_user_content = "{}--- QUERY ---\n{}".format(context_block, user_query)

        current_messages = list(CHAT_HISTORY[win_id])
        current_messages.append({"role": "user", "content": full_user_content})

        data = {
            "model": model,
            "messages": current_messages,
            "stream": False
        }

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = "Bearer {}".format(api_key)

        reply = ""
        try:
            req = urllib.request.Request(
                api_url, 
                data=json.dumps(data).encode("utf-8"), 
                headers=headers, 
                method="POST"
            )
            
            response = urllib.request.urlopen(req)
            result = json.loads(response.read().decode("utf-8"))
            
            if "choices" in result and len(result["choices"]) > 0:
                reply = result["choices"][0]["message"]["content"]
            elif "message" in result:
                reply = result["message"]["content"]
            else:
                reply = "Error: Unexpected API response format."

            CHAT_HISTORY[win_id].append({"role": "user", "content": full_user_content})
            CHAT_HISTORY[win_id].append({"role": "assistant", "content": reply})

        except Exception as e:
            reply = "Error connecting to AI: {}".format(str(e))

        sublime.set_timeout(lambda: self.update_chat_view(chat_view, reply), 0)

    def update_chat_view(self, view, reply):
        # Format lines with quote indicators for the gray background effect
        lines = reply.split('\n')
        quoted_lines = ["> " + line for line in lines]
        formatted_reply = "\n".join(quoted_lines) + "\n"

        # Call the new command that REPLACES the placeholder
        view.run_command("sublime_assistant_replace_placeholder", {"text": formatted_reply})

# Helper command just for appending
class SublimeAssistantAppendCommand(sublime_plugin.TextCommand):
    def run(self, edit, text):
        self.view.set_read_only(False)
        self.view.insert(edit, self.view.size(), text)
        self.view.show(self.view.size())
        self.view.set_read_only(True)

# NEW Helper command to Swap "Thinking..." with real answer
class SublimeAssistantReplacePlaceholderCommand(sublime_plugin.TextCommand):
    def run(self, edit, text):
        self.view.set_read_only(False)
        
        # Define the exact placeholder string we added in on_done
        placeholder = "> _Thinking..._"
        
        # Check the end of the file to see if the placeholder is there
        # We check the last N characters where N is length of placeholder
        file_size = self.view.size()
        region = sublime.Region(file_size - len(placeholder), file_size)
        
        if self.view.substr(region) == placeholder:
            # If found, replace it
            self.view.replace(edit, region, text)
        else:
            # If not found (user typed something else or logic drift), just append
            self.view.insert(edit, file_size, text)
            
        self.view.show(self.view.size())
        self.view.set_read_only(True)