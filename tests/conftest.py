"""Pytest bootstrap: stub the `sublime` module.

`sublime` only exists inside Sublime Text's embedded Python interpreter, so
importing any `assistant/*` module outside the editor fails at `import
sublime` unless a stand-in is registered first. The stub only needs to
satisfy module-level imports — the functions under test here never call into
the real Sublime API (they operate on plain strings/lists), so the stub's
attributes are never actually exercised.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _install_sublime_stub() -> None:
    if "sublime" in sys.modules:
        return

    stub = types.ModuleType("sublime")

    class Region:
        def __init__(self, a=0, b=0):
            self.a = a
            self.b = b

        def begin(self):
            return min(self.a, self.b)

        def end(self):
            return max(self.a, self.b)

        def __eq__(self, other):
            return isinstance(other, Region) and self.a == other.a and self.b == other.b

    class Phantom:
        def __init__(self, *args, **kwargs):
            pass

    class PhantomSet:
        def __init__(self, *args, **kwargs):
            pass

        def update(self, *args, **kwargs):
            pass

    stub.Region = Region
    stub.Phantom = Phantom
    stub.PhantomSet = PhantomSet
    stub.LAYOUT_BLOCK = 0
    stub.LAYOUT_INLINE = 1
    stub.View = object
    stub.Window = object
    stub.packages_path = lambda: ""
    stub.status_message = lambda *a, **k: None
    stub.error_message = lambda *a, **k: None
    stub.set_timeout = lambda fn, delay=0: fn()
    stub.load_settings = lambda name: None
    stub.save_settings = lambda name: None
    stub.active_window = lambda: None
    sys.modules["sublime"] = stub


def _install_sublime_plugin_stub() -> None:
    """SublimeAssistant.py's command/listener classes subclass these at class-definition
    time, so they must exist (even as bare placeholders) before that module can be
    imported outside Sublime Text."""
    if "sublime_plugin" in sys.modules:
        return

    stub = types.ModuleType("sublime_plugin")

    class TextCommand:
        def __init__(self, view=None):
            self.view = view

    class WindowCommand:
        def __init__(self, window=None):
            self.window = window

    class EventListener:
        pass

    class ViewEventListener:
        def __init__(self, view=None):
            self.view = view

    stub.TextCommand = TextCommand
    stub.WindowCommand = WindowCommand
    stub.EventListener = EventListener
    stub.ViewEventListener = ViewEventListener
    sys.modules["sublime_plugin"] = stub


_install_sublime_stub()
_install_sublime_plugin_stub()


_sublime_assistant_module = None


def load_sublime_assistant():
    """Import SublimeAssistant.py (the plugin entry point at the repo root) as a
    submodule of a throwaway package rooted at REPO_ROOT, so its `from .assistant
    import ...` relative imports resolve against the real assistant/ package.
    Cached after the first call; safe to call from any test module.
    """
    global _sublime_assistant_module
    if _sublime_assistant_module is not None:
        return _sublime_assistant_module

    import importlib.util

    pkg_name = "_sa_plugin_pkg"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(REPO_ROOT)]
        sys.modules[pkg_name] = pkg

    mod_name = f"{pkg_name}.SublimeAssistant"
    spec = importlib.util.spec_from_file_location(mod_name, REPO_ROOT / "SublimeAssistant.py")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = pkg_name
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    _sublime_assistant_module = module
    return module
